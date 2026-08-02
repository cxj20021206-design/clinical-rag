#!/usr/bin/env python3
"""卡的机械统计 —— 用来对照 prompt 改动前后的产物，不做任何语义判断。

为什么要有它：2026-08-01 重写 prompt 时定下的可观测预期（provenance 条目数、
取证位置分布、status 是否缺省、有没有 cohort_id、被消费字段有没有写中文）
全都是**可数的**。靠人翻 YAML 数会漏，而且没法逐次复现。

刻意只统计、不判定：某一项是好是坏由 run 目录的 README（跑之前写下的预期）来对照。

用法：
    python3 sample/_card_stats.py sample/*/03_cards/*.yaml corpus/gold/*.yaml
"""
from __future__ import annotations

import glob
import os
import re
import sys

import yaml

# 被程序消费、因而必须写英文的字段（stage2 规则⑧那张表）。
# 键是在**扁平视图**里的名字，取自 claim_card.ClaimCard.legacy。
CONSUMED_FIELDS = [
    "disease_or_condition", "excluded_conditions",
    "intended_use", "model_input", "model_output",
    "deployment_claim_level", "claimed_benefit",
    # ⚠️ 进检索人群约束的是**描述层**那个（legacy 视图里叫 population_description）。
    # legacy 的 `target_population` 是 gating 层由 age_group+special 拼出来的，
    # 天生是英文，查它等于没查 —— 初版就写错成 target_population，dkd 两张卡
    # 明明写着中文人群却报"全部非 CJK ✓"。
    "population_description",
]
CJK = re.compile(r"[　-鿿＀-￯]")

# 取证位置分三档，不是两档。硬约束 2 真正要求的是**从 Methods/Results/表格取证**；
# introduction/discussion 虽然也在正文里，但同样是作者的概括性表述，
# 把它们和 Methods 混成一个"正文"桶会让改动看起来比实际有效。
ABSTRACTISH = ("abstract", "title", "research in context", "summary", "highlight")
NARRATIVE = ("introduction", "discussion", "conclusion", "background", "related work")
EVIDENTIAL = ("method", "result", "table", "figure", "fig.", "supplement", "appendix",
              "section", "algorithm", "experiment", "protocol", "cohort")


def _flat(card: dict) -> dict:
    """尽量拿到扁平视图；拿不到（卡结构损坏）就退回原字典，不让统计工具挡住流程。"""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    try:
        from claim_card import ClaimCard
        return ClaimCard(card, None, legacy_input="gating" not in card).legacy
    except Exception:
        return card


def _bucket(locator: str) -> str:
    lo = (locator or "").lower()
    if not lo:
        return "(空)"
    if any(k in lo for k in ABSTRACTISH):
        return "abstract/title"
    # 先判 EVIDENTIAL：locator 常写成 "methods (table 1)" 这类组合，
    # 先判 NARRATIVE 会把 "discussion, table 3" 错分到叙述档。
    if any(k in lo for k in EVIDENTIAL):
        return "methods/results/表图"
    if any(k in lo for k in NARRATIVE):
        return "intro/discussion"
    return "其他"


def stat_one(path: str) -> dict:
    doc = yaml.safe_load(open(path, encoding="utf-8"))
    card = doc.get("claim_card", doc.get("clinical_claim", doc))
    prov = (card.get("provenance") or {})
    fields = (prov.get("fields") or {})
    # 老式卡把出处直接挂在 provenance 下（非 fields 子键）
    if not fields:
        fields = {k: v for k, v in prov.items() if isinstance(v, dict) and
                  ("quote" in v or "status" in v)}

    buckets: dict[str, int] = {}
    no_status = []
    with_cohort = 0
    for key, val in fields.items():
        val = val or {}
        buckets[_bucket(val.get("locator"))] = buckets.get(_bucket(val.get("locator")), 0) + 1
        if not val.get("status"):
            no_status.append(key)
        if val.get("cohort_id"):
            with_cohort += 1

    flat = _flat(card)
    cjk_hits = {f: str(flat.get(f))[:40] for f in CONSUMED_FIELDS
                if CJK.search(str(flat.get(f) or ""))}

    return {
        "path": path,
        "n_prov": len(fields),
        "buckets": buckets,
        "no_status": no_status,
        "with_cohort": with_cohort,
        "has_cohorts_block": bool(card.get("cohorts")),
        "cjk": cjk_hits,
    }


def main() -> int:
    paths = [p for pat in sys.argv[1:] for p in sorted(glob.glob(pat))]
    if not paths:
        print(__doc__)
        return 2
    tot_prov = tot_nostatus = tot_cohort = 0
    body = abst = 0
    for p in paths:
        try:
            s = stat_one(p)
        except Exception as e:
            print(f"\n=== {p} ===\n  ⚠️ 读取失败: {str(e)[:120]}")
            continue
        tot_prov += s["n_prov"]
        tot_nostatus += len(s["no_status"])
        tot_cohort += s["with_cohort"]
        body += s["buckets"].get("methods/results/表图", 0)
        abst += s["buckets"].get("abstract/title", 0)
        print(f"\n=== {os.path.relpath(p)} ===")
        print(f"  provenance 条目      {s['n_prov']}")
        print(f"  取证位置             {s['buckets']}")
        print(f"  缺 status            {len(s['no_status'])}"
              + (f"  {s['no_status'][:6]}" if s["no_status"] else ""))
        print(f"  带 cohort_id         {s['with_cohort']}"
              f"   (卡内 cohorts 块: {'有' if s['has_cohorts_block'] else '无'})")
        if s["cjk"]:
            print(f"  ⚠️ 被消费字段写了中文  {list(s['cjk'])}")
            for k, v in s["cjk"].items():
                print(f"       {k}: {v}")
        else:
            print("  被消费字段语言        全部非 CJK ✓")

    n = len(paths)
    print(f"\n=== 合计 {n} 张卡 ===")
    print(f"  provenance 条目  {tot_prov}  (均 {tot_prov / max(n,1):.1f}/张)")
    print(f"  取自 methods/results/表图  {body}      取自摘要/标题 {abst}")
    print(f"  缺 status        {tot_nostatus}")
    print(f"  带 cohort_id     {tot_cohort}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
