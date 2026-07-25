"""WHO Global Health Observatory (OData) 连接器 (无 key)。

用途：疾病负担 / 患病率 / 卫生系统指标 —— 支撑 unmet need 与地区背景
(clinical_question / population_validity 两个模块)。不能替代目标医院/目标人群的
本地流行病学。

注意：GHO 是 WHO 的**统计数据库**，不是 WHO 指南。WHO 指南属 `normative` 角色
(源 id `who_guidelines`)，目前尚未接入。

--- 2026-07-25 重写 ---
旧实现三个硬伤，实测结果基本不可用：

1. `kw = cond.split()[0]` —— **只拿病种的第一个词**去模糊匹配指标名。
   "lung cancer screening" → 只查 "lung" → GHO 里一条含 lung 的指标都没有 → 零命中。
2. **不校验指标是否真有数据**。"sepsis" 唯一命中 WHS2_515（5 岁以下儿童死因分布-
   新生儿脓毒症），而该指标**一行数据都没有**——系统却把它当成一条外部证据输出。
3. **只拿指标名，不拿数值**。旧注释自己写着"取值需 code+国家维度另查"，
   于是它最多只能说"WHO 有这么个指标"，说不出"发病率是多少"——而数值才是有用的部分。

现在：全部病种词依次尝试 → 人群相符性过滤 → **拉取真实数值，无数据的指标直接丢弃**
→ 附上按地区/年份筛出的数据点。检索不到就返回空——GHO 对很多专科病种（如肺癌）
确实没有指标，谎称有覆盖比空手更糟。
"""
from __future__ import annotations
import requests
from base import Connector, UA, clean_text, keywords, GENERIC_CLINICAL
from schema import ExternalStandard, compute_predates

API = "https://ghoapi.azureedge.net/api"

# 卡片 region → GHO 的 ISO3 国家码 (SpatialDim)
REGION_ISO3 = {
    "US": "USA", "USA": "USA", "CN": "CHN", "CHINA": "CHN",
    "UK": "GBR", "GB": "GBR", "DE": "DEU", "FR": "FRA", "JP": "JPN",
    "IN": "IND", "CA": "CAN", "AU": "AUS", "KR": "KOR", "BR": "BRA",
}
# 指标名里的人群标记 → 该指标服务的人群
PEDIATRIC_MARKERS = ("children", "child", "neonatal", "newborn", "infant",
                     "under-5", "under 5", "aged <5", "adolescent", "paediatric",
                     "pediatric", "birth")
ADULT_MARKERS = ("adult", "aged 18", "aged 30", "aged 15-49", "elderly", "older")


