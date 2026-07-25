# clinical-rag 设计文档

> 2026-07-22。本项目是 MedAI 论文审稿系统里**外部证据通道**的独立实现。

## 1. 为什么要一个独立的外部证据通道

MedAI 审稿有两个证据通道，**必须严格分工，不能混**：

- **外部查询系统（本项目）**：回答"现实医学世界的标准是什么"。
- **内部原文核验（另一子系统）**：回答"这篇论文究竟证明了什么"。

一旦混在一起，模型很容易**用外部常识替论文补证据**（"这类方法通常能到 90% AUC，所以本文大概也行"）。
所以本项目**只**建立临床上下文，绝不用外部知识替论文证明它的性能/代表性/外部验证/患者获益。

一句话：**外部证据定义"应该证明什么"，论文内部证据决定"作者有没有证明"。**

## 2. 输入：Clinical Claim Card

外部系统的输入是一张结构化的 Claim Card（PICO / intended-use）。示例见
`examples/claim_card_lung_ct.yaml`。字段（对应上游架构文档）：

```
disease_or_condition / intended_use / target_population / intended_user /
care_setting / model_input / model_output / clinical_decision_affected /
comparator / claimed_benefit / claimed_harm_reduction /
evidence_stage(C0-C4) / deployment_claim_level / region / submission_date
```

**证据阶段 C0–C4**（决定"用什么标准审"，避免拿临床试验要求苛责一个基础方法）：
- C0 基础方法，无明确临床效用主张 —— 不应因没临床试验被判低分
- C1 回顾性开发/内部测试 —— 不能声称临床效用已证明
- C2 独立外部/跨中心验证 —— 才能较强讨论 transportability
- C3 前瞻/静默部署验证 · C4 真实工作流/临床试验/患者结局 —— 必须评 human factors/workflow/safety

> Claim Card 的**自动抽取**（论文 → 卡）属上游/内部子系统，本项目消费已抽好的卡。

## 3. 八个审查模块 → 源角色路由

`clinical_sources.yaml: module_routing` 把 8 个模块映射到源角色：

| 模块 | 首选源角色 |
|---|---|
| clinical_question | normative, epidemiology, evidence_synthesis |
| population_validity | normative, epidemiology, registry |
| reference_standard | normative, regulatory, terminology |
| comparator_baseline | normative, evidence_synthesis, regulatory |
| endpoint_utility | normative, evidence_synthesis |
| generalization | normative, registry, regulatory, reporting_tool |
| safety_harm_equity | normative, regulatory |
| workflow_deployment | reporting_tool, regulatory, normative |

路由时另外两条规则（见 `retrieve.py`）：源若在自身 `modules` 字段声明服务某模块也会被选中；
`discovery` 源（Europe PMC 等文献发现）对所有模块作补充。**module_routing 里点名、但当前无任何带连接器
的源的角色**（如 normative）会被明确标为"待策展"缺口，而不是静默漏掉。

## 4. 输出：external_standard 记录（Claim–Evidence Graph 外部侧）

每条外部标准是一个 `ExternalStandard`（`schema.py`，对应资料源清单 §5），关键字段：

- 身份：`source_id / issuing_body / document_type / title / canonical_url`
- 时间：`version_or_publication_date / effective_date / retrieved_date`
- 适用：`region / target_population / care_setting / intended_use_or_decision_point`
- 规范内容：`recommendation_or_requirement / recommendation_strength / evidence_certainty / comparator / endpoint_or_threshold`
- 溯源：`passage / section_page_table`
- 治理：`license / machine_access / source_role / tier / source_quality`
- **硬门控：`predates_paper_submission`（true/false/unknown）**

这些记录写入 Claim–Evidence Graph 的**外部 requirement 侧**；论文内部的 supporting/contradicting/missing
证据由内部核验子系统填另一侧。最终临床评价来自 claim–evidence **对齐**，而非模型凭印象打分。

## 5. 源分层与治理

- **证据层级**（Tier 1→5）：Tier1 WHO/FDA/NICE/国家指南/学会指南 → Tier2 系统综述/共识/HTA →
  Tier3 高质量临床试验 → Tier4 观察性/专科研究 → Tier5 普通文献/预印本/发现层。
