# 相关工作调研

**调研日期**：2026-07-25
**调研动机**：补 `normative`（临床指南）缺口——8 个审查模块里 7 个把它列为首选源，
目前一个连接器都没有（见 [DESIGN.md](DESIGN.md) §7.1）。查市面上/文献里有没有
同类工作可借鉴，尤其是"指南全文怎么合法拿到、拿到后怎么结构化"这两步。

**证据强度标注**：本文档区分三档——
`[全文]` 读到全文并核对细节 · `[摘要]` 仅读摘要/搜索摘要 · `[未读]` 未获取到正文。
未读到的不得当作已核实的结论引用。

---

## 1. 最直接的同类工作：NICE 指南 RAG 系统

**《Grounding Large Language Models in Clinical Evidence: A Retrieval-Augmented
Generation System for Querying UK NICE Clinical Guidelines》** — arXiv 2510.02967 `[全文]`

这篇基本把本项目第三条腿的第②③步做完了（取全文 → 切块 → 检索）。

### 方法细节

| 环节 | 做法 |
|---|---|
| 取数 | 走 **NICE 官方 API**，2025-07-16 一次获取 **2164 份文档** |
| 语料 | 聚焦 300 份 NICE Guidelines (NG) + 历史 Clinical Guidelines (CG)，平均 **9611 词/份**，切出 **10195 个文本块** |
| 切块 | **层级语义分块**：先按一级/二级标题切，块长控制在 200–600 token；超长块在子标题或段落处再切，**重叠 50 token** |
| 检索 | 稠密 **Voyage-3-Large** + 稀疏 **BM25**，**加权 RRF 融合**（k=40）；另测过 text-embedding-3-large |
| 重排 | **Voyage Reranker-2**，作用于 top-15 |
| 效果 | **MRR 0.814**，第一块召回 **81%**，top-10 召回 **99.1%**；Faithfulness 0.995、Context Precision 1.0（无 RAG 对照组 Faithfulness 低至 0.348） |
| 评估 | 7901 条 query 上做检索评估（GPT-4.1-Nano 合成，9296 对 query-chunk）；另有 70 条人工策展 QA 对 |

### 对本项目的意义

**正面**：技术路线不必自己摸索。指南全文一旦到手，切块与检索是成熟技术，
**难点自始至终是"怎么合法拿到全文"**，不是拿到之后怎么处理。
建议直接沿用其层级语义分块 + 混合检索 + reranker 的配置。

**它的局限恰好是本项目已有的设计强项**（三条都对得上）：

| 论文自述局限 | 本项目现状 |
|---|---|
| 推荐强度、证据等级**没有抽成独立字段**，混在文本块内 | `schema.py` 已有 `recommendation_strength` / `evidence_certainty` 独立字段（目前 0/104 有值，待 normative 层填充） |
| **"未测试系统在没有合适指南时的拒答能力"** | 本项目自始即为"宁可不提，不可乱提"，缺口显式打印（⛔/🕳/⚠️ 四档标记） |
| 评估 query 由 GPT 合成，缺真人验证 | 可用已枚举的 225 篇顶刊（NBE/NM）真人临床评审做 gold |
| 多源 query（答案跨多份指南）未测试 | 路由层本就是多源分模块聚合 |
| 外部 API 有 GDPR/HIPAA 风险，临床部署需本地模型 | 本项目为离线评估用途，不涉及患者数据 |

---

## 2. ⛔ 坏消息：NICE API 这条路本项目走不通

查 NICE syndication API 官方条款 `[全文]`，三条限制叠加：

1. **免费仅限英国境内**；国际使用「subject to an approval process, licensing
   arrangement and a fee」
2. **「若要在 NICE 内容上使用 AI，必须通过 NICE API 单独取得许可——AI 用途不在
   NICE UK Open Content Licence 覆盖范围内」**
3. 仅面向**机构/公司**，不面向个人

第 2 条是致命的：本项目做的恰恰就是"在指南内容上用 AI"。
上节那篇论文能做，大概率因其为英国机构。

**注册表当初的判断是对的**——`clinical_sources.yaml` 里已记：

```yaml
nice_guidance:
  license: "免费阅读+API；国际复用(非个人研究)可能需许可付费"
  license_review_required: true
```

**结论：第一个攻的目标不是 NICE，是 USPSTF。**
USPSTF 为美国政府作品、公有领域、无版权障碍、网页结构整齐，是一线指南源里
唯一没有许可门槛的。此前选它是因为"好抄"，现在理由升级为**唯一无法律障碍**。

