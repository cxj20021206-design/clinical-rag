"""门控回归：把所有示例卡的准入判定打成一张表，外加三个已修复的误判反例。

不联网、秒级跑完。改动 claim_card.py / 任何门控逻辑后都应先跑它，因为门控失败
**是静默的**——卡被拦下或误配都不会报错，只会让检索结果悄悄变样。

    python3 check_gates.py
"""
from __future__ import annotations

import glob
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "connectors"))

from claim_card import ClaimCard, load_card                      # noqa: E402
from curated_guidelines import (check_population, match_disease,  # noqa: E402
                                load_guidelines)
from base import exclusion_overlap                                # noqa: E402
from uspstf import USPSTFConnector, check_setting, match_topics   # noqa: E402

U = USPSTFConnector()
GUIDES = load_guidelines()


def gate_view(card: dict) -> tuple[str, list[str]]:
    ok, why = check_setting(card)
    if ok:
        matched, _ = match_topics(card, U.doc)
        us = f"✅ {matched[0][0]['title'][:38]}" if matched else "⛔ 病种不匹配"
    else:
        us = f"⛔ {why[:34]}"
    cpg = []
    for d in GUIDES:
        scope = d.get("scope") or {}
        terms = [str(t).lower() for t in (scope.get("disease_terms") or [])]
        ov = exclusion_overlap(terms, card)
        if ov and len(ov) == len(terms):
            cpg.append(f"⛔排除项{d['slug'][:20]}")
            continue
        hits = match_disease(card, scope)
        if not hits:
            continue
        pok, _ = check_population(card, scope)
        cpg.append(("✅" if pok else "⛔人群") + d["slug"][:26] + ("⚠️部分排除" if ov else ""))
    return us, cpg


def main():
    print("=== 示例卡门控矩阵 ===")
    for p in sorted(glob.glob(os.path.join(HERE, "examples", "claim_card_*.yaml"))):
        c = load_card(p, strict=False)
        us, cpg = gate_view(c.legacy)
        tag = "旧扁平卡" if c.legacy_input else "分层卡"
        print(f"\n{os.path.basename(p)}  [{tag}]")
        print(f"   USPSTF : {us}")
        print(f"   CPG    : {cpg or '无匹配'}")

    print("\n=== 三个已修复的误判反例（2026-07-28 实测，见 claim_card.py 头部）===")

    # ① 详细化的病种字段把合并症语境混进来 → 命中新生儿 POCUS 指南
    old = {"disease_or_condition": "acute kidney injury in critical illness",
           "target_population": "ICU patients", "intended_use": "AI prediction of AKI"}
    new = ClaimCard({"submission_date": "2026-01-01", "gating": {
        "condition": {"primary": {"label": "acute kidney injury"},
                      "comorbid_context": ["critical illness"], "excluded": []},
        "population": {"age_group": "adult"}, "care_setting": "icu",
        "clinical_task": "prognostication", "evidence_stage": "C2"}}).legacy
    print(f"\n① 合并症语境混进病种字段")
    print(f"   旧扁平卡 → CPG {gate_view(old)[1]}")
    print(f"   分层卡   → CPG {gate_view(new)[1] or '无匹配（合并症语境不参与病种准入）'}")

    # ② 排除标准被当成命中
    old2 = {"disease_or_condition": "acute ischemic stroke, excluding hemorrhagic "
                                    "stroke and congenital heart disease",
            "target_population": "adults"}
    print(f"\n② 排除标准被当成命中")
    print(f"   旧扁平卡 → CPG {gate_view(old2)[1]}")
    print(f"   分层卡   → 见上表 claim_card_stroke_c2.yaml（excluded 进受控字段，不入病种文本）")
    # 第二道防线管的是另一件事：**指南自己的适用范围覆盖了论文明确排除的病种**
    synth = {"disease_or_condition": "sepsis", "excluded_conditions": ["neonatal sepsis"]}
    for scope_terms, label in ((["neonatal sepsis", "late-onset neonatal sepsis"], "整份都是排除病种"),
                               (["sepsis", "neonatal sepsis"], "部分覆盖排除病种"),
                               (["sepsis", "septic shock"], "与排除项无关")):
        ov = exclusion_overlap(scope_terms, synth)
        verdict = ("⛔ 硬拦" if ov and len(ov) == len(scope_terms)
                   else f"⚠️ 提示（重叠 {ov}）" if ov else "✅ 正常")
        print(f"   兜底 [{label}] scope={scope_terms} → {verdict}")
    print(f"        主病种不受牵连：excluded 含 'neonatal sepsis'，"
          f"但 scope 里的 'sepsis' 与主病种同名 → 不计入重叠")

    # ③ intended_use 里一个 triage 把 USPSTF 整个拦下
    old3 = {"disease_or_condition": "diabetic retinopathy", "target_population": "adults",
            "intended_use": "automated grading to triage referrals to ophthalmology"}
    new3 = ClaimCard({"submission_date": "2026-01-01", "gating": {
        "condition": {"primary": {"label": "diabetic retinopathy"}},
        "population": {"age_group": "adult"}, "care_setting": "primary_care",
        "clinical_task": "screening", "evidence_stage": "C2"},
        "descriptive": {"intended_use": "automated grading to triage referrals "
                                        "to ophthalmology"}}).legacy
    print(f"\n③ intended_use 里的 triage 翻掉筛查卡的 USPSTF 场景门")
    print(f"   旧扁平卡 → USPSTF {check_setting(old3)[0]}  ({check_setting(old3)[1][:56]})")
    print(f"   分层卡   → USPSTF {check_setting(new3)[0]}  ({check_setting(new3)[1][:56]})")


if __name__ == "__main__":
    main()
