#!/usr/bin/env python3
"""将现有 sample run 编译为静态演示网页的数据包。

不调用模型、不重跑检索；网页只是读取已存在的中间产物。每次 sample 更新后运行本脚本即可。
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "data" / "runs.json"


def read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def url(path: Path) -> str:
    return "../" + path.relative_to(ROOT).as_posix()


def card_summary(path: Path) -> dict:
    doc = read_yaml(path).get("claim_card") or read_yaml(path)
    g = doc.get("gating") or {}
    c = g.get("condition") or {}
    primary = c.get("primary") or {}
    d = doc.get("descriptive") or {}
    return {
        "file": url(path), "name": path.stem,
        "claim_id": doc.get("claim_id") or path.stem,
        "condition": primary.get("label") or doc.get("disease_or_condition"),
        "population": ((g.get("population") or {}).get("age_group") or doc.get("target_population")),
        "task": g.get("clinical_task") or doc.get("clinical_task"),
        "stage": g.get("evidence_stage") or doc.get("evidence_stage") or "由程序映射",
        "care_setting": g.get("care_setting") or doc.get("care_setting"),
        "intended_use": d.get("intended_use") or doc.get("intended_use"),
        "claimed_benefit": d.get("claimed_benefit") or doc.get("claimed_benefit"),
        "provenance_fields": len(((doc.get("provenance") or {}).get("fields") or {})),
    }


def overview_summary(path: Path) -> dict:
    doc = read_yaml(path).get("paper_overview") or {}
    return {
        "file": url(path),
        "n_cohorts": len(doc.get("cohorts") or []),
        "claims": [{"id": x.get("claim_id"), "label": x.get("summary") or x.get("label") or x.get("description")}
                   for x in (doc.get("claim_candidates") or [])],
        "study_type": doc.get("article_type") or doc.get("study_type") or doc.get("paper_type"),
    }


def run(slug: str) -> dict:
    base = ROOT / "sample" / slug
    metadata = read_yaml(base / "00_source" / "metadata.yaml")
    meta = metadata.get("source_paper") or metadata
    pdfs = sorted((base / "00_source").glob("*.pdf"))
    overview = base / "02_overview" / "paper_overview.yaml"
    cards = [card_summary(p) for p in sorted((base / "03_cards").glob("*.yaml"))]
    checks = [url(p) for p in sorted((base / "04_check").glob("*.yaml"))]
    retrieval = [url(p) for p in sorted((base / "05_retrieval").glob("*.jsonl"))]
    review = base / "06_review_output" / "review_notes.md"
    parse_notes = base / "01_parse" / "parse_notes.md"
    return {
        "id": slug,
        "title": meta.get("title") or slug.replace("_", " "),
        "citation": meta.get("citation") or " · ".join(str(x) for x in
                    (meta.get("journal"), meta.get("year"), meta.get("doi") or meta.get("source_url")) if x) or "",
        "pdf": url(pdfs[0]) if pdfs else None,
        "metadata": url(base / "00_source" / "metadata.yaml"),
        "parse_notes": url(parse_notes) if parse_notes.exists() else None,
        "overview": overview_summary(overview) if overview.exists() else None,
        "cards": cards,
        "checks": checks,
        "retrieval": retrieval,
        "retrieval_summary": url(base / "05_retrieval" / "summary.md")
                             if (base / "05_retrieval" / "summary.md").exists() else None,
        "review": url(review) if review.exists() else None,
    }


def main() -> None:
    slugs = ["dkd_retinal_ldh", "nature_med_apol1_kidney", "iclr2024_pediatric_hypoglycemia"]
    payload = {"schema_version": 1, "runs": [run(s) for s in slugs]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(payload['runs'])} runs)")


if __name__ == "__main__":
    main()
