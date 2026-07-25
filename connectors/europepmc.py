"""Europe PMC REST 连接器 (无 key)。

用途：外部证据*发现*层——找指南/系统综述/相关文献。
注意：命中记录本身不是临床标准 (source_role=discovery)，须回到全文核验；
这里只负责把候选文献带出来。

--- 2026-07-25 重写：修检索噪声 ---
旧实现把 Claim Card 的病种和干预拼成一个裸词串、按发表日倒序排，实测肺癌筛查卡
4 条命中里 2 条完全无关（放疗后放射性肺炎、肝癌双特异性抗体），且 4 条全部
predates=false（对投稿当时的评价零贡献）。四点改动：

1. **按相关度排序**，不再传 sort=P_PDATE_D desc——原来拿到的是"最新的沾边文献"
   而不是"最相关的文献"。这是噪声的元凶。
2. **病种作强制短语约束** `TITLE_ABS:"..."`，干预/输入输出词作 OR 软约束，
   掐掉裸词串的松散匹配（肝癌那条就是靠单个 cancer 词进来的）。
3. **出版类型分层检索**：指南/共识 → 系统综述/meta → 普通文献，按配额依次填充，
   并据此定 document_type 与 tier（旧实现注释写着"优先系统综述/指南"但查询里
   根本没有 PUB_TYPE 条件，_doctype() 因此永远返回 literature）。
4. **predates 前置成检索条件**：主检索限定发表日 <= 投稿日；投稿后的另开一小桶，
   且只收指南/综述（供"今天能否部署"评价用），不收普通文献。

`limit` 是主检索的配额；投稿后小桶（<= limit//4，至少 1 条）叠加在其上，
所以返回条数可能略超 limit。检索阶梯全部落空时返回空列表——宁可空手，
也不给审稿模型喂不相干文献。
"""
from __future__ import annotations
import re
import requests
from base import Connector, UA
from schema import ExternalStandard, compute_predates

API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
COMMON = '(SRC:MED) AND (HAS_ABSTRACT:Y)'

# 出版类型分层：(层名, PUB_TYPE 约束, 默认 document_type, 默认 tier, 配额权重)
# tier 对齐 DESIGN §5：Tier1 指南 / Tier2 系统综述·共识 / Tier5 普通文献·发现层。
STRATA = [
    ("guideline",
     '(PUB_TYPE:"Guideline" OR PUB_TYPE:"Practice Guideline" '
     'OR PUB_TYPE:"Consensus Statement" OR PUB_TYPE:"Consensus Development Conference")',
     "guideline", 1, 0.50),
    ("synthesis",
     '(PUB_TYPE:"Systematic Review" OR PUB_TYPE:"Meta-Analysis" '
     'OR PUB_TYPE:"systematic-review")',
     "systematic_review", 2, 0.25),
    ("literature", None, "literature", 5, 0.25),
]

# 干预词的停用词：功能词 + 空泛技术/临床词。留在 OR 子句里会让约束近乎失效。
_STOP = {
    "the", "a", "an", "of", "for", "and", "or", "in", "on", "with", "to", "by",
    "from", "at", "as", "via", "using", "use", "used", "based", "its", "their",
    "this", "that", "these", "those", "is", "are", "be",
    "system", "systems", "tool", "tools", "method", "methods", "approach",
    "model", "models", "interpretation", "assessment", "analysis", "evaluation",
    "support", "clinical", "patient", "patients", "care", "study", "studies",
    "new", "novel", "level", "onset",
    # 人群/场景里的空泛词——留着会让人群约束近乎失效
    "general", "medical", "medicine", "health", "healthcare", "setting", "settings",
}


def _expand_plural(terms: list[str]) -> list[str]:
    """adult ↔ adults：卡里写单数、文献写复数（或反之）不该漏掉。"""
    out = list(terms)
    for t in terms:
        alt = t[:-1] if t.endswith("s") else t + "s"
        if len(alt) >= 3 and alt not in out:
            out.append(alt)
    return out


def _or_clause(terms: list[str]) -> str | None:
    return "(" + " OR ".join(f'TITLE_ABS:"{t}"' for t in terms) + ")" if terms else None


def _clean(s: str) -> str:
    """去掉会破坏 Lucene 查询的字符。"""
    return re.sub(r'["\\:()\[\]{}^~?*]', " ", str(s or "")).strip()


def _phrase(s: str) -> str | None:
    s = _clean(s)
    return f'TITLE_ABS:"{s}"' if s else None


