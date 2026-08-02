# sample / nature_med_apol1_kidney

**Nature Medicine 2026** · *Proteomic risk score for early prediction of kidney disease
progression in individuals with APOL1 high-risk genotypes* · DOI `10.1038/s41591-026-04337-2`

这是 **2026-08-01 重写后的 prompt 的第一次实跑**（两篇之一，另一篇是
[`../iclr2024_pediatric_hypoglycemia`](../iclr2024_pediatric_hypoglycemia)）。
在此之前，仓库里所有的卡与核查报告**全部出自旧 prompt**。

## 版权

**PDF 与 MinerU 解析产物均已 gitignore**（Nature Medicine 为订阅制内容）。
仓库里只保留派生物：卡、核查报告、检索结果。
原文路径见 `00_source/metadata.yaml: pdf_origin`，据此可重跑 `01_parse` 复现。

## 目录

```
00_source/     PDF（不入库）+ metadata.yaml
01_parse/      MinerU 版面解析产物（不入库）
02_overview/   阶段一：paper_overview.yaml
03_cards/      阶段二：每个 claim 一张卡
04_check/      阶段三：反方核查报告（不改卡）
05_retrieval/  下游检索结果
06_review_output/  最终产物与人读小结
```

---

## 跑之前写下的预期（**这一节在出结果之前写完，不许事后改**）

写下预期是为了让这次实跑成为**可证伪的检验**。旧做法（先跑完再解释为什么合理）
在本项目里出过事故：`examples/claim_card_ctg_fetal.yaml` 是先看到 WHO 那条推荐
才写的卡，等于把答案写进题干。

### 本轮 prompt 改了什么，因此预期看到什么

| 改动 | 预期可观测的变化 | 对照基线（旧 prompt 产物） |
|---|---|---|
| `provenance.fields` 必填清单（19 条） | provenance 条目数 **≥19** | `sample/dkd_retinal_ldh` 两张卡分别 7 / 6 条 |
| 硬约束 2：摘要不是默认取证来源 | **出现 `locator: methods`（或表号）的条目**；`population.*` / `care_setting` / `comparator` / `evidence_basis` 一条都不许取自 abstract | 旧卡 51 条 provenance 里 32 条 abstract、2 条 title、**Methods 零条** |
| 硬约束 5：`status` 必填 | **每条都有 `status`**，无一省略 | 旧卡 51 条里 27 条没写 |
| 规则⑪：涉及人群/数字的条目带 `cohort_id` | 出现 `cohort_id` 字段 | 旧卡完全没有 |
| 规则⑧扩到 8 个字段的语言规则 | `target_population` / `condition.primary.label` 等**全部英文** | 旧卡 `target_population` 是中文 |
| 阶段一新增第 6、7 问 | overview 里出现 `reference_standard` 与 `current_practice.head_to_head` | 旧 overview 没有这两项 |

### 对这篇论文本身的预期

| 项 | 预期 | 判据 |
|---|---|---|
| `article_type` | `original_research` | — |
| 主张数 | **不确定**（≥1）。若蛋白组评分与 APOL1 基因分层各自有独立队列/对照/终点则应拆 | SPEC §1 四条信号 |
| `clinical_task` | `prognostication`（预测**进展**，不是筛查也不是诊断） | 由主要终点决定，见 SPEC §4 |
| `population.age_group` | `adult` | — |
| `evidence_stage` | **C1 或 C2**，取决于有无跨机构外部队列 | 由 `evidence_basis` 七事实经程序映射 |
| **报告清单** | **TRIPOD+AI ✅ 与 PROBAST+AI ✅ 应当启用**；CONSORT-AI / SPIRIT-AI / DECIDE-AI 应因阶段不匹配被 ⛔ 拦下并打印理由 | 这是验证"语言规则修好后清单没被静默关掉"的关键观测点 |
| **normative** | **预期 0 命中，且系统应如实报缺口** | 肾病进展属已知缺口：心衰/糖网/AKI/DKD 在 EPMC 与 WHO IRIS 两条通道都补不上 |
| `discovery` | 应检出若干切题的 CKD/APOL1 文献或候选指南 | 若同时给出候选指南清单，则可作为下一个摄入目标 |
| `submission_date` | 预期能拿到 `Received:`（Nature 系排版特点） | 若拿不到，走 stage1 §③ 阶梯 |

### 会被记为"prompt 没生效"的信号

- provenance 条目仍停在个位数；
- 仍然一条 Methods 出处都没有；
- `status` 仍有缺省；
- 被消费字段仍出现中文。

---

## 已知的、不属于本轮验证范围的东西

- 上一轮那 20 条 issue **不回填**（本轮只改 prompt，不改卡）；
- 本篇有**真人 peer review**（在 `journals/peer_review/reviewed_papers.jsonl` 的 225 篇内），
  但**不进入抽卡流程的任何一步** —— 那会是循环论证。它只作为日后 Flaw-Recall
  评测的对照答案保存。
