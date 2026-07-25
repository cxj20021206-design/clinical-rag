# clinical-rag 架构入门导读

> 面向第一次接触本项目的人。不假设你读过 `DESIGN.md`，也不假设你熟悉 RAG。
> 目标：读完能说清楚「输入什么 → 中间发生了什么 → 输出什么 → 为什么这么设计」。
>
> 分工：本文讲**是什么、为什么**；[`DESIGN.md`](DESIGN.md) 讲**每个决策的完整依据**；
> [`RELATED_WORK.md`](RELATED_WORK.md) 讲同类工作与许可调研；`README.md` 讲**怎么跑**。

---

## 0. 一句话

**这是一个"查资料的机器人"，专门为医学 AI 论文的审稿服务。**

给它一篇论文的临床主张，它去真实医学世界（指南、监管文件、试验注册库、报告规范清单）
把「这类研究应该达到什么标准」取回来，交给审稿模型。

---

## 1. 为什么需要它

让通用模型去审一篇医学 AI 论文，它只能读论文本身，所以只会说这类话：

> "实验设计描述清晰，但缺少消融实验。"

这是 **ICLR 式的评审**——只评论文内部写得好不好。而一个真实的临床审稿人会说：

> "你的对照组是'无辅助的放射科医生'，可肺癌筛查的现行标准是 USPSTF 2021 年 B 级推荐的
> LDCT 筛查流程，你没对上；而且 FDA 对这类器械的法定预期用途是'辅助'，你却宣称能替代。"

后面这些知识**不在论文里**，在外部世界里。clinical-rag 的唯一职责就是把它们取回来。

### 铁律（整个项目的地基）

> **外部源只回答"这篇论文*应该*证明什么"；绝不回答"它*实际*做到了什么"。**

后者要靠读论文原文，属于另一个子系统（内部原文核验）。这条把职责切得很干净——
它是防止系统编造"论文里说了 X"的结构性保证，而不是靠提示词约束。

两侧最终汇合成 **Claim–Evidence Graph**：外部侧（本项目）= 应该证明什么，
内部侧（待建）= 实际证明了什么，两侧对齐后的落差才是审稿意见。

---

## 2. 输入：Clinical Claim Card（临床主张卡）

系统不直接吃 PDF，吃一张**结构化卡片**——把论文的临床主张抽成固定字段。
见 `examples/claim_card_lung_ct.yaml`：

```yaml
clinical_claim:
  disease_or_condition:  "lung cancer screening"                 # 什么病
  intended_use:          "AI-assisted low-dose CT interpretation" # 这 AI 要干嘛
  target_population:     "high-risk adults eligible for..."       # 给谁用
  intended_user:         "radiologist"                            # 谁来用
  care_setting:          "screening"                              # 什么场景
  model_input:           "low-dose chest CT"                      # 输入
  model_output:          "nodule detection and malignancy risk"   # 输出
  comparator:            "unaided radiologist"                    # 跟谁比
  claimed_benefit:       "higher early-stage detection"           # 声称好处
  evidence_stage:        "C1"                                     # ★ 证据阶段
  deployment_claim_level:"assistive, retrospective"
  region:                "US"
  submission_date:       "2024-05-01"                             # ★ 投稿日
```

两个带 ★ 的字段是全系统的开关：

**`evidence_stage`（C0–C4）**——论文走到哪一步：

| 阶段 | 含义 |
|---|---|
| C0 / C1 | 回顾性数据集上开发 / 内部测试（绝大多数 MedAI 论文在这） |
| C2 | 外部验证 |
| C3 | 真实临床前瞻性小规模使用 |
| C4 | 随机对照试验 |

**`submission_date`**——投稿日，用于计算 predates 硬门控（见 §5）。

> 现状：这张卡目前**手写**。自动抽卡器尚未建（`DESIGN.md` §7 待办）。

---

## 3. 三个核心概念（架构骨架）

### 3.1 源注册表 —— 41 个外部源的"通讯录"

`clinical_sources.yaml` 登记 41 个权威源，每个记录：名称、URL、许可证、
能否机器访问（api/xml/html/pdf/manual）、可信度 tier(1–5)、以及**它扮演什么角色**。

同文件还有 `deferred_sources`（国内源 NHC/NMPA/CDE/CMDE/ChiCTR，2026-07-22 决定剔除，
无 API 且不易访问，留档备日后需要）和 `exclusions`（明确排除的源及理由）。

