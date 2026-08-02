# sample / iclr2024_pediatric_hypoglycemia

**ICLR 2024** · *A Reinforcement Learning Approach to Effective Forecasting of Pediatric
Hypoglycemia in Diabetes I Patients: an extended de Bruijn Graph* ·
OpenReview `kP6X9QBF1t`

2026-08-01 重写后 prompt 的第一次实跑（两篇之一，另一篇是
[`../nature_med_apol1_kidney`](../nature_med_apol1_kidney)）。

**这是本项目第一次跑 ICLR 论文。** ICLR MedAI 论文正是 MVP 三臂评测的目标语料
（`DESIGN.md` §8），此前所有样例都来自医学期刊。

## 版权

PDF 与解析产物均已 gitignore（ICLR/OpenReview 多为 CC BY 4.0，但本篇未逐条核实，
按保守侧处理）。原文路径见 `00_source/metadata.yaml: pdf_origin`。

---

## 跑之前写下的预期（**出结果之前写完，不许事后改**）

### 为什么选一篇会议论文（要压测什么）

| 压测点 | 期刊论文不具备的性质 | 预期 |
|---|---|---|
| **章节结构** | 会议论文没有 Nature 那种 Methods/Results 固定分节 | 硬约束 2 说"不得假定信息位于固定章节"—— 这篇是真正的检验；预期取证位置分散，且**仍应给出 `locator`** |
| **投稿日** | ICLR PDF 通常无 `Received:` / `Accepted:`，也少有"文献检索截止日" | 预期落到 stage1 §③ **第 4 档 `unavailable`**。届时 `validate_card()` 会**硬报错**（`submission_date` 缺失 → predates 门控没有基准）。**这是一个真实边界，不是 bug**，处理方式待定 |
| ↑ **实跑结果（2026-08-01 补）** | — | 预期应验：阶段一停在第 4 档、拒绝反推。**随后已拍板加第 5 档 `venue_deadline`**（`docs/STATUS_2026-08-02.md` §8）：日期由 `venue_deadlines.yaml` 提供、`extract.py stamp-date` 补入，两张卡现已 ✓ 通过硬门。重跑检索与临时变体产出的记录**逐条完全一致**（100 / 104） |
| **人群** | 儿科 | `population.age_group` 应为 `child`（或按 SPEC §5 填更宽的儿科档），**库里的成人 CPG 应被人群门控硬拦** |
| **临床声明强度** | 摘要写了"提前约 30 分钟，sufficient for a clinical setting"、"actionable rules" | `clinical_claim_made` 应为 **true**（不是纯基准论文）；但 `intended_context` 该按**已经做到的**那一层填，不按作者想去的那一层 —— 这正是规则⑬与阶段三第 1 问的靶子 |

### 对卡本身的预期

| 项 | 预期 |
|---|---|
| `condition.primary.label` | `type 1 diabetes`（英文；**不许写成 `pediatric hypoglycemia forecasting`** —— 那混了人群词与任务词） |
| `clinical_task` | `prognostication`（提前预警） |
| `care_setting` | **很可能该空**（论文没说部署在哪）→ 标 `absent`，**这是正确行为不是漏填** |
| `evidence_stage` | 预期 **C0 或 C1** —— 看有无独立外部队列 |
| **报告清单** | TRIPOD+AI / PROBAST+AI 视 `model_output` 判定；CONSORT-AI / DECIDE-AI / SPIRIT-AI 应因阶段不匹配 ⛔ 并打印理由 |
| **normative** | 预期 **0 命中**。USPSTF 有《儿童青少年糖尿病前期与 2 型糖尿病：筛查》，但本篇是 **1 型糖尿病的低血糖预警**，任务是预后不是筛查 → 应被**病种/职权双重拦下并说明理由**，而不是勉强命中 |

### 两篇合起来要看的对照

1. **人群门控双向**：成人（Nature 篇）与儿科（本篇）应当各自拦下对方那一侧的指南。
2. **缺口报告的可信度**：两篇预期 normative 都是 0 命中。系统必须区分
   "**不匹配**（该补库）" 与 "**不适用**（不该计入缺口）"，并对每一份 ⛔ 打印理由。
   如果两篇的 0 命中都只是静悄悄的空列表，那就是缺口报告失真。
3. **会议 vs 期刊的取证难度**：同一套 prompt 下，Methods 类出处的比例差多少。

---

## 不进入流程的东西

OpenReview 上有本篇的**真实评审分数与 decision**（`ratings` / `avg_rating` /
`decision`，在 `/work/hdd/bgkq/xchen48/aris-paper-review/data/dataset.jsonl`）。
**不喂给抽卡或核查的任何一步** —— 那是循环论证。只作为日后对照答案保存。
