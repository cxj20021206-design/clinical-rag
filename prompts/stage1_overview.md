# 阶段一：理解整篇论文 → paper_overview

你将读到一篇论文的**全文纯文本**（可能含补充材料）。你的任务**不是**填 Claim Card，
而是先搞清楚这篇论文整体在做什么，并列出它包含几个独立的临床主张。

遵守 `README.md` 的五条硬约束。其中与本阶段关系最大的是**硬约束 2**：
本阶段产出的队列信息、参考标准、现行做法，**几乎全部只存在于正文（Methods/Results/
表格）**，摘要里没有或被压缩过。**不要在摘要里凑答案。**

> **本阶段的产物是阶段二的约束，不是参考资料。** 阶段二被禁止使用不在本文件里的队列，
> 也被禁止填写你标记为"材料未给全"的内容。你在这里漏登记一个队列，
> 阶段二就有可能拿另一个队列的数字去填它——那种错误在成卡之后**看不出来**，
> 因为每个数字单独看都是论文里真实存在的。

## 你要回答的七个问题

1. 这篇论文是否属于原创研究？
2. 论文中有几个**独立的**临床主张？
3. 每个主张涉及什么疾病、人群、技术、任务？
4. 论文包含哪些研究队列？**每个队列的人是怎么被选进来的、有没有额外的亚组限制？**
5. 每个队列用于开发、内部测试、外部验证，还是临床部署？
6. 每个主张的**标准答案（参考标准）从哪来**？——用什么判定"真的有病"：
   病理、随访结局、另一项检查、专家判读、登记库编码？
7. 论文自己说的**现行临床做法**是什么？论文有没有真的拿它当对照跑过？

第 6、7 问是新增的，理由见文末「为什么加这两问」。

## 输出格式（YAML，不要输出别的）

```yaml
paper_overview:
  paper_id: "<DOI 或文件名>"
  article_type: original_research | review | commentary | editorial | protocol | other
  article_type_evidence:
    quote: "<原文>"

  submission_date:
    value: "YYYY-MM-DD" | null
    basis: received_date | accepted_date | search_cutoff_lower_bound | unavailable
    quote: "<原文>"          # basis=unavailable 时留空
    # 注意没有第 5 个取值可选：venue_deadline 那一档由程序补，你不得使用它
    note: "<说明>"

  cohorts:
    - cohort_id: <短标识>
      purpose: model_development | internal_validation | external_validation
             | prospective_evaluation | deployment | longitudinal_followup
      source: "<机构/数据集名，原文写法>"
      n: <人数或图像数，原文给什么写什么>
      retrospective: true | false | unknown
      different_site_from_development: true | false | unknown

      # —— 以下三项 2026-08-01 新增，直接为阶段二的取证服务 ——
      selection_basis: "<这批人是怎么被选进来的，原文说法>"
        # 例：做过某项确诊检查、某登记库连续入组、某门诊连续就诊、体检人群。
        # ⚠️ 这是**样本来源**，不是模型的使用人群，两者常常互斥（硬约束 4 的第三例）。
      subgroup_restrictions: "<额外的纳入/排除限制，没有就 null>"
        # 例：某指标高于阈值、某项初筛为阴性者。
        # 性能数字往往只在这个受限亚组里成立，阶段二引用该数字时必须一并带上。
      characteristics_locator: main_text_table | main_text_prose | appendix
                             | supplementary | not_reported
        # 这个队列的人群特征（年龄/性别/患病率）写在哪。
        # 标 appendix/supplementary 而材料未给全时，阶段二对该队列的人群字段
        # 只能标 not_extracted，**不得用别的队列的表来推断**。

      quote: "<原文，取自 Methods/Results，不要取摘要>"
      status: explicit | inferred | not_extracted

  claim_candidates:
    - claim_id: claim_1
      summary: "<一句话>"
      importance: primary | secondary
      # 这三项是阶段二起草 gating 与 descriptive 的原料，**用论文里的英文术语原词**
      # （它们最终会被拼进英文检索串，见 stage2 规则⑧）。不要翻译，不要中英混排。
      condition: "<病种，原文英文说法>"
      population: "<人群，原文英文说法>"
      task: "<任务，原文英文说法>"
      separate_claim_evidence:       # 必填，见判定规则 ②
        quote: "<原文>"
      uses_cohorts: [<cohort_id>, ...]     # **排他清单**，见判定规则 ⑥

      # —— 以下两项 2026-08-01 新增 ——
      reference_standard:
        label: "<本主张用什么当标准答案>"
        quote: "<原文>"
        status: explicit | inferred | absent | not_extracted
      current_practice:
        label: "<论文自陈的现行临床做法，没有就 null>"
        head_to_head: true | false | unknown
          # 论文有没有在**同一批人**里把自己的方法与现行做法直接比过。
          # 只在引言里引文献报告现行做法的表现 → false（那不是本文做的比较）。
        quote: "<原文>"
        status: explicit | inferred | absent | not_extracted

  input_coverage:
    - {part: main_text,      included: true|false, note: ""}
    - {part: appendix,       included: true|false, note: ""}
    - {part: supplementary,  included: true|false, note: ""}

  unavailable_content:            # 2026-08-01 新增；included:false 时必填，见判定规则 ⑦
    - part: appendix
      pages: "<原文提到的页码，没有就 null>"
      contains: "<论文说这部分放了什么>"
      affects_claims: [<claim_id>, ...]
      quote: "<论文中指向该部分的那句话>"
```