---

## 3. ✅ 意外收获：有论文证明本项目的核心设计原则是对的

**《Clinician-Level Agreement Without Clinical Caution: LLM Evaluator Limits in
Medical AI Benchmarking》** — arXiv 2607.01103（2026-07）`[摘要]`

### 发现

- 数据集 MedQADE：3800 道德语医学开放式问题，10 名执业医生标注；测 9 个 LLM 评委
- 最佳模型 Gemini 3 Flash **κ=0.694**，医生间一致性 **κ=0.709** —— 统计上看似追平人类
- 但拆开看有两个病：
  - **医生会随题目难度调整弃权率（难题更常弃权），前沿 LLM 弃权率为零**，任何情况都给出确定分数
  - **同血统偏好**（systematic lineage-dependent biases）：模型倾向给同架构模型的输出打高分
- 核心论断：**「statistical alignment does not ensure clinical caution」**

### 对本项目的意义

**这是现成的动机靶子。**本项目一系列看似"保守"的机制，本质上全是**弃权机制**：

```
⛔ 论文为 C1，CONSORT-AI 适用于 C4，套用属越级要求      ← 越级弃权
🕳 CLAIM 2024 适用但一条未摄入，不得显示为已覆盖        ← 覆盖度弃权
⚠️ 无连接器角色(待策展): ['normative']                 ← 能力边界弃权
WHO GHO 查不到 → 如实返回空，不编造覆盖                ← 数据缺失弃权
predates=false → 可评"今天能否部署"，不得指责作者       ← 时间越界弃权
```

`curated_reporting.py: check_applicability()` 的注释「无法判定研究设计 → 不启用，
宁可不提，不可乱提」即此原则的直接实现。

**别人诊断出的病（LLM 评委不会说"我不知道"），本架构从第一天就在治。**
建议写入论文动机段，并在 MVP 评测中**显式测量弃权/缺口报告率**作为一项指标——
该论文表明这是现有 LLM 评委的系统性空白。

---

## 4. 有一套标准，本项目在重新发明

- **CPG-on-FHIR** — HL7 的可计算临床指南标准 `[摘要]`
  （Representation of evidence-based CPG recommendations on FHIR, JBI 2023）
- **MAGICapp** — BMJ Rapid Recommendations 使用的结构化指南发布平台，
  内建 GRADE 方法学与 Evidence-to-Decision (EtD) 框架 `[摘要]`
- 学术领域名称：**Computer-Interpretable Guidelines (CIG)**，是有二十年积累的成熟方向

计划中的"指南条目 yaml"（推荐原文 + 推荐强度 + 证据确定性 + 适用人群），
**本质上就是 CPG-on-FHIR / MAGICapp 在做的事**。

**建议：不改用 FHIR**（该标准为临床决策支持系统设计，对本项目过重），
但**字段命名向其对齐**。两个实际理由：
1. 日后若要吃 MAGICapp 的结构化数据，字段对得上即可省一次转换
2. 论文中可声明"表示与 CPG-on-FHIR 对齐"，比自造一套有说服力

注：`clinical_sources.yaml` 已收录 `hl7_fhir` 源（terminology 角色，未接）。

---

## 5. 第二条腿（报告清单）也有同类验证工作

**《Large Language Models for Detecting CONSORT Guideline Compliance in Published
Randomized Clinical Trials: A Cross-Sectional Evaluation Study》** —
medRxiv 2025.10.03.25337291 `[未读]`（PDF 返回 403）

即用 LLM 自动核查论文是否符合 CONSORT 清单条目。

**意义**：说明本项目第二条腿"清单条目 → 自动核查"是学界在做的同类路线，
非孤立发明，MVP 评测设计中可引用。**全文未读到，具体准确率与结论待补。**

相关背景（搜索摘要，`[摘要]`）：GPT-4 类模型可检出约 **53%** 的人为植入错误，
接近人类审稿人水平；但另有综述指出 AI 生成评审与人类评审相关性有限、
与最终接收结果关联微弱。

---

## 6. 未读到 / 待补

| 文献 | 为何重要 | 状态 |
|---|---|---|
| 《High-precision information retrieval for rapid clinical guideline updates》npj Digital Medicine 2025 (s41746-025-01648-5) | 直接对应第①步"如何精确检出相关指南" | **被 Nature 登录墙拦截**，待换 PMC / 预印本 |
| medRxiv CONSORT 合规检测全文 | 第二条腿的直接同类验证 | HTTP 403，待换源 |
| Med-R², i-MedRAG, MedGraphRAG | 医学 RAG 的其他路线 | 仅见于搜索结果，未评估 |
| ECRI Guidelines Trust / GIN / Epistemonikos | 可能存在的现成指南聚合库 | **尚未调研**，值得单独查一轮 |

