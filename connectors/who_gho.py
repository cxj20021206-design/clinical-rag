"""WHO Global Health Observatory (OData) 连接器 (无 key)。
用途：疾病负担 / 患病率 / 卫生系统指标 —— 支撑 unmet need 与地区背景 (module 1/2)。
不能替代目标医院/目标人群的本地流行病学。
"""
from __future__ import annotations
import requests
from base import Connector, UA
from schema import ExternalStandard

API = "https://ghoapi.azureedge.net/api"


class WHOGHOConnector(Connector):
    source_id = "who_gho"
    issuing_body = "WHO Global Health Observatory"
    source_role = "epidemiology"
    tier = 3
    machine_access = "api"

    def search(self, query_context, submission_date=None, limit=5):
        # 用 condition 关键词在指标名里模糊匹配 (取首词，OData contains 对短词更稳)
        cond = (query_context.get("condition") or query_context.get("population", "")).strip()
        kw = cond.split()[0] if cond else ""
        if not kw:
            return []
        try:
            r = requests.get(f"{API}/Indicator",
                             params={"$filter": f"contains(IndicatorName,'{kw}')"},
                             headers={"User-Agent": UA}, timeout=45)
            r.raise_for_status()
            inds = r.json().get("value", [])[:limit]
        except Exception as e:
            print(f"  [who_gho] 失败: {str(e)[:80]}")
            return []
        out = []
        for ind in inds:
            code = ind.get("IndicatorCode")
            name = ind.get("IndicatorName", "")
            rec = ExternalStandard(
                source_id=self.source_id,
                issuing_body=self.issuing_body,
                document_type="epidemiology",
                title=name,
                canonical_url=f"https://www.who.int/data/gho/data/indicators/indicator-details/GHO/{code}",
                retrieved_date=self.today(),
                region=query_context.get("region") or "global",
                recommendation_or_requirement=None,
                passage=f"WHO GHO 指标：{name} (code {code})",
                section_page_table=code,
                source_role=self.source_role,
                tier=self.tier,
                machine_access=self.machine_access,
                license="WHO GHO open data",
                source_quality="epidemiology_context",
                predates_paper_submission="unknown",  # 指标是持续更新的时间序列
                notes="疾病负担背景；非本地流行病学；取值需 code+国家维度另查",
                query_context=query_context,
            )
            out.append(rec)
        return out
