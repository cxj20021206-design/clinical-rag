# 抽卡器设计：Prompt 驱动的 LLM Claim Card 抽取

**状态**：v0.1，2026-07-31。本文管**怎么从论文得到一张卡**；
**填字段时遇到歧义按什么判**在 [`CARD_EXTRACTION_SPEC.md`](CARD_EXTRACTION_SPEC.md)（十条规则，不变）。

## 0. 一句话

> 用通用程序读取论文，通过结构化 Prompt 引导大模型理解论文并生成**带原文证据**的
> Claim Card；期刊格式只能辅助定位，不能决定抽取逻辑。

分工的硬边界：

| | 负责 | **不负责** |
|---|---|---|
| 程序 | 取文字、保留页码、切批、校验格式与枚举、核验引文、存卡 | **理解论文** |
| 大模型 | 理解论文、识别临床主张、填字段、给原文 | **判定合规与分级** |

## 1. 被推翻的是什么

早先的思路是「按 Nature 的固定章节结构，用脚本找到位置，再让模型填字段」——
`if section == "Methods": extract_population()` 那种。推翻它的理由不是它现在不工作，
而是它**只在三本期刊上工作**：换 Lancet Digital Health、NEJM AI、IEEE TMI，或者拿到
作者投稿阶段的 Word 稿，规则全部失效。而本项目的真实场景就是审**投稿**。

需要说明的是：**这套方案从未落地**，仓库里没有任何按章节抽取的代码。所以"推翻"是
选型决定，不是返工。7-29 那批产物（三层卡结构、十条规则、6 张 gold、`validate_card`）
全部保留，它们描述的是**卡该长什么样**，与用什么手段读论文正交。

期刊结构降级为两个用途：① Prompt 里的**软提示**（"研究对象通常可能出现在 Methods、
纳入标准或基线表，但也可能在别处，请检查全部材料"）；② 测试语料的特征说明
（`corpus/README.md`）。**不进架构。**

## 2. 五层

```
论文（PDF / Word / HTML）
  ↓  ① 通用文档读取层        程序：取文字、保留分页、切批
  ↓  ② Prompt 驱动的抽取层    模型：三阶段——理解全文 → 逐主张填卡 → 反方自查
  ↓  ③ 证据核查层            程序：每条引文必须能在原文里字面定位（evidence.py）
  ↓  ④ 规则校验层            程序：枚举、逻辑矛盾、病种字段污染（claim_card.validate_card）
  ↓  ⑤ 人工确认层            人：只确认高危字段 + 处理三阶段之间的冲突
Claim Card（可进检索）
```

③④ 的顺序不能反：先确认"这句话确实是论文说的"，再判断"这个值填得对不对"。
引文都定位不到时，字段值对不对没有意义。

## 3. ① 通用文档读取层

**唯一职责：把任意格式变成"带位置的纯文本"。** 它不认识章节，也不该认识。

- PDF → `pypdf`（6 篇实测够用，**不需要 MinerU**）；Word → `python-docx`；HTML → 提取正文
- **必须保留分页**：页码由这一层给，不让模型填（见 §5）
- 输出 `SourceDoc(pages=[...])`，见 `evidence.py`

**长论文怎么办：先别做内部 RAG。** 实测 6 篇主文 pypdf 文本 66k–105k 字符
（≈17k–26k token），全文直投毫无压力。检索式喂长文引入的是**静默漏检**——§6a 那一轮
降噪的全部教训都是"漏了不报错比检回垃圾更难发现"。

因此定序：**默认全文直投** → 超限才分批 → 只有分批仍装不下时才考虑论文内部 RAG。
无论走到哪一档，**必须把"哪些部分进了上下文"写进 `provenance.input_coverage`**。
不记录就等于默认"看全了"，这与 ESPNIC 那次 10 条冒充 41 条是同一类风险
（§6d：沉默的截断比报错更危险）。