---

## 7. 由本次调研引出的计划修订

> **执行状态（2026-07-26 更新）**：
> - 第 1 条 ✅ USPSTF 全量摄入，`normative` 首个连接器上线（[DESIGN.md](DESIGN.md) §6c）。
>   其后又补第二个源族：学会/国家 CPG 经 **Europe PMC OA 全文**通道摄入 4 份 108 条（§6d）——
>   这条通道是本调研没预见到的：找"指南的官网"不可持续，找"指南的开放获取论文版本"才可行。
> - 第 2 条 ⏭ 仍未做。两个源族都靠结构化表格/推荐节躲开了长文档分块，一旦摄入整份 PDF
>   指南（WHO IRIS / VA-DoD）就必须做，届时沿用 arXiv 2510.02967 配置。
> - 第 3 条 ✅ `recommendation_strength` / `evidence_certainty` 已是独立字段并有值。
>   实做中发现本调研漏掉的一层：**各家分级词汇表不可通约**（GRADE 强度 vs A–D 质量等级 vs
>   "专家一致程度"），故只逐源如实记录、拒绝归一化，另单列 `agreement` 字段。
> - 第 4 条 ⏭ 弃权/缺口报告率指标待 MVP 评测。可测的弃权点已经攒了一批：越级、覆盖度、
>   能力边界、数据缺失、时间越界，现又添**许可弃权**（SSC 无开放许可 → 拒收，
>   系统不得声称已按 SSC 核查）与**结构弃权**（抽不出推荐结构 → 拒绝关键词抓句降级）。


对 [DESIGN.md](DESIGN.md) §7.1 规范指南策展摄入层的三处修订：

1. **首攻目标锁定 USPSTF**，理由从"结构好抄"升级为**唯一无许可障碍的一线指南源**；
   NICE 因 AI 用途许可禁令**降为不可行**（除非另行取得机构许可）。
2. **检索与切块直接沿用 arXiv 2510.02967 的配置**：层级语义分块（200–600 token，
   overlap 50）+ BM25/稠密混合 + RRF + reranker。不自行调参。
3. **`recommendation_strength` / `evidence_certainty` 必须抽为独立字段**——
   这是同类工作的公认短板，也是本项目可主张的差异点。

另新增一条：

4. **MVP 评测增设"弃权/缺口报告率"指标**，依据 arXiv 2607.01103 揭示的
   LLM 评委零弃权率问题。这一项现有工作没有测，是本架构的天然优势。

---

## 附：本次调研检索到的全部来源

- [arXiv 2510.02967 — RAG for UK NICE Guidelines](https://arxiv.org/abs/2510.02967) `[全文]`
- [arXiv 2607.01103 — Clinician-Level Agreement Without Clinical Caution](https://arxiv.org/abs/2607.01103) `[摘要]`
- [NICE syndication API 条款](https://www.nice.org.uk/reusing-our-content/nice-syndication-api) `[全文]`
- [NICE API getting started](https://www.nice.org.uk/corporate/ecd10/chapter/getting-started) `[全文]`
- [Representation of evidence-based CPG recommendations on FHIR](https://www.sciencedirect.com/science/article/pii/S1532046423000266) `[摘要]`
- [Computer-interpretable clinical guidelines: a methodological review](https://www.sciencedirect.com/science/article/pii/S1532046413000841) `[摘要]`
- [MAGICapp / BMJ RapidRecs](https://www.magicevidence.org/BMJ-RapidRecs/) `[摘要]`
- [LLMs for Detecting CONSORT Compliance (medRxiv)](https://www.medrxiv.org/content/10.1101/2025.10.03.25337291.full.pdf) `[未读，403]`
- [High-precision IR for rapid clinical guideline updates (npj Digit Med)](https://www.nature.com/articles/s41746-025-01648-5) `[未读，登录墙]`
- [A systematic review of LLM evaluations in clinical medicine (BMC MIDM)](https://bmcmedinformdecismak.biomedcentral.com/articles/10.1186/s12911-025-02954-4) `[摘要]`
- [Automated Monitoring of Adherence to CPG Recommendations (JMIR 2023)](https://www.jmir.org/2023/1/e41177) `[摘要]`
