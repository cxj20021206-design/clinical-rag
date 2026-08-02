# 06_review_output —— ICLR 2024 · 儿科 1 型糖尿病低血糖预警（扩展 de Bruijn 图）

本目录是**人读小结**，不是系统产物。数据在 `03_cards/`（卡）、`04_check/`（核查）、
`05_retrieval/`（检索）。跑之前写下的预期在本 run 的 `README.md`。

## 卡

- `claim_1.yaml` — 病种 `hypoglycemia` / 人群 `child` / 场景 `None` / 任务 `prognostication` / 阶段 **留空→由程序映射**
- `claim_2.yaml` — 病种 `hypoglycemia in type 1 diabetes` / 人群 `child` / 场景 `None` / 任务 `prognostication` / 阶段 **留空→由程序映射**

## 阶段三反方核查（独立进程）

**跑过两轮**（补投稿日前后各一次）。当前 `04_check/*.yaml` 是第二轮，
第一轮存在 `04_check/_before_venue_deadline/`。

| | findings | issue | pass | cannot_determine | high |
|---|---|---|---|---|---|
| 第一轮（卡上无投稿日） | 30 | 10（low 6 / medium 4） | 19 | 1 | **0** |
| 第二轮（补投稿日后） | 38 | 17（low 11 / medium 6） | 21 | 0 | **0** |

- **第二轮零条关于 `submission_date` 的 finding** —— 这正是重跑要验的：卡上多了一个
  论文里根本不存在的日期（`basis: venue_deadline`），若不在阶段三的「填卡约定」里
  说明它是程序补的运维元数据，核查者必然把它报成编造，而且**每一篇会议论文都会重犯**。
  约定第 8 条已补，实测未出现该假阳性。
- **两轮的发现集合只部分重合**（按字段位归一化：共有 5 个，旧独有 8 个，新独有 12 个）。
  这比 dkd 那次观察到的稳定性更差 —— 已知结论「隔离运行之间"抓到什么"稳定、
  "抓到多少/判多重"不稳定」在本 run 上要打折：**连集合本身也在漂**。
  下游若要用阶段三做指标，**必须多次运行取并集**，单次结果不足以支撑"没有问题"。
- **一条都没回填进卡**：阶段三不改卡，由人决定。

> ⚠️ **阶段三产物的顶层键不稳定**：6 个文件里 4 个是 `findings:` 顶层、2 个包在
> `adversarial_check:` 下。按单一形状统计会**静默少算**（本次第一版统计 14 条 vs 实际 30 条）。
> 任何汇总脚本都必须两种形状都认。

## 下游检索

- `claim_1_retrieved.jsonl` — **100 条**去重记录，schema 零错；normative **0 命中（真实缺口）**
- `claim_2_retrieved.jsonl` — **104 条**去重记录，schema 零错；normative **0 命中（真实缺口）**

## 这一 run 暴露的问题

见 `docs/STATUS_2026-08-02.md` §4（三个既存缺陷）与 §5（待拍板）。
