"""策展摄入层·学会/国家临床指南 (source_role = normative)。

读 curated/guidelines/cpg_*.yaml（由 guideline_ingest.py 按 manifest 摄入），
按 Claim Card 做**病种 + 人群 + 场景**门控，再按与本卡的相关度挑推荐条目。

三层与 USPSTF 的分工（同为 normative 角色，覆盖面互补，见 DESIGN §6c/§6d）：
    USPSTF          → 预防与筛查（谁该被筛、多久筛一次）
    学会/国家 CPG   → 急重症与治疗（报警之后该做什么、多快做、跟谁比）
一篇论文往往只落在其中一侧；两边都要能说出"我为什么不适用"。

门控设计（三条，严格程度递减）：
  ① 病种：判别词必须命中 scope.disease_terms —— 泛化词(cancer/screening/risk)不计入。
  ② 人群：成人指南 vs 儿科/新生儿论文 → **硬拦**。这是临床上最常见的越权外推，
     且极易发生（"sepsis" 在成人与新生儿是两套完全不同的标准）。
  ③ 场景：只**软提示**不拦截。ICU 指南用于普通病房论文并非无效，但外推需说明，
     所以写进 notes 让审稿端看得见，而不是替它决定。
"""
from __future__ import annotations

import glob
import os
import re

import yaml

from base import Connector, discriminative_terms, exclusion_overlap, expand_plural
from schema import ExternalStandard, compute_predates

GUIDE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "curated", "guidelines")

# 人群标记：儿科/新生儿 vs 成人。与 who_gho.py 的人群相符性检查同一动机。
_PED = ("pediatric", "paediatric", "child", "children", "infant", "neonat",
        "newborn", "adolescent", "school-age", "preterm")
_ADULT = ("adult", "elderly", "older", "geriatric", "aged")

# 推荐条目 → 审查模块。按推荐句里实际出现的东西判定，不给所有条目贴同一组模块。
_MODULE_RULES = [
    ("comparator_baseline", r"\b(rather than|over|compared|versus|vs\.?|preference to|"
                            r"superior|inferior|instead of|first-line|second-line)\b"),
    ("endpoint_utility", r"\b(mortality|survival|death|length of stay|outcome|morbidity|"
                         r"readmission|complication rate)\b"),
    ("workflow_deployment", r"\b(within \d+\s*(hour|hours|h|minute|min)|bundle|protocol|"
                            r"activation|triage|escalat|monitor|rota|pathway|time zero|"
                            r"as soon as|immediately)\b"),
    ("safety_harm_equity", r"\b(harm|adverse|bleeding|toxicit|contraindicat|risk of|"
                           r"avoid|should not|refrain)\b"),
    ("population_validity", r"\b(elderly|aged \d+|frail|pregnan|children|patients with "
                            r"risk factors|comorbid)\b"),
]


def _tokens(*vals) -> set[str]:
    out = set()
    for v in vals:
        if isinstance(v, (list, tuple, set)):
            out |= _tokens(*v)
        elif v:
            out |= {t for t in re.split(r"[^a-z0-9\-]+", str(v).lower()) if len(t) >= 3}
    return out


def load_guidelines(directory: str = GUIDE_DIR) -> list[dict]:
    """只加载 cpg_*.yaml —— uspstf.yaml 同在此目录，但有自己的连接器与门控。"""
    docs = []
    for p in sorted(glob.glob(os.path.join(directory, "cpg_*.yaml"))):
        d = yaml.safe_load(open(p))
        d["_path"] = os.path.relpath(p, os.path.dirname(os.path.dirname(p)))
        docs.append(d)
    return docs


def check_population(card: dict, scope: dict) -> tuple[bool, str]:
    blob = " ".join(str(card.get(k) or "") for k in
                    ("target_population", "disease_or_condition", "intended_use")).lower()
    card_ped = any(k in blob for k in _PED)
    card_adult = any(k in blob for k in _ADULT)
    gpop = str(scope.get("population") or "").lower()
    g_ped = any(k in gpop for k in _PED)
    g_adult = any(k in gpop for k in _ADULT)
    if card_ped and g_adult and not g_ped:
        return False, "人群不符：本卡为儿科/新生儿人群，该指南限成人 —— 成人标准外推到儿科属越权"
    if card_adult and g_ped and not g_adult:
        return False, "人群不符：本卡为成人人群，该指南限儿科"
    return True, f"人群相符（指南人群={gpop or '未声明'}）"


