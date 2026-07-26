"""开放获取临床指南（学会/国家 CPG）的抓取与推荐条目抽取。

第三条腿（策展摄入）的第二个源族。与 USPSTF 的区别：USPSTF 是**一个机构一个网站**，
学会指南是**几十家机构各发各的**，唯一能统一拿到"许可明确 + 全文可解析"的通道是
**Europe PMC 的 OA 全文 XML**（报告规范清单那批条目就是这么摄入的，见 DESIGN §6b）。

设计取舍（重要）：
  1. **只摄入有"推荐条目结构"的指南。**很多指南把推荐写在正文散文里
     （如中国肺癌筛查指南 PMC9987116，"建议"散落在背景段落中，其中一部分还是在
     转述别家指南的推荐）。对这类文档做关键词抓句会把"美国癌症协会建议…"抓成
     本指南的推荐——**绑错推荐来源比不给更糟**（同 uspstf.py 不用正则扫句子的理由）。
     抽不出结构的文档记入 manifest 的 deferred 并写明原因，不静默降级。
  2. **许可白名单**：只收 CC BY / BY-NC / BY-NC-ND / BY-SA / CC0 与公有领域。
     Europe PMC 的 `license` 字段为空 = 出版商未声明开放许可（如 SSC 2021
     PMC7101866），一律不摄入——不是"暂时没查到"，是不许可。
  3. 逐字保存推荐原文（verbatim），分级字段从原文里**解析**出来而不是重写。
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UA = {"User-Agent": "clinical-rag/0.1 (research; contact via repo)"}

# 许可白名单。ND(禁演绎)也收：本项目只做**逐字未改动**的摘录，不产生演绎作品——
# 与 USPSTF "without any changes" 同一条逻辑。SA 同理（只在演绎时触发）。
LICENSE_ALLOW = ("cc by", "cc by-nc", "cc by-nc-nd", "cc by-nd", "cc by-sa",
                 "cc by-nc-sa", "cc0", "public domain")


def license_ok(lic: str | None) -> tuple[bool, str]:
    l = (lic or "").strip().lower()
    if not l:
        return False, "Europe PMC 未标注开放许可 → 视为未授权，不摄入（不是'待查'）"
    if any(l.startswith(a) for a in LICENSE_ALLOW):
        return True, f"许可 {l}：允许逐字未改动的复制与再分发"
    return False, f"许可 {l} 不在白名单 {LICENSE_ALLOW}"


def _get(url: str, params: dict | None = None) -> str:
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def search_candidates(terms: str, date_max: str | None = None, limit: int = 25) -> list[dict]:
    """按主题词找 OA 指南候选。terms 是已经写好的 Europe PMC 查询片段。

    这条查询与 europepmc.py 的发现层查询是**同一批命中**（PUB_TYPE:"Guideline"），
    区别只在于这里进一步要求 OA + 许可，并去拿全文——即 DESIGN §6a 里说的
    "发现层自动产出待策展清单"落到实处。
    """
    q = f'({terms}) AND PUB_TYPE:"Guideline" AND OPEN_ACCESS:y AND IN_EPMC:y'
    if date_max:
        q += f" AND FIRST_PDATE:[1990-01-01 TO {date_max}]"
    d = json.loads(_get(EPMC + "/search", {
        "query": q, "format": "json", "pageSize": limit, "resultType": "core"}))
    out = []
    for r in d.get("resultList", {}).get("result", []):
        if not r.get("pmcid"):
            continue
        out.append({"pmcid": r["pmcid"], "title": r.get("title"),
                    "journal": r.get("journalTitle"),
                    "date": r.get("firstPublicationDate"),
                    "license": r.get("license"), "doi": r.get("doi"),
                    "pmid": r.get("pmid")})
    return out


def fetch_xml(pmcid: str, cache_dir: str | None = None) -> str:
    """取 OA 全文 XML。带本地缓存——摄入是反复调试的过程，不该反复打人家接口。"""
    cache_dir = cache_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "store", ".cache")
    os.makedirs(cache_dir, exist_ok=True)
    p = os.path.join(cache_dir, f"{pmcid}.xml")
    if os.path.exists(p) and os.path.getsize(p) > 1000:
        return open(p, encoding="utf-8").read()
    x = _get(f"{EPMC}/{pmcid}/fullTextXML")
    with open(p, "w", encoding="utf-8") as f:
        f.write(x)
    return x


# --------------------------------------------------------------------------
# 分级解析：GRADE 及其常见变体。原文逐字保留，这里只是把强度/确定性**读出来**。
# --------------------------------------------------------------------------
_STRENGTH = [
    (r"\bstrong(?:ly)?\s+recommend", "strong"),
    (r"\bstrong\s+recommendation", "strong"),
    (r"\bconditional\s+recommendation", "conditional"),
    (r"\bweak\s+recommendation", "weak"),
    (r"\bexpert\s+(?:consensus|opinion)", "expert_opinion"),
    (r"\b(?:good|best)\s+practice\s+statement", "good_practice_statement"),
    (r"\bungraded\b", "ungraded"),
    (r"\bwe\s+recommend\b", "strong"),          # GRADE 惯例：recommend=强，suggest=弱
    (r"\bwe\s+suggest\b", "conditional"),
]
_CERTAINTY = re.compile(
    r"(?:certainty|quality|level)\s+of\s+evidence\s*[:：]?\s*"
    r"(very\s+low|low|moderate|high|none)", re.I)
# "Recommendation strength B" / "推荐强度 B" 这类字母制（韩国 CPG 用 A/B/C/E）
_LETTER = re.compile(r"recommendation\s+strength\s+([A-E])\b", re.I)


# GRADE/牛津式证据等级："[Weak recommendation based on a low level of evidence 2C]"
_EV_LEVEL = re.compile(r"(?:level\s+of\s+evidence|evidence\s+level|grade)\s*[:：]?\s*"
                       r"([12][A-D]|[A-D]\b)", re.I)
# 一个单元格里塞多条推荐时的切点：每条以中括号分级标记收尾
_GRADE_TAIL = re.compile(r"\[[^\]]*\b(?:recommendation|evidence)\b[^\]]*\]", re.I)


def split_statements(text: str) -> list[str]:
    """一格多条 → 切开。WSES 2023 的推荐汇总表把一节里的三四条推荐塞进同一单元格：
    "We suggest X [Weak recommendation … 2C]We recommend Y [Strong …]"。
    不切开就会用最后一条的强度去标第一条——**绑错强度比不给更糟**。
    """
    tails = list(_GRADE_TAIL.finditer(text))
    if len(tails) < 2:
        return [text]
    out, start = [], 0
    for m in tails:
        seg = text[start:m.end()].strip()
        if len(seg) >= MIN_LEN:
            out.append(seg)
        start = m.end()
    rest = text[start:].strip()
    if len(rest) >= MIN_LEN:
        out.append(rest)
    return out or [text]


def parse_grade(text: str) -> dict:
    """从推荐原文里解析 强度/证据确定性。解析不出就留空——不猜。"""
    t = " ".join(text.split())
    low = t.lower()
    strength = next((v for pat, v in _STRENGTH if re.search(pat, low)), None)
    m = _LETTER.search(t)
    letter = m.group(1).upper() if m else None
    c = _CERTAINTY.search(t)
    certainty = re.sub(r"\s+", " ", c.group(1)).lower() if c else None
    if not letter:
        m2 = _EV_LEVEL.search(t)
        letter = m2.group(1).upper() if m2 else None
    return {"recommendation_strength": (f"{strength}" if strength else None),
            "grade_letter": letter,
            "evidence_certainty": certainty}


# --------------------------------------------------------------------------
# 抽取策略。每种策略对应一类真实存在的 JATS 结构；都抽不出 → 该文档不适合自动摄入。
# --------------------------------------------------------------------------
_REC_TITLE = re.compile(r"^\s*(recommendations?|recommendation\s+statements?|"
                        r"推荐意见|推荐建议)\s*[:：]?\s*\d*\s*$", re.I)
_REC_START = re.compile(r"^\s*(recommendation|推荐意见)\b", re.I)
# 汇总表 caption：必须是"推荐汇总"本身，不能只是句中出现 recommendation
_REC_CAPTION = re.compile(
    r"^\s*(table\s*\d*[.:]?\s*)?((summary|list|overview)\s+of\s+)?"
    r"(the\s+)?(initial\s+and\s+updated\s+|final\s+|key\s+)?recommendations?\b"
    r"|recommendation\s+statements?\b|summary\s+of\s+(the\s+)?recommendations?\b", re.I)
# 推荐句标志：推荐动词或"不推荐"。缺这个的行多半是入选标准/阈值/研究特征表
_REC_MARKER = re.compile(
    r"\b(recommend\w*|suggest\w*|should|shall|must|advise[ds]?|is\s+indicated|"
    r"contraindicated|refrain\s+from)\b|推荐|建议|应当|不宜", re.I)
MIN_LEN, MAX_LEN = 25, 3000
MIN_TABLE_LEN = 40          # 表格行更碎，门槛高于正文


def _txt(e) -> str:
    return re.sub(r"\s+", " ", "".join(e.itertext())).strip()


def _own_paragraphs(sec) -> str:
    """只取本节自己的 <p>/<list>，不吃子节——否则 Background/Evidence 会被并进推荐。"""
    parts = [_txt(c) for c in sec if c.tag in ("p", "list", "disp-quote")]
    return " ".join(x for x in parts if x)


def _strategy_rec_sections(root) -> list[dict]:
    """结构 A：每条推荐是一个标题为 Recommendation 的 <sec>（韩国脓毒症 CPG 等）。

    上下文（这条推荐回答的临床问题）取紧邻在前的 <boxed-text>（KQ 卡片）或
    上一级小节标题——没有上下文的推荐句在审稿里几乎不可用（不知道它在管什么）。
    """
    # 问题卡片按**文档顺序**取最近的一个在前者，而不是找兄弟节点。原因（实测）：
    # 韩国 CPG 里只有 KQ1 是 Recommendation 节的兄弟，KQ2-KQ12 都被排版嵌进了
    # **上一条推荐的 Comments 小节末尾**。按兄弟找会让 12/13 条推荐丢掉问题卡片，
    # 退化成"KEY QUESTIONS AND RECOMMENDATIONS"这种无用上下文。
    order = {id(e): i for i, e in enumerate(root.iter())}
    boxes = sorted((order[id(b)], _txt(b)) for b in root.iter("boxed-text"))

    out = []
    for parent in root.iter("sec"):
        for sec in parent.findall("sec"):
            ti = sec.find("title")
            if ti is None or not _REC_TITLE.match(_txt(ti)):
                continue
            body = _own_paragraphs(sec)
            if not (MIN_LEN <= len(body) <= MAX_LEN):
                continue
            here = order[id(sec)]
            prior = [t for p, t in boxes if p < here and len(t) >= MIN_LEN]
            ctx = prior[-1] if prior else None
            if ctx is None:
                pt = parent.find("title")
                ctx = _txt(pt) if pt is not None else None
            out.append({"statement": body, "context": ctx,
                        "section": _txt(ti), **parse_grade(body)})
    return out


def _strategy_rec_boxes(root) -> list[dict]:
    """结构 B：推荐写在 <boxed-text> 框里，以 "Recommendation" 开头。"""
    out = []
    for parent in root.iter("sec"):
        pt = parent.find("title")
        ctx = _txt(pt) if pt is not None else None
        for b in parent.findall("boxed-text"):
            s = _txt(b)
            if _REC_START.match(s) and MIN_LEN <= len(s) <= MAX_LEN:
                out.append({"statement": s, "context": ctx,
                            "section": "boxed-text", **parse_grade(s)})
    return out


_COL_PATTERNS = {
    "rec": r"^\s*(no\.?\s*)?(recommendations?|statements?|recommendation statements?)\s*$",
    "strength": r"strength|grade of recommendation|recommendation grade|^grade$",
    "certainty": r"quality of evidence|certainty|level of evidence|evidence level|"
                 r"^evidence$|^loe$",
    "agreement": r"agreement|consensus",
}


def _header_map(tw) -> tuple[dict, int] | tuple[None, None]:
    """找表头，返回 {角色: 列号} 与表头行序号。

    有表头就**按列取**，这是唯一能把整张表取全的办法。ESPNIC POCUS 指南的
    41 条推荐里只有 10 条含 should/must，其余写成 "POCUS is helpful to…"、
    "POCUS may detect…"——靠推荐动词过滤会静默丢掉 31 条（含全部肺部推荐），
    系统就会得出"该指南没有肺部推荐"的错误结论。
    """
    rows = list(tw.iter("tr"))
    for ri, tr in enumerate(rows[:2]):
        cells = [_txt(c) for c in tr if c.tag in ("td", "th")]
        if not cells:
            continue
        idx: dict[str, int] = {}
        for i, c in enumerate(cells):
            for role, pat in _COL_PATTERNS.items():
                if role not in idx and re.search(pat, c.lower()):
                    idx[role] = i
        if "rec" in idx:
            return idx, ri
    return None, None


def _strategy_rec_tables(root) -> list[dict]:
    """结构 C：推荐汇总表（"Summary of recommendations"），一行一条。

    两道过滤，缺一不可——初版只要求 caption 含 "recommendation"、行文本够长，
    结果把这些抓成了"推荐"：
      · Chest RBC 输血指南的《Hemoglobin Thresholds in Studies Included **per
        Recommendation**》表 → 抓出 "Gastrointestinal bleeding"、"Hemoglobin
        Threshold, g/dL" 这种单元格碎片；
      · 巴西胸科学会指南的筛查**入选标准**表 → 抓出 "Age 50-75 years old AND
        Exposure to asbestos…"（是准入条件，不是推荐）。
    故：① caption 必须**以**"(summary of) recommendations" 起头或明写
    recommendation statements，不能只是句中提到；② 行本身必须含推荐动词/分级标记。
    """
    out = []
    for tw in root.iter("table-wrap"):
        cap = _txt(tw.find("caption")) if tw.find("caption") is not None else ""
        if not _REC_CAPTION.search(cap):
            continue
        idx, hdr_i = _header_map(tw)
        for ri, tr in enumerate(tw.iter("tr")):
            if idx is not None and ri == hdr_i:       # 表头行本身
                continue
            cells = [_txt(td) for td in tr if td.tag in ("td", "th")]
            if not cells:
                continue
            row = " | ".join(c for c in cells if c)
            if len(row) > MAX_LEN:
                continue
            cols = {}
            if idx is not None and idx["rec"] < len(cells):
                main = cells[idx["rec"]]
                for role in ("strength", "certainty", "agreement"):
                    j = idx.get(role)
                    if j is not None and j < len(cells) and cells[j]:
                        cols[role] = cells[j]
            else:
                # 无表头可依 → 退回启发式：取最长单元格，且必须含推荐动词，
                # 否则会把入选标准/阈值表当推荐（见上文两个实例）。
                main = max(cells, key=len)
                if not _REC_MARKER.search(main):
                    continue
            if len(main) < MIN_TABLE_LEN:
                continue
            segs = split_statements(main)
            for seg in segs:
                # 一格一条时可借同行其它单元格（强度列/证据列）解析分级；
                # 一格多条时只能就地解析，否则会互相污染。
                g = parse_grade(seg if len(segs) > 1 else row)
                # 有专门的分级列时以列值为准：列是指南自己的标注，正则是猜的。
                if cols.get("strength"):
                    g["recommendation_strength"] = cols["strength"]
                if cols.get("certainty"):
                    g["evidence_certainty"] = cols["certainty"]
                out.append({"statement": seg, "context": cap,
                            "section": f"表：{cap[:60]}",
                            "agreement": cols.get("agreement"), **g})
    return out


STRATEGIES = {
    "rec_sections": _strategy_rec_sections,
    "rec_boxes": _strategy_rec_boxes,
    "rec_tables": _strategy_rec_tables,
}
MIN_YIELD = 3          # 少于 3 条视为结构没抽对，宁可不摄入


def article_meta(root) -> dict:
    def first(path):
        e = root.find(path)
        return _txt(e) if e is not None else None
    ids = {e.get("pub-id-type"): _txt(e) for e in root.iter("article-id")}
    dates = {}
    for pd in root.iter("pub-date"):
        y = pd.findtext("year"); m = pd.findtext("month"); d = pd.findtext("day")
        if y:
            dates[pd.get("pub-type") or pd.get("date-type") or "unk"] = (
                f"{int(y):04d}-{int(m or 1):02d}-{int(d or 1):02d}")
    lic = root.find(".//license")
    return {
        "title": first(".//article-meta//article-title"),
        "journal": first(".//journal-title"),
        "doi": ids.get("doi"), "pmid": ids.get("pmid"), "pmcid": ids.get("pmcid"),
        "publication_date": dates.get("epub") or dates.get("ppub") or
                            next(iter(dates.values()), None),
        "license_url": (lic.get("{http://www.w3.org/1999/xlink}href") if lic is not None else None),
        "license_text": (_txt(lic)[:400] if lic is not None else None),
        "publisher": first(".//publisher-name"),
    }


def extract(xml: str) -> dict:
    """跑全部策略，取产量最高的一个。返回抽取结果 + 各策略产量（诊断用）。"""
    root = ET.fromstring(xml)
    body = root.find(".//body")
    yields = {}
    best_name, best = None, []
    for name, fn in STRATEGIES.items():
        try:
            got = fn(body) if body is not None else []
        except Exception as e:                        # 单个策略挂掉不该拖垮整篇
            got, name = [], name
            print(f"    [warn] 策略 {name} 异常: {type(e).__name__}: {str(e)[:60]}")
        yields[name] = len(got)
        if len(got) > len(best):
            best_name, best = name, got
    # 去重：同一句话可能既在推荐节里、又出现在汇总表里
    seen, uniq = set(), []
    for r in best:
        k = re.sub(r"\W+", "", r["statement"].lower())[:160]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return {"strategy": best_name, "recommendations": uniq,
            "yields": yields, "meta": article_meta(root),
            "ok": len(uniq) >= MIN_YIELD,
            "reason": (f"策略 {best_name} 抽出 {len(uniq)} 条推荐" if len(uniq) >= MIN_YIELD
                       else f"三种结构策略产量均不足（{yields}）：本文的推荐未以"
                            f"可识别的结构（推荐节/推荐框/推荐表）呈现，"
                            f"关键词抓句会把转述他人指南的句子抓成本指南推荐 → 不自动摄入")}


if __name__ == "__main__":                            # 探查用：看一篇能不能抽
    import sys
    for pmcid in sys.argv[1:]:
        r = extract(fetch_xml(pmcid))
        print(f"\n{pmcid} {r['meta'].get('title', '')[:70]}")
        print(f"  {r['meta'].get('publication_date')} | {r['meta'].get('license_url')}")
        print(f"  策略产量 {r['yields']} → {r['reason']}")
        for x in r["recommendations"][:4]:
            print(f"   · [{x['recommendation_strength']}/{x['evidence_certainty']}] "
                  f"{x['statement'][:120]}")
