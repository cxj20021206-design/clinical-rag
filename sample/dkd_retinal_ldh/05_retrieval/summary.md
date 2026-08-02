# RAG 检索结果摘要

命令（每张卡各跑一次，联网约 2–3 分钟）：

```bash
python3 retrieve.py --claim sample/dkd_retinal_ldh/03_cards/claim_1_screening.yaml --out store/dkd_c1.jsonl
python3 retrieve.py --claim sample/dkd_retinal_ldh/03_cards/claim_2_differential_dx.yaml --out store/dkd_c2.jsonl
```

> 当初实跑用的路径是 `corpus/extracted/dkd_retinal_ldh_claim{1,2}.yaml`。那份是 `sample/`
> run 目录约定之前的副本，2026-08-02 已删（与 `03_cards/` 重复）。两版 **yaml 解析后完全
> 相同**（差异只有注释：旧版留着"本篇不在 corpus/gold 里"那句过程元信息，按 stage2 规则⑭
> 已删），故上面这两条命令复现的是同一批记录。

原始记录：`claim_1_retrieved.jsonl`（99 条）/ `claim_2_retrieved.jsonl`（104 条），
**schema 错误均为 0**。去重后每条记录带 `source_id` / `tier` / `document_type` /
`version_or_publication_date` / `predates_paper_submission` / `license` / `url`。

## 按源统计

| 源 | claim_1 | claim_2 | 说明 |
|---|---|---|---|
| `tripod_ai` | 52 | 52 | 报告清单，逐条 |
| `probast_ai` | 34 | 34 | 偏倚风险条目 |
| `quadas_3` | — | 4 | **只对 claim_2 启用**（诊断准确性），claim_1 是筛查故拦截 |
| `europepmc` | 5 | 5 | discovery：2 份指南 + 1 份共识 + 综述/文献 |
| `clinicaltrials_gov` | 4 | 4 | 同类试验的对照与终点 |
| `openfda` | 4 | 4 | 器械库：眼底 AI 的法定预期用途 |
| `who_gho` | 0 | 1 | claim_1 如实返空；claim_2 命中糖网筛查覆盖率 |
| **normative（USPSTF / CPG / WHO IRIS）** | **0** | **0** | **真实缺口，见 06** |

## 两个必须一起读的数字

**① `predates=false` 占 88/99 与 92/104。** 不是检索错了，是
`submission_date = 2024-03-10`（保守下界）比 TRIPOD+AI（2024-04-16）和
PROBAST+AI（2025-03-24）都早 → 这两份清单**不能用来要求作者**。
换句话说：**这张卡检回了 86 条清单条目，但按铁律一条都不能拿去指责作者。**
这是 `submission_date` 那个决定的直接代价，是要拍板的事（见 `docs/STATUS_2026-07-31.md` §4）。

**② normative 为 0，但 discovery 检出 6 份切题指南候选。** 前者是"库里没有"，
后者是"世界上有、我们没摄入"。两者必须分开呈现，否则缺口报告会把
"该补库"读成"该领域没有标准"。

## 一个反例（说明降噪仍有边界）

`claim_1` 的 EuropePMC 命中里有一条
*Clinical practice guideline for the management of lipids in adults with diabetic kidney disease* ——
病种切题（DKD），但主题是**血脂管理**，与"用眼底照筛查 DKD"无关。
§6a 的降噪解决的是"完全跑题"（肝癌、鼻窦炎），解决不了"同病种但不同临床问题"。
这需要的是 Claim Card 的 `clinical_task` 参与 discovery 层排序，**目前没做**。
