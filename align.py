"""阶段四：将可引用的 ExternalStandard 与论文原文证据对齐。

本文件不直接调用 LLM。它把受控证据包写成 bundle，接收 LLM 的结构化 YAML，逐字核验
论文 quote，最后渲染 review Markdown。这样 "External standard → 审稿建议" 不会退化成
一次不可审计的自由问答。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re

import yaml

import evidence
from claim_card import load_card
from connectors.base import discriminative_terms
from schema import Alignment, PaperEvidence, stable_external_standard_id

ROOT = os.path.dirname(os.path.abspath(__file__))
PROMPT = os.path.join(ROOT, "prompts", "stage4_align.md")
ELIGIBLE_ROLES = {"normative", "regulatory", "reporting_tool"}
VERDICTS = {"supported", "partial", "missing", "contradicted", "not_applicable",
            "cannot_determine", "post_submission_only"}
REVIEW_DIMENSIONS = {"clinical_question", "population_validity", "reference_standard",
                     "comparator_baseline", "endpoint_utility", "generalization",
                     "safety_harm_equity", "workflow_deployment"}

# 当外部条文不会逐字出现在论文中时，仍须查的论文常见表达。它们是**搜索扩展**，不是
# 临床判断规则；判断仍由模型基于原文完成。每个模块单列，避免一个宽泛的全局词表让候选页爆炸。
_MODULE_EXPANSIONS = {
    "comparator_baseline": ("standard of care", "usual care", "routine care", "clinical pathway",
                            "head-to-head", "comparator", "baseline", "clinician"),
    "reference_standard": ("reference standard", "ground truth", "gold standard", "pathology",
                           "adjudication", "expert review", "follow-up"),
    "endpoint_utility": ("clinical outcome", "mortality", "hospital", "false positive", "false negative",
                         "sensitivity", "specificity", "benefit", "harm"),
    "population_validity": ("inclusion", "exclusion", "participant", "cohort", "eligib", "subgroup"),
    "generalization": ("external validation", "multicentre", "multi-centre", "generaliz", "transportab",
                       "subgroup", "ethnic"),
    "safety_harm_equity": ("adverse", "harm", "safety", "equity", "fairness", "bias", "disparit"),
    "workflow_deployment": ("workflow", "implementation", "deployment", "clinician", "decision support",
                            "prospective", "silent"),
    "clinical_question": ("objective", "aim", "target population", "outcome", "clinical context"),
}


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _yaml(path: str) -> dict:
    return yaml.safe_load(_read(path)) or {}


def _source(card: dict) -> evidence.SourceDoc:
    path = ((card.get("provenance") or {}).get("source") or "")
    if not path or not os.path.exists(path):
        raise ValueError("Claim Card 的 provenance.source 不存在；阶段四不能换一份论文文本"
                         "来核验。请提供抽卡时实际使用的解析产物。")
    if path.endswith(("_content_list.json", "_content_list_v2.json")):
        return evidence.SourceDoc.from_mineru(path)
    if path.lower().endswith(".pdf"):
        return evidence.SourceDoc.from_pdf(path)
    return evidence.SourceDoc.from_text(path)


def _records(path: str, include_post: bool) -> list[dict]:
    out = []
    for line_no, line in enumerate(_read(path).splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{line_no} 不是合法 JSON: {e}") from e
        if rec.get("source_role") not in ELIGIBLE_ROLES:
            continue
        if not rec.get("recommendation_or_requirement"):
            continue
        pre = rec.get("predates_paper_submission", "unknown")
        if pre == "true" or (include_post and pre == "false"):
            rec["_post_submission"] = pre == "false"
            out.append(rec)
    return out


def _question(rec: dict) -> str:
    role = rec.get("source_role")
    req = rec.get("recommendation_or_requirement", "")
    if role == "reporting_tool":
        return f"论文是否报告了以下信息，并且范围足以回应此报告条目？{req}"
    if role == "regulatory":
        return f"论文的预期用途、使用者与临床结论是否与以下监管定位一致？{req}"
    return f"论文的人群、场景、对照、终点或流程是否有原文证据回应以下外部标准？{req}"


def _terms(rec: dict, card: dict) -> set[str]:
    vals = [rec.get("recommendation_or_requirement"), rec.get("title"),
            card.get("intended_use"), card.get("model_output"), card.get("comparator"),
            card.get("claimed_benefit")]
    return discriminative_terms(*vals)


def _page_hits(src: evidence.SourceDoc, terms: set[str]) -> list[dict]:
    """在全部解析页中找词。返回页号而非直接下结论，供 audit 与候选排序共用。"""
    out = []
    for i, page in enumerate(src.pages):
        low = page.lower()
        hits = sorted(t for t in terms if re.search(rf"(?<![a-z0-9]){re.escape(t)}", low))
        if hits:
            out.append({"index": i, "page": i + 1 if src.paginated else None, "hits": hits})
    return out


def _coverage(coverage: list[dict]) -> tuple[str, str]:
    """对 missing 使用保守策略：未声明覆盖或任何显式缺失均视为不完整。"""
    if not coverage:
        return "unknown", "Claim Card 未声明 input_coverage，无法证明相关材料齐全"
    missing = [str(x.get("part") or "unknown") for x in coverage if x.get("included") is False]
    if missing:
        return "incomplete", f"未提供的材料: {missing}"
    if not any(x.get("included") is True for x in coverage):
        return "unknown", "input_coverage 未声明任何已纳入材料"
    return "complete", "所有声明的材料均已纳入"


def _search_audit(src: evidence.SourceDoc, rec: dict, card: dict, coverage: list[dict],
                  card_quotes: list[dict]) -> dict:
    """三轮证据定位审计。只有三轮完成且材料完整，才允许模型报告 missing。"""
    base = _terms(rec, card)
    modules = rec.get("modules") or []
    expanded = {t for m in modules for t in _MODULE_EXPANSIONS.get(m, ())}
    # 即使记录没有模块，也用其角色/问题的常见词做一个窄扩展，避免只搜条文原词。
    if not expanded:
        expanded = set(_MODULE_EXPANSIONS["clinical_question"])
    provenance_hits = []
    for item in card_quotes:
        located = src.locate(item["quote"])
        if located.found:
            provenance_hits.append({"field": item["field"], "page": located.page,
                                    "locator": item.get("locator")})
    rounds = [
        {"name": "claim_card_provenance", "terms": [], "hits": provenance_hits},
        {"name": "external_requirement_terms", "terms": sorted(base), "hits": _page_hits(src, base)},
        {"name": "review_dimension_expansion", "terms": sorted(expanded), "hits": _page_hits(src, expanded)},
    ]
    status, note = _coverage(coverage)
    return {"searched_page_count": len(src.pages), "rounds": rounds,
            "coverage_status": status, "coverage_note": note,
            "search_exhausted": True,
            "missing_allowed": status == "complete"}


def _candidates(src: evidence.SourceDoc, audit: dict, limit: int = 3) -> list[dict]:
    """将审计的全页搜索结果聚合为少数提示页；没有命中不会阻断 audit 本身。"""
    scored: dict[int, set[str]] = {}
    for rnd in audit["rounds"]:
        for hit in rnd["hits"]:
            if "index" not in hit:      # Card provenance 只记定位结果，不能假装是关键词命中
                continue
            scored.setdefault(hit["index"], set()).update(hit["hits"])
    ranked = sorted(scored.items(), key=lambda x: (-len(x[1]), x[0]))[:limit]
    out = []
    for idx, hits in ranked:
        snippet = re.sub(r"\s+", " ", src.pages[idx]).strip()
        out.append({"page": idx + 1 if src.paginated else None, "matched_terms": sorted(hits),
                    "text": snippet[:1800]})
    return out
def _card_provenance(card: dict) -> list[dict]:
    out = []
    for field, val in ((card.get("provenance") or {}).get("fields") or {}).items():
        if (val or {}).get("quote"):
            out.append({"field": field, "quote": val["quote"],
                        "locator": val.get("locator")})
    return out


def build(args) -> int:
    loaded = load_card(args.claim)
    card = loaded.legacy
    card_doc = _yaml(args.claim)
    raw = card_doc.get("claim_card") or card_doc
    coverage = ((raw.get("provenance") or {}).get("input_coverage") or [])
    card_quotes = _card_provenance(raw)
    recs = _records(args.retrieved, args.include_post_submission)
    if not recs:
        print("没有可对齐的外部标准：默认会排除 discovery/registry/epidemiology、无条文记录和投稿后标准。")
        return 0
    recs = recs[:args.max_standards]
    src = _source(raw)
    os.makedirs(args.out, exist_ok=True)
    standards = []
    for rec in recs:
        sid = stable_external_standard_id(rec)
        audit = _search_audit(src, rec, card, coverage, card_quotes)
        standards.append({
            "standard_id": sid,
            "post_submission": bool(rec["_post_submission"]),
            "source": {k: rec.get(k) for k in ("source_id", "issuing_body", "title", "canonical_url",
                                                  "document_type", "source_role", "tier",
                                                  "version_or_publication_date", "predates_paper_submission",
                                                  "modules")},
            "external_requirement": rec["recommendation_or_requirement"],
            "verification_question": _question(rec),
            "evidence_search_audit": audit,
            "candidate_paper_passages": _candidates(src, audit, args.candidates_per_standard),
        })
    packet = {"claim": {k: (card.get(k) if card.get(k) is not None else raw.get(k))
              for k in ("paper_id", "claim_id", "disease_or_condition", "target_population", "care_setting",
                        "clinical_task", "evidence_stage", "intended_use", "model_input", "model_output",
                        "comparator", "claimed_benefit", "demonstrated_effect", "benefit_gap",
                        "deployment_claim_level")},
              "input_coverage": coverage, "card_provenance": card_quotes,
              "standards": standards}
    with open(os.path.join(args.out, "evidence_packet.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(packet, f, allow_unicode=True, sort_keys=False, width=110)
    manifest = {"schema_version": 1, "claim": os.path.abspath(args.claim),
                "retrieved": os.path.abspath(args.retrieved),
                "paper_source": src.source, "n_standards": len(standards),
                "include_post_submission": args.include_post_submission,
                "response_contract": ("alignments: [standard_id, verdict, paper_evidence, reason, "
                                      "clinical_review{dimension,concern,clinical_importance,author_request,acceptable_response}]")}
    with open(os.path.join(args.out, "manifest.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(manifest, f, allow_unicode=True, sort_keys=False)
    request = _read(PROMPT) + "\n\n---\n\n# Evidence packet\n\n```yaml\n" + \
              yaml.safe_dump(packet, allow_unicode=True, sort_keys=False, width=110) + "```\n"
    with open(os.path.join(args.out, "request.md"), "w", encoding="utf-8") as f:
        f.write(request)
    print(f"已建立 {args.out}：{len(standards)} 条可对齐标准，论文共 {len(src.pages)} 页。")
    print("把 request.md 交给模型，将 YAML 保存为 response.yaml；再运行 align.py verify。")
    return 0


def verify(args) -> int:
    bundle = args.bundle
    packet = _yaml(os.path.join(bundle, "evidence_packet.yaml"))
    manifest = _yaml(os.path.join(bundle, "manifest.yaml"))
    response_path = args.response or os.path.join(bundle, "response.yaml")
    response = _yaml(response_path)
    items = response.get("alignments")
    if not isinstance(items, list):
        print("ERROR response.yaml 必须有 alignments 列表")
        return 1
    standards = {x["standard_id"]: x for x in packet.get("standards") or []}
    raw_card = _yaml(manifest["claim"]).get("claim_card") or _yaml(manifest["claim"])
    src = _source(raw_card)
    errors, verified = [], []
    seen = set()
    for i, item in enumerate(items, 1):
        where = f"alignments[{i}]"
        sid, verdict = item.get("standard_id"), item.get("verdict")
        if sid not in standards:
            errors.append(f"{where}: 未知 standard_id {sid!r}")
            continue
        if sid in seen:
            errors.append(f"{where}: standard_id 重复 {sid}")
        seen.add(sid)
        if verdict not in VERDICTS:
            errors.append(f"{where}: verdict 必须是 {sorted(VERDICTS)}")
        if standards[sid]["post_submission"] and verdict != "post_submission_only":
            errors.append(f"{where}: 投稿后标准必须判为 post_submission_only")
        audit = standards[sid].get("evidence_search_audit") or {}
        if verdict == "missing" and not (audit.get("search_exhausted")
                                           and audit.get("missing_allowed")):
            errors.append(f"{where}: 材料覆盖不完整或搜索未穷尽，禁止判 missing；应使用 cannot_determine")
        raw_evidence = item.get("paper_evidence") or []
        if verdict in {"supported", "partial", "contradicted"} and not raw_evidence:
            errors.append(f"{where}: {verdict} 必须含至少一条论文 quote")
        paper_evidence = []
        for j, ev in enumerate(raw_evidence, 1):
            q = (ev or {}).get("quote")
            loc = src.locate(q or "")
            if not loc.found:
                errors.append(f"{where}.paper_evidence[{j}]: quote 无法逐字定位 ({loc.reason})")
            else:
                fingerprint = f"{src.source}\x1f{q}\x1f{loc.page or ''}".encode("utf-8")
                paper_evidence.append(PaperEvidence(
                    evidence_id="pev_" + hashlib.sha256(fingerprint).hexdigest()[:20],
                    quote=q, source=src.source, section=(ev or {}).get("section"), page=loc.page,
                    match_tier=loc.tier,
                ))
        if not str(item.get("reason") or "").strip():
            errors.append(f"{where}: 缺 reason")
        review = item.get("clinical_review")
        if verdict != "not_applicable":
            if not isinstance(review, dict):
                errors.append(f"{where}: 非 not_applicable 的结果必须含 clinical_review 对象")
            else:
                if review.get("dimension") not in REVIEW_DIMENSIONS:
                    errors.append(f"{where}: clinical_review.dimension 必须是 {sorted(REVIEW_DIMENSIONS)}")
                for key in ("concern", "clinical_importance", "author_request", "acceptable_response"):
                    if not str(review.get(key) or "").strip():
                        errors.append(f"{where}: clinical_review 缺 {key}")
        std = standards[sid]
        alignment = Alignment(
            alignment_id="aln_" + hashlib.sha256(
                f"{packet['claim'].get('claim_id') or ''}\x1f{sid}".encode("utf-8")).hexdigest()[:20],
            claim_id=str(packet["claim"].get("claim_id") or ""),
            external_standard_id=sid,
            external_standard_title=str(std["source"].get("title") or ""),
            external_standard_url=str(std["source"].get("canonical_url") or ""),
            verdict=verdict, reason=str(item.get("reason") or ""),
            temporal_status=("post_submission_only" if std["post_submission"] else "pre_submission"),
            evidence_search_audit=audit, paper_evidence=paper_evidence,
            clinical_review=review or {},
        )
        errors.extend(f"{where}: {e}" for e in alignment.validate())
        verified.append(alignment.to_dict())
    missing = set(standards) - seen
    if missing:
        errors.append(f"未对齐的标准: {sorted(missing)}")
    for e in errors:
        print("ERROR", e)
    if errors:
        return 1
    out = os.path.join(bundle, "verified_alignments.yaml")
    with open(out, "w", encoding="utf-8") as f:
        yaml.safe_dump({"alignments": verified}, f, allow_unicode=True, sort_keys=False, width=110)
    print(f"✓ {len(verified)} 条 Alignment 通过结构与逐字引文核验 → {out}")
    return 0


def render(args) -> int:
    packet = _yaml(os.path.join(args.bundle, "evidence_packet.yaml"))
    response = _yaml(os.path.join(args.bundle, "verified_alignments.yaml"))
    by_id = {x["standard_id"]: x for x in packet.get("standards") or []}
    lines = ["# 外部标准 × 论文证据对齐", "", "本报告仅汇总已通过逐字引文核验的 Alignment；"
             "它是审稿建议草案，仍须人工复核。", ""]
    for item in response.get("alignments") or []:
        std = by_id[item["external_standard_id"]]
        lines += [f"## {item['verdict']} — {std['source']['title']}", "",
                  f"**外部要求**：{std['external_requirement']}", "",
                  f"**核验问题**：{std['verification_question']}", ""]
        if item.get("paper_evidence"):
            lines += ["**论文证据**："]
            for ev in item["paper_evidence"]:
                page = ((ev.get("verified") or {}).get("page"))
                suffix = f"（p.{page}）" if page else ""
                lines.append(f"> {ev.get('quote')} {suffix}")
            lines.append("")
        lines += [f"**判断**：{item['reason']}", ""]
        review = item.get("clinical_review") or {}
        if review:
            lines += [f"**审查维度**：`{review.get('dimension', '')}`", "",
                      f"**具体关注点**：{review.get('concern', '')}", "",
                      f"**临床重要性**：{review.get('clinical_importance', '')}", "",
                      f"**给作者的请求**：{review.get('author_request', '')}", "",
                      f"**可接受的回应**：{review.get('acceptable_response', '')}", ""]
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"已写入 {args.out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="阶段四：外部标准与论文证据对齐")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build", help="从 Card、retrieved.jsonl 与解析论文建立 LLM evidence bundle")
    p.add_argument("--claim", required=True)
    p.add_argument("--retrieved", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--max-standards", type=int, default=12)
    p.add_argument("--candidates-per-standard", type=int, default=3)
    p.add_argument("--include-post-submission", action="store_true")
    p.set_defaults(func=build)
    p = sub.add_parser("verify", help="验证 LLM 输出的 Alignment YAML 与论文逐字 quote")
    p.add_argument("--bundle", required=True)
    p.add_argument("--response", help="默认 <bundle>/response.yaml")
    p.set_defaults(func=verify)
    p = sub.add_parser("render", help="把已验证 Alignment 渲染为审稿建议草案")
    p.add_argument("--bundle", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=render)
    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
