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
| europepmc.py (Europe PMC) | discovery | 全模块补充(找系统综述/指南/文献) | ✓ 按首次发表日 |
| who_gho.py (WHO GHO OData) | epidemiology | clinical_question/population | 时间序列，标 unknown |
| openfda.py (openFDA drug label) | regulatory | reference_standard/safety/generalization | ✓ 按 effective_time |

注册表里另有 4 个无 key Class-A 源可快速加连接器：`pubmed_eutils / pmc_oa / crossref / mesh`。

## 7. 未来工作

1. **规范指南策展摄入层（最高价值）**：WHO/NICE/USPSTF/学会指南多为 Class B PDF、许可敏感 →
   按 Claim Card 小批策展摄入（fetch + 分节 + provenance），补上路由里 `normative` 的"待策展"缺口。
2. **更多连接器**：Europe PMC 全文(OA)、openFDA 器械 510k/PMA、PubMed E-utils、术语(MeSH/ICD-11)。
3. **检索质量**：Europe PMC 目前是关键词发现，噪声偏高 → 加 PICO 结构化查询 + 出版类型/证据等级过滤。
4. **对接内部核验**：定义 Claim–Evidence Graph 的落盘格式，把外部 requirement 与内部 evidence 拼起来。
5. **Claim Card 自动抽取器**（上游）：论文 → 卡 + C0–C4 分级 + N/A 门控。

## 8. MVP 评测（对接主项目时）

首批 20–30 篇 ICLR MedAI 论文，先只 4 个模块（intended-use/unmet need、population validity、
comparator/endpoint、safety/generalization），外部只接少量权威指南 + 已建的 API 连接器。三臂对比：
Direct clinical score / Paper-only QA / **Paper + external RAG QA**。看事实正确率、临床问题召回、
证据引用准确性、重复稳定性是否提升。临床 gold 可复用已枚举的 225 篇顶刊（NBE/NM）**临床医生评审**做
Flaw-Recall（比招专家便宜）；注意 ICLR 评审是 ML 人写、临床 finding 召回天然低——那是论点，不是 bug。
