"""openFDA 连接器 (无 key)。
用途：监管语境 —— 药品标签的适应证/警示，或器械(SaMD)分类。
定义"监管认可的适应证与已知风险"(module 3 参考标准 / module 7 安全)。
不良事件自发报告 != 因果关系。
"""
from __future__ import annotations
import requests
from base import Connector, UA
from schema import ExternalStandard, compute_predates

DRUG_LABEL = "https://api.fda.gov/drug/label.json"
DEVICE_510K = "https://api.fda.gov/device/510k.json"


class OpenFDAConnector(Connector):
    source_id = "openfda"
    issuing_body = "US FDA (openFDA)"
    source_role = "regulatory"
    tier = 3
    machine_access = "api"

    def _iso(self, ymd):  # openFDA 日期常为 'YYYYMMDD'
        if ymd and len(ymd) == 8 and ymd.isdigit():
            return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
        return ymd

    def search(self, query_context, submission_date=None, limit=5):
        cond = query_context.get("condition") or query_context.get("population", "")
        out = []
        # 1) 药品标签：按适应证检索
        try:
            r = requests.get(DRUG_LABEL,
                             params={"search": f'indications_and_usage:"{cond}"', "limit": limit},
                             headers={"User-Agent": UA}, timeout=45)
            if r.status_code == 200:
                for d in r.json().get("results", []):
                    of = d.get("openfda", {})
                    name = (of.get("brand_name") or of.get("generic_name") or ["(unknown)"])[0]
                    eff = self._iso(d.get("effective_time"))
                    ind = " ".join(d.get("indications_and_usage", []) or [])[:800]
                    warn = " ".join(d.get("warnings", []) or [])[:400]
                    out.append(ExternalStandard(
                        source_id=self.source_id, issuing_body=self.issuing_body,
                        document_type="regulatory",
                        title=f"FDA Drug Label: {name}",
                        canonical_url="https://open.fda.gov/apis/drug/label/",
                        version_or_publication_date=eff, retrieved_date=self.today(),
                        region="US",
                        intended_use_or_decision_point="approved indication",
                        recommendation_or_requirement=ind or None,
                        passage=(ind + (" | WARNINGS: " + warn if warn else ""))[:1000] or None,
                        section_page_table="indications_and_usage",
                        source_role=self.source_role, tier=self.tier,
                        machine_access=self.machine_access, license="openFDA (US gov)",
                        source_quality="regulatory_label",
                        predates_paper_submission=compute_predates(eff, submission_date),
                        notes="监管认可适应证/警示；自发不良事件≠因果",
                        query_context=query_context,
                    ))
        except Exception as e:
            print(f"  [openfda drug] 失败: {str(e)[:80]}")
        return out[:limit]