### 3.2 源角色（`source_role`）—— 8 种"这个源能说什么话"

| 角色 | 源数 | 能回答什么 | 例子 |
|---|---|---|---|
| `normative` | 5 | **应该怎么做**（话语权最高） | USPSTF、WHO 指南 |
| `regulatory` | 6 | 法律上允许怎么宣称 | FDA |
| `evidence_synthesis` | 6 | 现有证据的总结 | Cochrane |
| `registry` | 2 | 别人做过什么试验 | ClinicalTrials.gov |
| `epidemiology` | 2 | 这病负担有多大 | WHO GHO |
| `terminology` | 6 | 术语的标准定义 | MeSH |
| `reporting_tool` | 10 | 论文该怎么写才合格 | TRIPOD+AI |
| `discovery` | 4 | 帮忙找线索（**不权威**） | Europe PMC |

角色决定**话语权重**：`normative` 说"应该做 X"可直接作审稿依据；
`discovery` 检回来的只是题录，只能当线索，不能当规范条文引用。

### 3.3 八个审查模块 —— 审稿要审的八个方面

```
clinical_question     这个临床问题真的存在吗
population_validity   人群选得对吗
reference_standard    金标准立得住吗
comparator_baseline   对照组够不够格
endpoint_utility      终点指标有临床意义吗
generalization        换个医院还能用吗
safety_harm_equity    有没有伤害 / 公平性问题
workflow_deployment   真能塞进临床流程吗
```

### 3.4 把三者连起来：`module_routing`（路由表）

这是架构的**心脏**——规定"审某个方面时，该去问哪类角色"：

```
clinical_question    → normative, epidemiology, evidence_synthesis
population_validity  → normative, epidemiology, registry
reference_standard   → normative, regulatory, terminology
comparator_baseline  → normative, evidence_synthesis, regulatory
endpoint_utility     → normative, evidence_synthesis
generalization       → normative, registry, regulatory, reporting_tool
safety_harm_equity   → normative, regulatory
workflow_deployment  → reporting_tool, regulatory, normative
```

读法举例：问"对照组够不够格"，去问指南 / 系统综述 / 监管；
**不问** epidemiology——对照组的事跟"这病有多少人得"无关。

**为什么必须有这层？** 没有路由 = 每个问题都把 41 个源全查一遍，结果全是噪音。
路由的本质是「问对的人」。

---

## 4. 连接器：三条腿

"连接器"= 去某个源取数据的那段代码。关键在于 **41 个源不是同一种东西**，
取数方式分三类，内部称"三条腿"。这是最容易混淆的地方。

### 🦵 腿 1：API 直连（4 个，✅ 已建）

有开放 API，**每次在线查**，结果随论文变化。

| 连接器 | 角色 | 取什么 |
|---|---|---|
| `europepmc.py` | discovery | 文献 / 指南题录；万能发现层，补充所有模块 |
| `clinicaltrials.py` | registry | 别人怎么设对照组、用什么终点 |
| `openfda.py` | regulatory | FDA **器械**库：法定预期用途 + 510(k) 获批先例 |
| `who_gho.py` | epidemiology | 疾病负担统计（带真实数值 + 人群相符性校验） |

这条腿在 2026-07-25 整体重写过一轮**降噪**（详见 `DESIGN.md` §6a）。典型翻车案例：
给"脓毒症预警 AI"检回了"磺胺嘧啶银烧伤乳膏"——因为原来接的是**药品库**，
且只拿病种首词去搜。修法是改接器械库 + 结构化 PICO + 相关度排序 + predates 前置。

「关键词没抽好 → 检回完全无关的东西」是 RAG 最常见的死法，所以 `connectors/base.py`
专门维护三张词表：

| 词表 | 作用 | 不做会怎样 |
|---|---|---|
| `STOPWORDS` | 滤功能词与空泛词 | 约束近乎失效 |
| `NON_CLINICAL` | "learning/neural/transformer" 等 ML 词**绝不**拿去查临床库 | 一篇表征学习论文被配上 FDA 器械先例 |
| `GENERIC_CLINICAL` | "cancer/screening" 满库都是，不算判别信号 | 查肺癌检回乳腺癌——匹配上的是 cancer，不是 lung |

注册表里另有 4 个无 key 的 Class-A 源可加：`pubmed_eutils` / `pmc_oa` / `crossref` / `mesh`。