## 判定规则

**① `article_type` 不是 `original_research` 就停在这一步。** 不出卡，并写明这是
**「不适用」而非「不匹配」**（`CARD_EXTRACTION_SPEC.md` §3）——综述/评论不该被记进
"临床规范覆盖不足"的缺口。

**② 拆几个主张，判据是「论文自己是否为它做了单独的临床声明」，不是数病种或任务个数。**
（SPEC §1/§2）判"做了单独声明"的可操作信号，满足两条以上才算：

- 有自己的数据集/队列
- 有自己的目标人群
- 有自己的对照
- 有自己的终点或性能报告

**`separate_claim_evidence.quote` 必填。给不出引文的候选，必须与其他候选合并。**
一个覆盖多种疾病但只有一个通用主张的系统 → **一个** claim，`condition` 填上位概念。

**③ `submission_date` 按阶梯找，找到哪一档就停：**

| 档 | 来源 | `basis` |
|---|---|---|
| 1 | 论文里的 `Received:` 日期 | `received_date` |
| 2 | 论文里的 `Accepted:` 日期 | `accepted_date` |
| 3 | 论文自报的文献检索截止日（"We searched … to <date>"） | `search_cutoff_lower_bound` |
| 4 | 都没有 | `unavailable`，value 填 null |

**永远不要用 Published / Advance online 日期**（SPEC §10）：那会把作者投稿后才出现的
指南判成 `predates=true`，等于拿作者当时看不到的标准指责他。第 3 档是**保守下界**
（检索截止必然早于投稿），会让更少指南被判 predates，宁可少不可多。

**第 4 档不是死路，但也不归你管。** 会议的匿名送审稿（PDF 上只有 "under
double-blind review"，一个日期都没有）必然落到第 4 档 —— 这是**正确行为**，照实写
`unavailable` 就好。此后会有一档 `venue_deadline`：由运维方维护的会议截止日表提供，
程序（`extract.py stamp-date`）机械补入。

> ⚠️ **你不得推断这个日期，一次都不行。** 包括但不限于：凭记忆写出某会议某届的截止日、
> 拿参考文献里最新的年份反推、按"论文看起来是哪年的"估。理由是**猜错了没有任何地方会
> 报错** —— 一个错误的投稿日会让整批 predates 判定悄悄反向，而结果看起来完全正常。
> 这跟"页码由程序给、你不要填"是同一条道理。日期缺就缺着，程序知道该找谁要。

