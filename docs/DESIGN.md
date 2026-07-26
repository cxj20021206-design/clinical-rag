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
| clinicaltrials.py (CT.gov v2) | registry | comparator/endpoint/population/generalization | ✓ **前置为检索条件** |
| europepmc.py (Europe PMC) | discovery | 全模块补充(找指南/系统综述/文献) | ✓ **前置为检索条件** |
| who_gho.py (WHO GHO OData) | epidemiology | clinical_question/population | 按指标最新数据年份 |
| openfda.py (openFDA **器械**库) | regulatory | reference_standard/safety/generalization | ✓ 510(k) 按 decision_date |

**四个连接器均已于 2026-07-25 重写降噪，见 §6a。**

注册表里另有 4 个无 key Class-A 源可快速加连接器：`pubmed_eutils / pmc_oa / crossref / mesh`。

## 6a. API 腿检索质量重写（2026-07-25）

### 6a.1 Europe PMC

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

### 6a.2 ClinicalTrials.gov

**问题**：不做日期约束，肺癌卡检回的试验首次公示于 2025-12 而论文 2024-05 投稿 →
`predates=false`，按铁律不能用来要求作者，那次检索对"作者当时该做什么"贡献为零；
且把 `intended_use` 长句原样丢给 `query.intr`（"AI early warning system for sepsis onset"）
→ **0 命中**，降级后只按病种查，返回一堆与 AI 无关的普通脓毒症试验。

**改动**：① `filter.advanced=AREA[StudyFirstPostDate]RANGE[MIN,投稿日]` 把 predates 前置；
② 干预检索式改为 **(AI 词) AND (功能词)**——AI 词是注册库里的通用说法
（`"artificial intelligence" OR "machine learning" OR "deep learning" OR "computer-aided"…`），
功能词从 `intervention`/`model_output` 抽取并剔掉病种词与 AI 词本身；③ 逐档放松
（AND → OR → 只按病种）；④ 投稿后另开小桶并显式标注。

**效果**：脓毒症卡从"经胸超声心动图/C1 酯酶抑制剂/血气分析仪"变成
**Early Warning System for Clinical Deterioration**（终点：24 小时内转 ICU 或意外死亡）、
**Early Prediction of Sepsis**、**An Algorithm Driven Sepsis Prediction Biomarker**；
肺癌卡拿到 **Evaluation of Lung Nodule Detection With Artificial Intelligence**、
上海早期肺癌筛查试验（终点写明"LDCT 与 LDCT+计算机辅助的敏感度"）。两卡主检索 predates 全 true。

### 6a.3 openFDA —— 从药品库改接器械库

**问题**：只连了 `drug/label`。脓毒症预警 AI 那张卡实测检回
**Silver Sulfadiazine（磺胺嘧啶银，二三度烧伤创面外用抗菌乳膏）**，只因标签里出现
`wound sepsis`。审的是医疗 AI 软件，该查器械库。

**改动**：改为按价值查三个端点——

1. `device/classification`：FDA 对每类器械的**法定预期用途**定义。
   例 "Lung Computed Tomography System, Computer-Aided Detection"(Class II, 21 CFR 892.2050)
   定义为 "To assist radiologists in the review of … and highlight potential nodules
   **that the radiologist should review**"。这句话直接支撑"本文声称可独立出报告，
   超出该类产品监管定位"这种意见。
2. `device/510k`：**按上一步拿到的 `product_code` 回查**——这是 FDA 自己的数据模型，
   分类给品类、510(k) 给该品类下已获批的具体产品。`PIB` → IDx-DR / EyeArt / iPredict-DR；
   `OEB` → syngo.CT Lung CAD / AVIEW Lung Nodule CAD。按器械名瞎猜则会把 "diabetic"
   检成"糖尿病针头废弃盒"。`decision_date` 作 predates 检索条件。
3. `drug/label`：**仅当卡片明确涉及用药决策时**才查，排最后。