### 🦵 腿 2：报告规范清单的策展摄入（✅ 已建 2026-07-24）

这条腿与腿 1 **根本不同**，也是最反直觉的地方：

> TRIPOD+AI 这类清单，内容是**固定的 52 条**，和论文讲什么病**毫无关系**。
> 所以根本不需要"检索"——一次性抄进本地 yaml，永久可用。

那难点在哪？在**该不该启用**。这叫**适用性门控**（`connectors/curated_reporting.py`）：

```
论文是 C1（回顾性开发）
  → TRIPOD+AI    ✅ 启用（适用 C0–C4 预测模型）
  → CONSORT-AI   ⛔ 拦下（只适用 C4 已完成试验）
```

**为什么这是核心而不是细节？** 不拦的话，系统会对一篇回顾性研究提出
"你没做随机对照试验"——这是**越级要求**，真实审稿人不会这么说，说了就暴露是机器。
C0–C4 分级的执行点就在这里。

而且门控 **不适用时必须打印理由**，不能静默返回空：

```
⛔ CONSORT-AI  证据阶段不匹配：论文为 C1，CONSORT-AI 适用于 ['C4']。
              对 C1 论文套用本清单属越级要求。
```

已摄入 7 份清单（6 个 `source_id`，CONSORT/SPIRIT 共用一个）：

| 清单 | 发布 | 条目 | 完整性 | 适用于 | 许可 |
|---|---|---|---|---|---|
| TRIPOD+AI | 2024-04-16 | 52 | 完整 | 预测模型开发/验证 (C0–C4) | CC BY 4.0 |
| PROBAST+AI | 2025-03-24 | 34 | 步骤3信号问题 | 预测模型偏倚评估 | CC BY-NC 4.0 |
| DECIDE-AI | 2022-05-18 | 38 | 完整 | 早期真实临床评价 (C3–C4) | CC BY-NC 4.0 |
| CONSORT-AI | 2020-09-09 | 14 | AI 专属扩展 | 已完成 AI 试验报告 (C4) | CC BY 4.0 |
| SPIRIT-AI | 2020-09-09 | 15 | AI 专属扩展 | AI 试验方案 (C4) | CC BY 4.0 |
| QUADAS-3 | 2026-02-17 | 4 | ⚠️ 仅域级骨架 | 诊断准确性研究 | 付费全文，待补 |
| CLAIM 2024 | 2024-07-01 | 0 | 🕳 **未摄入** | 影像 AI | 付费全文，待补 |

付费全文的两份**如实标为未摄入 / 仅骨架**，系统不得声称"已按其核查"。

另有一个细节：`search()` 的 `limit` 参数被**有意忽略**——清单是一个完整要求集，
截断等于静默丢弃要求，与"缺口必须显式"冲突。

### 🦵 腿 3：临床指南的策展摄入（🟡 已起步 2026-07-25，最高价值）

`normative` 话语权最高，但指南**几乎都没有 API**，只有网页 / PDF。
第一个落地的是 **USPSTF**（美国预防服务工作组），全量摄入
108 个主题 / 142 条推荐条目 / 18 条已停用或转交（解析失败 0，`completeness: full`），
产物在 `curated/guidelines/uspstf.yaml`。

**为什么是 USPSTF 而不是更有名的 NICE？** NICE 条款明令「在 NICE 内容上使用 AI
须另行取得许可」且国际使用收费——**法律上不可行**。USPSTF 是美国政府作品，
无改动前提下允许复制再分发，版权声明未涉及 AI 用途。详见 `RELATED_WORK.md` §2。

三个组件：`uspstf_fetch.py`（取数：索引分页 + 表解析）→
`uspstf_ingest.py`（一次性摄入，约 4 分钟）→ `uspstf.py`（连接器：场景门控 + 病种门控）。

这条腿带来一个此前完全空缺的能力——**推荐强度与证据确定性**：

```
Lung Cancer: Screening    Grade=B, certainty=moderate, 2021-03-09
```

在此之前所有记录的 `recommendation_strength` 都是空的（0/104）。
现在审稿模型能说"这是 B 级推荐"，而不只是"有篇文献提到过"。

两个关键实现决策：

1. **人群—推荐—等级靠页面 `Population|Recommendation|Grade` 表绑定，不靠正则扫句子。**
   前列腺癌 C 级那条不以 "The USPSTF recommends" 开头，正则会整条漏掉；
   而**绑错推荐强度比不给更糟**。
