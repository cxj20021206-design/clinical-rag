"""USPSTF 连接器（source_role = normative，第三条腿的第一个源）。

读 curated/guidelines/uspstf.yaml（由 uspstf_ingest.py 一次性摄入），按 Claim Card
做**病种 + 场景**匹配。与 curated_reporting 同构，但门控依据不同：
  报告清单 → 研究设计 + 证据阶段（一份清单适用于哪类研究）
  临床指南 → 病种 + 适用场景（一份指南管不管这个病、这个场景）

两条硬门控（不匹配时给出理由，不静默返回空）：
  ① 场景门控：USPSTF 职权仅限预防服务（筛查/预防用药/行为咨询、初级保健）。
     急重症、住院管理、治疗方案一律不适用——这是 clinical_sources.yaml 里
     uspstf.notes「筛查类第一优先；不能外推到治疗问题」的执行点。
  ② 病种门控：必须有**判别词**命中。泛化词（cancer/screening/risk/imaging…）
     不算数——否则 "lung cancer screening" 会匹配上 "Breast Cancer: Screening"
     （命中的是 cancer，不是 lung），见 base.py: GENERIC_CLINICAL 的注释。
"""
from __future__ import annotations
import os
import re

import yaml

from base import Connector, discriminative_terms, expand_plural
from schema import ExternalStandard, compute_predates

CURATED = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "curated", "guidelines", "uspstf.yaml")

# 明确不属于预防/筛查场景的信号 → 场景门控拦截
_ACUTE_KW = ("icu", "intensive care", "critical care", "inpatient", "ward",
             "emergency", "resuscitation", "sepsis", "septic", "shock",
             "ventilat", "deterioration", "triage", "perioperative", "surgical",
             "postoperative", "treatment planning", "therapy selection",
             "dosing", "chemotherap", "radiotherap")
# 属于预防/筛查场景的正面信号
_PREV_KW = ("screening", "screen", "prevention", "preventive", "early detection",
            "case-finding", "case finding", "surveillance", "risk assessment",
            "primary care", "counseling", "prophylaxis")


def check_setting(card: dict) -> tuple[bool, str]:
    """场景门控：这篇论文是不是预防/筛查类问题。"""
    blob = " ".join(str(card.get(k) or "") for k in (
        "intended_use", "care_setting", "clinical_decision_affected",
        "deployment_claim_level", "model_output", "disease_or_condition")).lower()
    acute = [k for k in _ACUTE_KW if k in blob]
    prev = [k for k in _PREV_KW if k in blob]
    if acute and not prev:
        return False, (f"场景不匹配：Claim Card 指向急重症/治疗场景（命中 {acute[:3]}），"
                       f"USPSTF 职权仅限预防服务（筛查/预防用药/行为咨询）。"
                       f"套用属越权外推，须改由学会指南/WHO 等 normative 源承担。")
    if not prev:
        return False, ("场景无法判定为预防/筛查：Claim Card 未出现筛查/预防类信号，"
                       "未启用 USPSTF（宁可不提，不可乱提）。")
    return True, f"场景适用：命中预防/筛查信号 {prev[:3]}。"


# 人群/技术修饰词：能匹配上但不是病种。必须排除，否则 "high-risk adults" 会把
# 「Aspirin Use to Prevent Preeclampsia in persons at high risk」配给一篇肺癌论文，
# 「low-dose CT」会撞上任何含 low-dose 的主题。同 base.py: GENERIC_CLINICAL 的教训。
_MODIFIER = {
    "high-risk", "low-risk", "increased", "eligible", "eligibility", "asymptomatic",
    "low-dose", "high-dose", "dose", "aided", "unaided", "ai-assisted", "assisted",
    "automated", "computer-aided", "annual", "biennial", "periodic", "routine",
    "older", "younger", "pregnant", "nonpregnant",
}


def match_topics(card: dict, doc: dict, limit: int = 4) -> tuple[list[tuple[dict, int, set]], set]:
    """病种匹配。返回 ([(topic, 得分, 命中词)], 准入判别词)。

    **准入只看 disease_or_condition**——病种匹配就该由病种字段决定。
    target_population / intended_use 只参与排序加分，不参与准入；否则人群修饰词
    （high-risk、low-dose）会把完全无关的主题拉进来。
    """
    gate = {w for w in discriminative_terms(card.get("disease_or_condition"))
            if w not in _MODIFIER}
    gate = set(expand_plural(sorted(gate)))
    if not gate:
        return [], gate
    bonus = {w for w in discriminative_terms(card.get("target_population"),
                                        card.get("intended_use"))
             if w not in _MODIFIER and w not in gate}

    scored = []
    for t in doc.get("topics") or []:
        hay = " ".join(str(t.get(k) or "") for k in
                       ("title", "category", "slug", "age_group")).lower()
        hit = {w for w in gate if re.search(rf"\b{re.escape(w)}", hay)}
        if not hit:                      # 病种词一个都没命中 → 不准入
            continue
        extra = {w for w in bonus if re.search(rf"\b{re.escape(w)}", hay)}
        scored.append((t, len(hit) * 10 + len(extra), hit | extra))
    scored.sort(key=lambda x: (-x[1], x[0].get("publication_date") or ""))
    return scored[:limit], gate