**排序**：病种词命中在 `device_name` 里权重加倍；泛化医学词（cancer/screening/risk）降到 0.25——
否则 "lung cancer screening" 里的 cancer 会让"遗传性肿瘤易感基因测序"压过"肺部 CT 计算机辅助检测"。
再按**功能**（检出/分诊/风险评估）与**模态**（CT/眼底…）加分，否则"电场肿瘤治疗仪(非小细胞肺癌)"
会压过肺部 CAD——两者都带 lung+cancer，但前者是治疗器械。
**不用池内 IDF**：查询词本身会污染池子（拿 lung 查就灌进一堆 lung 条目，反把 lung 权重压低），是反的。

**ML 术语不得作为病种词**：`NON_CLINICAL` 拦掉 learning/deep/benchmark 等，否则一篇
"representation learning benchmark" 论文会被配上 "Deep Learning Image Reconstruction" 的器械先例。
病种词被滤空 → 返回空列表（纯方法学论文本来就没有对应监管品类）。

### 6a.4 WHO GHO

**问题**（三个硬伤，结果基本不可用）：① `kw = cond.split()[0]` **只取病种第一个词**——
"lung cancer screening" 只查 "lung"，而 GHO 里**一条含 lung 的指标都没有** → 零命中；
② **不校验指标是否真有数据**——"sepsis" 唯一命中 `WHS2_515`（5 岁以下儿童死因分布-新生儿脓毒症），
该指标**一行数据都没有**，系统却把它当外部证据输出，而且卡片人群是**成人住院病人**；
③ **只拿指标名不拿数值**，最多说"WHO 有这么个指标"。

**改动**：全部病种词依次尝试（长词优先）→ **判别词过滤**（剔掉泛化词后必须命中具体词，
否则 "cancer" 会命中 36 条乳腺/宫颈癌指标）→ **人群相符性检查**（儿童指标 vs 成人卡片直接拦下）
→ **拉取真实数值，无数据的指标丢弃** → 按卡片 region 映射 ISO3 挑本地数据点，
predates 按最新数据年份算。

**效果**：肺癌卡、成人脓毒症卡现在都返回 **0 条**——这是正确答案，GHO 对这两个题目
确实没有可用指标。糖网卡返回 `NCD_CCS_diab_retin`（"公立体系中糖网筛查的可及性"，USA 2021: Yes），
糖尿病卡返回 3 条带真实数值的指标。**`epidemiology` 角色对专科病种覆盖很薄，这是数据源本身的
局限，如实返回空比编出覆盖更重要。**

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

## 6c. 已实现（规范指南这条腿的第一个源）：USPSTF —— 2026-07-25

`normative` 是 8 个审查模块里 7 个的首选源，此前**一个连接器都没有**。本节记录第一个落地的源。

### 为什么是 USPSTF 而不是 NICE

调研（[RELATED_WORK.md](RELATED_WORK.md)）表明 arXiv 2510.02967 已用 **NICE 官方 API** 一次取到
2164 份指南做 RAG，技术上完全可行。但 NICE 条款有三重限制：免费仅限英国境内、国际使用收费、
且**明令「在 NICE 内容上使用 AI 必须另行取得许可」**——本项目做的恰恰就是这件事，故 **NICE 不可行**。

USPSTF 的版权声明允许 "reproduce, redistribute, publicly display, and incorporate USPSTF work
into other materials"，**条件是 without any changes**，禁止收费再分发与营利用途，
并要求注明出处；**全文未提及 AI/文本挖掘限制**。
"不得改动"这一条与本项目"原文逐字、`verbatim: true`"的既有铁律天然一致——
项目原则在这里直接换来了合规。

### 三个组件

| 文件 | 职责 |
|---|---|
| `uspstf_fetch.py` | 索引分页抓取（`?topic_status=P&PAGE=n`，6 页 108 条）+ 单页解析 |
| `uspstf_ingest.py` | 一次性摄入 → `curated/guidelines/uspstf.yaml`（内容固定，不随论文变，故不在线检索） |
| `uspstf.py` | 连接器：按 Claim Card 做**场景门控 + 病种门控** |

### 关键实现决策

