# 阶段四：ExternalStandard × 论文原文对齐

本项目原有正式产物是 `ExternalStandard[]`：外部世界对某类论文的要求、推荐、监管定位
或报告规范。它本身不回答论文是否做到。阶段四补的是这一步：把一条**适用且可引用**的
外部标准与论文的可定位原文证据对齐，产出可审计的 `Alignment`。

```
Claim Card + retrieved.jsonl + MinerU 解析文本
  → 筛选外部标准、生成核验问题、定位候选论文片段
  → LLM 输出 Alignment YAML
  → 程序逐字验证论文 quote
  → review.md（人读报告）
```

## 为什么 Card 不够

Card 是路由用的、受控的摘要；它只保存已抽出的字段及少量 provenance。判断“论文是否与
现行临床路径头对头比较”通常还需要读 Methods、Results、表格和附录。阶段四因而以 Card
定位要查什么，却以论文解析文本作为论文事实的唯一依据。

## 输入筛选

默认只接受：

- `source_role` 为 `normative`、`regulatory` 或 `reporting_tool`；
- 有 `recommendation_or_requirement`；
- `predates_paper_submission=true`。

发现层、注册库、流调数据不自动成为“要求”。投稿后标准默认排除；以
`--include-post-submission` 纳入时，强制输出 `post_submission_only`，只可评价今天的部署，
不能指责作者投稿时未遵守。

## 命令

```bash
# 组装给 LLM 的证据包；不会调用模型
python3 align.py build \
  --claim sample/dkd_retinal_ldh/03_cards/claim_1_screening.yaml \
  --retrieved sample/dkd_retinal_ldh/05_retrieval/claim_1_retrieved.jsonl \
  --out /tmp/dkd-align

# 将模型的 YAML 写到 /tmp/dkd-align/response.yaml 后，逐字验证 quote
python3 align.py verify --bundle /tmp/dkd-align

# 只把验证通过的判定渲染成人读审稿报告
python3 align.py render --bundle /tmp/dkd-align --out /tmp/review.md
```

`build` 只创建 bundle；没有在仓库内调用特定模型。可以把 `request.md` 放进 Codex、Claude 或
其他模型的新会话。这样模型选择与审稿数据契约分离。

## 判定契约

`supported / partial / contradicted` 必须带逐字论文 quote；`missing` 仅在相关材料已覆盖时可用；
附录未提供、解析不完整或表格无法读取时必须是 `cannot_determine`。每条非 `not_applicable`
结果还须给一份结构化临床审稿意见：临床审查维度、具体 concern、临床重要性、作者应采取的
动作，以及无法补实验时可接受的透明回应。程序用现有
`evidence.SourceDoc` 验证模型返回的 quote，并反查页码。

这一步生成的是审稿建议草案，不替代人的高风险临床判断。

### `missing` 的安全门

`align.py build` 为每条外部标准写入 `evidence_search_audit`：先查 Card 已有逐字出处，再用
外部条文的判别词搜索全部解析页，最后用审查维度的同义词做扩展搜索。审计记录每轮查询词、
命中页与材料覆盖状态。

只有主文覆盖明确、没有声明缺失的 appendix/table/其他材料，且三轮搜索均执行完时，
`missing_allowed=true`；否则 `verify` 会拒绝模型输出的 `missing`。这条规则刻意保守：
“没有找到”不是“论文没有报告”。

## 正式记录 schema

`schema.py` 定义两个可落盘对象：

- `PaperEvidence`：`evidence_id`、逐字 `quote`、实际解析 `source`、section、程序反查的
  page 与 match tier。它代表论文中一条事实，不是模型摘要。
- `Alignment`：稳定的 `alignment_id`、claim、稳定的外部标准 id、外部文档身份、verdict、
  时间状态、搜索审计、`PaperEvidence[]` 与结构化临床审稿意见。

外部标准 id 是 `source_id + canonical_url + section_page_table + publication_date` 的 SHA-256
截断指纹，不依赖本次检索的排序。`align.py verify` 会将模型输入中的简写 `standard_id`
转换为这两个正式对象，并仅把通过 schema 与逐字引文核验的对象写入
`verified_alignments.yaml`。
