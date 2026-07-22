"""ClinicalTrials.gov API v2 连接器 (无 key)。
用途：核对 comparator / endpoint / population / 预注册 —— 定义"现实临床里
这个问题该怎么设计试验"，而非证明本文做到了。注册记录本身不是疗效证明。
"""
from __future__ import annotations
import requests
from base import Connector, UA
from schema import ExternalStandard, compute_predates

API = "https://clinicaltrials.gov/api/v2/studies"


class ClinicalTrialsConnector(Connector):
    source_id = "clinicaltrials_gov"
    issuing_body = "ClinicalTrials.gov"
    source_role = "registry"
    tier = 3
    machine_access = "api"

    def search(self, query_context, submission_date=None, limit=5):
        cond = query_context.get("condition") or query_context.get("population", "")
        interv = query_context.get("intervention", "")
        params = {
            "query.cond": cond,
            "query.intr": interv,
            "pageSize": limit,
            "fields": ",".join([
                "NCTId", "BriefTitle", "Condition", "InterventionName",
                "ArmGroupLabel", "PrimaryOutcomeMeasure", "StudyType",
                "OverallStatus", "StartDate", "StudyFirstPostDate",
                "EligibilityCriteria", "EnrollmentCount", "LocationCountry",
            ]),
        }
        r = requests.get(API, params=params, headers={"User-Agent": UA}, timeout=45)
        r.raise_for_status()
        out = []
        for s in r.json().get("studies", []):
            p = s.get("protocolSection", {})
            ident = p.get("identificationModule", {})
            arms = p.get("armsInterventionsModule", {})
            outc = p.get("outcomesModule", {})
            design = p.get("designModule", {})
            status = p.get("statusModule", {})
            elig = p.get("eligibilityModule", {})
            cont = p.get("contactsLocationsModule", {})

            nct = ident.get("nctId", "")
            first_post = (status.get("studyFirstPostDateStruct", {}) or {}).get("date")
            comparators = [a.get("label") for a in (arms.get("armGroups") or []) if a.get("label")]
            primary = [o.get("measure") for o in (outc.get("primaryOutcomes") or []) if o.get("measure")]
            countries = sorted({loc.get("country") for loc in (cont.get("locations") or []) if loc.get("country")})

            rec = ExternalStandard(
                source_id=self.source_id,
                issuing_body=self.issuing_body,
                document_type="registry",
                title=ident.get("briefTitle", ""),
                canonical_url=f"https://clinicaltrials.gov/study/{nct}",
                version_or_publication_date=first_post,
                retrieved_date=self.today(),
                region=", ".join(countries) or None,
                target_population=(elig.get("eligibilityCriteria", "") or "")[:500] or None,
                intended_use_or_decision_point=", ".join(design.get("phases", []) or []) or design.get("studyType"),
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
                notes="注册/预注册核对；registry ≠ 疗效证明",
                query_context=query_context,
            )
            out.append(rec)
        return out