2. **门控是双向的**：肺癌筛查卡 ✅ 命中（判别词仅 `lung`——泛化词 cancer/screening
   不计入准入，否则误配到乳腺癌/结直肠癌筛查）；脓毒症 C3 卡 ⛔ 被场景门控拦下，
   理由"USPSTF 职权仅限预防服务，套用属越权外推"。

**仍待策展**：WHO（CC BY-NC-SA 3.0 IGO）、学会指南（按 Claim Card 动态、每家许可不同）、
VA/DoD（纯 PDF，但许可无障碍）。

---

## 5. 输出：`ExternalStandard` 记录

三条腿最终吐出**同一种数据结构**（`schema.py`），这是统一存储契约。字段分五组：

```
身份     source_id / issuing_body / document_type / title / canonical_url
时间     version_or_publication_date / effective_date / retrieved_date
适用范围 region / target_population / care_setting / intended_use_or_decision_point
规范内容 recommendation_or_requirement / recommendation_strength /
        evidence_certainty / comparator / endpoint_or_threshold
引用溯源 passage(原文段落) / section_page_table(第几条) / license / tier / source_role
硬门控   predates_paper_submission            ★★★
```

另有两个工程字段：`query_context`（记录"为什么检到这条"，便于审计；
下划线前缀的键如 `_card` 只在进程内传递，不落盘）和 `modules`（见 §6 第 ④ 步）。

### `predates_paper_submission` —— 全系统唯一的硬门控

值只能是 `true` / `false` / `unknown`，逻辑极简：指南发布日 ≤ 投稿日 → `true`。

实测（肺癌卡，投稿 2024-05-01）：

```
TRIPOD+AI    发布 2024-04-16  ≤ 投稿  → predates=true   ✅ 可据此要求作者
PROBAST+AI   发布 2025-03-24  >  投稿  → predates=false  ⛔ 不得据此指责作者
```

**指责作者没遵守一个投稿时尚不存在的标准，是审稿里最恶劣的错误之一。**

注意 `predates=false` 的记录**不废弃**——它可以回答"今天还能不能部署"，
但绝不能变成对作者的指控。这个区分必须保留到下游。

---

## 6. 完整跑一遍

```bash
python3 retrieve.py --claim examples/claim_card_lung_ct.yaml --per-source 3
# 指定模块： --modules comparator_baseline endpoint_utility
```

`retrieve.py` 内部发生了什么：

```
① 读卡    claim_to_query() 把 yaml 拍成 query_context
          前 6 键 (condition/intervention/population/outcome/setting/region)
            → 给 API 连接器做查询翻译
          后 4 键 (evidence_stage/model_input/model_output/deployment_claim_level)
            → 给策展层做适用性门控（API 连接器忽略即可）
          `_card` 塞原卡，下划线前缀 = 不落盘

② 路由    对 8 个模块各查 module_routing 得到所需角色
          → 筛出这些角色下**有连接器**的源

③ 取数    每个连接器只调一次，结果进 cache
          （不是每模块调一次——否则 Europe PMC 要被调 8 遍）
          单个连接器抛异常只打 [warn] 并返回空，不拖垮整轮

④ 分配    结果散到相关模块：
          · API 记录 modules 为空 → 按源角色散射到所有相关模块
          · 策展条目自带 modules 声明 → 只落到声明的模块（精确投放）

⑤ 报缺口  module_routing 点名了、但一个带连接器的源都没有的角色
          → 打印 "⚠️ 无连接器角色(待策展)"，并顺带报出发现层检到的候选指南篇数
          这是**显式缺口**，不是静默失败

⑥ 写盘    去重键 = (source_id, canonical_url, section_page_table)
          必须带第三项——TRIPOD 的 52 条共用同一 canonical_url，
          只按 (source_id, url) 去重会把整份清单塌成 1 条
          原子写 (temp + os.replace) 到 store/*.jsonl，并跑一遍 schema 校验
```

### 输出长什么样

CLI 打印三段：① 报告规范清单适用性（启用了哪些 + **没启用的理由**）
② 临床指南适用性（USPSTF 命中/拦截 + 理由）③ 按 8 模块分组的检索结果 + 缺口提示。

落盘产物 `store/retrieved_lung_ct.jsonl`，104 条记录：

```
tripod_ai 52 | probast_ai 34 | europepmc 5 | clinicaltrials_gov 5 | quadas_3 4 | openfda 4
```