- **access_class**：A 开放可机器获取 / B 免费阅读但复用受限 / C 部分开放或需注册。
  **逐文档核对许可**，不按网站整体判断。
- **`predates_paper_submission` 必须是硬字段**：论文投稿后才出的指南可用于"今天能否部署"评价，
  但不能用来指责作者在投稿当时违背了尚不存在的标准。
- **排除**（`exclusions`）：付费临床工具(UpToDate 等)/搜索引擎摘要/科普问答站/裸 PubMed 命中当标准/
  未标版本或已撤回文档/仅因能下 PDF 就默认可批量抓取的内容。
- **已剔除国内源**（`deferred_sources`）：NHC / NMPA·CDE·CMDE / ChiCTR —— 无 API、不易访问
  （2026-07-22 老师决定），保留记录备中国部署场景日后策展。

## 6. 已实现（API 这条腿）

4 个无 key 连接器，端到端跑通（`retrieve.py`）：

| 连接器 | 源角色 | 喂哪些模块 | predates |
|---|---|---|---|
| clinicaltrials.py (CT.gov v2) | registry | comparator/endpoint/population/generalization | ✓ 按试验首次公示日 |
| europepmc.py (Europe PMC) | discovery | 全模块补充(找指南/系统综述/文献) | ✓ **前置为检索条件**，见 §6a |
| who_gho.py (WHO GHO OData) | epidemiology | clinical_question/population | 时间序列，标 unknown |
| openfda.py (openFDA drug label) | regulatory | reference_standard/safety/generalization | ✓ 按 effective_time |

注册表里另有 4 个无 key Class-A 源可快速加连接器：`pubmed_eutils / pmc_oa / crossref / mesh`。

## 6a. Europe PMC 检索质量重写（2026-07-25）

**问题**：旧实现把 Claim Card 的病种和干预拼成一个裸词串、`sort=P_PDATE_D desc` 按发表日倒序。
肺癌筛查卡实测 4 条命中里 2 条完全无关（*放疗后放射性肺炎*、*肝癌双特异性抗体*——后者靠单个
`cancer` 词进来的），且 **4 条 predates 全为 false**（论文 2024-05 投稿，命中全是 2025-12 之后
发表的），对"作者投稿当时该做什么"的评价贡献为零。而 Europe PMC 是 `discovery` 角色、对全部
8 个模块散射，噪声被放大 8 倍。

**四点改动**（`connectors/europepmc.py`）：

1. **按相关度排序**——去掉 `sort` 参数。旧的日期倒序拿到的是"最新的沾边文献"而非"最相关的"，
   这是噪声元凶，也是 predates 全 false 的直接原因。
2. **结构化查询取代裸词串**：病种作强制短语 `TITLE_ABS:"..."`；干预/模型输入输出词作 OR 软约束；
   **人群/场景作第二个 AND 约束**。人群约束实测把成人脓毒症卡的儿科指南、肺癌卡的地区性共识
   挤掉，换成 ESICM 成人脓毒症 CPG 与 ACS/中国肺癌筛查指南。
   人群词会**剔除病种短语里已有的词**——否则 病种=`"lung cancer screening"` 而人群约束是
   `(lung OR cancer OR screening)`，凡命中病种必然命中人群，约束等于没加。
3. **出版类型分层 + 证据等级**：指南/共识 → 系统综述/meta → 普通文献，三层按配额（5:2.5:2.5）
   依次填充，未填满的配额顺延给下一层；据此定 `document_type` 与 `tier`（Tier1 指南 /
   Tier2 共识·系统综述 / Tier5 普通文献）。旧实现注释写着"优先系统综述/指南"但查询里根本没有
   `PUB_TYPE` 条件，`_doctype()` 因此**永远返回 literature**，实测 4 条全是 tier 5。
4. **predates 前置成检索条件**：主检索限定 `FIRST_PDATE <= 投稿日`；投稿后的另开一小桶
   （<= `limit//4`），且**只收指南/综述**——投稿后才出的普通文献既不能用来要求作者、也不构成
   规范，纯噪声；指南/综述则可用于"今天能否部署"评价，记录 `notes` 里显式标注不得据此指责作者。

