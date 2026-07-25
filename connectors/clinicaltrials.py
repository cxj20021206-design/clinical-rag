"""ClinicalTrials.gov API v2 连接器 (无 key)。

用途：核对 comparator / endpoint / population / 预注册 —— 定义"现实临床里
这个问题该怎么设计试验"，而非证明本文做到了。注册记录本身不是疗效证明。

--- 2026-07-25：补上 predates 前置与降级阶梯 ---
旧实现直接把 query.cond + query.intr 丢过去、不做日期约束。肺癌卡实测检回的
试验首次公示于 2025-12，而论文 2024-05 投稿 → `predates=false`，按项目铁律
不能用来要求作者，那次检索对"作者当时该做什么"的贡献为零（与 Europe PMC
当时是同一个病）。现在：

1. **投稿日作检索条件**：`filter.advanced=AREA[StudyFirstPostDate]RANGE[MIN,投稿日]`，
   主检索只要论文投稿时**已经公示**的试验。
2. **降级阶梯**：病种+干预 → 只要病种。干预词是自由文本（"AI-assisted low-dose
   CT interpretation"），叠加日期上界后经常把结果掐到 0。
3. **投稿后另开小桶**：投稿后注册的试验说明"这个方向后来有人在做"，可用于
   "今天还值不值得做"的评价，但记录里显式标注不得据此指责作者。
"""
from __future__ import annotations
import requests
from base import Connector, UA, clean_text, keywords
from schema import ExternalStandard, compute_predates

API = "https://clinicaltrials.gov/api/v2/studies"

# 试验注册库里 AI 干预的通用说法。卡片写 "AI-assisted low-dose CT interpretation"
# 这样的长句直接丢给 query.intr 会 0 命中；拆成 (AI 词) AND (功能词) 才检得到
# Sepsis Watch / GRADY / Lung Nodule Detection With AI 这些真正可比的试验。
AI_TERMS = ('"artificial intelligence" OR "machine learning" OR "deep learning" '
            'OR "computer-aided" OR "computer aided" OR algorithm')
# 这些词属于 AI 说法本身，不该再进"功能词"子句
_AI_WORDS = {"ai", "artificial", "intelligence", "machine", "deep", "learning",
             "algorithm", "algorithmic", "neural", "network", "automated", "computer"}
FIELDS = ",".join([
    "NCTId", "BriefTitle", "Condition", "InterventionName",
    "ArmGroupLabel", "PrimaryOutcomeMeasure", "StudyType",
    "OverallStatus", "StartDate", "StudyFirstPostDate",
    "EligibilityCriteria", "EnrollmentCount", "LocationCountry",
])


