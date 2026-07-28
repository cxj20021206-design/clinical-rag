# clinical-rag —— MedAI 审稿的外部临床证据通道

独立项目。唯一职责：给定一篇论文的 **Clinical Claim Card**（PICO / intended-use / 证据阶段），
从分层的权威临床源检索"现实医学世界的标准"，返回带完整 provenance 的 `external_standard` 记录，
写入 Claim–Evidence Graph 的**外部侧**。

> **第一次看这个项目？先读 [`docs/ARCHITECTURE_GUIDE.md`](docs/ARCHITECTURE_GUIDE.md)**——
> 零基础入门导读，讲清"输入什么 → 中间发生了什么 → 输出什么 → 为什么这么设计"。
> 设计与决策的完整说明见 [`docs/DESIGN.md`](docs/DESIGN.md)。
> 同类工作调研（NICE RAG / CPG-on-FHIR / LLM 评委局限）见 [`docs/RELATED_WORK.md`](docs/RELATED_WORK.md)。

## 铁律
- 外部源只定义论文**应该证明什么**；**绝不**替论文证明它"实际做到了什么"（那是内部原文核验，属另一子系统）。
- 每条外部证据保存完整溯源；`predates_paper_submission` 是硬字段——投稿后才出现的指南可评价"今天能否部署"，但不能指责作者违背当时尚不存在的标准。
- 只用开放/免费且许可允许的内容（access_class A/B/C 逐文档核对）。排除清单见 `clinical_sources.yaml: exclusions`。
- **国内源（NHC/NMPA/CDE/CMDE/ChiCTR）已剔除**（无 API/不易访问，2026-07-22 老师决定），记录在 `clinical_sources.yaml: deferred_sources` 备日后需要。

## 快速开始
```bash
cd /work/hdd/bgkq/xchen48/clinical-rag
# 首次使用：摄入指南语料（产物已入库，通常无需重跑）
# python3 connectors/uspstf_ingest.py       # USPSTF，约 4 分钟 → curated/guidelines/uspstf.yaml
# python3 connectors/guideline_ingest.py    # 学会/国家 CPG，按 manifest → curated/guidelines/cpg_*.yaml
#                                           # 加 --check 只核许可与抽取、不写盘；末尾会打印 deferred（未摄入的指南与原因）
python3 retrieve.py --claim examples/claim_card_lung_ct.yaml --per-source 3
# 输出：① 报告规范清单适用性判定(启用了哪些、没启用的理由)
#       ② 按 8 个审查模块分组的外部标准 + 无连接器角色(待策展)提示
#       结果写 store/retrieved.jsonl
```
指定模块：`--modules comparator_baseline endpoint_utility`

四张示例卡刻意用来验证适用性门控**各个方向**都正确：
```bash
python3 retrieve.py --claim examples/claim_card_lung_ct.yaml    # C1 影像 → CLAIM/QUADAS-3 启用，DECIDE-AI 拦截；normative 走 USPSTF
python3 retrieve.py --claim examples/claim_card_sepsis_c3.yaml  # C3 病房 → DECIDE-AI 启用，CLAIM/QUADAS-3 拦截；normative 走韩国脓毒症 CPG
python3 retrieve.py --claim examples/claim_card_neonatal_sepsis.yaml  # 同为 sepsis，仅人群换成新生儿 → 成人 CPG 被人群门控拦下
python3 retrieve.py --claim examples/claim_card_stroke_c2.yaml  # 库里原本没有卒中指南 → 由自动扩库补上，记录带 🤖 标记
```

**病种没覆盖时**：自动扩库（`normative` 按病种组织，人工策展追不上论文的病种分布）
```bash
python3 connectors/guideline_autocurate.py --disease "heart failure"          # 只探不写（默认 dry-run）
python3 connectors/guideline_autocurate.py --claim examples/claim_card_stroke_c2.yaml --write
# 四道门：标题主题门 / 许可硬门 / 结构门 / 抽全性核查。产物 curated/guidelines/cpg_auto_*.yaml
# 带 curation_level: auto（scope 为机器草稿、未经人工核验），记录写 manifest_auto.yaml，**不碰人工 manifest.yaml**

# 或者让检索自己补：normative 无覆盖 → 扩库 → 纳入本次检索（默认关闭）
python3 retrieve.py --claim examples/claim_card_stroke_c2.yaml --auto-curate
```
⚠️ `--auto-curate` **批量评测时务必关闭**：库会随运行日期变化，同一篇论文两次跑出的结果不同，
三臂对比（Direct / Paper-only-QA / Paper+RAG-QA）就不可比了。正确做法是先批量预热扩库、
人工扫一遍 scope、然后评测跑在**冻结的库**上。

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
  uspstf_fetch.py       USPSTF 取数层 (索引分页 + Population|Recommendation|Grade 表解析)
  uspstf_ingest.py      USPSTF 一次性摄入 → curated/guidelines/uspstf.yaml
  uspstf.py             USPSTF 连接器 (normative；场景门控 + 病种门控)
  guideline_fetch.py    学会/国家 CPG 取数层 (Europe PMC OA 全文 + 三种结构策略抽推荐 + GRADE 解析)
  guideline_ingest.py   按 manifest 一次性摄入 → curated/guidelines/cpg_<slug>.yaml
  curated_guidelines.py 学会/国家 CPG 连接器 (normative；病种+人群硬拦、场景软提示)
  guideline_autocurate.py  自动扩库：按 Claim Card 病种缺口触发摄入 (四道门 + scope 草稿生成)
