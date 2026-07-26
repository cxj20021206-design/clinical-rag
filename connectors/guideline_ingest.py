"""按 manifest 摄入学会/国家 CPG → curated/guidelines/cpg_<slug>.yaml。

一次性运行，产物入库（与 uspstf_ingest.py 同一模式：内容固定，不随论文变，
所以不做在线检索）。区别在于 USPSTF 是一个机构一个站点，这里是**多机构多文档**，
故用 manifest.yaml 记录策展决定，脚本只做取全文 / 抽条目 / 解析分级。

用法：
    python3 connectors/guideline_ingest.py                 # 摄入 manifest.ingested 全部
    python3 connectors/guideline_ingest.py --only PMC11617831
    python3 connectors/guideline_ingest.py --check         # 只检查不写盘

许可：逐字保存推荐原文，不改写不摘要；每条记录带 canonical_url 与许可标注。
license 字段为空的文档在 manifest 里就被拦下（见 guideline_fetch.license_ok）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yaml                                                    # noqa: E402
from guideline_fetch import (EPMC, UA, extract, fetch_xml,      # noqa: E402
                             license_ok)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDE_DIR = os.path.join(ROOT, "curated", "guidelines")
MANIFEST = os.path.join(GUIDE_DIR, "manifest.yaml")


def epmc_record(pmcid: str) -> dict:
    """拿 Europe PMC 的书目记录——**许可以这里的 license 字段为准**，
    因为全文 XML 里的 <license> 有时只有一段散文、拿不到机读值。"""
    # PMCID 字段**不能加引号**：PMCID:"PMC11617831" 返回 0 条，PMCID:PMC11617831 返回 1 条。
    u = EPMC + "/search?" + urllib.parse.urlencode({
        "query": f"PMCID:{pmcid}", "format": "json", "resultType": "core", "pageSize": 1})
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
        d = json.load(r)
    res = d.get("resultList", {}).get("result", [])
    return res[0] if res else {}


def ingest_one(entry: dict, write: bool = True) -> dict:
    pmcid = entry["pmcid"]
    rec = epmc_record(pmcid)
    if not rec:                    # 取不到书目记录 ≠ 许可不合格，错误信息不能混
        raise RuntimeError(f"Europe PMC 查不到 {pmcid} 的书目记录，无法核对许可")
    ok, why = license_ok(rec.get("license"))
    if not ok:
        raise RuntimeError(f"许可未通过：{why}")

    res = extract(fetch_xml(pmcid))
    if not res["ok"]:
        raise RuntimeError(res["reason"])
    meta = res["meta"]
    pub = (meta.get("publication_date") or (rec.get("firstPublicationDate") or "")[:10]) or None

    doc = {
        "source_id": "society_guidelines",       # 对应 clinical_sources.yaml 的源 id
        "slug": entry["slug"],
        "name": rec.get("title") or meta.get("title"),
        "short_name": entry.get("short_name"),
        "issuing_body": entry["issuing_body"],   # 策展人确认，不取期刊名
        "document_type": "guideline",
        "source_role": "normative",
        "tier": entry.get("tier", 1),
        "region": entry.get("region"),
        "publication_date": pub,
        "scope": entry["scope"],                 # 病种/场景/人群门控依据
        "curation_note": entry.get("notes"),
        "provenance": {
            "canonical_url": f"https://europepmc.org/article/PMC/{pmcid}",
            "pmcid": pmcid, "pmid": rec.get("pmid"), "doi": rec.get("doi"),
            "journal": rec.get("journalTitle") or meta.get("journal"),
            "license": rec.get("license"),
            "license_note": (f"{why}。逐字摘录、未改动、注明出处；"
                             f"不做演绎、不收费再分发。"),
            "ingested_from": "Europe PMC OA fullTextXML",
            "extraction_strategy": res["strategy"],
            "strategy_yields": res["yields"],
            "ingested_date": time.strftime("%Y-%m-%d"),
            "verbatim": True,
            # 抽取是结构化的、但不保证抽全（正文里散落的推荐不会被抽到）。
            # 与报告清单的 completeness 同义：不得让系统以为"这份指南已全覆盖"。
            "completeness": "structured_recommendations_only",
            "completeness_note": (
                f"仅摄入以结构（{res['strategy']}）呈现的推荐条目 {len(res['recommendations'])} 条。"
                f"正文讨论中未进入推荐结构的表述不在库内，系统不得声称已覆盖全文。"),
            "n_recommendations": len(res["recommendations"]),
        },
        "recommendations": [
            {"statement": r["statement"],                      # 逐字
             "context": r["context"],                          # 这条推荐回答的临床问题
             "section": r["section"],
             "recommendation_strength": r["recommendation_strength"],
             "grade_letter": r["grade_letter"],
             "evidence_certainty": r["evidence_certainty"],
             # 共识度：部分指南（ESPNIC POCUS）只给"专家一致程度"而不给 GRADE 强度。
             # 二者不是一回事，不能拿一致程度冒充推荐强度，所以单列一个字段。
             "agreement": r.get("agreement")}
            for r in res["recommendations"]],
    }
    if write:
        os.makedirs(GUIDE_DIR, exist_ok=True)
        out = os.path.join(GUIDE_DIR, f"cpg_{entry['slug']}.yaml")
        tmp = out + ".tmp"
        with open(tmp, "w") as f:
            yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False, width=100)
        os.replace(tmp, out)
        doc["_path"] = out
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="只摄入这些 PMCID")
    ap.add_argument("--check", action="store_true", help="只检查，不写盘")
    a = ap.parse_args()

    man = yaml.safe_load(open(MANIFEST))
    entries = man.get("ingested") or []
    if a.only:
        entries = [e for e in entries if e["pmcid"] in set(a.only)]

    ok_n = 0
    for e in entries:
        try:
            d = ingest_one(e, write=not a.check)
            ok_n += 1
            p = d["provenance"]
            print(f"✓ {e['pmcid']}  {(d['name'] or '')[:56]:56s} "
                  f"{d['publication_date']}  {p['license']:11s} "
                  f"{p['extraction_strategy']}×{p['n_recommendations']}")
        except Exception as ex:
            print(f"✗ {e['pmcid']}  {type(ex).__name__}: {str(ex)[:150]}")
        time.sleep(0.5)

    # deferred 不是"待办事项列表"，是**覆盖边界的声明**：这些指南没进库，
    # 系统就不能拿它们说事。每次摄入都打印一遍，避免被遗忘成"以为已覆盖"。
    print(f"\n摄入 {ok_n}/{len(entries)} 份。未摄入（manifest.deferred）：")
    for d in man.get("deferred") or []:
        print(f"  🕳 [{d.get('blocker')}] {d['pmcid']} {d['title'][:60]}")
        print(f"       {' '.join(str(d['reason']).split())[:150]}")


if __name__ == "__main__":
    main()
