# clinical-rag —— MedAI 审稿的外部临床证据通道

独立项目。唯一职责：给定一篇论文的 **Clinical Claim Card**（PICO / intended-use / 证据阶段），
从分层的权威临床源检索"现实医学世界的标准"，返回带完整 provenance 的 `external_standard` 记录，
写入 Claim–Evidence Graph 的**外部侧**。

> 设计与决策的完整说明见 [`docs/DESIGN.md`](docs/DESIGN.md)。

## 铁律
- 外部源只定义论文**应该证明什么**；**绝不**替论文证明它"实际做到了什么"（那是内部原文核验，属另一子系统）。
- 每条外部证据保存完整溯源；`predates_paper_submission` 是硬字段——投稿后才出现的指南可评价"今天能否部署"，但不能指责作者违背当时尚不存在的标准。
- 只用开放/免费且许可允许的内容（access_class A/B/C 逐文档核对）。排除清单见 `clinical_sources.yaml: exclusions`。
- **国内源（NHC/NMPA/CDE/CMDE/ChiCTR）已剔除**（无 API/不易访问，2026-07-22 老师决定），记录在 `clinical_sources.yaml: deferred_sources` 备日后需要。

## 快速开始
```bash
cd /work/hdd/bgkq/xchen48/clinical-rag
python3 retrieve.py --claim examples/claim_card_lung_ct.yaml --per-source 3
# 输出：① 报告规范清单适用性判定(启用了哪些、没启用的理由)
#       ② 按 8 个审查模块分组的外部标准 + 无连接器角色(待策展)提示
#       结果写 store/retrieved.jsonl
```
指定模块：`--modules comparator_baseline endpoint_utility`

两张示例卡刻意用来验证适用性门控**双向**都正确：
```bash
python3 retrieve.py --claim examples/claim_card_lung_ct.yaml    # C1 影像 → CLAIM/QUADAS-3 启用，DECIDE-AI 拦截
python3 retrieve.py --claim examples/claim_card_sepsis_c3.yaml  # C3 病房 → DECIDE-AI 启用，CLAIM/QUADAS-3 拦截
```

## 结构
```
clinical_sources.yaml   源注册表：41 个源 + 角色/地域/access_class/machine_access
                        + module_routing(8模块) + retrieval_order + exclusions + deferred_sources
schema.py               ExternalStandard 记录契约(§5) + validate + compute_predates + 原子写
retrieve.py             路由层：Claim Card → 模块路由 → 连接器(各调一次) → 分配到模块 → 写 store
connectors/
  base.py               连接器接口
  clinicaltrials.py     ClinicalTrials.gov v2   (registry；comparator/endpoint/population)
  europepmc.py          Europe PMC              (discovery；分层检索指南/系统综述/文献，全模块补充)
  who_gho.py            WHO GHO OData           (epidemiology；疾病负担；带真实数值+人群校验)
  openfda.py            openFDA 器械库          (regulatory；法定预期用途 + 510(k) 获批先例)
  curated_reporting.py  策展摄入层 (reporting_tool；适用性门控 + 条目→模块精确投放)
curated/reporting_tools/  人工策展的报告规范清单 (yaml，带 provenance/许可/完整性标注)
examples/               示例 Clinical Claim Card
store/                  检索记录缓存(jsonl，带日期 + query_context)
docs/DESIGN.md          完整设计文档
```

## 三条腿（刻意区分）
1. **干净 API 直连**（背景/发现证据，✅ 已建）：CT.gov / Europe PMC / WHO GHO / openFDA（无 key）。
   **四个连接器均已于 2026-07-25 重写降噪**（相关度/结构化查询、predates 前置、openFDA 改接器械库、
   WHO GHO 补数值与人群校验，见 DESIGN §6a）。
   注册表里另有 4 个无 key Class-A 可加：pubmed_eutils / pmc_oa / crossref / mesh。
2. **报告规范清单策展摄入**（✅ 已建，2026-07-24）：内容固定、与疾病无关 → 不需检索，一次录入永久可用。
   见下节。
3. **规范指南策展摄入**（最高价值、最不 API 化，⏭ 待建）：WHO/NICE/USPSTF/学会指南多为 Class B PDF、
   许可敏感 → 按 Claim Card 小批策展摄入，不做通用爬虫。路由时 `normative` 角色仍被标为"待策展"缺口。

## 报告规范清单策展层（`curated/reporting_tools/`）

与 API 连接器的根本区别：这些文档**内容固定**，不随论文变化，所以不检索，而是按
**研究设计 + 证据阶段**做适用性门控。