> ⚠️ **补充材料现在一份都没有**：`journals/` 下 `find -iname '*supp*'` 零命中，
> 只有主文 PDF。"容易漏掉补充材料"目前是 100% 漏，但这是**输入缺失**，不是 Prompt
> 问题。在补下载之前，卡上必须如实标 `supplementary: not_available`——
> 否则又变成"系统故障被记成论文缺陷"（SPEC §9）。

## 4. ② Prompt 驱动的抽取层（三阶段）

三个阶段**全部由模型完成理解**，程序只负责传递与校验。所有阶段共用一条硬约束：

> 无论信息位于标题、摘要、正文、Methods、表格、图注、附录还是补充材料，都应按语义
> 寻找答案。**不得假定任何字段一定出现在某个固定章节。**

### 阶段一：理解整篇论文 → `paper_overview`

回答：是否原创研究？有几个主要临床主张？各涉及什么疾病/人群/技术/任务？有哪些研究
队列？每个队列用于开发、内部测试、外部验证还是临床部署？

```yaml
paper_overview:
  article_type: original_research
  cohorts:
    - {cohort_id: development, purpose: model_development, source: "Hospital A", retrospective: true}
    - {cohort_id: external,    purpose: external_validation, source: "Hospital B", retrospective: true}
  claim_candidates:
    - claim_id: claim_1
      summary: "AI 辅助肺癌 CT 筛查"
      importance: primary
      separate_claim_evidence:        # ← 必填，见下
        quote: "..."
```

两处不是照搬而是加固：

- **`separate_claim_evidence` 必填。** 拆几张卡的判据是 SPEC §1 的
  「**论文自己是否为这个病种/任务做了单独的临床声明**」，不是数病种个数。
  拿不出引文的候选**强制合并**——否则 `cardiac_mri_dl_system` 会出 39 张卡
  （它覆盖 39 种心血管疾病，但只有一个通用主张）。
- **`cohorts` 不只是记录，它是判证据阶段的原料**（§6）。

`article_type != original_research`（综述/评论/社论）→ 不出卡，且归入
**"不适用"**而非"不匹配"（SPEC §3），不计入 normative 缺口。

### 阶段二：为每个主张填卡

逐字段填，每个字段必须给 `value` + `status` + `evidence.quote` + `explanation`。
不要求信息位于特定章节。

**这一阶段模型不填 `evidence_stage`**，改填 `evidence_basis` 里的可观测事实（§6）。

### 阶段三：反方自查

七问：把未来设想当成已完成研究？把数据来源场景当成实际使用场景？把算法比较对象当成
临床比较对象？把性能提升当成患者获益？混合了不同队列？有字段缺原文支持？证据阶段被
高估？

**同一个模型审自己的输出会附和自己**——`RELATED_WORK.md` §2 那篇（arXiv 2607.01103）
诊断出的"同血统偏好"正是这个。三条约束：

1. 只给**卡 + 引文**，**不给阶段二的推理过程**，否则它会顺着原推理往下走；
2. 七问逐条给 verdict + 引文，**禁止"未发现问题"这种整体答复**——能判负才算核查；
3. 「性能提升当患者获益」与「证据阶段高估」**单开一次调用**（可换模型）：
   它们正是本项目"越级要求"的核心，不该跟另外五问挤在一次里稀释。

自查的产出**不是重填卡**，而是写进 `descriptive.benefit_gap` 等字段并交人工（§5、§7）。

## 5. ③ 证据核查层（`evidence.py`，已实现）

LLM 会**改写引文**：调整标点、补全缩写、把 PDF 抽错的字母拼回正确单词。这些改写读起来
毫无破绽，**人工确认层看不出来，只有 `str.find` 看得出来**。

**规则：每条 quote 必须能在解析文本里字面定位；定位不到 → 该字段判 `not_extracted`
（硬错误，不得进入检索）。**

两档归一化是 2026-07-31 拿 6 张 gold 卡的 27 条人工引文实测逼出来的：