**① 人群—推荐—等级必须由页面的 `Population | Recommendation | Grade` 表绑定，不能正则扫句子。**
两个理由：(a) 并非所有推荐都以 "The USPSTF recommends" 开头——前列腺癌 C 级那条以
"For men aged 55 to 69 years, the decision to undergo..." 开头，正则整条漏掉，
实测 grades=['C','D'] 却只抓到 1 条；(b) 一条推荐常含多亚组分级（结直肠 A+B+C 对应
50–75 / 45–49 / 76–85 岁），只有该表能正确绑定。**绑错推荐强度比不给更糟。**
额外收益：表格的 Population 列直接就是 `target_population` 字段。

**② Inactive / Referred 主题不是解析失败。**
18 条主题（CKD 筛查、儿童免疫接种等）页面标题带 `Inactive:` / `Referred:` 前缀，
本身没有 Recommendation Summary 表——USPSTF 已停止维护或转交其他机构（免疫接种转给 ACIP）。
按状态区分后：`status=active` 却解析不出才算失败（现为 0），其余记入 `retired_topics` 保留。
论文所在领域的推荐若已被停用，本身即有价值的审稿背景，不应因"无推荐条目"而从库中消失。

**③ 两条门控，不适用时给理由。**

- **场景门控**：USPSTF 职权仅限预防服务（筛查/预防用药/行为咨询、初级保健）。
  Claim Card 命中急重症/治疗信号（icu / inpatient / sepsis / ventilat…）而无预防信号 → 拦截。
  这是 `clinical_sources.yaml: uspstf.notes「筛查类第一优先；不能外推到治疗问题」`的执行点。
- **病种门控**：准入**只看 `disease_or_condition`**，且泛化词（cancer / screening / risk /
  imaging，见 `base.py: GENERIC_CLINICAL`）与人群/技术修饰词（high-risk / low-dose /
  ai-assisted，见 `uspstf.py: _MODIFIER`）均不计入判别。
  初版把 `target_population` / `intended_use` 也纳入准入，结果肺癌卡因 "high-risk" 命中了
  「Aspirin Use to Prevent Preeclampsia in persons at high risk」、因 "low-dose" 命中了心血管饮食推荐。
  收紧后判别词只剩 `lung`，精确命中且仅命中 `Lung Cancer: Screening`。
  `target_population` / `intended_use` 降级为排序加分项。

### 实测（双向验证）

| Claim Card | 结果 |
|---|---|
| 肺癌 CT (C1, screening) | ✅ 命中 `Lung Cancer: Screening`，Grade **B**，certainty **moderate**，人群 "Adults aged 50 to 80 years who have a 20 pack-year..."，发布 2021-03-09 **早于**投稿 2024-05-01 → `predates=true`，**可据此要求作者** |
| 脓毒症预警 (C3, ICU 病房) | ⛔ 场景门控拦截：「命中 inpatient/ward/sepsis，USPSTF 职权仅限预防服务，套用属越权外推，须改由学会指南/WHO 承担」 |

`recommendation_strength` 与 `evidence_certainty` **首次有值**（此前全库 0/104）——
这两个字段自 §5 定义起即为临床指南预留，至此才被真正填充。
USPSTF 的 **I 级（证据不足）** 在审稿中价值尤高：官方都认为证据不足的领域，
论文若声称临床价值，正是应追问之处；连接器对 I 级记录附加专门提示。

### 覆盖边界（重要）

USPSTF **只能覆盖预防与筛查**。急重症、住院管理、治疗方案、诊断细节一概不在其职权内——
脓毒症那张卡接完 USPSTF 之后**仍是缺口**。`normative` 是一个**角色**而非单一源，
架构（`sources_for_module` 按角色聚合）本就支持多源；USPSTF 只是第一个。
后续按"许可难度 × 覆盖价值"排序：WHO（标准 CC 许可）→ 学会指南（按需、每家单独对付）→
VA/DoD（许可无碍但纯 PDF）。

**多源之后会出现新问题：指南互相打架**（USPSTF 与 NCCN 与中国指南对肺癌筛查的入选标准并不一致）。
拟沿用 `predates` 的处理思路——全部收录、各自标明发布方与适用地区、**如实呈现分歧而不代为裁决**。

## 6d. 已实现（规范指南这条腿的第二个源族）：学会 / 国家 CPG —— 2026-07-26

