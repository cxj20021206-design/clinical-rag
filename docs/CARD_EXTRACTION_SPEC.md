# Claim Card 拆解规则（论文 → 卡）

**状态**：v0.1，2026-07-29（2026-07-31 订正措辞）。规则来自**逐篇通读 6 篇真实论文**
拆卡的过程，不是设计推演。⚠️ 拆卡的是 **Claude 不是人**（原文写成"人工拆解"是错标）——
规则本身依然成立（它们来自实际撞到的问题），但这 6 张卡**不能当准确率标准答案**：
抽卡器也是 Claude，那是循环论证（同 §0）。

> 本文管**填字段时遇到歧义按什么判**（与用什么手段读论文无关，故 2026-07-31 改用
> LLM 主导抽取后**一字未改**）。**怎么从论文得到一张卡**见
> [`EXTRACTOR_DESIGN.md`](EXTRACTOR_DESIGN.md)。
配套 gold 卡在 `corpus/gold/`，语料在 `corpus/text/`（Nature Medicine / Nature BME，
均带真人 peer review，来自 `journals/peer_review/reviewed_papers.jsonl` 的 225 篇）。

## 0. 为什么要有这份文档

在此之前，`examples/` 下的 5 张卡**全部是 `synthetic_example`** —— 不只是手写，而且不
对应任何真实论文，是为测门控编的合成例子。它们掩盖了真实论文的复杂性：合成卡天然是
"单病种 + 单任务 + 人群整齐"的，而**6 篇真实论文里没有一篇是这样**。

必须记住的一条方法论教训：**编卡的人如果已经知道库里有什么指南，写出来的卡天然会命中。**
`examples/claim_card_ctg_fetal.yaml` 就是先看到 WHO RECOMMENDATION 17 才写的 —— 它作为
门控回归有效（验证代码路径），作为能力证明无效（把答案写进了题干）。gold 卡必须在
**不看库内容**的前提下从论文抽出。

## 1. 一篇论文出几张卡

**判据：按 `(condition.primary × clinical_task)` 的组合拆，每张卡必须能单独回答
"这一条主张该对齐什么临床标准"。**

不拆的代价是病种字段变成混合物 —— 这正是 2026-07-28 分层改造要解决的问题，会从上游
重新引入。拆过细的代价同样真实：cardiac MRI 那篇覆盖 39 种心血管疾病，拆 39 张卡毫无意义。

所以真正的判据不是"有几个病种/任务"，而是：

> **论文自己是否为这个病种（或任务）做了单独的临床声明？**

- 做了 → 单独出卡（它有自己的目标人群、对照与终点）
- 没做，只是同一个模型在多个标签上跑了一遍 → 一张卡，`condition.primary` 填上位概念，
  具体病种列进 `descriptive.clinical_context`，并标 `condition.breadth: broad`

## 2. 多病种：填上位概念，但要标明宽度

`cardiac_mri_dl_system` 诊断 39 种疾病（含心脏淀粉样变、肥厚型心肌病），但对每一种都没有
单独的临床主张，是一个通用视觉系统在多标签上评测。

- `condition.primary.label: "cardiovascular disease"`，`condition.breadth: broad`
- 39 种具体病名进 `descriptive.clinical_context`，**不进 gating**

**为什么具体病名不能进 gating**：一进去，病种匹配就会命中一堆专病指南，而论文并没有
为任何单一疾病做临床声明 —— 拿单病种指南去要求一个通用系统，是越级要求。这与 §6e
"自动腿把错误固化进库比漏一份严重"是同一条逻辑的上游版本。

`condition.breadth` 是新增字段（`narrow` | `broad`），下游可据此决定是否放宽/收紧
normative 匹配。**本版只记录不消费**，等有了消费方再接。

## 3. 无病种论文：`不适用` ≠ `不匹配`

`llm_chatbot_transitions_rct` 是跨 24 个专科的转诊流程研究，**没有特定病种**。

- `condition.primary` 允许为 `null`，但必须显式标 `condition.scope_type: care_process`
  （取值：`disease` | `care_process` | `population_health`）

**这是本文档最重要的一条规则**：病种门控在这里的结果是**不适用**，而不是**不匹配**。

| 结果 | 含义 | 正确处置 |
|---|---|---|
| 不匹配 | 论文有明确病种，但库里没有对应指南 | 计入**缺口报告**（该补库） |
| 不适用 | 论文本就不针对病种 | **不计入缺口**，应改走流程类规范 |

