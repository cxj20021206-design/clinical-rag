# 外部通道能对这篇论文说什么

**这是整条链的终点产物。** 上游六步（解析 → overview → 卡 → 核查 → 检索）的价值，
全部体现在下面这些句子能不能被说出来、能不能被追溯到出处。

> **铁律再述**：本通道只说论文**应该证明什么**（对齐现实医学标准），
> 绝不代替论文说它**实际做到了什么**（那是内部原文核验，另一个子系统，尚未开工）。
> 因此下面每条都是"该对齐/该回答"，不是"该给几分"。

论文：*Non-invasive biopsy diagnosis of diabetic kidney disease via deep learning applied
to retinal images*（Lancet Digit Health 2025, DOI 10.1016/j.landig.2025.02.008）
两张卡：`claim_1` 筛查 DKD（primary_care / screening / C2）、
`claim_2` 区分糖尿病肾病 vs 非糖尿病肾病（outpatient_specialty / diagnosis / C2）。

---

## A. 报告规范（可直接对照条目，检索命中最多的一层）

| 清单 | 状态 | 对本文意味着什么 |
|---|---|---|
| TRIPOD+AI（52 条） | ✅ 适用 | 预测模型开发+验证报告清单。本文是开发+外部验证，应逐条对照 |
| PROBAST+AI（34 条） | ✅ 适用 | 偏倚风险评估。C2 论文的核心工具 |
| QUADAS-3 | ✅ **仅 claim_2** | 诊断准确性研究专用；claim_1 是筛查不是诊断，**不适用于 claim_1** |
| CONSORT-AI / SPIRIT-AI | ⛔ 拦截 | "论文为 C2，本清单适用于 C4，**对 C2 论文套用属越级要求**" |
| DECIDE-AI | ⛔ 拦截 | 同上（适用 C3/C4） |
| CLAIM 2024 | ⚠️ 适用但**库内无条目** | 付费墙未摄入（`completeness: none`）。**系统不得声称已按其核查** |

> ⚠️ **但 predates 全为 false**：TRIPOD+AI 发布于 2024-04-16、PROBAST+AI 2025-03-24，
> 都晚于本卡采用的 `submission_date = 2024-03-10`。按项目铁律，**不能拿这两份清单
> 去指责作者**——只能作为"读者/编辑视角的参考"，不能写成"作者未遵守 TRIPOD+AI"。
> 而 2024-03-10 是**保守下界**（论文的文献检索截止日），不是真实收稿日，见 §D。

## B. 规范指南（normative）：**0 条命中，这是真实缺口**

两张卡的 USPSTF / 学会 CPG / WHO IRIS 三个源族**全部为空**。

这不是故障，是库的已知缺口被真实论文撞上了：`DESIGN.md §7` 早就记着
**心衰 / 糖网 / AKI 三个缺口两条通道都补不上**（EuropePMC 侧许可拿不到，
WHO IRIS 侧无推荐结构）。糖尿病肾病属同一片空白。

**所以对这篇论文，外部通道必须如实说："我没有可对齐的临床指南"**，
而不是拿相邻病种的指南凑数。这正是 `RELATED_WORK.md` §2 说的**弃权**能力。

### 但 discovery 层给出了由这篇论文驱动的待策展清单（真实收获）

EuropePMC 以 `PUB_TYPE:"Guideline"` 检出 6 份高度切题的规范文档候选，
**均为题录+摘要，`recommendation_or_requirement` 留空，不得冒充规范条目**：

| tier | 文档 | 用于 |
|---|---|---|
| 1 | National technical guidelines for the prevention and treatment of diabetic kidney disease（中国） | claim_1 筛查路径 |
| 1 | Clinical Practice Guideline for detection and management of diabetic kidney disease | claim_2 |
| 1 | Clinical practice guidelines for management of hyperglycaemia in adults with diabetic kidney disease | claim_2 |
| 1 | Validation of the 2007 KDOQI clinical practice guideline | claim_2 |
| 1 | Clinical practice guideline for the management of lipids in adults with diabetic kidney disease | claim_1（predates=false） |
| 2 | Information and consensus document for the detection and management of chronic kidney disease | claim_1 |

→ **糖尿病肾病成为继 WHO IMCI 之后，第二个由真实论文驱动、而非按病种猜测得出的摄入目标。**

## C. 试验注册与监管（registry / regulatory）

- **CT.gov 4 条**：同类研究的对照与终点设置，可用于问"你的对照选得合不合现实"。
- **openFDA 器械库 4 条**：糖网 AI 的法定预期用途（PIB 类：IDx-DR / EyeArt 等）。
  对本文有直接可比性——**同样是眼底照相 AI，监管定位是"辅助"还是"独立出报告"**，
  是审稿该追问的问题。
- **WHO GHO**：claim_2 命中 1 条糖网筛查覆盖率指标；claim_1 为 0（如实返空）。

## D. 由本次抽卡暴露、必须写进审稿意见的三点

这三点**不来自检索，来自阶段三反方核查**（`04_check/`），是外部通道的另一半产出：

1. **对照的性质被并列了**（high）。论文真正做过头对头比较的只有自建的 metadata model；
   "尿蛋白试纸敏感度 43·6%–69·4%" 是**引自文献**（ref 10–12），不是本研究在同一人群
   里测的。审稿应问：**与现行临床筛查做法（eGFR/ACR、试纸）有无头对头比较？**

2. **主验证并非前瞻**。前瞻性只存在于 325 人的 proof-of-concept 子研究；
   十个外部数据集的主验证不是前瞻设计。宣称"real-world effectiveness"时需注意范围。

3. **appendix 未获取**（`input_coverage: included: false`）。数据集的纳入排除标准写在
   appendix pp 4–5/15–16，**本次核查无法核实**，`different_site` 一项按
   `cannot_determine` 处理。系统不得声称"已按全文核查"。

## E. 三个"不说"

外部通道**不会**说下面这些话，即使听起来合理：

- ❌ "该模型可用于临床" —— 那是内部核验+人的判断，不是外部标准能定的
- ❌ "AUC 0·842 偏低" —— 阈值判断需要疾病特异的临床语境，库里没有就不编
- ❌ "未遵守 TRIPOD+AI" —— 清单发布于投稿之后（见 §A 的 predates 警告）
