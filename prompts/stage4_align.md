# 阶段四：外部标准与论文证据对齐

你的任务不是凭常识审稿，也不是重述 Claim Card。你要对每一条外部标准，依据给出的论文
证据包作出**可追溯的对齐判定**。

## 输入的含义

- `Claim Card` 只说明该论文主张什么、检索为何路由到这里；它不是论文全部事实的替代品。
- `External standard` 是一条已通过适用性与时间筛选的外部要求或标准。
- `Candidate paper passages` 是程序从论文解析产物中找出的候选位置；它们是线索，不是结论。
- `Input coverage` 说明哪些材料没有提供。材料未提供时，不能写成论文没有报告。
- `Evidence search audit` 记录程序已做的多轮搜索、命中的页和材料覆盖结论。它是决定能否使用
  `missing` 的机械依据。

## 判定枚举（只能选一个）

- `supported`：给出的论文原文直接支持该要求已被满足/报告。
- `partial`：论文只覆盖该要求的一部分，必须说清缺的是哪一部分。
- `missing`：在给定且覆盖完整的材料范围内，未找到应报告的内容。仅能说“未报告”，不能推断研究无效或有偏倚。
- `contradicted`：论文原文与要求明确相反。
- `not_applicable`：仔细阅读 Card 与要求后，确认这条记录其实不适用于本 claim；写明原因。
- `cannot_determine`：材料缺失、解析不足或原文含糊，无法判断。
- `post_submission_only`：外部标准晚于投稿，只能作为当前部署视角；不得写成作者投稿时的缺陷。

## 硬约束

1. 只能引用 `Candidate paper passages` 或 Card 内已有的逐字 quote；不得改写或拼接 quote，禁止省略号。
2. `supported`、`partial`、`contradicted` 必须至少给一条论文 quote。
3. `missing` **只能**在本条 `evidence_search_audit.missing_allowed=true` 时使用。该字段为 false
   表示材料不全、解析覆盖未知或搜索未完成；无论你是否没看到相关内容，都必须用
   `cannot_determine`，并说明限制。
4. 报告规范的 `missing` 仅表示**报告不完整**，不等于研究有偏倚或临床无效。
5. 不得从 discovery 文献、题录或模型常识虚构临床要求。
6. 不要只写一句“请补充 X”。每条有效的审稿意见要解释**临床上为什么重要、对哪类患者/
   场景有影响、论文现有证据的边界，以及作者可以怎样回应**；但不能虚构风险或用外部常识
   补论文事实。不要给总分。

## 输出

只输出 YAML，不要 Markdown 围栏或额外说明：

```yaml
alignments:
  - standard_id: "..."              # 必须逐字使用输入中的 ID
    verdict: supported|partial|missing|contradicted|not_applicable|cannot_determine|post_submission_only
    paper_evidence:
      - quote: "论文中的连续原文；没有则 []"
        section: "Methods / Results / Table ..."
    reason: "为何得到该 verdict；明确范围与不确定性"
    clinical_review:                 # not_applicable 可为空；其他 verdict 必填
      dimension: "clinical_question|population_validity|reference_standard|comparator_baseline|endpoint_utility|generalization|safety_harm_equity|workflow_deployment"
      concern: "具体缺口/矛盾；不要泛称证据不足"
      clinical_importance: "它会怎样影响临床解释、患者安全、获益-危害、可迁移性或部署边界"
      author_request: "作者应补什么分析、报告、比较、敏感性分析或结论限定"
      acceptable_response: "若无法补实验，可如何透明说明、限制结论或解释合理性"
```
