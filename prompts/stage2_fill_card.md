# 阶段二：为一个临床主张填 Claim Card

输入：论文全文 + 阶段一的 `paper_overview` + 要填的 `claim_id`。
输出：**一张** Claim Card（YAML）。一次只填一个 claim，不要把多个主张混进一张卡。

遵守 `README.md` 的五条硬约束。**你不判定证据阶段**（见 §3 规则⑨）。

---

## §0 本阶段的三条纪律

这三条不是建议，是三类**成卡之后看不出来**的错误的堵口。它们的共同点是：
每个单看都是论文里真实存在的内容，只有放回上下文才知道用错了地方。

**纪律一：阶段一的 `uses_cohorts` 是白名单。**
本卡的任何人群特征、任何性能数字、任何场景描述，只能来自这张卡的 `uses_cohorts`
所列的队列。清单之外的队列即使论文里有、数字即使真实，也**不得**用来填本卡。
理由：一篇论文的多个主张常常各有各的队列，混用会让卡描述一个论文里不存在的研究——
而且每个字段单独核对都是对的。

**纪律二：阶段一的 `unavailable_content` 是禁令。**
落在未取到部分（附录/补充材料）里的内容，一律标 `not_extracted`。
**不得用主文里别的队列的表来代替。** "材料没给全"是故障，标成 `absent`（论文没写）
等于把系统的问题算成论文的缺陷。

**纪律三：先定位，再复制，最后回搜一次。**
引文是从原文复制来的连续片段，不是凭印象重述的。PDF 伪影原样保留。
（硬约束 3。这条实测最容易破功：抽出来的引文往往"意思完全对，字符对不上"。）

---

## §1 受控枚举（只能从这些取值里选，不得自造）

| 字段 | 取值 |
|---|---|
| `condition.scope_type` | `disease` \| `care_process` \| `population_health` |
| `condition.breadth` | `narrow` \| `broad` |
| `population.age_group` | `neonate` `infant` `child` `adolescent` `adult` `older_adult` `mixed` |
| `care_setting` | `icu` `nicu` `picu` `emergency_department` `inpatient_ward` `primary_care` `outpatient_specialty` `community` `prehospital` `perioperative` `home` |
| `clinical_task` | `screening` `prevention` `diagnosis` `triage` `prognostication` `treatment_selection` `monitoring` `risk_stratification` `documentation` |
| `intended_context` | `clinical_care` \| `clinical_trial` \| `research_only` |
| `finding_direction` | `positive` \| `negative` \| `mixed` \| `not_applicable` |
| `evidence_basis.endpoint_type` | `model_metric` \| `clinical_process` \| `clinical_outcome` |
| `provenance.*.status` | `explicit` \| `inferred` \| `absent` \| `not_extracted` |

`study_design` **留空**（由程序推断）。唯一例外：论文本身是临床试验方案时填
`clinical_trial_protocol`。自造取值会**整体替换**推断结果并静默关掉所有报告清单。

---

## §2 输出骨架