| 归一化 | 定位成功 |
|---|---|
| 仅折叠空白 | 20/27 |
| 第一档：Unicode 破折号/撇号/空格族 + 跨行断字缝合 | 23/27 |
| 第二档：只保留字母数字 | **25/27** |
| 第二档 + 修掉 gold 卡自身的省略号 | **27/27** ✅ |

三类 PDF 伪影，`corpus/README.md` 那句"实测零连字丢失"只覆盖了其中一类：

1. **Unicode 同形字符**——`children aged 1−59 months` 里的 `−` 是 U+2212 数学减号
   （既不是连字符也不是 en dash），`59` 与 `months` 之间是 U+2009 窄空格；
   `Alzheimerʼs` 用的是 U+02BC。模型输出时几乎必然写成 ASCII 形式。→ 第一档解决。
2. **字间空格污染**——cardiac MRI 那篇有整段被抽成
   `h yp er tr ophic c ar-\ndi om yo path`（PDF 字距渲染）。
   **第一档救不了**：模型读到这种文本会自行拼回 `hypertrophic cardiomyopathy`，
   而那个字符串在原文里根本不存在。→ 只能靠第二档。
3. **自行省略**——唯二剩下的失败是 gold 卡自己写了
   `"(sensitivity 55.5% ...) for identification"`。**这已经不是逐字引用**，两档都不该救。
   → 定下规则：**quote 内禁止省略号；要引两段就给两条 quote**（已在 `validate_card`
   里做成硬错误）。人写卡尚且如此，模型只会更频繁。

第二档为什么不危险：它**只用于确认这句话在原文里存在**，不用于展示。落盘的 quote
始终是模型给的原样字符串，程序一个字节不改——与 §6f「造出原文没有的词是 verbatim
违规，留下可辨认伪影不是」同一条铁律。

**页码是定位的副产物**，不是模型的输出字段：模型填页码基本必错，而错页码是
"看起来最像正确"的那种错。

## 6. `evidence_stage`：模型报事实，程序做映射

**这是本文对原方案唯一的实质改动。** 原方案把"论文实际完成了哪一个证据阶段"
列为模型直接回答的字段之一，改成：模型只填可观测事实，程序按判定表映射。

```yaml
gating:
  evidence_basis:
    clinical_claim_made: true      # 论文是否作出任何临床效用主张
    external_cohort: true          # 有无独立外部验证队列
    different_site: true           # 外部队列是否来自不同机构/地区
    prospective: false
    randomised: false
    deployed_in_care: false        # 模型输出是否真的进入了临床流程
    endpoint_type: model_metric    # model_metric | clinical_process | clinical_outcome
```

判定表（`claim_card.stage_from_basis()`，顺序即优先级）：

| 条件 | 阶段 |
|---|---|
| 未作任何临床效用主张 | **C0** |
| 已进入真实临床流程，且随机对照或以患者结局为终点 | **C4** |
| 前瞻/静默部署，尚未构成随机或结局终点 | **C3** |
| 存在独立外部队列 | **C2** |
| 其余（仅回顾性开发/内部测试） | **C1** |
| 事实不全 | **不映射**，留空并报原因 |

三条理由：

1. SPEC §11 已承认 **C1/C2 的界线（内部 vs 外部验证）人读都常含糊**；
2. **stage 错一级整条路由全错**——报告清单的启停完全靠它
   （`pathology_fm_benchmark` 那张 C0 卡就是靠它把 CONSORT-AI / DECIDE-AI /
   QUADAS-3 / SPIRIT-AI 四份全拦下来的）；
3. **分级映射是策略**，要能改、能审计、能对作者解释。这与 §6d「不做跨源分级归一化」、
   §6f「保留 WHO 自家 `Not recommended` 取值不映射成 strong/conditional」是同一条原则：
   分级是安全关键字段，宁可显式映射，不要模型一口报。

**本版只接入不消费**：映射结果与卡里声明的 `evidence_stage` 不一致时**报警**，
不覆盖、不改写（`study_design` 那次的教训——引入受控字段前必须先查既有消费方，
自造值曾让肺癌卡 105→15 条、所有报告清单静默关闭）。等抽卡器产出的卡积累到可对账，
再把映射改成权威来源。

