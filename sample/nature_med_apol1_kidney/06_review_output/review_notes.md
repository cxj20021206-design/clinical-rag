# 06_review_output —— Nature Medicine 2026 · APOL1 高危基因型肾病进展的蛋白组风险评分

本目录是**人读小结**，不是系统产物。数据在 `03_cards/`（卡）、`04_check/`（核查）、
`05_retrieval/`（检索）。跑之前写下的预期在本 run 的 `README.md`。

## 卡

- `claim_1.yaml` — 病种 `chronic kidney disease` / 人群 `adult` / 场景 `None` / 任务 `prognostication` / 阶段 **留空→由程序映射**

## 阶段三反方核查（独立进程，18 findings）

- issue **8**（low 4 / medium 4）、pass 9、cannot_determine 1
- **无 high 级** —— 按新的严重度定义（high = 错在 gating 层），本 run 的准入层是干净的
- **一条都没回填进卡**：阶段三不改卡，由人决定

## 下游检索

- `claim_1_retrieved.jsonl` — **104 条**去重记录，schema 零错；normative **0 命中（真实缺口）**

## 这一 run 暴露的问题

见 `docs/STATUS_2026-08-02.md` §4（三个既存缺陷）与 §5（待拍板）。