```yaml
claim_card:
  paper_id: "..."
  claim_id: claim_1
  submission_date: "YYYY-MM-DD"       # 抄阶段一，连同 basis 写进注释
  region: "<ISO2，研究开展地；跨国取主队列所在地>"

  cohorts:                            # 抄阶段一里属于本 claim 的那几条（白名单）
    - cohort_id: <抄 paper_overview>
      purpose: ...
      n: ...
      subgroup_restrictions: "..."    # 引用该队列的数字时必须一并带上
      characteristics_locator: ...

  gating:                             # ← 只有这一层参与"哪份指南适用"的准入
    condition:
      primary: {label: "<病种>", codes: {}}
      breadth: narrow | broad
      scope_type: disease | care_process | population_health
      qualifiers: {}
      comorbid_context: []            # 合并症语境，**不参与准入**
      excluded: []                    # 论文明确排除的病种
    population:
      age_group: <枚举>
      age_range_text: "<原文年龄区间，没有就 null>"
      special: []                     # ← 描述**使用人群**，不是样本来源，见 §4 第 2 条
    care_setting: <枚举 或 null>
    clinical_task: <枚举 或 null>
    evidence_stage: null              # ← 不要填，见规则⑨；也不要给它 provenance 条目
    evidence_basis:
      clinical_claim_made: true|false
      external_cohort: true|false
      different_site: true|false
      prospective: true|false
      randomised: true|false
      deployed_in_care: true|false
      endpoint_type: <枚举>
    study_design: []
    intended_context: <枚举>
    finding_direction: <枚举>

  descriptive:                        # 自由文本，不参与准入……但见规则⑧的英文要求
    intended_use / target_population / intended_user / model_input / model_output /
    clinical_decision_affected / comparator / reference_standard / current_practice /
    secondary_tasks / claimed_benefit / demonstrated_effect / benefit_gap /
    future_intent / deployment_claim_level / clinical_context

  provenance:
    source: "<你实际读的那份解析产物的路径>"   # 见规则⑫
    extraction: claude_llm_pipeline_v0
    input_coverage: [...]             # 抄阶段一
    unavailable_content: [...]        # 抄阶段一（属于本 claim 的那几条）
    fields:
      <字段名>:
        status: <枚举>                # ← 必填，一条不许省（硬约束 5）
        quote: "<连续原文，禁止省略号>"
        locator: "<你在哪找到的：章节名 + 表号/图号；同一句在多处出现时指你复制的那一处>"
        cohort_id: <该证据属于哪个队列，涉及人群/性能时必填>
        note: "<status=inferred 时必填：依据什么推断、依据在哪>"
```

### `provenance.fields` 的必填清单

> 实测：8 张卡的 `provenance` 平均只覆盖 11 个 gating 字段里的 4–6 个。
> 骨架里写 `<字段名>` 这种泛指，实际执行成了"填几个算几个"。下面是清单，逐条对着填。

**必须有条目**（无论 status 是什么）：

```
condition.primary          condition.breadth         condition.scope_type
population.age_group       care_setting              clinical_task
intended_context           finding_direction
evidence_basis.clinical_claim_made      evidence_basis.external_cohort
evidence_basis.different_site           evidence_basis.prospective
evidence_basis.randomised               evidence_basis.deployed_in_care
evidence_basis.endpoint_type
descriptive.intended_use   descriptive.comparator    descriptive.claimed_benefit
descriptive.demonstrated_effect
```

**取值非空时才需要条目**：`condition.qualifiers.subtype`、`condition.comorbid_context`、
`condition.excluded`、`population.special`、`descriptive.reference_standard`、
`descriptive.current_practice`、`descriptive.future_intent`。

**不要给条目**：`evidence_stage`（你不填它）、`study_design`（留空由程序推断）。

填不出来不是省略的理由——填不出来正是 `absent` / `not_extracted` 存在的意义。

---

## §3 填字段规则

**① 病种字段是最容易被污染的字段。** `condition.primary.label` 里**不得出现**：
ML 术语、影像/数据模态词（CT、MRI、fundus、imaging…）、任务词（screening、triage、
prediction…）、修饰词（high-risk、low-dose、AI-assisted…）。这些分别写进
`clinical_task` 与 `descriptive`。被污染时所有门控会**静默失效**（不报错、不命中）。

同理，病种字段里不得写入**合并症语境**（"某病 in critical illness"）——
合并症写 `comorbid_context`，它不参与准入。

**② 一个模型跑多标签 ≠ 多个病种。** 论文没为单个病种做单独临床声明时，`label` 填
**上位概念**、`breadth: broad`，具体病名写进 `descriptive.clinical_context`。拿单病种
指南要求一个通用系统属越级要求（SPEC §2）。

