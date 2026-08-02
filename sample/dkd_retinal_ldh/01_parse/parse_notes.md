# 解析层对照：MinerU 版面解析 vs pypdf/pdfplumber

**结论先行：这一步不是"把 PDF 转成字"那么简单——解析层的能力直接决定了卡里能有什么
证据，也直接决定了核查阶段是"判得出"还是"判不出"。**

## 产物

```
10.1016_j.landig.2025.02.008/auto/
  ├── *.md                    73 209 字符，表格以 HTML 呈现
  ├── *_content_list.json     分块 + page_idx（**卡的 provenance.source 指向它**）
  ├── *_middle.json           版面中间结果
  ├── *_layout.pdf            ← 版面框可视化，人看这个最快
  ├── *_span.pdf              文本 span 可视化
  └── images/                 18 张切图
pypdf_baseline/
  ├── images/                 PyMuPDF 抽的 4 张原图
  ├── tables.json             pdfplumber 抽的 6 个"表格"
  └── captions.txt            正则扫出的 4 条图表题注
```

MinerU 在 GPU 节点上跑（`gpuA100x4-interactive`，15 页约 9 分钟，含模型加载）。

## 分块统计（MinerU `content_list.json`）

| 类型 | 数量 |
|---|---|
| text | 96 |
| header / footer / page_number | 27 / 14 / 14 |
| **image** | **12** |
| **chart** | **4** |
| **table** | **2**（结构化 HTML） |
| list / aside_text / page_footnote | 2 / 4 / 1 |

## 三个实测差异

### ① 表格：pdfplumber 抽出来是**逐字符逆序**的

```
pdfplumber:  ['xeS', ')%40⋅72(\n931', ')%69⋅27(\n573']
读作:         Sex      (27·04%) 139      (72·96%) 375
```

`xeS` = `Sex` 反写，`)%40⋅72(` = `(27·04%)` 反写。这是该 PDF 的表格文字渲染顺序问题，
pypdf 同样中招。MinerU 的版面模型把它还原成正常的 HTML 表格：

```html
<table><tr><td rowspan="2"></td><td>Developmental dataset</td>
<td>Internal validation dataset</td><td colspan="11">External validation datasets</td></tr>
<tr><td>CNDCS</td><td>NDSP</td><td>PUDM</td><td>ECHM</td><td>CUHK-STDR</td>
<td>SEED</td><td>UKB</td><td>MeLODY</td><td>NICOLA</td><td>AHES</td></tr>…
```

**这不是锦上添花——它改变了卡里能有什么证据。**

### ② 表格里的内容在 pypdf 文本中**根本不存在**

实测：

| 字符串 | pypdf 文本 | MinerU |
|---|---|---|
| `Number of participants with diabetes` | **无** | 有（p.5） |
| `66.05`（开发集平均年龄） | **无** | 有 |
| `MeLODY` / `NICOLA` | 有 | 有 |

→ 由此定下一条规矩，已写进 `evidence.py`：

> **`provenance.source` 必须指向抽卡时实际读的那份解析产物。**
> 拿 MinerU 抽卡、却用 pypdf 文本核验，表格来源的引文会被判定位失败 →
> 标成 `not_extracted`（硬错误）→ **把解析层的能力差异误报成抽取故障**。

新增入口 `SourceDoc.from_mineru(*_content_list.json)`：保留分页、包含表格与图注。
两张卡改用它后 **13/13 引文定位成功**，且多数从 `normalized` 升到 `exact` 档
（MinerU 的文本比 pypdf 干净，不需要归一化就能字面命中）。

### ③ 但表格结构对了，**单元格顺序仍可能交错**

表 1 的年龄行还原后是：

```
Age, years  (45.34%) 66.05  (45.38%) 66.19  (49.73%) 57.11  …
```

那些百分比来自**上一行（Men）**，被交错进了年龄行。所以：

- 「各数据集平均年龄 57.11–66.19 岁」这个**事实**可读、可用于推断 `age_group: adult`；
- 但**拿不出可逐字引用的连续片段** → 按 `stage2_fill_card.md` §⑩，
  `inferred` 允许无 quote，**note 必须写清依据**。两张卡就是这么处理的。

宁可不给引文，也不拿一个交错拼出来的字符串冒充原文——与 §6f「造出原文没有的词是
verbatim 违规」同一条铁律。

## 这一步改掉了阶段三的两个判断

| 核查项 | MinerU 之前 | MinerU 之后 |
|---|---|---|
| Q6：`age_group: adult` 的引文支持不了推断 | **issue (low)** | **已修**：改用表 1 的各数据集平均年龄作依据，且如实不给 quote |
| Q7：`different_site` 无法核实（细节在 appendix） | **cannot_determine** | **部分解决**：表 1 列出十个外部数据集及其种族构成（SEED 新加坡 / UKB 英国 / MeLODY 马来西亚 / NICOLA 北爱 / AHES 澳大利亚），跨机构跨国已可从主文核实；纳入排除标准仍在 appendix，故整体仍标 `cannot_determine` |

## 成本与可行性

- **登录节点跑不了**：CPU 推理的 worker 被杀（`BrokenProcessPool`），
  vlm 后端还会尝试起本地服务并超时。必须 `sbatch` 到计算节点，且要 `-b pipeline -d cuda`。
- 15 页 / 9 分钟 / 1 张 A100。批量处理几百篇是可行的，但**不是随手能跑的一步**。
- pypdf 依旧有价值：**快、无依赖、正文完整**。合理的分工是
  **正文用 pypdf 兜底，表格/图注/阅读顺序必须 MinerU**——只是二者不能混用于核验。