**降级阶梯**：`病种短语+干预+人群 → 去人群 → 去干预 → 只要病种短语 → 病种词全部 AND`。
阶梯**按出版类型层各走各的**——指南远比普通文献稀少，全局用同一档严格度会把指南层饿死。
最后一档是病种词逐词 AND，**不是**旧的松散裸词串：卡里病种写得不规范时（如
`sepsis (severe) "shock" [ICU]`）裸词串会检回"鼻窦炎指南"这种东西，逐词 AND 仍锁得住主题。
全部落空则返回空列表——**宁可空手，也不给审稿模型喂不相干文献**。

**效果**（两张示例卡，per_source=4）：

| | 旧 | 新 |
|---|---|---|
| 肺癌筛查卡 | 2/4 完全无关；predates 全 false；全部 tier5/literature | 4 条 predates=true（中国肺癌筛查指南 T1、Update on Lung Cancer Screening Guideline T1、CT 影像组学结节恶性度系统综述 T2、文献 T5）+ 1 条投稿后指南另桶 |
| 脓毒症 C3 卡 | — | ESICM 2025 成人脓毒症 CPG T1、德国 S3 脓毒症指南 T1、脓毒症预警系统对死亡率影响的系统综述 T2、文献 T5 + 投稿后 AI 早期预警系统综述另桶 |

**副产物：normative 缺口有工作清单了。** 发现层只有题录+摘要、拿不到条文原文，因此这些指南命中
**仍是 `source_role=discovery`**，`recommendation_or_requirement` 留空，`notes` 标"候选 normative
文档，须策展摄入全文后方可作为规范条目引用"——不得冒充规范条目。但 `retrieve.py` 现在会在
"待策展"提示后报出候选数（如"发现层已检出 3 篇候选指南/共识可供策展"），
下一步的指南策展摄入层因此有了**自动生成的待摄入清单**，不必人工凭空找指南。

**顺带修**：`schema.py: to_dict()` 现在剥掉 `query_context` 里下划线前缀的键。
`_card`（原卡）本意只在进程内传递（`retrieve.py` 注释已如此声明），实际却随每条记录落盘，
等于每条记录都带一份完整 Claim Card 副本。

## 6b. 已实现（策展摄入这条腿）：报告规范清单

**2026-07-24 补齐。** `curated/reporting_tools/*.yaml` + `connectors/curated_reporting.py`。

为什么这批先做：`reporting_tool` 角色的文档（TRIPOD+AI / PROBAST+AI / DECIDE-AI /
CONSORT-AI / SPIRIT-AI / QUADAS-3 / CLAIM）**内容固定、与疾病无关**——它们是"这类研究必须
报告/必须核查什么"的清单，不随 Claim Card 变化。因此不需要检索、不需要连接器、不需要爬虫，
一次人工录入即永久可用。而 `module_routing` 里 `generalization` 与 `workflow_deployment`
两个模块都点名要 reporting_tool，此前是"待策展"缺口。

### 与 API 连接器的三点不同

1. **不检索，只做适用性门控。** 输入不是查询词，而是 Claim Card 的
   `evidence_stage` + 推断出的研究设计（`infer_study_designs`：从 `model_input` 判影像、
   `model_output` 判预测/诊断、`evidence_stage` 判部署阶段）。
2. **不适用时必须写明理由**（`check_applicability` 返回 `(bool, reason)`）。
   这是 C0–C4 分级的执行点：对 C1 回顾性论文套 CONSORT-AI 会输出
   "论文为 C1，CONSORT-AI 适用于 C4，套用属越级要求"，而不是静默返回空。
   `clinical_trial_protocol` **永不推断**，必须由卡显式声明——误判会引入
   "你没写数据监查计划"这类完全不适用的意见。
3. **条目自带模块归属。** `ExternalStandard.modules` 新增字段：条目声明自己服务哪些审查模块，
   路由层只把它投放到声明的模块，而非按源角色散射到全部相关模块（API 连接器行为不变）。
   `limit` 被有意忽略——清单是完整要求集，截断等于静默丢弃要求。