class WHOGHOConnector(Connector):
    source_id = "who_gho"
    issuing_body = "WHO Global Health Observatory"
    source_role = "epidemiology"
    tier = 3
    machine_access = "api"

    def _get(self, path, params=None) -> list[dict]:
        try:
            r = requests.get(f"{API}/{path}", params=params or {},
                             headers={"User-Agent": UA}, timeout=45)
            r.raise_for_status()
            return r.json().get("value", [])
        except Exception as e:
            print(f"  [who_gho] {path} 失败: {str(e)[:70]}")
            return []

    @staticmethod
    def _population_conflict(indicator_name: str, population: str) -> str | None:
        """指标人群与卡片人群明显冲突时返回冲突说明，否则 None。

        成人住院病人的脓毒症预警模型，配上"5 岁以下儿童死因分布"是错的——
        这不是噪声大小问题，是人群根本不同，会直接导出错误的 unmet need 论证。
        """
        ind, pop = indicator_name.lower(), (population or "").lower()
        ind_ped = any(m in ind for m in PEDIATRIC_MARKERS)
        pop_ped = any(m in pop for m in PEDIATRIC_MARKERS)
        pop_adult = any(m in pop for m in ADULT_MARKERS)
        if ind_ped and pop_adult and not pop_ped:
            return "指标为儿童/新生儿人群，卡片为成人人群"
        if not ind_ped and pop_ped and any(m in ind for m in ADULT_MARKERS):
            return "指标为成人人群，卡片为儿童人群"
        return None

    def search(self, query_context, submission_date=None, limit=5):
        cond = query_context.get("condition") or query_context.get("population", "")
        population = query_context.get("population", "")
        # 全部病种词依次尝试（旧实现只取首词）；先长后短，长词更具体。
        terms = sorted(keywords(cond, max_terms=5), key=lambda t: -len(t))
        if not terms:
            return []
        iso3 = REGION_ISO3.get(str(query_context.get("region", "")).strip().upper())

        # 判别词 = 剔掉泛化医学词后剩下的。GHO 里 "cancer" 能命中 36 条乳腺/宫颈癌
        # 指标，跟肺癌毫无关系；必须靠 "lung" 这种具体词来判定相关性。
        specific = [t for t in terms if t not in GENERIC_CLINICAL]
        indicators, seen = [], set()
        for kw in [clean_text(cond)] + terms:
            if len(indicators) >= limit * 3:
                break
            for ind in self._get("Indicator",
                                 {"$filter": f"contains(IndicatorName,'{kw}')"}):
                code = ind.get("IndicatorCode")
                if not code or code in seen:
                    continue
                # 只靠泛化词匹配上的指标一律丢弃（乳腺癌筛查 ≠ 肺癌筛查）
                if specific and not any(
                        t in ind.get("IndicatorName", "").lower() for t in specific):
                    continue
                seen.add(code)
                indicators.append(ind)

        out: list[ExternalStandard] = []
        skipped_empty, skipped_pop = 0, 0
        for ind in indicators:
            if len(out) >= limit:
                break
            code, name = ind.get("IndicatorCode"), ind.get("IndicatorName", "")

            conflict = self._population_conflict(name, population)
            if conflict:
                skipped_pop += 1
                continue

            # **必须确认指标真有数据**。WHS2_515 就是个空指标，旧实现照样输出。
            rows = self._get(code, {"$top": 200})
            if not rows:
                skipped_empty += 1
                continue

            local = [r for r in rows if iso3 and r.get("SpatialDim") == iso3]
            picked = sorted(local or rows,
                            key=lambda r: (r.get("TimeDim") or 0), reverse=True)[:3]
            years = [r.get("TimeDim") for r in picked if r.get("TimeDim")]
            latest = f"{max(years)}-12-31" if years else None
            vals = "; ".join(
                f"{r.get('SpatialDim')} {r.get('TimeDim')}: "
                f"{r.get('NumericValue') if r.get('NumericValue') is not None else r.get('Value')}"
                for r in picked)

            out.append(ExternalStandard(
                source_id=self.source_id, issuing_body=self.issuing_body,
                document_type="epidemiology",
                title=name,
                canonical_url=("https://www.who.int/data/gho/data/indicators/"
                               f"indicator-details/GHO/{code}"),
                version_or_publication_date=latest,
                retrieved_date=self.today(),
                region=(iso3 if local else "global"),
                target_population=population or None,
                recommendation_or_requirement=None,   # 统计数字不是规范要求
                passage=f"WHO GHO {code} — {name}｜数据点（{'本地区' if local else '全球'}，"
                        f"最新在前）：{vals}",
                section_page_table=code,
                source_role=self.source_role, tier=self.tier,
                machine_access=self.machine_access, license="WHO GHO open data",
                source_quality="epidemiology_context",
                predates_paper_submission=compute_predates(latest, submission_date),
                notes="疾病负担背景；非本地流行病学，不能替代目标医院人群统计",
                query_context=query_context,
            ))

        if not out and (skipped_empty or skipped_pop or indicators):
            print(f"  [who_gho] 无可用指标（候选 {len(indicators)}，"
                  f"空数据 {skipped_empty}，人群不符 {skipped_pop}）")
        return out