class ClinicalTrialsConnector(Connector):
    source_id = "clinicaltrials_gov"
    issuing_body = "ClinicalTrials.gov"
    source_role = "registry"
    tier = 3
    machine_access = "api"

    def _fetch(self, cond, interv, date_filter, n) -> list[dict]:
        if n <= 0:
            return []
        params = {"query.cond": cond, "pageSize": min(n, 20), "fields": FIELDS}
        if interv:
            params["query.intr"] = interv
        if date_filter:
            params["filter.advanced"] = date_filter
        try:
            r = requests.get(API, params=params, headers={"User-Agent": UA}, timeout=45)
            r.raise_for_status()
            return r.json().get("studies", [])
        except Exception as e:
            print(f"  [clinicaltrials] 失败: {str(e)[:80]}")
            return []

    def search(self, query_context, submission_date=None, limit=5):
        cond = clean_text(query_context.get("condition")
                          or query_context.get("population", ""))
        if not cond:
            return []
        # 功能词：干预/模型输出里剔掉病种词与 AI 说法后剩下的（early, warning,
        # nodule, triage ...）。与 AI 词子句组合成检索式。
        cond_tokens = set(clean_text(cond).lower().split())
        func = [t for t in keywords(query_context.get("intervention", ""),
                                    query_context.get("model_output", ""),
                                    exclude=cond_tokens, max_terms=6)
                if t not in _AI_WORDS]
        func_clause = " OR ".join(f'"{t}"' for t in func)
        # 由紧到松：AI词 AND 功能词 → AI词 OR 功能词 → 只按病种
        intr_rungs = [q for q in (
            f'({AI_TERMS}) AND ({func_clause})' if func_clause else None,
            f'({AI_TERMS}) OR ({func_clause})' if func_clause else AI_TERMS,
        ) if q]

        d = str(submission_date)[:10] if submission_date else None
        before = f"AREA[StudyFirstPostDate]RANGE[MIN,{d}]" if d else None
        after = f"AREA[StudyFirstPostDate]RANGE[{d},MAX]" if d else None

        seen: set = set()
        out: list[ExternalStandard] = []

        def collect(studies, want, note_extra=""):
            n = 0
            for s in studies:
                if n >= want:
                    break
                rec = self._to_record(s, submission_date, query_context, note_extra)
                if not rec or rec.section_page_table in seen:
                    continue
                seen.add(rec.section_page_table)
                out.append(rec)
                n += 1
            return n

        # 主检索：投稿时已公示的试验。逐档放松，直到有命中。
        got, used = 0, None
        for i, intr in enumerate(intr_rungs + [None]):
            got = collect(self._fetch(cond, intr, before, limit * 2), limit)
            if got:
                used = intr
                if i:
                    print(f"  [clinicaltrials] 干预检索降级到第 {i + 1} 档")
                break

        # 投稿后小桶
        if d and got:
            want_post = max(1, limit // 4)
            collect(self._fetch(cond, used, after, want_post * 3), want_post,
                    note_extra="；于投稿后才注册，可用于'今天该方向如何'的评价，"
                               "不得据此指责作者")
        return out

    def _to_record(self, s, submission_date, query_context, note_extra=""):
        p = s.get("protocolSection", {})
        ident = p.get("identificationModule", {})
        arms = p.get("armsInterventionsModule", {})
        outc = p.get("outcomesModule", {})
        design = p.get("designModule", {})
        status = p.get("statusModule", {})
        elig = p.get("eligibilityModule", {})
        cont = p.get("contactsLocationsModule", {})

        nct = ident.get("nctId", "")
        if not nct:
            return None
        first_post = (status.get("studyFirstPostDateStruct", {}) or {}).get("date")
        comparators = [a.get("label") for a in (arms.get("armGroups") or []) if a.get("label")]
        primary = [o.get("measure") for o in (outc.get("primaryOutcomes") or []) if o.get("measure")]
        countries = sorted({loc.get("country") for loc in (cont.get("locations") or [])
                            if loc.get("country")})
        return ExternalStandard(
            source_id=self.source_id,
            issuing_body=self.issuing_body,
            document_type="registry",
            title=ident.get("briefTitle", ""),
            canonical_url=f"https://clinicaltrials.gov/study/{nct}",
            version_or_publication_date=first_post,
            retrieved_date=self.today(),
            region=", ".join(countries) or None,
            target_population=(elig.get("eligibilityCriteria", "") or "")[:500] or None,
            intended_use_or_decision_point=", ".join(design.get("phases", []) or [])
                                           or design.get("studyType"),
            comparator="; ".join(comparators) or None,
            endpoint_or_threshold="; ".join(primary) or None,
            recommendation_or_requirement=None,   # 注册记录不是"要求"
            passage=f"[{status.get('overallStatus','')}] " + ident.get("briefTitle", ""),
            section_page_table=nct,
            source_role=self.source_role,
            tier=self.tier,
            machine_access=self.machine_access,
            license="ClinicalTrials.gov Terms of Service",
            predates_paper_submission=compute_predates(first_post, submission_date),
            notes="注册/预注册核对；registry ≠ 疗效证明" + note_extra,
            query_context=query_context,
        )