**③ 没有病种的论文要显式声明。** 跨专科的流程类研究 → `primary.label: null` +
`scope_type: care_process`。这让"没命中"被读成**不适用**而不是**缺口**（SPEC §3）。

**④ 主任务由主要终点决定，不看模型在做什么。** 模型做检测、但主要终点是"多久做上
CT" → `clinical_task: triage`，`detection` 进 `descriptive.secondary_tasks`（SPEC §4）。

**⑤ 人群跨段但同属儿科 → 填更宽的那个**（如 1–59 months 填 `child`），原文写进
`age_range_text`。只有真跨儿科/成人才填 `mixed`，且必须注明**人群门控对本卡失效**
（SPEC §5）。

**⑥ 不要为了填满字段而硬编场景。** 论文说模型用于临床试验富集 → `intended_context:
clinical_trial`，`care_setting` 该空就空（标 `absent`）。纯基准论文 → `evidence_basis.
clinical_claim_made: false`、`intended_context: research_only`，**照样要出卡**并让下游
显式打印 N/A（SPEC §6/§7）。

**⑦ `claimed_benefit` 填作者主张的获益（被检验的假设），`demonstrated_effect` 填论文
实测到的，两者不同就在 `benefit_gap` 写出差距。** 阴性结果论文的 `claimed_benefit`
仍填原假设，方向记在 `finding_direction`（SPEC §8）。

- **`claimed_benefit` 逐字贴近作者原话，不得添加原文没有的限定词。**
  加一个"不必要的""高危的""早期的"，就等于替论文多主张了一个它没有定义、
  也没有测量过的分层。（判据不是词表：任何**收窄了主张适用范围**的修饰词都算。）
- **主张里的动作词决定 `demonstrated_effect` 必须给出哪一侧的量。** 主张"多检出"→
  代价在假阳性侧；主张"少做检查/分流/优先"→ 代价在漏诊侧与特异度侧；
  主张"更早"→ 代价在提前量与随访完整性。**只给受益侧的数字而不给代价侧的，
  就写进 `benefit_gap`**，不要当作已兑现。

**⑧ 被程序消费的字段必须写英文；其余字段语言不限。**

分界不是"重要不重要"，是"程序读不读"。这些字段会被**拼进英文查询串**发给
Europe PMC / ClinicalTrials.gov / openFDA，或被**英文词表**匹配以决定报告清单启停。
写中文**不报错，只静默失效**：查询串里出现中文词必然零命中（实测复数扩展还会写出
`型糖尿病人群s` 这种东西），清单门控则整层关闭。

**必须英文**——直接用论文里的英文术语原词：

| 字段 | 被谁消费 | 写中文的后果 |
|---|---|---|
| `condition.primary.label` | 检索的强制主题短语 `TITLE_ABS:"…"`；指南库病种门控 | 检索零命中 **且** 所有指南判不匹配 |
| `condition.excluded` | 与指南 scope 做排除比对 | 排除这道防线失效 |
| `descriptive.intended_use` | 检索干预词；报告清单启停 | 干预约束失效 + 清单静默关闭 |
| `descriptive.model_input` | 同上 | 同上 |
| `descriptive.model_output` | 同上 | 同上 |
| `descriptive.deployment_claim_level` | 报告清单启停 | 清单静默关闭 |
| `descriptive.target_population` | 检索的人群约束 | 该约束整档白走，降级后人群过滤消失 |
| `descriptive.claimed_benefit` | 已进 query_context，当前无连接器读取 | 暂无后果，但**一并写英文** |

**语言不限**（只给人读，程序不碰）：`intended_user`、`clinical_decision_affected`、
`comparator`、`reference_standard`、`current_practice`、`secondary_tasks`、
`demonstrated_effect`、`benefit_gap`、`future_intent`、`clinical_context`，
以及全部 `note` 与 YAML 注释。

两条写法要求：

- **不要自己翻译。** 英文字段取论文里的原词，不要译成"更标准"的说法——
  翻译会引入论文里不存在的词，而检索是拿这些词去对别人的标题摘要。