§6c 末尾写明的缺口：USPSTF 只管预防与筛查，**急重症与治疗一类论文（脓毒症预警、创伤分诊、
院内肺炎）接完 USPSTF 之后仍然没有任何规范源**。本节把这块补上。

两个源族的分工是互补的，任何一篇论文都应当能从某一侧得到"标准"或得到"我为什么不适用"：

| 源族 | 覆盖 | 典型问题 |
|---|---|---|
| USPSTF (§6c) | 预防与筛查 | 谁该被筛、多久筛一次 |
| 学会 / 国家 CPG (本节) | 急重症与治疗 | 报警之后该做什么、多快做、跟谁比 |

### 通道：为什么是 Europe PMC 而不是各学会官网

USPSTF 是**一个机构一个网站**，写一个抓取器即可。学会指南是**几十家机构各发各的**
（ESICM、WSES、KSCCM、德国六学会联合…），逐家写抓取器不可持续，且官网 PDF 的许可
多半含糊。唯一能统一拿到"许可机读 + 全文可解析"的通道是 **Europe PMC 的 OA 全文 XML**——
§6b 的报告规范清单就是这么摄入的，而 §6a.1 Europe PMC 降噪后 `PUB_TYPE:"Guideline"`
分层检索**自动产出的候选清单**（NCCN/ESICM/中国肺癌筛查指南等）正好是本节的入口。
发现层与摄入层就此接上：**发现层找到候选 → 策展层核许可与结构 → 摄入为规范条目**。

### 入选三条件（`curated/guidelines/manifest.yaml`）

① Europe PMC 有 OA 全文 XML；② `license` 在白名单内（CC BY / BY-NC / BY-NC-ND / BY-SA / CC0）；
③ 推荐以**可识别结构**呈现且抽出 ≥3 条。三条缺一即记入 `deferred` 并写明 blocker，不静默降级。

- **`license` 字段为空 = 未授权，不是"待查"。** SSC（Surviving Sepsis Campaign）系列因此
  全部落榜——这是本腿最大的内容损失，SSC 是脓毒症领域引用最多的指南。如实记在 deferred 里。
- **ND / SA 也收**：本项目只做逐字未改动的摘录，不产生演绎作品——与 USPSTF "without any
  changes" 是同一条逻辑，`verbatim: true` 这条铁律在这里第二次直接换来合规。
- **拒绝关键词抓句降级。** 中国肺癌筛查指南（PMC9987116）许可合规，但"建议"散在背景散文里，
  其中相当一部分是在**转述 ACS/USPSTF 的推荐**——抓句会把别家的推荐记成本指南的。
  **绑错推荐来源比不给更糟**，与 §6c 不用正则扫句子是同一个判断。

### 三个组件与三种抽取策略

| 文件 | 职责 |
|---|---|
| `guideline_fetch.py` | 候选检索 + OA 全文 XML（带磁盘缓存）+ **三种结构策略抽推荐** + GRADE 解析 |
| `guideline_ingest.py` | 按 manifest 一次性摄入 → `curated/guidelines/cpg_<slug>.yaml` |
| `curated_guidelines.py` | 连接器：病种 + 人群 + 场景门控，按与本卡相关度挑条目 |

策略跑全部、取产量最高者，产量 <3 视为"结构没抽对"，宁可不摄入：

- `rec_sections` —— 每条推荐是一个标题为 Recommendation 的 `<sec>`（韩国脓毒症 CPG）；
- `rec_boxes` —— 推荐写在 `<boxed-text>` 里；
- `rec_tables` —— "Summary of recommendations" 汇总表，一行一条（德国 S3 / WSES / ESPNIC）。

### 关键实现决策（四条，都是实测撞出来的）

**① 推荐的"上下文"按文档顺序取最近的前置 `<boxed-text>`，不能按兄弟节点找。**
韩国 CPG 里只有 KQ1 是 Recommendation 节的兄弟，KQ2–KQ12 都被排版嵌进了**上一条推荐的
Comments 小节末尾**。按兄弟找会让 13 条推荐里 12 条丢掉临床问题卡片，退化成
"KEY QUESTIONS AND RECOMMENDATIONS"这种无用上下文——而没有上下文的推荐句在审稿里几乎不可用
（不知道它在管什么）。