混淆二者会让缺口报告直接失真：一堆流程类论文会把"normative 覆盖不足"的数字撑大，掩盖
真正缺指南的病种。这与 `absent` / `not_extracted` 的区分是同一类错误（见 §9）。

## 4. 多任务：主任务由主要终点决定

`clinical_task` 是受控单值字段。多任务论文（PreA 做问诊 + 初步诊断 + 开单 + 转诊报告）：

- **主任务进 gating，其余进 `descriptive.secondary_tasks`**
- 主任务的判据是：**论文的主要终点检验的是哪个任务**

`lungimpact_cxr_rct` 是这条规则的关键例子：AI 做的是胸片**检测**，但主要终点是
time to CT / time to diagnosis —— 检验的是**分诊优先级**是否改变了诊断路径。
故 `clinical_task: triage` 而非 `diagnosis`。

理由：主要终点决定了论文实际主张什么，也决定了该拿什么标准去要求它。按"模型在做什么"
填会填成 detection，那就会去找诊断准确性标准，而这篇论文根本没有主张诊断准确性。

## 5. 人群跨段：只有真跨儿科/成人才填 mixed

`febrile_children_referral` 的人群是 1–59 months，横跨枚举里的 `infant` 与 `child`。

- 跨段但**同属儿科** → 填更宽的那个（`child`），原文写进 `population.age_range_text`
- 只有真正跨儿科/成人才填 `mixed`，且必须在 notes 里显式提示**人群门控对本卡失效**

理由：`mixed` 会让 `check_population` 的成人/儿科硬拦失效，而那道门是核心保护
（"sepsis" 在成人与新生儿是两套完全不同的标准，§6d）。为了字面精确而牺牲一道硬门控，
不划算。

## 6. 用途语境：临床照护 / 临床试验 / 纯研究

`ptau217_alzheimer_clock` 明说模型 "would be useful to clinical trials and, **eventually**,
clinical practice" —— 它当下的用途是**临床试验富集**，不是临床决策。

新增 `intended_context`（`clinical_care` | `clinical_trial` | `research_only`），
**不要为了填满 `care_setting` 而硬编一个临床场景**。

后果：`intended_context != clinical_care` 时，临床服务类 normative（USPSTF 那种"预防服务"
职权门控）不应硬套 —— 对一个明说自己用于试验富集的模型要求"符合预防服务推荐"，是
越级要求，与 §6b 的"论文 C1 却拿 CONSORT-AI 要求它"同类。

## 7. C0 纯基准论文：要出卡，而且要显式 N/A

`pathology_fm_benchmark` 是 19 个基础模型 × 13 个队列的离线基准，全文搜不到任何临床用途
主张。这类论文**要出卡**：

- `evidence_stage: C0`、`intended_context: research_only`
- 外部临床标准检索结果应当**大部分是 N/A**，并把 N/A 的理由打印出来

**为什么不是"跳过不出卡"**：不出卡的话，系统无法区分"这篇不需要临床标准"与"这篇漏检了"。
出卡并显式 N/A 是可审计的，跳过是不可审计的。

更重要的是，这正是 `RELATED_WORK.md` §2 认定的差异化能力：arXiv 2607.01103 指出 LLM 评委
**弃权率为零**（医生遇到超出能力范围的问题会弃权），而"能说出**本文不需要临床规范对齐，
因为它不主张临床用途**"本身就是一种弃权。这条规则是那个指标的落点，不是省事的借口。

## 8. 阴性结果论文：claim 与 finding 分开记

`lungimpact_cxr_rct` 的结论是阴性（AI 优先级排序对肺癌诊断路径无显著影响），
且明确建议 "CXR AI deployments should not include workflow prioritization"。

- `claimed_benefit` 填**被检验的获益假设**（论文设立的主张），不填实测结果
- 新增 `finding_direction`（`positive` | `negative` | `mixed` | `not_applicable`）

理由：外部通道该检索什么标准，取决于论文进入了什么临床场景，与结论方向无关 —— 一篇
阴性的分诊 RCT 和一篇阳性的分诊 RCT 要对齐的是同一批标准。但**审稿重点不同**：阴性结果
论文的审查重点是结论是否被正确解读、是否过度外推否定结论，所以方向必须记下来。

## 9. provenance：`absent` 与 `not_extracted` 必须分开