这个分布直接反映系统现状：**策展层贡献 90 条（86%），API 层仅 14 条。**
这符合设计预期——固定清单本就是成批的硬要求，API 层只该给少量精准的关键证据。

### 两张示例卡是刻意设计的门控回归测试

```bash
python3 retrieve.py --claim examples/claim_card_lung_ct.yaml    # C1 影像
python3 retrieve.py --claim examples/claim_card_sepsis_c3.yaml  # C3 病房
```

| | 肺癌 CT (C1 影像) | 脓毒症 (C3 病房) |
|---|---|---|
| CLAIM / QUADAS-3 | ✅ 启用 | ⛔ 拦截 |
| DECIDE-AI | ⛔ 拦截 | ✅ 启用 |
| CONSORT / SPIRIT-AI | ⛔ 拦截（仅 C4） | ⛔ 拦截（仅 C4） |
| USPSTF | ✅ 命中肺癌筛查 | ⛔ 场景门控（越权外推） |

**门控必须双向验证**：只测"该启用的启用了"会漏掉最危险的失败模式——什么都启用。

---

## 7. 贯穿全局的四条设计原则

1. **外部只说"应该证明什么"** —— 职责隔离，防止系统编造论文内容。
2. **缺口必须显式** —— 没有源就打印 🕳 / ⚠️，绝不静默返回空；付费全文没摄入就标"未摄入"，
   不许假装覆盖；查不到就返回空，不许编（WHO GHO 曾输出过一行数据都没有的空指标）。
3. **不适用必须给理由** —— 拦下一份清单要说明"因为你是 C1 而它管 C4"。防越级要求的执行点。
4. **时间门控是硬的** —— predates 决定一条证据能否变成对作者的指控。

另有两条治理约束：只用开放/免费且许可允许的内容（access_class A/B/C 逐文档核对）；
国内源已剔除并留档在 `deferred_sources`。

---

## 8. 现状与缺口（诚实版，截至 2026-07-25）

### 已完成
- ✅ 端到端跑通：注册表 / schema / 原子写 / 4 个 API 连接器 / 报告规范策展层 /
  USPSTF 指南层 / predates 门控 / 路由层 / 2 张示例卡
- ✅ 实测 104 条去重记录，schema 零错误
- ✅ 门控双向验证通过（见 §6 表格）
- ✅ API 腿四连接器全部降噪重写

### 缺口

| 缺口 | 严重度 | 说明 |
|---|---|---|
| `normative` 只有 USPSTF | 🔴 最大 | 急重症（脓毒症等）**一个可用指南源都没有**；WHO / 学会指南 / VA-DoD 待策展；NICE 因许可禁令不可行 |
| `epidemiology` 覆盖薄 | 🟠 | 肺癌、成人脓毒症在 WHO GHO 里都无可用指标——数据源本身的局限，连接器如实返回空 |
| CLAIM 2024 / QUADAS-3 条目 | 🟠 | 全文付费，未摄入 |
| Claim Card 自动抽取 | 🟠 | 现在靠手写 |
| 内部原文核验对接 | 🟠 | Claim–Evidence Graph 的另一半尚未建 |

### 一个可用的抓手

发现层（Europe PMC）已能自动检出「中国肺癌筛查指南 2023」「ESICM 成人脓毒症 CPG」
这类 tier1 候选，并在路由输出里报数——**它在自动生成待策展摄入的工作清单**。
这是补 `normative` 最大缺口最现实的路径：发现层找目标，策展层摄入全文。

---

## 9. 术语速查

| 词 | 意思 |
|---|---|
| Claim Card | 从论文抽出的结构化临床主张，系统的输入 |
| 源角色 (source_role) | 一个源"能说什么话"，决定话语权重 |
| 审查模块 | 审稿的 8 个方面 |
| module_routing | 模块 ↔ 角色的映射表，架构心脏 |
| 连接器 (connector) | 去某个源取数的代码 |
| 三条腿 | API 直连 / 报告规范策展 / 指南策展 |
| 适用性门控 | 判断某份清单该不该对这篇论文启用 |
| predates | 该标准是否早于论文投稿日，硬门控 |
| ExternalStandard | 统一输出记录格式 |
| C0–C4 | 证据阶段分级，从回顾性开发到 RCT |
| Claim–Evidence Graph | 外部"应该证明什么" + 内部"实际证明了什么"的汇合结构 |
