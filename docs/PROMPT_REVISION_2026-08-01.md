# 抽卡 Prompt 重写（2026-08-01 下半场）

**这份是给"回来接着看"用的。§1 一分钟，§2 是结论，§5 是没做完的。**

---

## 1. 一句话

上一轮隔离核查挑出 20 条 issue。**没有回填进卡**——按老师的意见，
改卡只治这一篇，**改 prompt 才治以后每一篇**。

老师另加一条约束：**不许针对单个 case 改写，要泛化地看。**
所以先做了一次跨论文验证，只有在多篇上复现的失败模式才写进规则；
只在一篇上出现的，写进文档当例子。

---

## 2. 泛化验证的结果（这是本轮的实质结论）

在 `corpus/gold/` 6 张卡 + 本样例 2 张卡（共 8 张、7 篇论文、跨 Nature Medicine /
Nature BME / Lancet Digital Health）上机械扫描同一批失败模式。

### 2.1 我原来的猜测被证伪一条

| 猜测 | 结果 |
|---|---|
| "引文取自引言里的领域背景句" | **证伪**。51 条 `provenance` 里只有 4 条来自 introduction |

方向猜对了一半——问题确实在**取证的地方**，但不是"引言"，是**摘要**。

### 2.2 三条在 8/8 或 6/6 上复现的真问题

| # | 现象 | 覆盖 | 后果 |
|---|---|---|---|
| A | **67% 的出处只来自摘要和标题**（51 条里 32 条 `abstract` + 2 条 `title`），**来自 Methods 的一条都没有** | 8/8 张卡 | `population.age_group` 有 6 张卡是"推断且无引文"（年龄纳入标准写在 Methods）；`comparator` 8 张卡里只有 1 张有出处 |
| B | **27/51 条 `provenance` 根本没写 `status`** | 8/8 张卡 | 旧约定是"有引文且没写 status ＝ explicit"，与"忘了填"无法区分。而 `absent`（审稿发现）与 `not_extracted`（系统故障）后果完全相反 |
| C | **27 条引文没有一条精确匹配原文**：25 条要靠折叠空白与 Unicode 归一化，2 条要靠"只留字母数字"这一最激进档 | 6/6 篇 | 说明引文是凭印象**重述**的，不是复制的。伪影：ﬁ/ﬂ 合字、丢失的所有格撇号、U+2212 数学减号冒充连字符 |

附带：两张 gold 卡上出现**同一条引文同时充当两个字段的出处**（2/6 篇）。

### 2.3 两条不依赖论文的结构缺陷（不需要多篇验证）

| # | 缺陷 |
|---|---|
| D | **阶段二拿不到阶段一的队列表**。阶段一辛苦登记了 `cohorts`，阶段二的输入骨架里根本没有队列这一项，也没有任何规则说"本卡的数字只能来自本 claim 的队列"→ 上一轮 4 条跨队列混合全部由此而来 |
| E | **阶段三的七问，在阶段二没有任何对应的预防条款**。阶段二的规则全是"这个字段填什么值"，阶段三的问题全是"你有没有把 A 当成 B"。两边不对称 ⇒ 每篇都会重犯同样的错，只能靠事后抓 |

### 2.4 规则的写法也改了

原来差点写成"不许写'不必要的'"——那是**黑名单**，只挡一个词。
改成给判据：**任何收窄了主张适用范围的修饰词都算**，因为它隐含了一个论文
没有定义、也没有测量过的分层。前者只对一篇有用，后者对所有
"减少不必要的 X""识别高危 Y"都有用。

---

## 3. 改了什么

### `prompts/README.md`

- 硬约束 **三条 → 五条**：
  - 2（重写）：**摘要不是默认取证来源**。给了可执行清单——
    `population.*` / `care_setting` / `comparator` / `evidence_basis` 的引文
    **不得取自摘要、标题或 Research in context**，必须取正文。
    （旧版"不得假定信息位于固定章节"描述的是愿望，没给动作，实际执行成了"在摘要里找一句像的"。）
  - 3（重写）：引文是**复制**来的不是重述来的。给了三步动作：定位 → 整段照搬（PDF 伪影原样保留）→ **回原文再搜一次自己写下的字符串**。
  - 4（新增）：**一条引文只能支持它真正在讲的那个字段**。自检问法：这句话的主语是不是我要填的那个东西。
  - 5（新增）：**`status` 必填**，四种状态不可互相顶替；本项目自己的标签词（`C2`、`screening`）不构成 `explicit` 的依据。
- 结构改造：加 `<!-- STAGE3_SAFE_BEGIN/END -->` 标记，**标记内不得出现任何具体论文的名字、病种、原句或数字**；规则的实测证据全部移到文末「附录：规则的来历」（在标记之外）。

### `prompts/stage1_overview.md`

