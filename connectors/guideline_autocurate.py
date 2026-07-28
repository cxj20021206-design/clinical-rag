"""Claim Card 驱动的指南自动扩库（腿 3 的覆盖面问题）。

`normative` 是按病种组织的，而人工策展一份一份加，永远追不上论文的病种分布
（现有 4 份 CPG 只覆盖脓毒症/院内肺炎/创伤/新生儿 POCUS，心衰、卒中、糖网、AKI
这些常见 MedAI 方向一份都没有）。本脚本把 §6d 那条摄入流水线从"人工写 manifest
条目触发"改成"**按 Claim Card 的病种缺口触发**"：

    读卡 → 查本地覆盖 → 无覆盖则按病种检索 OA 指南候选 → 许可硬门 →
    抓全文抽推荐 → 抽全性核查 → 自动生成 scope 草稿 → 写 cpg_auto_*.yaml

**机器那半边本来就是全自动的**（guideline_fetch.py：候选检索/许可判定/全文抓取/
三策略抽取/GRADE 解析），人工的只剩 manifest 里的 slug / issuing_body / scope。
所以这个脚本新增的只有三件事：覆盖检测、抽全性核查、scope 草稿生成。

三条边界（都是铁律的延续，不因为"自动"而放松）：

1. **许可门不放松**：仍以 Europe PMC 的结构化 `license` 字段为准，空 = 未授权。
   绝不从正文/页脚推断许可——判错许可的后果是法律的，不是召回率的。
2. **自动摄入必须自报身份**：产物带 `curation_level: auto`，检索时连接器会在
   notes 里写明"scope 未经人工核验"。铁律靠**如实标注**来守，不靠"不确定就排除"
   ——后者的实际后果是 normative 永远只有 4 个病种。
3. **抽全性要主动核查**：`recommendation_count_minimum: 3` 挡不住静默截断
   （ESPNIC 那次错误实现抽 10 条、正确实现 41 条，两个都 ≥3、都过门、都不报错）。
   这里把"结构槽位数"与实际抽出条数做交叉核对，差得多就标 needs_review。

用法：
    # 只看能捞到什么，不写盘（默认）
    python3 connectors/guideline_autocurate.py --claim examples/claim_card_hf.yaml
    # 不写卡，直接按病种探覆盖率
    python3 connectors/guideline_autocurate.py --disease "heart failure" --population adult
    # 确认后写入 curated/guidelines/cpg_auto_*.yaml + manifest_auto.yaml
    python3 connectors/guideline_autocurate.py --claim ... --write

自动化解决的是"没人去策展"，解决不了"许可拿不到"——AHA/ACC、ESC、SSC 这些最权威的
指南大多不是 CC-BY，它们不会因为这个脚本而进库。所以跑完之后缺口报告不会消失，
只会从"这个病种没人管过"变成"这个病种只有二线指南可用"。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import yaml                                                        # noqa: E402
from base import STOPWORDS, discriminative_terms                   # noqa: E402
from guideline_fetch import (_REC_CAPTION, _REC_START,             # noqa: E402
                             _REC_TITLE, _header_map, _txt,
                             extract, fetch_xml, license_ok,
                             search_candidates)
from guideline_ingest import GUIDE_DIR, MANIFEST, ingest_one       # noqa: E402
from curated_guidelines import (_ADULT, _PED, load_guidelines,     # noqa: E402
                                match_disease)
from uspstf import USPSTFConnector, check_setting, match_topics    # noqa: E402

AUTO_MANIFEST = os.path.join(GUIDE_DIR, "manifest_auto.yaml")

# 场景词表：从指南标题/推荐原文里认场景。与 curated_guidelines.setting_note 用的
# scope.care_settings 取值对齐（那边只做词重叠的软提示，词形不必完全一致）。
_SETTING_KW = {
    "intensive care unit": ("intensive care", "icu", "critically ill", "critical care"),
    "emergency department": ("emergency department", "emergency room", " ed ", "prehospital"),
    "inpatient": ("inpatient", "hospitalized", "hospitalised", "in-hospital", "ward"),
    "primary care": ("primary care", "general practice", "family medicine"),
    "outpatient": ("outpatient", "ambulatory", "clinic"),
    "perioperative": ("perioperative", "operating room", "intraoperative", "postoperative"),
    "community": ("community", "population-based", "public health"),
}


# --------------------------------------------------------------------------
# ① 覆盖检测：这张卡的病种，本地 normative 库到底管不管
# --------------------------------------------------------------------------
def check_coverage(card: dict) -> dict:
    """跑一遍与检索时**完全相同**的门控，看有没有现成指南接得住这张卡。

    刻意复用 curated_guidelines.match_disease 与 uspstf.match_topics，而不是另写一套
    ——否则"自动扩库判定为没覆盖"和"检索时判定为没命中"会不一致，扩了库照样命中不了。
    """
    hits = []
    for d in load_guidelines():
        m = match_disease(card, d.get("scope") or {})
        if m:
            hits.append({"slug": d["slug"], "kind": "cpg",
                         "name": d.get("short_name") or d.get("name"),
                         "terms": sorted(m),
                         "curation_level": d.get("curation_level", "curated")})
    u = USPSTFConnector()
    if u.available():
        ok, _ = check_setting(card)
        if ok:
            matched, _terms = match_topics(card, u.doc)
            for t, _s, h in matched:
                hits.append({"slug": t.get("slug") or t["title"], "kind": "uspstf",
                             "name": t["title"], "terms": sorted(h),
                             "curation_level": "curated"})
    return {"covered": bool(hits), "by": hits}


# --------------------------------------------------------------------------
# ② 查询构造：病种短语 → Europe PMC 查询片段
# --------------------------------------------------------------------------
def core_phrases(text: str, max_n: int = 3) -> list[str]:
    """从自由文本里取"像病种名"的短语：连续 n-gram，且至少含一个判别词。

    单词不能直接当短语用——scope 里的多词条目是按整词组匹配的，而单个泛化词
    （failure / acute）当病种词会满库乱撞。所以这里只产出 2-3 词短语，
    单词只有在整个病种字段就是一个词时才保留（如 "sepsis"）。
    """
    disc = discriminative_terms(text)
    toks = [t for t in re.split(r"[^a-z0-9\-]+", str(text or "").lower()) if t]
    if not toks:
        return []
    if len(toks) == 1 and toks[0] in disc:
        return toks
    out: list[str] = []
    for n in range(min(max_n, len(toks)), 1, -1):
        for i in range(len(toks) - n + 1):
            gram = toks[i:i + n]
            if any(g in STOPWORDS or len(g) < 3 for g in gram):
                continue
            if not any(g in disc for g in gram):
                continue
            p = " ".join(gram)
            if not any(p in o for o in out):        # 已被更长的短语包含就不重复收
                out.append(p)
    return out


def build_query(card: dict) -> tuple[str, list[str]]:
    ph = core_phrases(card.get("disease_or_condition"))
    if not ph:
        raise SystemExit("Claim Card 的 disease_or_condition 抽不出病种短语，无法检索")
    terms = " OR ".join(f'TITLE_ABS:"{p}"' for p in ph[:3])
    return terms, ph


def title_covers(phrases: list[str], title: str) -> tuple[bool, list[str]]:
    """病种短语必须出现在**标题**里，摘要里提到不算。

    检索用 TITLE_ABS（要召回），准入只看 TITLE（要准确）。第一版没有这道门，
    结果心衰卡捞回来的三份"可入库"文档是两份肥胖药物治疗指南和一份 UK 肾脏病
    SGLT-2 指南——它们只是在摘要里提到心衰获益。更糟的是 scope 草稿会把
    `heart failure` 写进这三份的 `disease_terms`，从此**任何心衰论文都会命中一份
    肥胖指南**，而且是以 normative（"你应该做到什么"）的身份。
    自动腿把错误固化进库里，比人工腿漏一份指南严重得多。
    """
    t = (title or "").lower()
    hit = [p for p in phrases if p in t or p.rstrip("s") in t]
    return bool(hit), hit


# --------------------------------------------------------------------------
# ③ 抽全性核查：结构槽位数 vs 实际抽出条数
# --------------------------------------------------------------------------
def structural_slots(xml: str) -> dict:
    """数"本文里有多少个看起来该是推荐的结构位置"，**不带内容过滤**。

    这是 §6d ESPNIC 教训的机器化：那次表格抽取按行内推荐动词过滤，41 条只抽出 10 条，
    丢光了全部肺部推荐（原文写 "POCUS is helpful to…"，本来就没有推荐动词），而
    ≥3 条的门禁照样放行、没有任何报错。跨策略产量比较**也救不了**——错的是同一个
    策略内部的过滤器。唯一能发现它的办法是拿"结构槽位"这个上界去对账。
    """
    root = ET.fromstring(xml)
    body = root.find(".//body")
    if body is None:
        return {}
    slots = {"rec_sections": 0, "rec_boxes": 0, "rec_tables": 0}
    for parent in body.iter("sec"):
        for sec in parent.findall("sec"):
            ti = sec.find("title")
            if ti is not None and _REC_TITLE.match(_txt(ti)):
                slots["rec_sections"] += 1
        for b in parent.findall("boxed-text"):
            if _REC_START.match(_txt(b)):
                slots["rec_boxes"] += 1
    for tw in body.iter("table-wrap"):
        cap = _txt(tw.find("caption")) if tw.find("caption") is not None else ""
        if not _REC_CAPTION.search(cap):
            continue
        idx, hdr_i = _header_map(tw)
        rows = [tr for i, tr in enumerate(tw.iter("tr"))
                if not (idx is not None and i == hdr_i)]
        slots["rec_tables"] += len(rows)
    return slots


_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def fulltext_diagnosis(xml: str) -> dict:
    """抽不出条目时，说清楚到底是哪一种"抽不出"。三种成因的处置完全不同：

      no_fulltext  —— OPEN_ACCESS:y 但 EPMC 实际只有摘要/短摘要（如某些指南的
                      翻译版摘要）。这是取数问题，换通道可能有救。
      language     —— 全文在但非英语（西班牙语 CPG 的 "Tipos de recomendación"、
                      德国 S3 的 "Empfehlungsgrad"）。要写对应语种的结构标记才行，
                      是 DESIGN §7 1b 里已经登记的缺口。
      structure    —— 全文在、英语、就是没有推荐节/框/表（综述、专家意见文章，
                      或推荐散在正文里）。这类**不该救**，抓句会把转述句抓成本文推荐。
    """
    root = ET.fromstring(xml)
    lang = (root.get(_XML_LANG) or "en").lower()
    body = root.find(".//body")
    blen = len(_txt(body)) if body is not None else 0
    if blen < 6000:
        return {"blocker": "no_fulltext", "lang": lang,
                "note": f"Europe PMC 标记 OA，但全文正文只有 {blen} 字符 —— 多半只是摘要/译文摘要。"}
    if not lang.startswith("en"):
        return {"blocker": "language", "lang": lang,
                "note": f"全文可得（{blen} 字符）但语种为 {lang} —— 需要该语种的推荐结构标记，"
                        f"同德国 S3 脓毒症指南（DESIGN §7 1b）。"}
    return {"blocker": "structure", "lang": lang,
            "note": f"全文可得（{blen} 字符、英语）但无推荐节/推荐框/推荐汇总表 —— "
                    f"推荐散在正文里，关键词抓句会把转述他人指南的句子抓成本文推荐，不降级处理。"}


def yield_audit(res: dict, xml: str, floor: float = 0.6) -> dict:
    """抽出条数 / 该策略的结构槽位数。低于 floor → needs_review，不静默入库。"""
    slots = structural_slots(xml)
    strat = res["strategy"]
    n = len(res["recommendations"])
    slot = slots.get(strat, 0)
    ratio = (n / slot) if slot else None
    flags = []
    if slot and ratio is not None and ratio < floor:
        flags.append(f"抽取覆盖率 {n}/{slot}={ratio:.0%} 低于 {floor:.0%}"
                     f"——可能存在静默截断（过滤器把没有推荐动词的条目丢掉了），须人工比对原文")
    return {"slots": slots, "extracted": n, "slot_of_strategy": slot,
            "coverage_ratio": ratio, "flags": flags}


# --------------------------------------------------------------------------
# ④ scope 草稿生成
# --------------------------------------------------------------------------
def gen_scope(card: dict, title: str, recs: list[dict]) -> dict:
    """生成 disease_terms / population / care_settings 草稿。

    判错 scope 的后果是**召回**（该启用的指南没启用、不该启用的被启用），不是失真
    ——引用的原文仍然逐字、许可仍然合规。所以它可以自动生成 + 事后抽查，
    而许可那道门不行。三条产出规则：

    · disease_terms 必须包含**触发本次检索的卡片病种短语**：这份指南是被它检出来的，
      不放进去会出现"扩了库但触发它的那张卡自己匹配不上"的荒唐情况。
    · population 只在证据单向时才写：标题/推荐里只见成人或只见儿科才声明，
      两者都有或都没有就留空——`check_population` 只在**明确冲突**时硬拦，
      留空 = 不拦，这是安全侧。乱写人群会把整份指南永久拦死。
    · care_settings 宁多勿少：它只做软提示不拦截，写少了反而丢掉"场景外推"警告。
    """
    blob = " ".join([title or ""] + [r.get("statement") or "" for r in recs[:40]]).lower()

    # 病种词**只从标题取**（含已通过 title_covers 的触发短语）。不从摘要/推荐正文取：
    # 一份肥胖指南的推荐里满是 "heart failure"，那是获益描述，不是它的适用病种。
    terms: list[str] = []
    for p in [p for p in core_phrases(card.get("disease_or_condition"))
              if p in (title or "").lower()]:
        if p not in terms:
            terms.append(p)
    for p in core_phrases(title, max_n=3):
        if len(terms) >= 8:
            break
        if p not in terms and not any(p in t for t in terms):
            terms.append(p)

    ped = any(k in blob for k in _PED)
    adult = any(k in blob for k in _ADULT)
    population = "adult" if (adult and not ped) else ("pediatric" if (ped and not adult) else None)

    settings = [name for name, kws in _SETTING_KW.items() if any(k in blob for k in kws)]

    return {"disease_terms": terms[:8],
            "care_settings": settings,
            "population": population,
            "topic": (title or "")[:160],
            "_auto": True}


_SOCIETY = re.compile(
    r"((?:American|European|British|Canadian|Australian|Japanese|Korean|Chinese|"
    r"German|French|Italian|Spanish|Brazilian|International|World|National|Global)"
    r"[A-Za-z \-]{0,60}?(?:Society|Association|College|Federation|Academy|Alliance|"
    r"Task Force|Organization|Organisation|Institute|Council|Group|Consortium))")


def guess_issuer(title: str, meta: dict, journal: str | None) -> tuple[str, bool]:
    """从标题里认发布机构。认不出就退回期刊名并**标记未确认**——
    manifest.yaml 头部写明 issuing_body 要人来确认（不取期刊名），
    自动腿不能假装做到了这一点，只能如实说"没确认"。"""
    m = _SOCIETY.search(title or "")
    if m:
        return m.group(1).strip(), True
    pub = meta.get("publisher") or journal or "未知"
    return f"{pub}（自动摄入：发布机构未确认，须人工核对）", False


def slugify(title: str, pmcid: str) -> str:
    toks = [t for t in re.split(r"[^a-z0-9]+", (title or "").lower())
            if t and t not in STOPWORDS and len(t) > 2][:5]
    return "_".join(toks) or pmcid.lower()


# --------------------------------------------------------------------------
# ⑤ 主流程
# --------------------------------------------------------------------------
def known_pmcids() -> dict[str, str]:
    """人工 manifest 里已经处理过的 PMCID —— 已摄入的别重复，**已 deferred 的更要尊重**
    （人工因"无推荐结构""转述他人推荐"排除掉的，自动腿不能绕过去把它加回来）。"""
    out = {}
    if os.path.exists(MANIFEST):
        man = yaml.safe_load(open(MANIFEST)) or {}
        for e in man.get("ingested") or []:
            out[e["pmcid"]] = f"人工 manifest 已摄入 → {e['slug']}"
        for e in man.get("deferred") or []:
            out[e["pmcid"]] = f"人工 manifest 已排除 [{e.get('blocker')}]"
    if os.path.exists(AUTO_MANIFEST):
        am = yaml.safe_load(open(AUTO_MANIFEST)) or {}
        for e in am.get("auto_ingested") or []:
            out[e["pmcid"]] = f"自动腿已摄入 → {e['slug']}"
    return out


def autocurate(card: dict, limit: int = 20, write: bool = False,
               force: bool = False, date_max: str | None = None) -> dict:
    cov = check_coverage(card)
    print(f"\n=== ① 本地 normative 覆盖检测：{card.get('disease_or_condition')} ===")
    if cov["covered"]:
        for h in cov["by"]:
            print(f"  ✅ [{h['kind']}/{h['curation_level']}] {h['name'][:64]}  命中词 {h['terms']}")
        if not force:
            print("  → 已有覆盖，不触发自动扩库（--force 可强制跑）")
            return {"covered": True, "ingested": [], "deferred": []}
        print("  → --force：仍然继续")
    else:
        print("  🕳 无任何本地指南匹配该病种 —— 触发自动扩库")

    terms, phrases = build_query(card)
    print(f"\n=== ② 检索 OA 指南候选 ===\n  病种短语 {phrases[:3]}\n  查询 {terms}")
    cands = search_candidates(terms, date_max=date_max, limit=limit)
    print(f"  Europe PMC 命中 {len(cands)} 篇 (PUB_TYPE:Guideline AND OPEN_ACCESS:y)")

    known = known_pmcids()
    ingested, deferred = [], []
    for c in cands:
        pmcid, title = c["pmcid"], (c.get("title") or "").strip()
        head = f"  · {pmcid} {title[:72]}"
        if pmcid in known:
            print(f"{head}\n      ↷ 跳过：{known[pmcid]}")
            continue
        t_ok, t_hit = title_covers(phrases, title)
        if not t_ok:
            print(f"{head}\n      ⛔ 主题：病种短语 {phrases[:2]} 只出现在摘要、不在标题 "
                  f"→ 这份指南不是管这个病的（只是提到）")
            deferred.append({"pmcid": pmcid, "title": title, "blocker": "topic_mismatch",
                             "reason": f"病种短语 {phrases[:3]} 未出现在标题中，"
                                       f"仅摘要提及；不得据此赋予它该病种的 normative 身份"})
            continue
        ok, why = license_ok(c.get("license"))
        if not ok:
            print(f"{head}\n      ⛔ 许可：{why}")
            deferred.append({"pmcid": pmcid, "title": title, "blocker": "license",
                             "license": c.get("license"), "reason": why})
            continue
        try:
            xml = fetch_xml(pmcid)
            res = extract(xml)
        except Exception as e:
            # fullTextXML 404 = EPMC 标了 OA 但拿不到全文，与"有全文没结构"是两回事
            b = "no_fulltext" if "404" in str(e) else "fetch"
            print(f"{head}\n      ⛔ {b}：{type(e).__name__}: {str(e)[:80]}")
            deferred.append({"pmcid": pmcid, "title": title, "blocker": b,
                             "reason": f"{type(e).__name__}: {str(e)[:150]}"
                                       f"（Europe PMC 标记 OA 但 fullTextXML 不可得）"})
            continue
        if not res["ok"]:
            # 区分"没全文"和"有全文但没推荐结构"——错误归类会让缺口报告变成误导
            # （同 guideline_ingest.py:50「取不到书目记录 ≠ 许可不合格」的理由）。
            diag = fulltext_diagnosis(xml)
            print(f"{head}\n      ⛔ {diag['blocker']}：{diag['note']}")
            deferred.append({"pmcid": pmcid, "title": title, "blocker": diag["blocker"],
                             "license": c.get("license"), "language": diag["lang"],
                             "reason": f"{diag['note']} 抽取产量 {res['yields']}。"})
            continue

        audit = yield_audit(res, xml)
        scope = gen_scope(card, title, res["recommendations"])
        issuer, issuer_ok = guess_issuer(title, res["meta"], c.get("journal"))
        entry = {
            "pmcid": pmcid,
            "slug": "auto_" + slugify(title, pmcid),
            "issuing_body": issuer,
            "short_name": title[:70],
            "region": None,
            "tier": 2,          # 自动摄入一律降一档：tier1 该留给人工确认过发布机构的
            "scope": {k: v for k, v in scope.items() if not k.startswith("_")},
            "notes": (f"自动摄入（Claim Card 病种缺口触发：{card.get('disease_or_condition')}）。"
                      f"scope 为机器生成草稿，未经人工核验。"),
            "_audit": audit, "_issuer_confirmed": issuer_ok,
            "_license": c.get("license"), "_date": c.get("date"),
            "_strategy": res["strategy"], "_n": len(res["recommendations"]),
        }
        mark = "⚠️" if (audit["flags"] or not issuer_ok) else "✅"
        ratio = audit["coverage_ratio"]
        ratio_s = "无槽位可对账" if ratio is None else f"{ratio:.0%}"
        print(f"{head}\n      {mark} {c.get('license')} | {c.get('date')} | "
              f"{res['strategy']}×{len(res['recommendations'])} | 槽位 {audit['slots']} "
              f"(覆盖率 {ratio_s})")
        print(f"         scope 草稿: 病种={scope['disease_terms']} 人群={scope['population']} "
              f"场景={scope['care_settings']}")
        print(f"         发布机构: {issuer}")
        for f in audit["flags"]:
            print(f"         ⚠️ {f}")
        ingested.append(entry)
        time.sleep(0.4)

    print(f"\n=== ③ 小结 ===\n  可入库 {len(ingested)} 份 / 未入库 {len(deferred)} 份")
    from collections import Counter
    if deferred:
        for b, n in Counter(d["blocker"] for d in deferred).items():
            print(f"    🕳 {b}: {n} 份")
    if write and ingested:
        write_out(ingested, deferred, card)
    elif ingested:
        print("  （dry-run：加 --write 才写盘。按设计，自动腿的产物应先由人扫一眼 scope）")
    return {"covered": cov["covered"], "ingested": ingested, "deferred": deferred}


def write_out(ingested: list[dict], deferred: list[dict], card: dict):
    """写 cpg_auto_*.yaml + manifest_auto.yaml。

    刻意**不写人工的 manifest.yaml**：那份文件是策展决定的记录，两条腿混在一个文件里，
    以后就分不清哪条 scope 是人确认过的。加上 2026-07-26 那次并行会话事故的教训，
    自动腿只碰自己的文件。
    """
    entries = []
    for e in ingested:
        clean = {k: v for k, v in e.items() if not k.startswith("_")}
        doc = ingest_one(clean, write=True)
        # 自报身份：连接器与下游都靠这两个字段判断该不该降档呈现
        path = os.path.join(GUIDE_DIR, f"cpg_{clean['slug']}.yaml")
        d = yaml.safe_load(open(path))
        d["curation_level"] = "auto"
        d["provenance"]["scope_source"] = "auto_generated_draft"
        d["provenance"]["issuer_confirmed"] = e["_issuer_confirmed"]
        d["provenance"]["yield_audit"] = e["_audit"]
        d["provenance"]["triggered_by"] = card.get("disease_or_condition")
        with open(path + ".tmp", "w") as f:
            yaml.safe_dump(d, f, allow_unicode=True, sort_keys=False, width=100)
        os.replace(path + ".tmp", path)
        entries.append({**clean, "yield_audit": e["_audit"],
                        "issuer_confirmed": e["_issuer_confirmed"],
                        "needs_review": bool(e["_audit"]["flags"]) or not e["_issuer_confirmed"]})
        print(f"  ✓ 写入 cpg_{clean['slug']}.yaml")

    man = {}
    if os.path.exists(AUTO_MANIFEST):
        man = yaml.safe_load(open(AUTO_MANIFEST)) or {}
    man.setdefault("auto_ingested", []).extend(entries)
    man.setdefault("auto_deferred", []).extend(deferred)
    with open(AUTO_MANIFEST + ".tmp", "w") as f:
        f.write("# 自动扩库（guideline_autocurate.py）的产物记录 —— **与人工 manifest.yaml 分开**。\n"
                "# 这里的 scope 是机器生成草稿；needs_review=true 的必须人工比对原文后才可信。\n"
                "# 许可门与人工腿完全相同（Europe PMC 结构化 license 字段，空 = 未授权）。\n\n")
        yaml.safe_dump(man, f, allow_unicode=True, sort_keys=False, width=100)
    os.replace(AUTO_MANIFEST + ".tmp", AUTO_MANIFEST)
    print(f"  ✓ 记录写入 {os.path.relpath(AUTO_MANIFEST, ROOT)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claim", help="Clinical Claim Card YAML")
    ap.add_argument("--disease", help="不写卡时直接给病种（探覆盖率用）")
    ap.add_argument("--population", default="")
    ap.add_argument("--setting", default="")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--date-max", default=None, help="只要该日期前发布的指南")
    ap.add_argument("--write", action="store_true", help="写盘（默认 dry-run）")
    ap.add_argument("--force", action="store_true", help="已有覆盖也继续")
    a = ap.parse_args()

    if a.claim:
        from claim_card import load_card               # 分层卡/旧扁平卡都收
        card = load_card(a.claim).legacy
    elif a.disease:
        card = {"disease_or_condition": a.disease,
                "target_population": a.population, "care_setting": a.setting}
    else:
        raise SystemExit("需要 --claim 或 --disease")
    autocurate(card, limit=a.limit, write=a.write, force=a.force, date_max=a.date_max)


if __name__ == "__main__":
    main()
