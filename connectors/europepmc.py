"""Europe PMC REST 连接器 (无 key)。
用途：外部证据*发现*层——找系统综述/指南/相关文献。
注意：命中记录本身不是临床标准 (source_role=discovery，低层级)，
须回到具体文献核验；这里只负责把候选文献带出来。
"""
from __future__ import annotations
import requests
from base import Connector, UA
from schema import ExternalStandard, compute_predates

API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# 出版类型 → document_type 粗映射
def _doctype(pubtypes: str) -> str:
    t = (pubtypes or "").lower()
    if "systematic" in t or "meta-analysis" in t:
        return "systematic_review"
    if "guideline" in t or "consensus" in t:
        return "guideline"
    return "literature"


class EuropePMCConnector(Connector):
    source_id = "europepmc"
    issuing_body = "Europe PMC"
    source_role = "discovery"
    tier = 5
    machine_access = "api"

    def search(self, query_context, submission_date=None, limit=5):
        cond = query_context.get("condition") or query_context.get("population", "")
        interv = query_context.get("intervention", "")
        # 优先系统综述/指南；同时限定人类研究
        q = f'({cond} {interv}) AND (SRC:MED) AND (HAS_ABSTRACT:Y)'
        params = {"query": q, "format": "json", "pageSize": limit,
                  "resultType": "core", "sort": "P_PDATE_D desc"}
        try:
            r = requests.get(API, params=params, headers={"User-Agent": UA}, timeout=45)
            r.raise_for_status()
            hits = r.json().get("resultList", {}).get("result", [])
        except Exception as e:
            print(f"  [europepmc] 失败: {str(e)[:80]}")
            return []
        out = []
        for h in hits:
            pubdate = h.get("firstPublicationDate")
            doi = h.get("doi")
            pmid = h.get("pmid") or h.get("id")
            url = (f"https://doi.org/{doi}" if doi
                   else f"https://europepmc.org/article/{h.get('source','MED')}/{pmid}")
            ptl = h.get("pubTypeList", {})
            pts = ptl.get("pubType", []) if isinstance(ptl, dict) else []
            if isinstance(pts, str):
                pts = [pts]
            rec = ExternalStandard(
                source_id=self.source_id,
                issuing_body=h.get("journalTitle") or self.issuing_body,
                document_type=_doctype(" ".join(pts)),
                title=h.get("title", ""),
                canonical_url=url,
                version_or_publication_date=pubdate,
                retrieved_date=self.today(),
                recommendation_or_requirement=None,
                passage=(h.get("abstractText", "") or "")[:800] or None,
                section_page_table=f"PMID:{pmid}" + (f" DOI:{doi}" if doi else ""),
                source_role=self.source_role,
                tier=self.tier,
                machine_access=self.machine_access,
                license="open" if h.get("isOpenAccess") == "Y" else "publisher",
                source_quality="discovery/needs_verification",
                predates_paper_submission=compute_predates(pubdate, submission_date),
                notes="发现层：命中≠临床标准，须回到全文核验",
                query_context=query_context,
            )
            out.append(rec)
        return out