- 五问 → **七问**，新增：第 6 问「参考标准从哪来」、第 7 问「论文自陈的现行做法是什么、有没有真拿它当对照跑过」。
- `cohorts` 每条新增三项：`selection_basis`（样本怎么选进来的）/ `subgroup_restrictions`（性能数字往往只在受限亚组成立）/ `characteristics_locator`（人群特征写在主文还是附录）。
- `claim_candidates` 新增 `reference_standard` 与 `current_practice{label, head_to_head}`。
- 新增 `unavailable_content`：未取到的部分装了什么、影响哪些 claim → **这是给阶段二的禁令**。
- 新增判定规则 ⑥（`uses_cohorts` 是**排他清单**）、⑦（`unavailable_content` 是禁令）、⑧（**参考标准 / 对照 / 现行做法是三个不同的东西**，判"是不是对照"的动作判据＝论文有没有报告过它与本方法在同一批人里的并列数字）。

### `prompts/stage2_fill_card.md`（改动最大）

- 新增 **§0 三条纪律**：白名单（队列）/ 禁令（未取到的材料）/ 先定位再复制再回搜。
- 新增 **`provenance.fields` 必填清单**（19 个必填 + 7 个按需），替代原来 `<字段名>` 泛指。
- 骨架新增 `cohorts`（抄阶段一白名单）、`descriptive.reference_standard` / `current_practice` / `demonstrated_effect` / `benefit_gap` / `future_intent`，`provenance` 条目新增 `cohort_id`。
  **注：这些都只是 descriptive/自由层，`claim_card.py` 与 schema 一行没改**（沿用"只接入不消费"）。
- 原十条规则**全部保留**（它们是 SPEC §1–§10 的镜像），新增：
  - ⑦ 补：`claimed_benefit` 不得加原文没有的限定词；**主张里的动作词决定 `demonstrated_effect` 必须给出哪一侧的量**（"多检出"→假阳性侧；"少做检查/分流"→漏诊与特异度侧；"更早"→提前量与随访完整性）。
  - ⑨ 补：`evidence_basis` 七事实各自要引文且取自正文；`external_cohort` / `different_site` / `prospective` / `deployed_in_care` 的从严判据。
  - ⑪ 涉及人群或数字的 provenance 必须写 `cohort_id`；受限亚组的限制条件要和数字写在同一字段。
  - ⑫ `provenance.source` 必须指向你实际读的那份解析产物。
  - ⑬ 情态动词句（could/eventually/promising…）不能支撑 gating 字段。
  - ⑭ **卡里不写过程元信息**（属于哪个语料集、有没有参照答案）——卡会原样进阶段三。
- 新增 **§4 七类混淆的预防条款**，与阶段三七问**一一对应、同序号**，并注明两边必须同步改。
- 新增 **§5 自检清单** 13 条（取证 / 队列 / 主张三组）。

### `prompts/stage3_adversarial_check.md`

- 新增 **§填卡约定 9 条**（不点名论文）：判 issue 前先确认这不是约定行为，否则会把"按约定填对了"报成错。
- 七问逐条细化，并注明与 stage2 §4 一一对应。
- 新增 **severity 判据**：从"感觉多严重"改成**按后果分档**（`high`＝错在 gating 层会整片拿错标准 / `medium`＝会读出论文里不存在的研究 / `low`＝出处措辞问题不改变语义）。起因是实测同一条发现两次运行判出不同严重度。
- **不要自己填 `context_isolated`**——实测模型自报时两个方向都错过。

### `extract.py`（配套的程序保证）

| 改动 | 为什么 |
|---|---|
| README 切法从"按标题名 find"改成 **按 `STAGE3_SAFE` 标记，找不到就硬退出** | 原来 find 的是字符串 `"## 三条贯穿所有阶段的硬约束"`。我把标题改成"五条"，它会找不到，然后**默默退回整份 README**，把上次修掉的泄漏原样放回来且不报错——本项目已记过多次的同型静默失效 |
| `_claim_constraints()`：把阶段一的**队列白名单**与**未取到材料**摘成显式约束附进阶段二的 request | 白名单藏在一份大 YAML 里 ≠ 白名单被单独列出来。摘录是纯机械动作，判断仍在模型那边 |
| 同上：`--claim` 打错字 / `uses_cohorts` 引用未登记队列 → **直接退出** | 此前打错 claim_id 会照常装配、照常出卡，卡里 claim_id 是错的而流程一路绿灯 |
| `_audit_stage3_leak()`：装配阶段三时**自动扫**指令段有没有提到语料库里任何论文的标识 | 见下 §4 |
| `STAGE_SPEC["3"]["extra_docs"]` 清空 | 见下 §4 |

---

## 4. 顺带抓到的一处真泄漏

`docs/CARD_EXTRACTION_SPEC.md` 一直被当作"字段判据"喂给阶段三，而**它是从 6 篇真实
论文归纳出来的，逐条点名那 6 篇**，§12 还给出它们跑出来的结果。

⇒ 被审论文若正好是其中一篇，**核查者先拿到了标准答案**。这与 `ctg_fetal` 那次
循环论证同型（先看到答案再编题）。

处置：把 SPEC 移出阶段三的 `extra_docs`；阶段三真正需要的那 9 条填卡约定，
以**不点名论文**的形式写进 `prompts/stage3` §填卡约定。
另把样例卡头部"本篇不在 corpus/gold 里"那句过程注释删掉（同类问题）。