自测：6 张 gold 卡的人工分级与判定表输出**逐条吻合**（C4/C4/C2/C2/C2/C0）。

## 7. ④ 规则校验层 / ⑤ 人工确认层

④ 已有（`claim_card.validate_card()`），本次新增：`explicit` 进入 `PROVENANCE_STATUS`
（此前"有原文"靠"有 quote 且无 status"隐式表示，模型少写一个 quote 就静默变成"未声明"）、
引文省略号硬错误、`evidence_basis` 枚举与一致性检查。

⑤ 要可执行，不是"看一遍"。**只确认五个高危字段**：
`condition.primary` / `clinical_task` / `evidence_stage` / `submission_date` /
`intended_context`——它们错了会静默改变门控；其余抽样。

**并且：人工确认过的卡不能回流成 gold。** 否则评测变成"抽卡器对照被它自己影响过的
答案"，是 SPEC §0 那条循环论证教训的新形态。

## 8. 卡的六处改动（2026-07-31，均只接入不消费）

| # | 改动 | 为什么现在需要 |
|---|---|---|
| ① | `paper_id` + `claim_id` + `cohorts` | 阶段一的产物此前无处安放；一篇多卡（SPEC §1）要能串回一篇 |
| ② | `gating.evidence_basis` + `stage_from_basis()` | §6 |
| ③ | `descriptive.demonstrated_effect` / `benefit_gap` | 自查第 4 问此前**没有落点**——卡里只有一个 `claimed_benefit`，模型发现问题只能原地改写，改完看不出改过。`claimed_benefit` 仍按 SPEC §8 填被检验的假设 |
| ④ | `provenance` 加页码 + `explicit` 状态 | §5、§7 |
| ⑤ | `descriptive.future_intent` | ptau217 那句 "clinical trials and **eventually** clinical practice" 被 `intended_context` 吸收后原句就消失了，自查第 1 问没有可对照的对象 |
| ⑥ | `provenance.input_coverage` | §3 |

回归：`check_gates.py` 输出**逐字一致**；11 张卡（5 示例 + 6 gold）全部零错零警告。

## 9. 评测

- **基准 6 篇**（`corpus/gold/`）：**由 Claude 拆出、未经人工核验**，所以只能当
  回归基准（跑通性、门控不变性），**不能当准确率标准答案**——抽卡器也是 Claude。
  升级成真 gold 的唯一途径：人工核验五个高危字段（§7），或拿论文自带的真人
  peer review 作外部对照。
- **留出集**：从 `journals/pdfs/` 的 **lancet_digital_health (45) / npj_digital_medicine
  (1721) / ieee_tmi (344)** 各取 2 篇——泛化性不用等将来验，非 Nature 语料手上已经有了。
  留出集**不参与调 Prompt**，且 gold 由**不写抽卡器的人**拆（老师提的偏差问题）。
- 指标分三类，**不合成单一分**：字段准确率（对 gold）；**引文定位率**（③ 的通过率）；
  **弃权正确率**（`absent` 与 `not_extracted` 有没有混）。第三类是 `RELATED_WORK.md` §2
  认定的差异化能力，别人的工作里是空白。

## 10. 执行层：`extract.py`（2026-08-01）

`prompts/` 是**指令**，本文是**设计**，中间缺的是**执行**。7-31 第一次抽 LDH 那篇时，
执行者照着框架临场做：阶段一的 `paper_overview` 根本没产出、阶段三的七问一条没跑，
而两张卡通过了全部程序校验、27/27 引文定位。**凡是靠人记得去做的环节，迟早会漏。**

`extract.py` 把四件事从"自觉"改成"程序保证"：