- **被消费的字段里不要中英混排。** 需要中文说明时写进同字段的 `note`，
  或写进 `clinical_context`。混排会让关键词切分把中文片段当成检索词。

**⑨ `evidence_stage` 留空，只填 `evidence_basis` 的七个事实。** 分级由程序按判定表
映射（`claim_card.stage_from_basis()`）。理由：C1/C2 界线常含糊，分级错一级整条路由
全错，而分级映射是策略、必须可审计。看不出来的事实**不要猜**，宁可整卡不映射。

七个事实**每一条都要有自己的 provenance 条目和引文，且引文取自正文不取摘要**
（硬约束 2）。特别注意这三条的常见高估：

- `external_cohort`：同院另一批数据、同一数据集的另一次切分，**不算外部**；
- `different_site`：要有原文说明数据来自不同机构/地区，不能由数据集名字推断；
- `prospective`：**前瞻是研究设计，不是"有随访"**。回顾性队列做了随访仍是回顾性。
  论文主体回顾、只有一个小的前瞻子研究时，`prospective: true` 可以填，
  但 `note` 必须写明前瞻成分只覆盖哪一部分、多大规模——
  否则下游会把整篇当成前瞻验证研究读。
- `deployed_in_care`：要有证据表明**模型输出真的改变了照护**，
  "在临床环境里采集了数据"不算。

**⑩ provenance 的四种状态含义完全不同**——见 `README.md` 硬约束 5，此处不重复。
本阶段额外一条：**`status` 一条不许省。**

**⑪ 涉及人群或数字的 provenance 条目必须写 `cohort_id`。**
（纪律一的落地动作。）年龄、性别、患病率、任何性能指标——写下它的时候先回答
"这是哪个队列的"。答不上来就说明你不该引用它。

引用受限亚组的数字时，**限制条件必须和数字写在同一个字段里**，不能只写在
`clinical_context`（那一层下游不读）。例：某敏感度只在"某指标达标且初筛阴性"的
亚组里测得 → `demonstrated_effect` 里就要带上这个限制。

**⑫ `provenance.source` 必须指向你实际读的那份解析产物。**
同一篇 PDF 用不同工具解析，抽出来的文本可以差很多（有的工具能还原表格，有的不能，
有的会丢连字）。指错了会让**解析层的能力差异被误报成抽取故障**，
也会让核验层拿另一份文本去搜你的引文，搜不到。

**⑬ 情态动词句不能支撑 gating 字段。**
`could` / `would` / `may` / `might` / `eventually` / `promising` / `has the potential to`
所在的句子讲的是作者的**设想**，不是已完成的研究。它们只能进
`descriptive.future_intent`，不能作为 `care_setting`、`intended_context`、
`clinical_task` 的 `explicit` 依据。

**⑭ 卡里不写过程元信息。**
卡会**原样**进入阶段三的核查材料。所以卡里（包括 YAML 注释）不得出现：
这篇论文属于哪个语料集、有没有可参照的答案、这次抽取是第几遍、用了什么模型、
以往对它的核查结论。这些都是**关于这篇论文的场外信息**，会让核查者先入为主。

允许写的注释只有一种：**对某个字段取值的判据说明**（"论文无显式年龄纳入标准，
依据表 X 的平均年龄推断"）——那是卡的内容，不是过程日志。
流程与语料归属记在 run 目录的 README 里。

---

## §4 七类混淆的预防条款

> 这一节与阶段三的七问**一一对应**。阶段三是事后核查，这里是事前预防——
> 两边必须同步修改，改了一边只改一半，等于把检查项变成了摆设。