⚠️ `Received:` 一次命中是某些期刊的排版特点，**不通用**。找不到就老实走到第 4 档，
不要拿在线发表日或修回日顶替。

**④ `cohorts` 要逐个列，不要合并同类项。** 它是下一阶段判证据阶段的原料：
`different_site_from_development` 直接决定 C1/C2 的界线。看不出来就写 `unknown`，
**不要猜**。

同一个数据集被用于两个不同用途（既做外部验证又做纵向随访）→ **列成两条**，
`cohort_id` 分开。阶段二引用性能数字时要能指到唯一一条。

**⑤ `input_coverage` 如实填。** 论文引用了 appendix 但你没拿到 → `included: false`
并在 note 里写明。不填等于默认"看全了"。

**⑥ `uses_cohorts` 是排他清单，不是提示。**
阶段二填某张卡时，**只允许引用该 claim 的 `uses_cohorts` 里列出的队列**。所以：

- 拿不准某队列属于哪个 claim → **不要两边都写**，写进你最有把握的那个，
  并在 `summary` 里说明另一个 claim 可能也用到它；
- 一个队列确实同时服务两个 claim（例如同一批人既测了 A 又测了 B）→ 两边都列，
  但这是**需要引文支持的判断**，不是省事的默认选项；
- 漏列的代价：阶段二会发现某个字段无据可填，标 `not_extracted` 停下——**这是安全侧**。
  多列的代价：阶段二会拿别的主张的人群/数字来填这张卡，而且成卡之后看不出来。

**⑦ `unavailable_content` 是给阶段二的禁令。**
只要有 `included: false` 的部分，就要在这里逐条登记它装了什么、影响哪些 claim。
阶段二遇到这些内容时**只能标 `not_extracted`**，不得用主文里别的队列的表来代替。

判据很简单：论文出现"…… are in the appendix / see supplementary table …"这类指路句时，
把那句话抄下来，并判断它指的内容属于哪个 claim。

**⑧ 第 6、7 问要分清三件常被混作一谈的东西。**

| 是什么 | 定义 | 在卡里的去处 |
|---|---|---|
| **参考标准** | 用来判定"真值"的东西 | `reference_standard`，**不是** comparator |
| **对照** | 被拿来和本方法比高低的另一种方法 | `comparator` |
| **现行做法** | 临床上现在实际在用的办法 | `current_practice`；它**可能**被当对照跑过，也可能没有 |

三者常常是三个不同的东西。判"是不是对照"的动作判据：
**论文有没有报告过它与本方法在同一批人里的对比数字。** 没有就不是对照，
哪怕论文反复提到它。

`current_practice.head_to_head: false` 本身就是一条有价值的记录——
它意味着"这个模型没跟现在临床上实际在用的办法比过"，那是审稿要问的问题。

## 自检（交卷前逐条过）

1. 每个 `quote` 我是**从原文复制**的，不是重写的？（硬约束 3；写完回原文再搜一次）
2. `cohorts` 里每条的 `quote` 是不是取自正文，不是摘要？（硬约束 2）
3. 每个 `status` 都填了？没有靠"有引文就默认 explicit"？（硬约束 5）
4. 论文提到的每一个数据集/队列，我都登记了？还是有几个被我合并掉了？
5. 每个 `claim_candidate` 的 `uses_cohorts` 我都能说出理由？有没有为了保险两边都写？
6. 有 `included: false` 的部分，我在 `unavailable_content` 里登记了吗？

## 为什么加第 6、7 问

这两问不是为了信息更全，是因为**阶段二没有它们就必然填错一个特定的字段**：

`comparator` 这个字段有三个候选占位者（参考标准、算法对照、现行做法），
而阶段二一次只读一个 claim，很难分清哪个是哪个。实测中出现过把**参考标准写进
comparator 位**的情况——后果是下游会拿"跟现行做法比过"的标准去要求一篇
从未做过该比较的论文，或者反过来，把"没跟现行做法比过"这条审稿发现给掩盖掉。

在阶段一把三者当成三个字段分开登记，阶段二就只是搬运，不用再判断。