def match_disease(card: dict, scope: dict) -> set[str]:
    """病种匹配。空集 = 不适用。

    **多词条目按整词组匹配，单词条目才按词匹配。**否则把 scope 里的
    "lung ultrasound" 拆成 lung/ultrasound，一篇肺癌 CT 论文就会因为一个 lung
    命中儿科床旁超声指南（实测发生过，随后才被人群门控拦下——靠第二道门兜住
    第一道门的错，是运气不是设计）。同 base.py: GENERIC_CLINICAL 的教训。
    """
    card_text = " ".join(str(card.get(k) or "") for k in
                         ("disease_or_condition",)).lower()
    gate = set(expand_plural(sorted(discriminative_terms(card.get("disease_or_condition")))))
    hits = set()
    for term in (scope.get("disease_terms") or []):
        t = str(term).lower().strip()
        if " " in t or "-" in t:                       # 词组：整体出现才算
            if t in card_text or t.rstrip("s") in card_text:
                hits.add(t)
        else:                                          # 单词：进判别词集才算
            if t in gate or (t + "s") in gate or t.rstrip("s") in gate:
                hits.add(t)
    return hits


def setting_note(card: dict, scope: dict) -> str | None:
    cs = str(card.get("care_setting") or "").lower()
    gs = [str(s).lower() for s in (scope.get("care_settings") or [])]
    if not cs or not gs:
        return None
    if any(_tokens(cs) & _tokens(g) for g in gs):
        return None
    return (f"⚠️ 场景外推：指南适用场景 {gs}，本卡场景为「{cs}」。"
            f"推荐本身仍可作为标准，但迁移到该场景须由论文另行论证（不代为裁决）。")


def rank_recommendations(card: dict, recs: list[dict], limit: int) -> list[tuple[dict, int]]:
    """按与本卡的相关度排序：与干预/输出/受影响决策/宣称获益/对照的词重叠。

    卡里这些字段说的是"这篇论文声称要改变什么"，指南里最该拿出来的就是
    **管着同一件事**的推荐（AI 预警声称缩短抗生素时间 → 抗生素时机那条）。
    """
    want = discriminative_terms(
        card.get("intended_use"), card.get("model_output"),
        card.get("clinical_decision_affected"), card.get("claimed_benefit"),
        card.get("comparator"))
    want = set(expand_plural(sorted(want)))
    # 病种词作次要信号：指南按器官/应用分节时（如 POCUS 指南），干预词常与推荐句
    # 用词对不上（卡里写 ultrasound、条文里写 POCUS），全 0 分会退化成任意顺序。
    disease = set(expand_plural(sorted(discriminative_terms(
        card.get("disease_or_condition"), card.get("model_input")))))
    scored = []
    for r in recs:
        hay = f"{r.get('statement','')} {r.get('context') or ''}".lower()
        hits = {w for w in want if re.search(rf"\b{re.escape(w)}", hay)}
        dhits = {w for w in disease - want if re.search(rf"\b{re.escape(w)}", hay)}
        # 有分级的条目略微优先：能给出强度/确定性的推荐在审稿里更可用
        graded = 1 if (r.get("recommendation_strength") or r.get("evidence_certainty")) else 0
        scored.append((r, len(hits) * 10 + len(dhits) * 3 + graded, hits | dhits))
    scored.sort(key=lambda x: -x[1])
    return [(r, s) for r, s, _ in scored[:limit]]


def modules_for(text: str) -> list[str]:
    mods = ["clinical_question"]
    low = text.lower()
    for mod, pat in _MODULE_RULES:
        if re.search(pat, low):
            mods.append(mod)
    return mods