### 摄入内容与许可

条目原文取自各清单的开放全文（Europe PMC `fullTextXML`），逐字保存并记录
citation / DOI / PMID / PMCID / 发布日 / 许可 / 摄入来源 / `verbatim` / `completeness`。

| 清单 | 发布日 | 条目数 | completeness | 许可 |
|---|---|---|---|---|
| TRIPOD+AI | 2024-04-16 | 52 | full | CC BY 4.0 |
| PROBAST+AI | 2025-03-24 | 34 | partial（步骤3信号问题） | CC BY-NC 4.0 |
| DECIDE-AI | 2022-05-18 | 38 | full | CC BY-NC 4.0 |
| CONSORT-AI | 2020-09-09 | 14 | partial（仅 AI 专属扩展） | CC BY 4.0 |
| SPIRIT-AI | 2020-09-09 | 15 | partial（仅 AI 专属扩展） | CC BY 4.0 |
| QUADAS-3 | 2026-02-17 | 4 | **structure_only** | 付费全文 |
| CLAIM 2024 | 2024-07-01 | 0 | **none** | 付费全文 |

CC BY-NC 两份（PROBAST+AI / DECIDE-AI）为非商业许可，学术研究用途符合条款；系统若转商用须重新授权。
QUADAS-3 与 CLAIM 2024 全文在付费墙后，**条目未摄入**——文件里如实标注并写明补齐路径，
系统在补齐前只能提示"本文属该类研究，应对照该清单"，不得声称"已按其逐条核查"。

### predates 门控在这里同样生效

清单本身有发布日。例如 C1 肺癌卡（投稿 2024-05-01）：TRIPOD+AI(2024-04-16) → `true`；
PROBAST+AI(2025-03-24) → `false`。后者可用于"今天能否部署"的评价，但不得用来指责作者。

## 7. 未来工作

0. ~~报告规范清单策展层~~ —— ✅ 2026-07-24 完成，见 §6b。`reporting_tool` 缺口已闭合。
1. **规范指南策展摄入层（最高价值，仍是最大缺口）**：WHO/NICE/USPSTF/学会指南多为 Class B PDF、
   许可敏感 → 按 Claim Card 小批策展摄入（fetch + 分节 + provenance），补上路由里 `normative`
   的"待策展"缺口。注意 8 个模块里有 7 个把 normative 列为首选源，目前**一个连接器都没有**。
   §6b 的 yaml 结构（provenance + applicability + items + completeness）可直接复用为指南条目的载体。
1b. **补齐两份付费清单**：CLAIM 2024 条目表（影像 AI，优先）、QUADAS-3 信号问题原文。
2. **更多连接器**：Europe PMC 全文(OA)、openFDA 器械 510k/PMA、PubMed E-utils、术语(MeSH/ICD-11)。
3. ~~**检索质量**：Europe PMC 关键词发现噪声偏高~~ —— ✅ 2026-07-25 完成，见 §6a。
   剩余：CT.gov / openFDA 两个连接器尚未做同等的相关度与 predates 前置处理。
4. **对接内部核验**：定义 Claim–Evidence Graph 的落盘格式，把外部 requirement 与内部 evidence 拼起来。
5. **Claim Card 自动抽取器**（上游）：论文 → 卡 + C0–C4 分级 + N/A 门控。

## 8. MVP 评测（对接主项目时）

首批 20–30 篇 ICLR MedAI 论文，先只 4 个模块（intended-use/unmet need、population validity、
comparator/endpoint、safety/generalization），外部只接少量权威指南 + 已建的 API 连接器。三臂对比：
Direct clinical score / Paper-only QA / **Paper + external RAG QA**。看事实正确率、临床问题召回、
证据引用准确性、重复稳定性是否提升。临床 gold 可复用已枚举的 225 篇顶刊（NBE/NM）**临床医生评审**做
Flaw-Recall（比招专家便宜）；注意 ICLR 评审是 ML 人写、临床 finding 召回天然低——那是论点，不是 bug。
