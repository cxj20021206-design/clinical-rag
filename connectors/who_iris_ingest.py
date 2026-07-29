"""WHO IRIS 指南的策展摄入 —— 按 manifest 写出 curated/guidelines/cpg_who_*.yaml。

**产物刻意与学会/国家 CPG 同名同形**（`cpg_*.yaml`）：curated_guidelines.py 的
glob 直接吃到，病种/人群/场景门控与相关度排序**零改动继承**。WHO 指南在系统里
就是一份 normative CPG，只是取得通道不同（IRIS TEXT bundle vs Europe PMC XML），
没有理由为它另开一套门控逻辑 —— 两套并行的 normative 判定迟早会分叉。

与 §6d（EPMC 腿）的两处差别：
  1. **issuing_body 不需要人工确认**。EPMC 腿要人工填，因为期刊名 ≠ 发布机构
     （韩国脓毒症 CPG 发在 *Acute and Critical Care* 上）。IRIS 是 WHO 自己的
     机构库，出版者恒为 WHO，从 dc.publisher 取、缺省填 WHO 都是可靠的。
  2. **许可字段更干净**。IRIS 的 dc.rights 直接给 "CC BY-NC-SA 3.0 IGO"，
     不必像 EPMC 那样区分 openAccess 与 license 两个字段。空值一样按未授权处理。

用法：
    # 发现：按病种看 IRIS 有什么（不写盘），供策展决定
    python3 connectors/who_iris_ingest.py --discover "heart failure"
    # 摄入 manifest 里的全部条目
    python3 connectors/who_iris_ingest.py
    # 只摄入指定 uuid（须已在 manifest 中）
    python3 connectors/who_iris_ingest.py --only <uuid>
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import yaml                                                        # noqa: E402
from guideline_fetch import license_ok                             # noqa: E402
from who_iris_fetch import (IRIS, _get, _meta, bundles, extract,   # noqa: E402
                            fetch_text, pick_text, search_candidates,
                            title_gate, yield_audit)

GUIDE_DIR = os.path.join(ROOT, "curated", "guidelines")
MANIFEST = os.path.join(GUIDE_DIR, "manifest_who.yaml")
CACHE = os.path.join(ROOT, "store", ".cache")


def discover(disease: str, limit: int = 25) -> list[dict]:
    """按病种列 IRIS 候选，并预跑标题门 —— 供人工策展挑选，不写盘。"""
    cands = search_candidates(disease, limit=limit)
    phrases = [disease]
    out = []
    for c in cands:
        ok, why = title_gate(c["title"], phrases)
        lic_ok, lic_why = license_ok(c.get("rights"))
        out.append({**c, "title_gate": ok, "title_note": why,
                    "license_gate": lic_ok, "license_note": lic_why})
    return out


def ingest_one(entry: dict, write: bool = True) -> dict:
    uuid = entry["uuid"]
    it = _get(f"{IRIS}/core/items/{uuid}")
    title = it.get("name")
    rights = _meta(it, "dc.rights")
    ok, why = license_ok(rights)
    if not ok:
        raise RuntimeError(f"许可未通过：{why}（dc.rights={rights!r}）")

    bs, note = pick_text(bundles(uuid))
    if not bs:
        raise RuntimeError(f"取不到可用全文：{note}")
    res = extract(fetch_text(bs["url"], CACHE, key=uuid))
    if not res["ok"]:
        raise RuntimeError(res["reason"])
    audit = yield_audit(res)

    n_norec = sum(1 for r in res["recommendations"] if r.get("is_no_recommendation"))
    doc = {
        "source_id": "who_guidelines",           # clinical_sources.yaml 里的 tier1 源
        "slug": entry["slug"],
        "name": title,
        "short_name": entry.get("short_name"),
        "issuing_body": entry.get("issuing_body") or _meta(it, "dc.publisher")
                        or "World Health Organization",
        "document_type": "guideline",
        "source_role": "normative",
        "tier": entry.get("tier", 1),
        "region": entry.get("region"),           # WHO 指南多为全球，留空即"不限地区"
        "publication_date": _meta(it, "dc.date.issued"),
        "scope": entry["scope"],
        "curation_note": entry.get("notes"),
        "provenance": {
            "canonical_url": _meta(it, "dc.identifier.uri")
                             or f"https://iris.who.int/handle/{uuid}",
            "iris_uuid": uuid,
            "isbn": _meta(it, "dc.identifier.isbn"),
            "license": rights,
            "license_note": (f"{why}。逐字摘录、未改动、注明出处；"
                             f"不做演绎、不收费再分发。"),
            "ingested_from": f"WHO IRIS TEXT bundle（DSpace 预抽文本，{bs['name']}）",
            "extraction_strategy": res["strategy"],
            "strategy_yields": res["yields"],
            # PDF 文本的排版伪影处理必须如实记录 —— 做了归一化就不能再声称
            # "字节级未改动"，但内容未改：只接合跨行连字符、剔除页码与目录行。
            "text_normalization": "接合跨行连字符（保留连字符本身）、剔除纯页码行与目录行、"
                                  "空白规范化；不改写任何词句",
            "ingested_date": time.strftime("%Y-%m-%d"),
            "verbatim": True,
            "completeness": "structured_recommendations_only",
            "completeness_note": (
                f"仅摄入以编号锚点（Recommendation N / Good practice statement / "
                f"No recommendation）呈现的条目 {len(res['recommendations'])} 条。"
                f"正文讨论、证据综述与 GRADE 证据表中的表述不在库内，"
                f"系统不得声称已按全文核查。"),
            "n_recommendations": len(res["recommendations"]),
            "n_no_recommendation": n_norec,
            "yield_audit": audit,
        },
        "recommendations": [
            {"statement": r["statement"],
             "context": r["context"],
             "section": r["section"],
             "recommendation_strength": r["recommendation_strength"],
             "grade_letter": r["grade_letter"],
             "evidence_certainty": r["evidence_certainty"],
             "agreement": r.get("agreement"),
             "is_no_recommendation": r.get("is_no_recommendation", False)}
            for r in res["recommendations"]],
    }
    if write:
        os.makedirs(GUIDE_DIR, exist_ok=True)
        out = os.path.join(GUIDE_DIR, f"cpg_who_{entry['slug']}.yaml")
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False, width=100)
        os.replace(tmp, out)
        doc["_path"] = out
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", help="按病种列 IRIS 候选（策展辅助，不写盘）")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--only", nargs="*", help="只摄入这些 uuid")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.discover:
        rows = discover(a.discover, a.limit)
        passed = [r for r in rows if r["title_gate"] and r["license_gate"]]
        print(f"IRIS 候选 {len(rows)} 条，双门通过 {len(passed)} 条 —— 病种「{a.discover}」\n")
        for r in rows:
            mark = "✅" if (r["title_gate"] and r["license_gate"]) else "  "
            print(f"{mark} {r['date_issued']}  {r['title'][:78]}")
            print(f"     uuid={r['uuid']}  rights={r['rights']}")
            if not r["title_gate"]:
                print(f"     ⛔ {r['title_note']}")
            elif not r["license_gate"]:
                print(f"     ⛔ {r['license_note']}")
        return

    man = yaml.safe_load(open(MANIFEST, encoding="utf-8")) if os.path.exists(MANIFEST) else {}
    entries = man.get("ingested") or []
    if a.only:
        entries = [e for e in entries if e["uuid"] in a.only]
    ok = fail = 0
    for e in entries:
        try:
            d = ingest_one(e, write=not a.dry_run)
            pr = d["provenance"]
            print(f"✅ {e['slug']}: {pr['n_recommendations']} 条"
                  f"（其中 No recommendation {pr['n_no_recommendation']} 条）"
                  f" coverage={pr['yield_audit']['coverage_ratio']}"
                  f" {'⚠️ ' + ';'.join(pr['yield_audit']['flags']) if pr['yield_audit']['flags'] else ''}")
            ok += 1
        except Exception as ex:                                    # noqa: BLE001
            print(f"❌ {e.get('slug', e['uuid'])}: {ex}")
            fail += 1
    print(f"\n摄入完成：成功 {ok}，失败 {fail}")


if __name__ == "__main__":
    main()