每个 gating 字段都要给 `quote`（原文片段）+ `locator`（节名）。抽不到时二选一：

- **`absent`** —— 论文确实没写。**这是审稿发现**（如 pathology benchmark 没有 care_setting）
- **`not_extracted`** —— 抽卡器没找到。**这是故障**

判据：抽卡器必须能报告"我在哪些节找过"。找遍 Abstract/Methods/Results/Discussion 仍无
→ `absent`；只搜了摘要就放弃 → `not_extracted`。

混掉的后果：**"弃权/缺口报告率"这个指标会失效** —— 系统的故障会被记成论文的缺陷，
分数看起来还更好。这是 2026-07-28 分层改造时就定下的规矩，抽卡器是它的第一个真实消费方。

## 10. `submission_date` 取 Received，不取 Published

从 `Received: DD Month YYYY` 抽。**不能用 Published 日期。**

`lungimpact_cxr_rct`：Received 2025-10-06，Published online 2026-03-24 —— 相差 5.5 个月。
用 Published 会让这期间发布的指南从 `predates=false` 翻成 `true`，等于**拿作者投稿后才
出现的标准去指责作者**，直接违反项目铁律。

六篇论文的 `Received:` 字段全部一次命中，是可靠字段。

## 11. 已知未决（本版没解决）

- **`evidence_stage` 的判定仍靠人读**。六篇里 C0/C2/C4 尚可由"有无前瞻随机 / 有无外部
  验证队列 / 有无真实部署"判断，但 C1 与 C2 的界线（内部验证 vs 外部验证）在论文里
  常常写得含糊。抽卡器上线前需要一份更细的判定表。
- **一篇论文出多张卡的自动拆分未验证**：本版 6 篇里只有 `llm_chatbot_transitions_rct`
  真正需要拆（问诊 / 开单 / 转诊报告三个任务），gold 里先只出了主卡。
- ~~四个新字段尚未接入 `claim_card.py`~~ —— ✅ 2026-07-29 已接入。三处放宽 + 四个受控枚举
  （`CONDITION_BREADTH` / `SCOPE_TYPES` / `INTENDED_CONTEXTS` / `FINDING_DIRECTIONS`）
  + `PROVENANCE_STATUS`。**刻意只接入不消费**：新字段通过 `ClaimCard.scope_type` /
  `disease_gating_applicable` / `intended_context` / `finding_direction` 暴露，但不改变
  任何检索行为 —— `study_design` 那次的教训是引入受控字段前必须先查既有消费方。
  回归证明零副作用：`check_gates` 输出**逐字一致**，五张示例卡记录数不变
  （105/141/137/110/105）。
- **`not_extracted` 现在是硬错误**（而非警告）：它表示抽卡器没找到，是系统故障。放它过去，
  故障会被下游当成"论文没写"记进缺口报告 —— 系统的问题算成论文的缺陷，分数反而更好看。
  确认论文确实没写请改标 `absent`。另新增第三种状态 `inferred`（没明写但可合理推断，
  可接受但必须标出来；6 张 gold 里有 4 处用到，全部是年龄纳入标准）。

## 12. gold 卡跑出来的第一批真实结果（2026-07-29）

两张 gold 卡端到端跑通，**这是本系统第一次在"不知道答案"的输入上运行**：

- `febrile_children_referral` → 101 条，schema 零错，**normative 命中 0 条**。
  这是真实缺口而非故障：论文以 **WHO danger signs（IMCI 体系）** 为对照基线，而库里
  没有 IMCI。registry 侧检回了高度切题的同类研究（*Electronic Algorithms Based on Host
  Biomarkers to Manage Febrile Children*、*Validation of a CDSA Strategy to Reduce
  Antibiotic Prescription in Senegal*）。
  → **WHO IMCI 成为第一个由真实论文驱动、而非按病种猜测得出的摄入目标。**
- `pathology_fm_benchmark`（C0 / research_only） → 100 条。CONSORT-AI、DECIDE-AI、
  QUADAS-3、SPIRIT-AI **全部因证据阶段不匹配被拦截并打印理由**（"对 C0 论文套用本清单
  属越级要求"），TRIPOD+AI 52 条与 PROBAST+AI 34 条正确启用，normative 为空。
  与本卡 `expected_external_behaviour` 里预先写下的期望**逐条吻合** —— 这是 gold 卡
  第一次发挥"可对照的预期"作用，而不是事后解释。