def _terms(*texts: str, exclude: set[str] | None = None,
           max_terms: int = 8) -> list[str]:
    """从自由文本里抽软约束词（保留连字符：low-dose / ai-assisted）。

    `exclude` 传病种短语里已有的词——否则会出现 病种="lung cancer screening" 而
    人群约束是 (lung OR cancer OR screening) 这种情况：凡命中病种的必然命中人群，
    约束等于没加。
    """
    exclude = exclude or set()
    out: list[str] = []
    for t in texts:
        for tok in _clean(t).lower().split():
            tok = tok.strip(".,;/")
            if len(tok) < 2 or tok in _STOP or tok in exclude or tok in out:
                continue
            out.append(tok)
            if len(out) >= max_terms:
                return out
    return out


def _doctype_and_tier(pubtypes: str, default_type: str, default_tier: int):
    """出版类型 → (document_type, tier)。命中层的默认值兜底。"""
    t = (pubtypes or "").lower()
    if "systematic review" in t or "systematic-review" in t or "meta-analysis" in t:
        return "systematic_review", 2
    if "practice guideline" in t or "guideline" in t:
        return "guideline", 1
    if "consensus" in t:
        return "consensus", 2
    return default_type, default_tier


class EuropePMCConnector(Connector):
    source_id = "europepmc"
    issuing_body = "Europe PMC"
    source_role = "discovery"
    tier = 5
    machine_access = "api"

    # ---------- 底层调用 ----------
    def _fetch(self, query: str, n: int) -> list[dict]:
        """不传 sort → Europe PMC 默认按相关度排序（旧实现按发表日倒序，是噪声元凶）。"""
        if n <= 0:
            return []
        params = {"query": query, "format": "json",
                  "pageSize": min(max(n, 1), 25), "resultType": "core"}
        try:
            r = requests.get(API, params=params, headers={"User-Agent": UA}, timeout=45)
            r.raise_for_status()
            return r.json().get("resultList", {}).get("result", [])
        except Exception as e:
            print(f"  [europepmc] 查询失败: {str(e)[:80]}")
            return []

    # ---------- 查询构造 ----------
    @staticmethod
    def _date_clause(submission_date: str | None, after: bool) -> str:
        if not submission_date:
            return ""
        d = str(submission_date)[:10]
        return (f' AND (FIRST_PDATE:[{d} TO 2999-12-31])' if after
                else f' AND (FIRST_PDATE:[1900-01-01 TO {d}])')

    def _build(self, core: str, stratum_clause: str | None,
               submission_date: str | None, after: bool = False) -> str:
        q = core + (f' AND {stratum_clause}' if stratum_clause else "")
        return f'{q} AND {COMMON}' + self._date_clause(submission_date, after)

    # ---------- 主入口 ----------
    def _rungs(self, query_context) -> list[tuple[str, str]]:
        """收紧程度阶梯，从最严到最松。逐层降级，直到某档有命中。"""
        cond = query_context.get("condition") or query_context.get("population", "")
        cond_phrase = _phrase(cond)
        cond_tokens = set(_clean(cond).lower().split())

        # 干预软约束：干预 + 模型输入/输出
        soft = _or_clause(_terms(query_context.get("intervention", ""),
                                 query_context.get("model_input", ""),
                                 query_context.get("model_output", ""),
                                 exclude=cond_tokens))
        # 人群约束：实测能把成人脓毒症卡的儿科指南、肺癌卡的地区性共识挤掉，
        # 换成 ESICM 成人脓毒症 CPG / ACS 肺癌筛查指南。
        pop = _or_clause(_expand_plural(
            _terms(query_context.get("population", ""),
                   query_context.get("setting", ""),
                   exclude=cond_tokens, max_terms=5)))

        rungs: list[tuple[str, str]] = []
        if cond_phrase:
            for name, parts in [("strict", [cond_phrase, soft, pop]),
                                ("no_pop", [cond_phrase, soft]),
                                ("no_interv", [cond_phrase, pop]),
                                ("phrase", [cond_phrase])]:
                core = " AND ".join(p for p in parts if p)
                if core not in {c for _, c in rungs}:
                    rungs.append((name, core))
        # 兜底：病种词**全部 AND**（不是旧的松散裸词串）。短语档落空多半是因为卡里
        # 病种写得不规范（如 'sepsis (severe) "shock" [ICU]'），逐词 AND 仍锁得住主题；
        # 换成裸词串会检回"鼻窦炎指南"这种东西——宁可空手也不给审稿模型喂垃圾。
        toks = _terms(cond, max_terms=6)
        if toks:
            core = " AND ".join(f'TITLE_ABS:"{t}"' for t in toks)
            if core not in {c for _, c in rungs}:
                rungs.append(("tokens_and", core))
        return rungs

    def search(self, query_context, submission_date=None, limit=5):
        rungs = self._rungs(query_context)
        if not rungs:
            return []

        # 配额：指南层优先。未填满的配额顺延给下一层。
        quotas, left = {}, limit
        for name, _, _, _, w in STRATA[:-1]:
            quotas[name] = min(left, max(1, round(limit * w)))
            left -= quotas[name]
        quotas[STRATA[-1][0]] = max(0, left)

        seen: set[str] = set()
        out: list[ExternalStandard] = []

        def collect(hits, stratum, dtype, dtier, want, note_extra=""):
            n = 0
            for h in hits:
                if n >= want:
                    break
                key = h.get("doi") or h.get("pmid") or h.get("id") or h.get("title", "")
                if key in seen:
                    continue
                seen.add(key)
                out.append(self._to_record(h, stratum, dtype, dtier,
                                           submission_date, query_context, note_extra))
                n += 1
            return n

        # 主检索：predates=true 的证据（论文投稿时已存在，才可用来要求作者）。
        # 阶梯**按层各走各的**——指南远比普通文献稀少，用同一档严格度会把指南层饿死。
        used: dict[str, str] = {}
        carry = 0
        for name, clause, dtype, dtier, _ in STRATA:
            want = quotas[name] + carry
            got = 0
            for rung_name, core in rungs:
                got = collect(self._fetch(self._build(core, clause, submission_date),
                                          max(want * 2, 10)),
                              name, dtype, dtier, want)
                if got:
                    used[name] = core
                    if rung_name != rungs[0][0]:
                        print(f"  [europepmc] {name} 层降级到 '{rung_name}' 档")
                    break
            carry = want - got          # 该层不够，配额顺延到下一层

        # 投稿后桶：只收指南/综述——投稿后才出的普通文献既不能用来要求作者，
        # 也不构成规范，收进来纯噪声。指南/综述可用于"今天能否部署"评价。
        if submission_date:
            want_post = max(1, limit // 4)
            for name, clause, dtype, dtier, _ in STRATA[:2]:
                core = used.get(name) or (rungs[0][1] if rungs else None)
                if want_post <= 0 or not core:
                    break
                want_post -= collect(
                    self._fetch(self._build(core, clause, submission_date, after=True),
                                max(want_post * 2, 10)),
                    name, dtype, dtier, want_post,
                    note_extra="；投稿后发布，仅供'今天能否部署'评价，不得据此指责作者")
        return out

    # ---------- 记录映射 ----------
    def _to_record(self, h, stratum, dtype, dtier, submission_date,
                   query_context, note_extra="") -> ExternalStandard:
        pubdate = h.get("firstPublicationDate")
        doi = h.get("doi")
        pmid = h.get("pmid") or h.get("id")
        url = (f"https://doi.org/{doi}" if doi
               else f"https://europepmc.org/article/{h.get('source','MED')}/{pmid}")
        ptl = h.get("pubTypeList", {})
        pts = ptl.get("pubType", []) if isinstance(ptl, dict) else []
        if isinstance(pts, str):
            pts = [pts]
        doc_type, tier = _doctype_and_tier(" ".join(pts), dtype, dtier)

        note = f"发现层(检索分层={stratum})：命中≠临床标准，须回到全文核验"
        if doc_type in ("guideline", "consensus"):
            # 只有题录+摘要，拿不到条文原文，因此仍是 discovery 而非 normative。
            note += "；候选 normative 文档，须策展摄入全文后方可作为规范条目引用"
        return ExternalStandard(
            source_id=self.source_id,
            issuing_body=h.get("journalTitle") or self.issuing_body,
            document_type=doc_type,
            title=h.get("title", ""),
            canonical_url=url,
            version_or_publication_date=pubdate,
            retrieved_date=self.today(),
            recommendation_or_requirement=None,   # 摘要不含条文，不得伪造要求
            passage=(h.get("abstractText", "") or "")[:800] or None,
            section_page_table=f"PMID:{pmid}" + (f" DOI:{doi}" if doi else ""),
            source_role=self.source_role,
            tier=tier,
            machine_access=self.machine_access,
            license="open" if h.get("isOpenAccess") == "Y" else "publisher",
            source_quality="discovery/needs_verification",
            predates_paper_submission=compute_predates(pubdate, submission_date),
            notes=note + note_extra,
            query_context=query_context,
        )