class CuratedGuidelinesConnector(Connector):
    source_id = "society_guidelines"
    issuing_body = "学会 / 国家临床实践指南（逐份见记录内 issuing_body）"
    source_role = "normative"
    tier = 1
    machine_access = "curated"

    def __init__(self, directory: str = GUIDE_DIR):
        self.docs = load_guidelines(directory)

    def available(self) -> bool:
        return bool(self.docs)

    def _judge(self, card: dict, doc: dict) -> tuple[bool, str, set]:
        scope = doc.get("scope") or {}
        terms = [str(t).lower() for t in (scope.get("disease_terms") or [])]
        excl_ov = exclusion_overlap(terms, card)
        if excl_ov and len(excl_ov) == len(terms):        # 整份指南管的就是论文排除的病
            return False, (f"该指南 scope {terms} 全部落在本卡明确排除的病种 "
                           f"{card.get('excluded_conditions')} 内 —— 论文既已排除，"
                           f"不得据此提要求"), set()
        hits = match_disease(card, scope)
        if not hits:
            return False, (f"病种不匹配：本卡判别词与该指南 scope.disease_terms "
                           f"{scope.get('disease_terms')} 无交集"), set()
        ok, why = check_population(card, scope)
        if not ok:
            return False, why, hits
        note = ""
        if excl_ov:                                       # 部分重叠 → 提示不硬拦
            note = (f"；⚠️ 该指南 scope 还覆盖本卡明确排除的病种 {excl_ov}，"
                    f"引用其推荐时须确认不是针对被排除人群的那部分")
        return True, f"病种命中 {sorted(hits)}；{why}{note}", hits

    def report(self, card: dict) -> list[dict]:
        out = []
        for d in self.docs:
            ok, why, _ = self._judge(card, d)
            recs = d.get("recommendations") or []
            out.append({
                "slug": d["slug"],
                "name": d.get("short_name") or d.get("name"),
                "issuing_body": d.get("issuing_body"),
                "publication_date": d.get("publication_date"),
                "applicable": ok, "reason": why,
                "n_recs": len(recs) if ok else 0,
                "n_total": len(recs),
                # curated=人工确认过 scope 与发布机构；auto=自动扩库产物（见
                # guideline_autocurate.py）。许可与逐字原文两条腿一样硬，差别在
                # scope 是机器草稿——必须让下游看得见，不能混作一谈。
                "curation_level": d.get("curation_level", "curated"),
                "needs_review": bool(((d.get("provenance") or {})
                                      .get("yield_audit") or {}).get("flags")),
                "completeness": (d.get("provenance") or {}).get("completeness"),
                "setting_note": setting_note(card, d.get("scope") or {}) if ok else None,
            })
        return out

    def search(self, query_context, submission_date=None, limit: int = 4):
        card = query_context.get("_card") or query_context
        qc = {k: v for k, v in query_context.items() if not k.startswith("_")}
        out: list[ExternalStandard] = []
        for d in self.docs:
            ok, why, hits = self._judge(card, d)
            if not ok:
                continue
            prov = d.get("provenance") or {}
            recs = d.get("recommendations") or []
            picked = rank_recommendations(card, recs, limit)
            # 条目在本指南推荐列表里的序号。用来给 section_page_table 兜底唯一性：
            # 表格策略抽出的条目，section 是「表：caption」、context 也是同一个 caption，
            # 整张表所有行的 section_page_table 完全相同 → retrieve.py 的
            # (source_id, url, section_page_table) 去重会把**整份指南塌成 1 条**
            # （脓毒症卡 4 条其实是 4 份指南各剩 1 条，不是取了 4 条推荐）。
            # 与 §6b 报告清单当初加 section_page_table 是同一个坑的另一半。
            order = {id(r): i for i, r in enumerate(recs)}
            s_note = setting_note(card, d.get("scope") or {})
            pub = d.get("publication_date")
            predates = compute_predates(pub, submission_date)
            for r, score in picked:
                strength = r.get("recommendation_strength") or r.get("grade_letter")
                notes = [
                    f"适用性判定：{why}。",
                    f"相关度：{score}（按干预/结局/对照词与本卡的重叠排序；"
                    f"本指南共 {len(recs)} 条结构化推荐，取前 {len(picked)} 条）。",
                    f"许可：{prov.get('license_note')}",
                    f"覆盖边界：{prov.get('completeness_note')}",
                ]
                if r.get("grade_letter") and r.get("recommendation_strength"):
                    notes.append(f"原文分级标记：{r['grade_letter']}")
                if s_note:
                    notes.append(s_note)
                if r.get("agreement"):
                    notes.append(f"专家一致程度：{r['agreement']}（这是共识度，**不是**推荐强度，"
                                 f"不可当作 GRADE 强度使用）")
                if not strength:
                    notes.append("⚠️ 该条未解析出推荐强度 —— 原文未在推荐句内给 GRADE 标注，"
                                 "不得替它假定强度。")
                if d.get("curation_level") == "auto":
                    aud = (prov.get("yield_audit") or {})
                    notes.append(
                        "🤖 自动扩库产物：许可与逐字原文与人工腿同标准，但**适用范围"
                        "（scope：病种/人群/场景）为机器生成草稿，未经人工核验**，"
                        "发布机构亦未确认。据此提出的要求应标为待核实，不可作为"
                        "已确认的规范依据。" +
                        (f" 抽取覆盖率告警：{aud['flags']}" if aud.get("flags") else ""))
                out.append(ExternalStandard(
                    source_id=self.source_id,
                    issuing_body=d.get("issuing_body") or self.issuing_body,
                    document_type="guideline",
                    title=f"{d.get('short_name') or d.get('name')} — "
                          f"{(r.get('context') or r.get('section') or '')[:70]}",
                    canonical_url=prov.get("canonical_url"),
                    version_or_publication_date=pub,
                    retrieved_date=self.today(),
                    region=d.get("region"),
                    target_population=(d.get("scope") or {}).get("population"),
                    care_setting=", ".join((d.get("scope") or {}).get("care_settings") or []),
                    intended_use_or_decision_point=r.get("context"),
                    recommendation_or_requirement=r.get("statement"),
                    recommendation_strength=strength,
                    evidence_certainty=r.get("evidence_certainty"),
                    passage=r.get("statement"),          # 逐字原文
                    section_page_table=(f"{r.get('section')} | 第 {order[id(r)] + 1} 条 | "
                                        f"{(r.get('context') or '')[:60]}"),
                    license=prov.get("license"),
                    machine_access=self.machine_access,
                    source_role=self.source_role,
                    tier=d.get("tier", self.tier),
                    predates_paper_submission=predates,
                    notes=" | ".join(n for n in notes if n),
                    modules=modules_for(f"{r.get('statement','')} {r.get('context') or ''}"),
                    query_context=qc,
                ))
        return out
