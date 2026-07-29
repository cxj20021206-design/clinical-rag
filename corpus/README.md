# corpus —— 抽卡规则与 gold 标注的语料

- `text/`（**已 gitignore**）：6 篇论文的 PDF 抽取文本，来自
  `/work/hdd/bgkq/xchen48/journals/pdfs/`。第三方版权内容，不入库；用
  `pypdf` 从 PDF 抽取即可复现（6 篇文本质量实测零连字丢失，**不需要 MinerU**）。
- `gold/`：人工拆解的 Claim Card 基准，**抽卡器的评测对照**。

论文选取标准：全部来自 `journals/peer_review/reviewed_papers.jsonl` 的 225 篇
**带真人 peer review** 的论文（Nature Medicine / Nature BME），便于后续直接接
Flaw-Recall 评测。6 篇刻意覆盖 C0/C2/C4 三个证据阶段与 5 种 clinical_task。

拆解规则见 [`docs/CARD_EXTRACTION_SPEC.md`](../docs/CARD_EXTRACTION_SPEC.md)。

## 复现文本抽取

```python
import pypdf
r = pypdf.PdfReader("<journals>/pdfs/nature_medicine/10.1038_s41591-026-04253-5.pdf")
open("text/lungimpact_cxr_rct.txt","w").write(
    "\n".join((p.extract_text() or "") for p in r.pages))
```

| slug | DOI | 阶段 | 任务 |
|---|---|---|---|
| lungimpact_cxr_rct | 10.1038/s41591-026-04253-5 | C4 | triage（阴性结果 RCT） |
| llm_chatbot_transitions_rct | 10.1038/s41591-025-04176-7 | C4 | documentation（无病种） |
| febrile_children_referral | 10.1038/s41591-026-04338-1 | C2 | triage（儿科 / 对照 WHO 危险征象） |
| ptau217_alzheimer_clock | 10.1038/s41591-026-04206-y | C2 | prognostication（用于临床试验） |
| cardiac_mri_dl_system | 10.1038/s41551-026-01637-3 | C2 | diagnosis（39 病种） |
| pathology_fm_benchmark | 10.1038/s41551-025-01516-3 | C0 | 无（纯基准，research_only） |
