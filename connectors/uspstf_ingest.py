"""USPSTF 策展摄入：抓全部 108 条推荐 → curated/guidelines/uspstf.yaml。

一次性运行，产物入库。之所以不做成在线连接器：USPSTF 内容固定、与具体论文无关，
且网页抓取慢而不稳；与报告规范清单同属"第三条腿/策展摄入"（见 docs/RELATED_WORK.md §7）。

用法：
    python3 connectors/uspstf_ingest.py            # 全量摄入
    python3 connectors/uspstf_ingest.py --limit 5  # 冒烟测试

许可：逐字保存推荐原文，不改写不摘要（USPSTF 版权声明要求 without any changes），
并强制记录每条的 canonical_url 作为出处。
"""
from __future__ import annotations
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yaml                                    # noqa: E402
from uspstf_fetch import (BASE, GRADE_CERTAINTY, fetch_index,  # noqa: E402
                          parse_recommendation)

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "curated", "guidelines", "uspstf.yaml")

LICENSE_NOTE = (
    "AHRQ permits members of the public to reproduce, redistribute, publicly display, "
    "and incorporate USPSTF work into other materials provided that it is reproduced "
    "without any changes to the work or portions thereof. 禁止收费再分发与营利用途；"
    "引用须注明 USPSTF 网页为出处。版权声明未提及 AI/文本挖掘限制"
    "（2026-07-25 核对；对比 NICE 明令 AI 用途需另行许可 → NICE 不可行）。"
)


def ingest(limit: int | None = None, delay: float = 1.0) -> dict:
    index = fetch_index()
    print(f"索引：{len(index)} 条主题")
    if limit:
        index = index[:limit]

    topics, retired, failed = [], [], []
    for i, row in enumerate(index, 1):
        try:
            d = parse_recommendation(row["url"])
            recs = [r for r in d["recommendations"] if r["recommendation"]]
            if not recs:
                # 无推荐条目有两种原因，必须区分：
                #  (a) 主题已停用/转交 → 该主题确实没有现行推荐，如实记录，不是缺陷
                #  (b) 状态为 active 却解析不出 → 真解析失败，必须显式报错
                entry = {"slug": row["slug"], "title": row["title"],
                         "url": row["url"], "status": d.get("status")}
                if d.get("status") in ("inactive", "referred", "in_progress", "draft"):
                    retired.append(entry)
                    print(f"  [{i:3d}/{len(index)}] ○ {row['slug'][:52]:52s} "
                          f"[{d['status']}] 无现行推荐")
                else:
                    failed.append((row["slug"], "status=active 但 Recommendation Summary 表未解析出条目"))
                    print(f"  [{i:3d}/{len(index)}] ⚠️ {row['slug']}  无推荐条目（状态 active，需排查）")
                continue
            topics.append({
                "slug": row["slug"],
                "title": row["title"],
                "url": row["url"],
                "publication_date": d["publication_date"],
                "type": row["type"],                 # Screening / Preventive Medication / Counseling
                "age_group": row["age_group"],
                "category": row["category"],
                "index_year": row["year"],
                "recommendations": [
                    {"population": r["population"],
                     "text": r["recommendation"],     # 逐字
                     "grade": r["grade"],
                     "certainty": GRADE_CERTAINTY.get(r["grade"], (None, None))[0],
                     "grade_meaning": GRADE_CERTAINTY.get(r["grade"], (None, None))[1]}
                    for r in recs],
            })
            print(f"  [{i:3d}/{len(index)}] ✓ {row['slug'][:52]:52s} "
                  f"{d['publication_date']} {[r['grade'] for r in recs]}")
        except Exception as e:
            failed.append((row["slug"], str(e)[:100]))
            print(f"  [{i:3d}/{len(index)}] ✗ {row['slug']}  {str(e)[:70]}")
        time.sleep(delay)

    n_recs = sum(len(t["recommendations"]) for t in topics)
    doc = {
        "source_id": "uspstf",
        "name": "U.S. Preventive Services Task Force Recommendations",
        "issuing_body": "U.S. Preventive Services Task Force (USPSTF) / AHRQ",
        "document_type": "guideline",
        "source_role": "normative",
        "tier": 1,
        "region": "US",
        "provenance": {
            "canonical_url": BASE + "/uspstf/topic_search_results?topic_status=P",
            "copyright_notice_url": BASE + "/uspstf/recommendation-topics/copyright-notice",
            "license": "US government work (AHRQ) — 无改动前提下允许复制与再分发",
            "license_note": LICENSE_NOTE,
            "ingested_from": "USPSTF 官网 Recommendation Summary 表 (Population|Recommendation|Grade)",
            "ingested_date": "2026-07-25",
            "verbatim": True,
            "completeness": "full" if not failed else "partial",
            "completeness_note": (
                f"索引 {len(index)} 条主题：{len(topics)} 条有现行推荐（共 {n_recs} 条推荐条目），"
                f"{len(retired)} 条已停用/转交（USPSTF 页面标注 Inactive/Referred，"
                f"本身无现行推荐，非解析缺陷）。"
                + (f"另有 {len(failed)} 条解析失败需排查：{failed}" if failed
                   else "无解析失败。")),
            "n_topics": len(topics),
            "n_recommendations": n_recs,
            "n_retired": len(retired),
        },
        # 已停用/转交的主题：保留记录。若论文所在领域的推荐已被 USPSTF 停用或转交，
        # 这本身是有价值的审稿背景，不应因"没有推荐条目"而从库中消失。
        "retired_topics": retired,
        "scope_note": (
            "USPSTF 职权范围仅限预防服务：筛查、预防用药、行为咨询，面向社区与初级保健。"
            "不覆盖急重症、住院管理、治疗方案与诊断细节 —— 该类问题须由学会指南/WHO 等其他 "
            "normative 源承担，不得用本源外推（clinical_sources.yaml: uspstf.notes"
            "「筛查类第一优先；不能外推到治疗问题」）。"),
        "grade_definitions": {g: {"certainty": c, "meaning": m}
                              for g, (c, m) in GRADE_CERTAINTY.items()},
        "topics": topics,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False, width=100)
    os.replace(tmp, OUT)
    print(f"\n写入 {OUT}")
    print(f"  主题 {len(topics)} / 推荐条目 {n_recs} / 失败 {len(failed)}")
    if failed:
        print("  失败清单（如实记录，不静默）：")
        for s, e in failed:
            print(f"    - {s}: {e}")
    return doc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--delay", type=float, default=1.0)
    ingest(**vars(ap.parse_args()))