curated/reporting_tools/  人工策展的报告规范清单 (yaml，带 provenance/许可/完整性标注)
curated/guidelines/       策展摄入的临床指南 (normative)：uspstf.yaml + cpg_*.yaml
                          manifest.yaml 记**人工**策展决定；deferred 段如实记录未摄入的指南与原因
                          cpg_auto_*.yaml + manifest_auto.yaml 为**自动扩库**产物，两者分开存
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
3. **规范指南策展摄入**（最高价值、最不 API 化，🟡 两个源族已建）：
   - **USPSTF**（2026-07-25，§6c）：108 条主题 / 142 条推荐条目。选它而非 NICE 的原因：
     NICE 条款明令「在 NICE 内容上使用 AI 须另行取得许可」且国际使用收费，**不可行**；
     USPSTF 为美国政府作品，无改动前提下允许复制再分发。详见
     [`docs/RELATED_WORK.md`](docs/RELATED_WORK.md) §2。**只覆盖预防与筛查**。
   - **学会 / 国家 CPG**（2026-07-26，§6d）：补上急重症与治疗那一侧。学会指南没有统一门户，
     实际通道是 **Europe PMC OA 全文 XML**（发现层 §6a.1 自动产出候选清单 → 策展层核许可与结构）。
     已摄入 4 份（韩国脓毒症 / 德国院内肺炎 S3 / WSES 老年创伤 / ESPNIC 新生儿 POCUS），
     未摄入 6 份连原因记在 `curated/guidelines/manifest.yaml: deferred`。
   **仍待策展**：WHO IRIS（CC BY-NC-SA 3.0 IGO，许可最干净）、VA/DoD（纯 PDF 但许可无障碍）、
   更多病种的学会指南。

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

## 状态（2026-07-28）
- ✅ **自动扩库（DESIGN §6e）**：`normative` 按病种组织、人工策展追不上论文病种分布，故把 §6d 的
  摄入流水线改成**按 Claim Card 病种缺口触发**（机器那半边本来就全自动，人工只剩 manifest 的
  slug/issuing_body/scope）。四道门：标题主题门（检索用 TITLE_ABS 要召回、准入只看 TITLE 要准确——
  没有这道门时心衰卡"捞到"的三份文档是两份**肥胖指南**加一份肾脏病 SGLT-2 指南，只因摘要提到心衰获益，
  而 scope 草稿会把 `heart failure` 写进它们、**从此每篇心衰论文都命中一份肥胖指南**）/ 许可硬门（同人工腿，
  禁止从正文推断）/ 结构门（三策略 + ≥3 条，抽不出时区分 `no_fulltext` / `language` / `structure` 三种成因）/
  **抽全性核查**（拿"结构槽位数"当上界对账，低于 60% 标 needs_review——`≥3 条`挡不住 ESPNIC 那类静默截断）。
  产物 `curation_level: auto`、tier 降至 2、检索时打 `🤖` 并在 notes 声明"scope 未经人工核验"，
  写 `manifest_auto.yaml` 不碰人工 manifest。
- 📊 **实测产出率 4%**（47 候选 → 2 入库）：卒中 2/6 ✅、心衰 0/28、AKI 0/12、糖网 0/1。
  入库的是 *Neuroprognostication in Critically ill Adults with AIS*（CC BY, 13 条，含
  "we suggest the iScore prediction model not be used…"——正是 AI 预后论文要对齐的标准）与巴西 AIS 方案。
  **自动化解决的是"没人去策展"，解决不了"许可拿不到"**——AHA/ACC、ESC、KDIGO、SSC 多数不是 CC-BY，
  心衰/AKI/糖网仍是 0，要补只能换通道（WHO IRIS / VA-DoD，都需先做 PDF 解析）。
- 🔧 实测顺带查出并修掉三个**既存**缺陷：`wses_elderly_trauma` 的裸词 `injury` 让 **AKI 卡命中老年创伤指南**
  （ESPNIC 的裸词 `ultrasound` 同理，此前只靠人群门控兜住）；**表格策略抽出的指南写盘时被去重塌成每份 1 条**
  （`section_page_table` 对同一张表的所有行完全相同，卒中卡 2 条→应有 8 条）；表内分节小标题行被当成推荐
  （判推荐动词要在去掉 `Recommendations:` 前缀之后做）。