| # | 混淆 | 填卡时怎么做 |
|---|---|---|
| 1 | **把作者的未来设想当成已完成的研究** | 见规则⑬。情态动词句 → `future_intent`。`intended_context` 按论文**已经做到**的那一层填，不按它想去的那一层填 |
| 2 | **把数据来源场景当成实际使用场景** | `care_setting` 回答"模型将被用在哪"，`cohorts[].selection_basis` 回答"数据从哪来"。二者不同以 `intended_use` 为准；只有数据来源的证据时 `care_setting` 标 `inferred`（note 写明依据是数据来源）或 `absent`。**`population.special` 同理**：它描述使用人群，不是入组来源——"做过某项确诊检查"是入组来源，而模型往往正是要用在**还没做这项检查**的人身上，两者是互斥人群 |
| 3 | **把算法比较对象当成临床比较对象** | `comparator` 只填**在同一批人里报告过对比数字**的对象。判定动作：论文有没有给出它与本方法的并列数字？没有就不填。参考标准（判定真值的东西）填 `reference_standard`；论文自陈的现行做法填 `current_practice`，并注明有没有真的比过。三者抄阶段一，不要在这里重新判断 |
| 4 | **把模型性能提升当成患者获益** | 见规则⑦。`claimed_benefit` 不加限定词；`demonstrated_effect` 必须覆盖主张的每一半，覆盖不到的进 `benefit_gap`。论文若断言"经济""可及""省时"而全文无对应分析，这**本身**就是 `benefit_gap` 的内容 |
| 5 | **混合了不同研究队列的信息** | 见纪律一与规则⑪。每条涉及人群/数字的证据带 `cohort_id`；受限亚组的数字带上限制条件；白名单之外的队列一律不用 |
| 6 | **存在缺乏原文支持的字段** | 见 §2 必填清单与硬约束 5。`status` 一条不许省；标 `explicit` 的必须有能定位的引文；标 `inferred` 的 `note` 必须写依据**且依据本身要能核对**（写"表 1 显示平均年龄 A–B 岁"时，A 和 B 要真的是表里的值） |
| 7 | **证据阶段被高估** | 见规则⑨。七个事实各有引文、各取自正文；`external_cohort` / `different_site` / `prospective` / `deployed_in_care` 按上面的判据从严 |

---

## §5 自检（交卷前逐条过，不要跳）

**取证**

1. 每个 `quote` 我是**从原文复制**的？写完回原文再搜了一次？（硬约束 3）
2. `population.*` / `care_setting` / `comparator` / `evidence_basis` 的引文，
   有没有哪条是从摘要或标题取的？（硬约束 2——这几个字段不允许）
3. 有没有一条引文被我用在了两个字段上？那句话的主语真的是我要填的东西吗？（硬约束 4）
4. `provenance.fields` 对着 §2 的必填清单点过一遍了？还是填到哪算哪？
5. 每个条目都有 `status`？（硬约束 5）标 `inferred` 的都写了 `note`？
   `note` 里引的数值我核对过吗？

**队列**

6. 本卡引用的每一个数字、每一条人群特征，我都能说出它来自哪个队列？
7. 有没有哪个数字来自 `uses_cohorts` 之外的队列？
8. 有没有哪个数字其实只在某个受限亚组里成立，而我把限制条件漏在别处了？
9. 落在 `unavailable_content` 里的内容，我标的是 `not_extracted` 而不是 `absent`？

**主张**

10. `claimed_benefit` 里有没有我自己加的限定词？逐词对一遍原文。
11. `demonstrated_effect` 覆盖了 `claimed_benefit` 的每一半吗？没覆盖的写进 `benefit_gap` 了吗？
12. `comparator` 里躺着的是真正比过的对象，不是参考标准、也不是只在引言里提过的现行做法？
13. `evidence_stage` 我留空了吗？没有偷偷给它写 provenance 条目吧？

**语言**

14. 规则⑧那张表里的**每一个**字段，我都写成英文了吗？——特别是
    `target_population` 与 `condition.primary.label`（这两个最容易顺手写成中文）。
15. 被消费的字段里有没有中英混排、或我自己译的词而非论文原词？