**这两处都是人工 grep 才发现的**，所以加了 `_audit_stage3_leak()` 在装配时自动扫：
指令段若提到 `corpus/gold/*` 的论文名或 `sample/*` 的 run 目录名 → 硬退出。
实测能抓到人为注入的两种泄漏。

---

## 5. 补记（同日晚）：字段语言规则从 4 个字段扩到 8 个

**触发**：讲解检索链路时把 DKD 卡的查询串打了出来，看到人群约束被拼成

```
TITLE_ABS:"糖尿病患者；前瞻研究为" OR TITLE_ABS:"型糖尿病人群"
OR TITLE_ABS:"糖尿病患者；前瞻研究为s" OR TITLE_ABS:"型糖尿病人群s"
```

—— `descriptive.target_population` 写的是中文，被 `keywords()` 切成片段、还被
`expand_plural()` 加上了复数 `s`。查英文文献库必然零命中。

**性质**：与 2026-07-31 那次"写中文静默关掉报告清单"**同型**，只是换了个字段。
旧规则⑧只点名了 `model_input` / `model_output` / `intended_use` /
`deployment_claim_level` 四个——那份清单是按"清单门控的消费方"列的，
**漏掉了"检索查询的消费方"**。

**核对方式**（不靠回忆，逐个 grep 消费方）：

```bash
grep -rn 'query_context.get' connectors/*.py        # 连接器实际读哪些 query 键
grep -n  'def infer_study_designs' -A 30 connectors/curated_reporting.py
grep -n  'def legacy' -A 40 claim_card.py            # 键 → 卡字段的映射
```

结论：必须英文的字段共 **8** 个（旧 4 个 + `condition.primary.label` +
`condition.excluded` + `target_population` + `claimed_benefit`）。
`claimed_benefit` 当前无连接器读取（`outcome` 键无消费方），但已在 `query_context`
里占位，一并要求英文。其余 descriptive 字段与全部 `note` 语言不限——
**分界是"程序读不读"，不是"重不重要"**。

**改动**：`stage2` 规则⑧改写为带消费方与后果的表格 + 两条写法要求（不自己翻译、
被消费字段不中英混排）；`stage2` §5 自检加 14/15 两条；`stage1` 的
`claim_candidates.condition/population/task` 注明用论文英文原词。
**schema 与代码一行没改。**

⚠️ 现有的两张 DKD 样例卡 `target_population` 仍是中文，**尚未按新规则重填**
（本轮不改卡，同 §5 第 3 条）。

**待办**：这条同样"只写在 prompt 里靠模型自觉"。程序侧更可靠的堵法是在
`keywords()` / `expand_plural()` 里过滤非拉丁字符（至少别给中文加 `s`），
或在 `validate_card()` 加"被消费字段含 CJK ＝ 错误"的机械校验。**两者都还没做。**

---

## 6. 没做完的（回来从这里接）

1. **改完的 prompt 一次都还没实跑过。** 现有的卡与核查报告全部出自旧 prompt。
   最小验证：拿本样例重跑阶段二（两张卡）+ 阶段三，看新规则是否真的改变产物——
   预期变化是 `provenance` 条目数从 7–8 涨到 19+、引文更多来自 Methods、
   出现 `cohort_id`。**这一步没做，所以本轮的效果尚属未验证。**
2. **`docs/CARD_EXTRACTION_SPEC.md` 没同步。** 本轮新增的判据（取证位置、status 必填、
   队列白名单、参考标准/对照/现行做法三分）应当补进 SPEC，否则 SPEC 与 prompt 会漂移。
3. **上一轮那 20 条 issue 仍未回填进卡**（本轮刻意不做——先修 prompt）。
4. `claim_card.validate_card()` 可以增加一条机械校验：**gating 字段缺 `provenance` 条目
   ＝ 错误**。现在这条只写在 prompt 里靠模型自觉，程序不检查。
5. 上一轮遗留的四件待拍板（`STATUS_2026-08-01.md` §4）一件都还没定：
   A 收稿日取不到怎么办 / B′ 阶段三默认走 `claude-cli` / C′ 该加的是 `reference_standard`
   而不是拆 comparator / D push 前剥 `corpus/text/` 的 6 篇 Nature 全文。
   **其中 C′ 本轮已在 prompt 侧按"加 `reference_standard`"的方向落地，
   但只落在 descriptive 自由层，没动 schema。**
6. **两天的改动全部未 commit**（现在是三天了）。

---

## 7. 回来怎么验（都不联网，秒级）

```bash
cd /work/hdd/bgkq/Code/clinical-rag

python3 check_gates.py                                  # 门控回归，退出码 0
python3 extract.py verify --run sample/dkd_retinal_ldh  # ③④ 硬门，零错零警告

# 看阶段三实际收到的指令段（应无任何论文标识，无 SPEC，无阶段一/二产物）
python3 extract.py stage3 --run sample/dkd_retinal_ldh \
    --card sample/dkd_retinal_ldh/03_cards/claim_1_screening.yaml --questions 4
less sample/dkd_retinal_ldh/_bundles/stage3_claim_1_screening_q4/request.md
```

以上三条本轮跑过，全绿。
