# 全流程样例：一篇 PDF → 审稿要点

**这是本系统目前能做到的全部，端到端跑一遍留下的所有中间产物。**
按目录序号读即可，每一步都注明了"程序做的"还是"模型做的"。

论文：*Non-invasive biopsy diagnosis of diabetic kidney disease via deep learning applied
to retinal images: a population-based study*，**The Lancet Digital Health 2025**，
DOI `10.1016/j.landig.2025.02.008`。

选它的三个理由：**CC BY-NC 开放许可**（PDF 可以直接放进仓库，`corpus/` 里那批 Nature
论文不行）、**不是 Nature**（顺带验抽卡流程对期刊结构的独立性）、
**两个独立临床主张 + 六个研究队列**（能真正验"一篇论文出多张卡"）。

---

## 目录

| 目录 | 内容 | 谁做的 |
|---|---|---|
| `00_source/` | 原始 PDF + `metadata.yaml`（含"三处都没有收稿日"的完整记录） | — |
| `01_parse/` | **MinerU 版面解析**：markdown / `content_list.json` / `images/` / `layout.pdf`；`parse_notes.md` 是与 pypdf 的对照 | 程序 |
| `02_overview/` | 阶段一 `paper_overview.yaml`：6 个队列 + 2 个主张候选，每项带引文 | 模型 |
| `03_cards/` | 阶段二：`claim_1_screening.yaml` / `claim_2_differential_dx.yaml` | 模型 |
| `04_check/` | 阶段三 `adversarial_check.yaml`：七问反方核查 | 模型 |
| `05_retrieval/` | RAG 检索原始 jsonl（99 / 104 条）+ `summary.md` | 程序 |
| `06_review_output/` | **`review_notes.md` —— 终点产物：外部通道能对这篇说什么** | 二者 |

**只读一个文件的话：`06_review_output/review_notes.md`。**

---

## 七步都发生了什么

### ① 解析（程序）
MinerU 做版面解析（GPU 节点，15 页约 9 分钟），目的是拿到 **pypdf 拿不到的东西**。
实测最要紧的一条：**该 PDF 的表格在 pypdf/pdfplumber 下是逐字符逆序的**
（`Sex`→`xeS`），且表格内容在 pypdf 文本里**根本不存在**。
→ 卡的 `provenance.source` 因此必须指向 MinerU 产物。详见 `01_parse/parse_notes.md`。

### ② 概览（模型，`prompts/stage1_overview.md`）
先不填卡，先回答"这篇论文整体在做什么"：是不是原创研究、有几个独立临床主张、
有哪些队列、每个队列干什么用。

产物 `02_overview/paper_overview.yaml`：6 个队列（开发 12 万人 / 外部验证 10 个数据集
6.5 万人 / 活检队列 267 人 / 外部验证 244 人 / 前瞻 325 人 / 纵向 207 人）、
2 个主张候选，**10 条引文全部可逐字定位**。

判"该拆成两张卡"的依据（`CARD_EXTRACTION_SPEC.md` §1）：两个主张各自有
**自己的队列、人群、对照、终点** —— 12 万人 vs 267 人、全体糖尿病人 vs 活检者、
元数据模型 vs 肾活检、筛查敏感度 vs 4·6 年肾功能。合成一张卡这些会全变成混合物。

### ③ 填卡（模型，`prompts/stage2_fill_card.md`）
每个主张各填一张，每个字段带 `status` + 可定位引文。
**模型不填 `evidence_stage`**，只填 `evidence_basis` 七个可观测事实，分级由程序映射。

### ④ 引文核验（程序，`evidence.py`）
每条 quote 必须在解析文本里字面定位，**定位不到就判 `not_extracted`（硬错误）**。
本样例 **13/13 全部定位成功，页码由程序从解析产物反查**（另 2 个 `age_group` 字段
按规则标 `inferred` 且不给引文——理由见 `01_parse/parse_notes.md` ③）。

### ⑤ 字段校验（程序，`claim_card.py`）
枚举、逻辑矛盾、病种字段污染（混入 AI/CT/screening 等词）。本样例零错。

### ⑥ 反方核查（模型，`prompts/stage3_adversarial_check.md`）
七问挑错，每问必须给 verdict + 引文，禁止整体答"没问题"。
本样例挑出 **4 个问题 + 1 个 `cannot_determine`**，其中一个 high。

⚠️ 本次是**在同一上下文里跑的**（已标 `context_isolated: false`），
按 prompt 只能当**下界**：挑出来的是真的，没挑出来的不能算没有。

### ⑦ 检索（程序，`retrieve.py`）
两张卡各跑一次，99 / 104 条，schema 零错。

---

## 这一遍最值得看的三个结果

**① 报告清单双向门控正确。** TRIPOD+AI(52) 与 PROBAST+AI(34) 两张卡都启用；
QUADAS-3 **只在 claim_2 启用**（诊断准确性），claim_1 是筛查故拦截；
CONSORT-AI / SPIRIT-AI / DECIDE-AI 全部因"C2 套 C4 属越级要求"被拦并打印理由。

**② normative 命中 0 条 —— 这是真实缺口不是故障。** 糖尿病肾病与心衰/糖网/AKI 同属
库的已知空白。系统必须如实说"我没有可对齐的临床指南"，而不是拿相邻病种凑数。
同时 discovery 层检出 **6 份高度切题的 DKD 指南候选**（题录+摘要，**不得冒充规范条目**）
→ 糖尿病肾病成为继 WHO IMCI 之后第二个**由真实论文驱动**的摄入目标。

**③ 检回来的 86 条清单条目，按铁律一条都不能用来要求作者。** 因为
`submission_date` 取的是保守下界 2024-03-10，而 TRIPOD+AI 发布于 2024-04-16。
这是 `docs/STATUS_2026-07-31.md` §5-A 要拍板的事。

---

## 复现

```bash
cd /work/hdd/bgkq/Code/clinical-rag

# 引文核验（不联网）
python3 evidence.py sample/dkd_retinal_ldh/03_cards/*.yaml
# 字段校验 + 门控视图
python3 claim_card.py sample/dkd_retinal_ldh/03_cards/*.yaml
# 检索（联网，每张卡 2–3 分钟）
python3 retrieve.py --claim sample/dkd_retinal_ldh/03_cards/claim_1_screening.yaml --out /tmp/c1.jsonl
```