| 保证什么 | 之前 | 现在 |
|---|---|---|
| 阶段不被跳过 | 执行者自觉 | `stage2` 找不到 `paper_overview` **直接拒绝跑** |
| 阶段三换上下文 | prompt 里写"必须新开会话" | `--backend claude-cli` 开**新进程**；`context_isolated` 由程序写入，不接受执行者自报 |
| 第 4/7 问单独调用 | prompt 里写"必须单独一次调用" | 一次调用 = 一个问题子集 = 一个进程，默认拆 `1,2,3,5,6` / `4` / `7` |
| 阶段三看不到填卡推理 | prompt 里写"不给你，也不该要" | **白名单装配**：`STAGE_SPEC["3"]["needs"] = []`，组装函数根本不读 `02_overview/` |

### 三个值得记的实现选择

**① bundle 而不是直接调 API。** 仓库里没有任何 LLM 调用代码，环境也没有 `anthropic`
包和 API key。与其写一段没跑过的 API 代码，不如把"一次调用所需的全部输入"物化成
`request.md` + `manifest.yaml`，再交给可插拔后端：`bundle`（只装配）/
`claude-cli`（`claude -p`，新进程）/ `exec`（任意外部命令）。
**隔离于是成了操作系统给的，不是靠模型自律。**

**② `context_isolated` 是三值，不是布尔。**
`true`（本程序开的独立进程）/ `asserted`（调用方在 driver 之外安排了隔离，本程序
**没有证明**）/ `false`（同上下文，只能当下界）。把 `asserted` 折叠进 `true` 就等于让
调用方自报隔离，正是这一层要防的事。

**③ 模型读 md，程序核验 `content_list.json`。** 两者是同一次 MinerU 解析的两种序列化
（`_parse_paths` 强制同目录）。模型读 md 是因为它可读；核验用 content_list 是因为它带
`page_idx`，而页码必须由程序反查。manifest 里 `model_read` 与 `verified_against`
分别记录，**不合并成一个 `source`**——合并就说不清卡上的 source 指谁。

### 装配时抓到的一处真实泄漏

第一版把 `prompts/README.md` 整个塞进阶段三的 bundle。而 README 第 8 行拿
**LDH 那篇**当反例讲流程事故（"阶段一的 `paper_overview` 根本没产出"）——那正是被审的
这篇论文。核查者读到会先入为主地认为这张卡出自一次草率的抽取。这不是阶段二的推理泄漏，
但同样是**关于这篇论文的场外信息**。→ `_readme_for()` 对阶段三只截取"三条硬约束"之后
的部分，`excluded_by_design` 里记一条。

> 教训：隔离不是"别把上一阶段的产物给它"，是**别把任何关于这篇论文的场外信息给它**。
> 而且这条只有在把材料真正物化成一个文件、再去 grep 它的时候才会被发现。

## 11. 已知未决

- **理解论文这一步仍然是 LLM**，`extract.py` 只做装配、执行、落盘、校验——它不认识论文，
  也不该认识。"规则先于抽卡器"这条已经守住了：十条规则、三层卡、`validate_card`
  都在 driver 之前写完（SPEC §11 的理由不变：先写抽卡器，它会为通过校验给流程类论文
  编一个病种，错误固化进 Prompt 且没人看得出）。
- **阶段一尚未用 driver 实跑过**：现有的 `paper_overview.yaml` 是 7-31 手做的。
- **`evidence_basis` 的事实本身也可能被模型答错**——判定表只保证"事实→分级"这一步
  可审计，不保证事实为真。缓解：`cohorts` 与 `evidence_basis` 交叉对账（有
  `purpose: external_validation` 的队列却 `external_cohort: false` 应报警）。**未实现。**
- **一篇论文出多张卡的自动拆分未验证**（`llm_chatbot_transitions_rct` 该拆问诊/开单/
  转诊报告三张，gold 只出了主卡）。
- **补充材料未接入**（§3）。
- `SourceDoc.from_text` 走扁平文本时**页码一律为 None**——不猜页码。`corpus/text/`
  是拼接后的扁平文本，要拿页码得从 PDF 直接读。