| 清单 | 发布 | 条目 | 完整性 | 适用于 | 许可 |
|---|---|---|---|---|---|
| TRIPOD+AI | 2024-04-16 | 52 | 完整 | 预测模型开发/验证 (C0–C4) | CC BY 4.0 |
| PROBAST+AI | 2025-03-24 | 34 | 步骤3信号问题 | 预测模型偏倚评估 | CC BY-NC 4.0 |
| DECIDE-AI | 2022-05-18 | 38 | 完整 | 早期真实临床评价 (C3–C4) | CC BY-NC 4.0 |
| CONSORT-AI | 2020-09-09 | 14 | AI 专属扩展 | 已完成 AI 试验报告 (C4) | CC BY 4.0 |
| SPIRIT-AI | 2020-09-09 | 15 | AI 专属扩展 | AI 试验方案 (C4) | CC BY 4.0 |
| QUADAS-3 | 2026-02-17 | 4 | ⚠️仅域级骨架 | 诊断准确性研究 | 付费全文，待补 |
| CLAIM 2024 | 2024-07-01 | 0 | 🕳**未摄入** | 影像 AI | 付费全文，待补 |

条目原文取自各清单的 CC BY / CC BY-NC 开放全文（Europe PMC），逐字保存并注明出处；
付费全文的两份**如实标为未摄入/仅骨架**，系统不得声称"已按其核查"。

**关键设计：不适用时必须说明理由。** 一份清单被拦下不是静默返回空，而是打印
"论文为 C1，CONSORT-AI 适用于 C4，套用属越级要求"。这是 C0–C4 分级的执行点——
防止系统对一篇回顾性研究提出"你没做随机对照试验"。

## 状态（2026-07-25）
- ✅ 端到端跑通：注册表 / schema / 原子写 / 4 个 API 连接器 / **报告规范策展层** / predates 门控 /
  路由层 / 2 张示例 Claim Card。
- ✅ 实测（C1 肺癌 CT 卡）：98 条去重记录、schema 零错误；TRIPOD+AI(2024-04-16) 早于投稿 → `predates=true`，
  PROBAST+AI(2025-03-24) 晚于投稿 → `predates=false`，不得据此指责作者。
- ✅ 门控双向验证：C1 影像卡启用 CLAIM/QUADAS-3 拦截 DECIDE-AI；C3 病房卡启用 DECIDE-AI 拦截 CLAIM/QUADAS-3；
  CONSORT/SPIRIT-AI 两卡均拦截（仅 C4）。
- ✅ **API 腿四连接器全部降噪（2026-07-25）**，详见 DESIGN §6a：
  - Europe PMC：日期倒序+裸词串 → 相关度+结构化 PICO+出版类型分层。肺癌卡原 4 条里 2 条完全无关
    （放射性肺炎、肝癌抗体）、predates 全 false、全 tier5；现全部切题并分出 Tier1 指南/Tier2 综述。
  - ClinicalTrials.gov：补 predates 前置；干预检索改 (AI词) AND (功能词)。脓毒症卡从
    "经胸超声/血气分析仪"变成 Early Warning System for Clinical Deterioration、Early Prediction of Sepsis。
  - openFDA：**从药品库改接器械库**。原来给脓毒症预警 AI 检回"磺胺嘧啶银烧伤乳膏"；
    现在给出 FDA 法定预期用途定义 + 按 product_code 回查的获批先例（IDx-DR / syngo.CT Lung CAD）。
  - WHO GHO：原来只取病种首词、且输出了**一行数据都没有**的空指标（还是儿童人群配成人卡片）；
    现在带真实数值 + 人群相符性校验，查不到就如实返回空。
- 🕳 已知缺口（显式，不静默）：`normative` 角色（WHO/NICE/USPSTF/学会指南，8 模块里 7 个的首选源）
  仍无任何**规范条目**连接器——发现层现在能检出候选指南（中国肺癌筛查指南、ESICM 成人脓毒症 CPG 等）
  并在路由输出里报数，但只有题录+摘要，须策展摄入全文后方可作为规范条目引用；
  CLAIM 2024 条目、QUADAS-3 信号问题在付费全文中未摄入。
- 🕳 `epidemiology` 角色（WHO GHO）对专科病种覆盖很薄——肺癌、成人脓毒症在 GHO 里都没有可用指标。
  这是数据源本身的局限，连接器如实返回空而非编出覆盖。
- ⏭ 待建（见 DESIGN.md §7）：规范指南策展摄入层（现已有发现层自动生成的待摄入清单）、
  更多无 key 连接器（PubMed/PMC 全文/术语/openFDA PMA）、Claim Card 自动抽取器(上游/内部)、
  与内部原文核验对接成 Claim–Evidence Graph。