## 状态（2026-07-26）
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
- ✅ **`normative` 首个连接器落地（2026-07-25）**：USPSTF 全量摄入 108 条主题
  （90 条现行推荐 / 142 条推荐条目 / 18 条 Inactive·Referred 如实记入 `retired_topics`，
  解析失败 0 → `completeness: full`）。
  - 人群—推荐—等级由页面 `Population|Recommendation|Grade` 表**绑定**，不靠正则扫句子——
    前列腺癌 C 级那条不以 "The USPSTF recommends" 开头，正则会整条漏掉；绑错推荐强度比不给更糟。
  - **`recommendation_strength` / `evidence_certainty` 首次有值**（此前 0/104）。实测肺癌卡：
    Grade=B、certainty=moderate、发布 2021-03-09 早于投稿 2024-05-01 → `predates=true`，可据此要求作者。
  - 门控双向验证：肺癌筛查卡 ✅ 命中 `Lung Cancer: Screening`（判别词仅 `lung`——泛化词
    cancer/screening 不计入准入，否则会误配到乳腺癌/结直肠癌筛查）；脓毒症 C3 卡 ⛔ 被场景门控拦下
    （"USPSTF 职权仅限预防服务，套用属越权外推"），这是 `uspstf.notes: 不能外推到治疗问题` 的执行点。
- ✅ **`normative` 补上急重症一侧（2026-07-26）**：学会/国家 CPG 经 Europe PMC OA 全文通道摄入
  4 份共 108 条推荐（韩国脓毒症 13 / 德国院内肺炎 S3 10 / WSES 老年创伤 44 / ESPNIC 新生儿 POCUS 41），
  详见 DESIGN §6d。
  - 三种结构策略（推荐节 / 推荐框 / 推荐汇总表）取产量最高者，<3 条视为没抽对宁可不摄入；
    汇总表**按表头列取值**——初版按行内推荐动词过滤，在 ESPNIC 上 41 条只抽出 10 条且
    **丢光了全部肺部推荐**（原文写 "POCUS is helpful to…"，本来就没有推荐动词），
    系统会据此得出"该指南没有肺部推荐"的错误结论；沉默的截断比报错更危险；
    **拒绝关键词抓句降级**——中国肺癌筛查指南的"建议"有相当一部分是在转述 ACS/USPSTF，
    抓句会把别家的推荐记成本指南的。未摄入 6 份连原因记入 `manifest.yaml: deferred`
    （2 份许可为空 = 未授权，含 **SSC 脓毒症指南，本腿最大内容损失**；4 份无推荐结构）。
  - 实测脓毒症 C3 卡取到 **"septic shock 识别后 1 小时内给抗生素"（conditional / 证据确定性 low）**——
    正是该论文 claimed benefit（更早识别→更早用药）必须对齐的现实标准，且 `predates=true`。
  - 门控三向：成人脓毒症卡 ✅ 命中；**新生儿脓毒症卡 ⛔ 被人群门控拦下**（病种同为 sepsis，
    成人标准外推到 NICU 属越权）；肺癌卡 ⛔ 四份全不匹配、由 USPSTF 承担 → 两个源族互补性成立。
  - 覆盖边界如实标注 `completeness: structured_recommendations_only`：正文散文里的表述不在库内，
    系统不得声称"已按该指南全文核查"。
- 🕳 已知缺口（显式，不静默）：`normative` 仍缺 WHO IRIS / VA-DoD（NICE 因许可禁令不可行），
  现有 4 份 CPG 只覆盖 4 个病种，心衰/卒中/糖网/AKI 等常见 MedAI 方向尚无对应指南；
  德国 2025 脓毒症 S3 许可合规但因德语 + `<list>` 散列结构未能摄入；发现层能检出更多候选
  （ESICM 成人脓毒症 CPG、NCCN 等）并在路由输出报数，但只有题录+摘要，须策展摄入全文方可作规范条目引用；
  CLAIM 2024 条目、QUADAS-3 信号问题在付费全文中未摄入。
- 🕳 `epidemiology` 角色（WHO GHO）对专科病种覆盖很薄——肺癌、成人脓毒症在 GHO 里都没有可用指标。
  这是数据源本身的局限，连接器如实返回空而非编出覆盖。
- ⏭ 待建（见 DESIGN.md §7）：扩大指南摄入面（WHO IRIS 优先，许可最干净）、非英语 GRADE 策略、
  长文档分块检索（现有两个源都靠结构化表格躲过去了，摄入整份 PDF 指南就必须做）、
  更多无 key 连接器（PubMed/PMC 全文/术语/openFDA PMA）、Claim Card 自动抽取器(上游/内部)、
  与内部原文核验对接成 Claim–Evidence Graph。