# grade → 额外相关模块。D 级(不推荐/弊大于利)关乎危害；I 级(证据不足)关乎临床问题本身。
_BASE_MODULES = ["clinical_question", "population_validity", "comparator_baseline"]
_EXTRA_BY_GRADE = {"D": ["safety_harm_equity"], "I": ["endpoint_utility"]}


class USPSTFConnector(Connector):
    source_id = "uspstf"
    issuing_body = "U.S. Preventive Services Task Force (USPSTF) / AHRQ"
    source_role = "normative"
    tier = 1
    machine_access = "curated"

    def __init__(self, path: str = CURATED):
        self.path = path
        self.doc = yaml.safe_load(open(path)) if os.path.exists(path) else None

    def available(self) -> bool:
        return bool(self.doc and self.doc.get("topics"))

    def report(self, card: dict) -> dict:
        """给 CLI 用：是否启用、为什么、匹配到哪些主题。不适用的理由必须可见。"""
        if not self.available():
            return {"applicable": False, "reason": f"未摄入：{self.path} 不存在或为空。"
                                                   f"先跑 python3 connectors/uspstf_ingest.py",
                    "topics": [], "n_recs": 0}
        ok, reason = check_setting(card)
        if not ok:
            return {"applicable": False, "reason": reason, "topics": [], "n_recs": 0}
        matched, terms = match_topics(card, self.doc)
        if not matched:
            return {"applicable": False, "n_recs": 0, "topics": [],
                    "reason": (f"病种不匹配：判别词 {sorted(terms) or '无'} 在 "
                               f"{len(self.doc['topics'])} 条 USPSTF 主题中无命中。"
                               f"（泛化词如 cancer/screening 不计入判别，"
                               f"否则会误配到其他癌种。）")}
        return {"applicable": True,
                "reason": f"{reason} 病种判别词 {sorted(terms)} 命中 {len(matched)} 条主题。",
                "topics": [{"title": t["title"], "date": t.get("publication_date"),
                            "hits": sorted(h),
                            "grades": [r.get("grade") for r in t["recommendations"]]}
                           for t, _, h in matched],
                "n_recs": sum(len(t["recommendations"]) for t, _, _ in matched)}

    def search(self, query_context, submission_date=None, limit: int = 4):
        card = query_context.get("_card") or query_context
        if not self.available():
            return []
        ok, setting_reason = check_setting(card)
        if not ok:
            return []
        matched, terms = match_topics(card, self.doc, limit=limit)
        prov = self.doc.get("provenance") or {}
        qc = {k: v for k, v in query_context.items() if not k.startswith("_")}

        out: list[ExternalStandard] = []
        for topic, _, hits in matched:
            pub = topic.get("publication_date")
            predates = compute_predates(pub, submission_date)
            for r in topic["recommendations"]:
                grade = r.get("grade")
                notes = [f"适用性判定：{setting_reason} 病种命中词 {sorted(hits)}。",
                         f"USPSTF Grade {grade}：{r.get('grade_meaning') or '未知等级'}",
                         f"许可：{prov.get('license')}（逐字引用，未改动）"]
                if grade == "I":
                    notes.append("⚠️ I 级=证据不足：USPSTF 认为现有证据不足以评估收益与危害。"
                                 "论文若在此领域声称临床价值，应重点追问其证据强度。")
                if topic.get("type"):
                    notes.append(f"USPSTF 类型：{topic['type']}")
                out.append(ExternalStandard(
                    source_id=self.source_id,
                    issuing_body=self.issuing_body,
                    document_type="guideline",
                    title=f"{topic['title']} — {r.get('population') or '全人群'} (Grade {grade})",
                    canonical_url=topic["url"],
                    version_or_publication_date=pub,
                    retrieved_date=self.today(),
                    region="US",
                    target_population=r.get("population"),
                    care_setting=topic.get("type"),
                    intended_use_or_decision_point=topic.get("title"),
                    recommendation_or_requirement=r.get("text"),
                    recommendation_strength=grade,
                    evidence_certainty=r.get("certainty"),
                    passage=r.get("text"),          # 逐字原文
                    section_page_table=f"Recommendation Summary — {r.get('population')}",
                    license=prov.get("license"),
                    machine_access=self.machine_access,
                    source_role=self.source_role,
                    tier=self.tier,
                    predates_paper_submission=predates,
                    notes=" | ".join(notes),
                    modules=_BASE_MODULES + _EXTRA_BY_GRADE.get(grade, []),
                    query_context=qc,
                ))
        return out