**② 一格多条必须切开（`split_statements`）。** WSES 2023 的汇总表把三四条推荐塞进同一单元格：
`We suggest X [Weak recommendation … 2C]We recommend Y [Strong …]`。不切开就会用最后一条的
强度去标第一条。切点是"以中括号分级标记收尾"，且**一格多条时只能就地解析分级**，
一格一条时才可借同行的强度列/证据列——否则同行条目互相污染。

**③ 汇总表要两道过滤，缺一不可。** 初版只要求 caption 含 "recommendation"、行文本够长，结果：
Chest/AABB 输血指南的《Hemoglobin Thresholds in Studies Included **per Recommendation**》
是研究特征表，抓出 "Gastrointestinal bleeding" 这种单元格碎片；巴西胸科学会指南的筛查
**入选标准**表被抓成推荐。故 caption 必须**以** "(summary of) recommendations" 起头，
且行本身必须含推荐动词/分级标记。

**④ 病种门控：多词条目按整词组匹配，单词条目才按词匹配。**
把 scope 里的 `lung ultrasound` 拆成 lung / ultrasound，一篇肺癌 CT 论文就会因为一个 `lung`
命中儿科床旁超声指南——实测发生过，随后被人群门控拦下。**靠第二道门兜住第一道门的错是运气，
不是设计**，所以在第一道门修掉。同 §6a 的教训：泛化词当判别信号必翻车。

### 检索侧三道门（严格程度递减，理由都要打印）

| 门 | 行为 | 理由 |
|---|---|---|
| 病种 | 硬拦 | 判别词与 `scope.disease_terms` 无交集 → 该指南与本文无关 |
| 人群 | **硬拦** | 成人指南 vs 新生儿论文：`sepsis` 在成人与新生儿是两套完全不同的标准，是临床上最常见的越权外推 |
| 场景 | **软提示** | ICU 指南用于普通病房论文并非无效，但外推须由论文论证 → 写进 notes 让审稿端看见，**不代为裁决** |

### 实测

摄入 **4 份 / 未摄入 6 份**（deferred：2 份许可、4 份结构），schema 零错误：

| 指南 | 策略×条数 | 许可 |
|---|---|---|
| Korean sepsis CPG 2024 (KSCCM) | rec_sections × 13 | CC BY-NC |
| German nosocomial pneumonia S3 2024 | rec_tables × 10 | CC BY |
| WSES elderly trauma 2023 | rec_tables × 44 | CC BY |
| ESPNIC neonatal/paediatric POCUS 2020 | rec_tables × 10 | CC BY |

三张卡的门控**三向**验证：

| Claim Card | 结果 |
|---|---|
| 脓毒症预警 (C3, 成人 ICU) | ✅ Korean sepsis CPG，13/13 条可用，取到 **"septic shock 识别后 1 小时内给抗生素"（conditional / certainty low）** 与 3 小时那条（expert_opinion / very low）——正是这篇论文 `claimed_benefit`（更早识别→更早用药）必须对齐的现实标准；`predates=true` |
| 新生儿脓毒症 (C3, NICU) | ⛔ 韩国 CPG 被**人群门控**拦下：「本卡为新生儿人群，该指南限成人——成人标准外推到儿科属越权」。病种同为 sepsis，仅人群不同即拦截 |
| 肺癌 CT (C1, 筛查) | ⛔ 四份 CPG 全不匹配（病种），该卡由 USPSTF 承担 ✅ —— 两个源族的互补性在此得到验证 |

条目→模块按推荐句里实际出现的东西判定（`rather than`→comparator、`within N hours`→workflow、
`mortality`→endpoint），不给同一份指南的所有条目贴同一组模块。抗生素时机那两条因此同时进入
comparator_baseline / endpoint_utility / workflow_deployment 三个模块。

### 覆盖边界（重要）

`completeness: structured_recommendations_only` —— **只摄入以结构呈现的推荐条目，
正文讨论里散落的表述不在库内**，系统不得声称"已按这份指南全文核查"。这与 §6b 报告清单的
`completeness` 字段同义。另有两处如实记录、不得掩盖：

- **SSC 因许可缺席**（脓毒症最权威的那份指南不在库里）；
- 德国 2025 脓毒症 S3 许可合规但**德语**、推荐以 `<list>` 散列呈现，三策略产量均为 0 →
  需为德语 GRADE 标记（`starke Empfehlung` / `Empfehlungsgrad`）单独写策略。

覆盖只有 4 份指南，远谈不上"全"；但架构上 `normative` 从"USPSTF 一个源"变成了
**多源角色**，多源打架的处理（全收、标明发布方与适用地区、如实呈现分歧）沿用 §6c 末尾的定调。

## 7. 未来工作

0. ~~报告规范清单策展层~~ —— ✅ 2026-07-24 完成，见 §6b。`reporting_tool` 缺口已闭合。
1. **规范指南策展摄入层** —— 🟡 两个源族已建：USPSTF（§6c，预防/筛查）+ 学会与国家 CPG
   （§6d，急重症/治疗，经 Europe PMC OA 通道摄入 4 份）。`normative` 从"零连接器"变成多源角色。
   NICE 因条款明令「在 NICE 内容上使用 AI 须另行取得许可」+ 国际使用收费 → **不可行**
   （见 [RELATED_WORK.md](RELATED_WORK.md) §7）。**剩余工作**：
   1a. **扩摄入面**：现有 4 份 CPG 只覆盖脓毒症/院内肺炎/创伤/新生儿 POCUS，
       常见 MedAI 病种（心衰、卒中、糖网、AKI…）尚无对应指南；WHO IRIS（CC BY-NC-SA 3.0 IGO，
       许可最干净）与 VA/DoD（纯 PDF，许可无碍）仍未接。
   1b. **德语等非英语 GRADE 策略**：德国 2025 脓毒症 S3 许可合规却因语言+结构落榜（§6d）。
   1c. **SSC 许可**：脓毒症最权威指南因 Europe PMC 无许可标注而缺席，若日后取得许可应优先补。
   1d. **长文档分块检索仍未做**：USPSTF 与 CPG 都是靠结构化表格/推荐节躲过去的，
       一旦要摄入整份 PDF 指南就必须做——沿用 arXiv 2510.02967 配置（层级语义分块
       200–600 token / overlap 50 + BM25·稠密混合 + RRF + reranker），不自行调参。
   1e. **多源分歧**：定调已定（全收、标明发布方与适用地区、不代为裁决），但库里指南尚未真正撞车，
       实现要等同病种多源之后。
1b. **补齐两份付费清单**：CLAIM 2024 条目表（影像 AI，优先）、QUADAS-3 信号问题原文。
2. **更多连接器**：Europe PMC 全文(OA)、openFDA 器械 510k/PMA、PubMed E-utils、术语(MeSH/ICD-11)。
3. ~~**检索质量**~~ —— ✅ 2026-07-25 完成，**四个 API 连接器全部重写**，见 §6a。
   剩余：openFDA 器械分类排序在同分时仍会把邻近品类（如肺纤维化影像软件）排到肺部 CAD 之前；
   `device/pma` 与不良事件端点未接。
4. **对接内部核验**：定义 Claim–Evidence Graph 的落盘格式，把外部 requirement 与内部 evidence 拼起来。
5. **Claim Card 自动抽取器**（上游）：论文 → 卡 + C0–C4 分级 + N/A 门控。

## 8. MVP 评测（对接主项目时）

首批 20–30 篇 ICLR MedAI 论文，先只 4 个模块（intended-use/unmet need、population validity、
comparator/endpoint、safety/generalization），外部只接少量权威指南 + 已建的 API 连接器。三臂对比：
Direct clinical score / Paper-only QA / **Paper + external RAG QA**。看事实正确率、临床问题召回、
证据引用准确性、重复稳定性是否提升。临床 gold 可复用已枚举的 225 篇顶刊（NBE/NM）**临床医生评审**做
Flaw-Recall（比招专家便宜）；注意 ICLR 评审是 ML 人写、临床 finding 召回天然低——那是论点，不是 bug。
