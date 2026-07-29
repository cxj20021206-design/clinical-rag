"""WHO IRIS（机构知识库）的指南抓取与推荐条目抽取 —— normative 第三个源族。

**为什么单独开一条腿**：前两个 normative 源族都够不到 WHO 自己的规范指南 ——
USPSTF 只管美国预防服务，学会/国家 CPG 走 Europe PMC OA 期刊通道（WHO 指南是
机构出版物，不发期刊，不在 EPMC 里）。而 §6e 自动扩库实测证明"许可拿不到"是
覆盖面的真正瓶颈（心衰 0/28、AKI 0/12、糖网 0/1，因为 AHA/ESC/KDIGO 多不是
CC-BY）。IRIS 是少数**许可预先干净**的规范源：WHO 自 2020 年前后的出版物统一
标 CC BY-NC-SA 3.0 IGO。

**关键实测发现（推翻了原计划）**：原以为 IRIS 正文是 PDF、必须先接 MinerU 才能
用。实际上 DSpace 为每个条目预抽了 `TEXT` bundle（纯文本），而 WHO 正式指南在
纯文本里**仍保留编号锚点**：

    Recommendation 6
    An oral penicillin test dose may be given prior to IM BPG administration ...
    (Conditional recommendation, low certainty evidence)

所以这里用**编号锚点切块**（strategy = `rec_numbered`），整条腿不需要 GPU。

**这不是"关键词抓句降级"**（guideline_fetch.py 头部禁止的那件事）：抓句是扫全文
找含 recommend 的句子，会把"美国癌症协会建议…"这类**转述别家指南**的句子记成本
指南的推荐；这里锚定的是 WHO 自己排版的编号条目，块边界由编号与 GRADE 尾括号
界定，块内逐字。两者的区别是"有没有作者给的结构标记"，不是严格程度的差别。

四个 PDF 文本特有的坑（JATS XML 都不存在，实测撞出来的）：
  1. **断字**：跨行断词 "recommen-\\ndation" join 后变 "recommen- dation"，
     不修则 verbatim 文本里全是断字。修复记在 provenance.text_normalization，
     不当作"未改动"偷偷做。
  2. **编号跨章重复**：同一份指南里 Recommendation 1 出现 6 次（执行摘要一遍、
     正文一遍，且每章从 1 重编）→ **去重键必须是正文哈希，不能是编号**。
  3. **块尾粘连**：块边界取到下一个锚点，中间会夹进章节标题
     （"…low certainty evidence) Diagnosis and treatment of skin…"）→ 有 GRADE
     尾括号的截到括号止（同 _GRADE_TAIL 处理 WSES 一格多条的思路）。
  4. **页码行**：纯数字/罗马数字行会插进块中间（"xvii"），按行剔除。

许可门与另两条腿完全一致：`dc.rights` 为空 = 未授权，不是"待查"。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request

from guideline_fetch import MIN_YIELD, license_ok, parse_grade

IRIS = "https://iris.who.int/server/api"
UA = {"User-Agent": "clinical-rag/0.1 (research; contact via repo)",
      "Accept": "application/json"}

MIN_LEN = 40            # 短于此的块当排版碎片丢弃


def _get(url: str, params: dict | None = None) -> dict:
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _raw(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
        return r.read()


def _meta(item: dict, key: str):
    v = (item.get("metadata") or {}).get(key)
    return v[0]["value"] if v else None


# --------------------------------------------------------------------------
# 检索
# --------------------------------------------------------------------------
def search_candidates(terms: str, limit: int = 25, item_type: str = "Publications") -> list[dict]:
    """按主题词找 IRIS 候选。

    `itemtype=Publications` 是必须的：IRIS 收录量最大的是 `Journal articles`
    （WHO Bulletin/EMHJ 转载，**不是 WHO 的规范文件**，且多数没有 dc.rights），
    不筛类型的话 "sepsis" 会检回 813 篇期刊文章。

    注意这里**只做召回**，主题准入交给 title_gate() —— 与 §6e 同一条分工
    （检索用宽字段要召回，准入只看标题要准确）。IRIS 的默认检索是全文松散匹配，
    实测 "heart failure" 会命中 *Urban HEART*（城市健康公平评估工具，纯字面）
    和清洁家用能源报告。
    """
    out, page, size = [], 0, min(limit, 50)
    while len(out) < limit:
        d = _get(IRIS + "/discover/search/objects",
                 {"query": terms, "dsoType": "item", "size": size, "page": page,
                  "f.itemtype": f"{item_type},equals"})
        objs = d["_embedded"]["searchResult"]["_embedded"]["objects"]
        if not objs:
            break
        for o in objs:
            it = o["_embedded"]["indexableObject"]
            out.append({
                "uuid": it["uuid"],
                "title": it.get("name") or "",
                "date_issued": _meta(it, "dc.date.issued"),
                "rights": _meta(it, "dc.rights"),
                "handle": _meta(it, "dc.identifier.uri"),
                "language": _meta(it, "dc.language.iso"),
                "publisher": _meta(it, "dc.publisher"),
            })
        page += 1
        if page > 5:
            break
    return out[:limit]


def title_gate(title: str, disease_phrases: list[str]) -> tuple[bool, str]:
    """标题主题门 —— §6e 那道门在本腿的同款实现。

    只在摘要/正文提到某病种的文档**不得被赋予该病种的 normative 身份**：自动腿
    第一版没这道门时，心衰卡"捞到"的三份是两份肥胖指南 + 一份肾脏病 SGLT-2 指南
    （只在摘要提到心衰获益），而 scope 草稿会把 heart failure 写进它们，从此每篇
    心衰论文都命中一份肥胖指南。**把错误固化进库比漏一份严重得多。**

    多词短语按整词组匹配（同 curated_guidelines.match_disease 的教训：把
    "lung ultrasound" 拆成 lung/ultrasound 会让肺癌 CT 卡命中儿科超声指南）。
    """
    t = (title or "").lower()
    for p in disease_phrases:
        p = str(p).lower().strip()
        if not p:
            continue
        if p in t or p.rstrip("s") in t:
            return True, f"标题命中病种短语「{p}」"
    return False, (f"病种短语 {disease_phrases} 未出现在标题中 —— "
                   f"不得据此赋予它该病种的 normative 身份")


def bundles(uuid: str) -> dict[str, list[dict]]:
    d = _get(f"{IRIS}/core/items/{uuid}/bundles")
    out = {}
    for bu in d["_embedded"]["bundles"]:
        bs = _get(bu["_links"]["bitstreams"]["href"])
        out[bu["name"]] = [
            {"name": x["name"], "size": x.get("sizeBytes"),
             "url": x["_links"]["content"]["href"]}
            for x in bs["_embedded"]["bitstreams"]]
    return out


def pick_text(bundle_map: dict) -> tuple[dict | None, str]:
    """挑英文版 TEXT bitstream。

    IRIS 一个条目常挂多语言 PDF（-eng/-fre/-spa/-chi），各自有 TEXT。**必须显式
    选英文**，否则会把法语版当成另一份指南重复摄入，且下游全部正则都是英文的。
    只有一个候选且无语言后缀时才接受它（如从 PMC 转存的文件名）。
    """
    texts = bundle_map.get("TEXT") or []
    if not texts:
        return None, "该条目没有 TEXT bundle（DSpace 未抽出文本，需 PDF 解析才能用）"
    eng = [x for x in texts if re.search(r"[-_]eng\.", x["name"], re.I)]
    if eng:
        return eng[0], "英文版 TEXT bundle"
    if len(texts) == 1:
        return texts[0], "唯一 TEXT bundle（文件名无语言标识）"
    langs = [x["name"] for x in texts]
    return None, f"有多个 TEXT 但无法确定英文版（{langs}）—— 不猜，避免摄入非英文版本"


def fetch_text(url: str, cache_dir: str | None = None, key: str = "") -> str:
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        p = os.path.join(cache_dir, f"iris_{key or hashlib.md5(url.encode()).hexdigest()}.txt")
        if os.path.exists(p):
            return open(p, encoding="utf-8").read()
    t = _raw(url).decode("utf-8", "replace")
    if cache_dir:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(t)
        os.replace(tmp, p)
    return t


# --------------------------------------------------------------------------
# 清理与抽取
# --------------------------------------------------------------------------
_PAGE_LINE = re.compile(r"^\s*(?:[ivxlcdm]{1,7}|\d{1,4})\s*$", re.I)
_TOC_LINE = re.compile(r"\.{4,}\s*\d{1,4}\s*$")      # 目录行：点线 + 页码
# 跨行连字符：**保留连字符，只消掉换行与空白**。
#
# 这里有个不可能完美的取舍：PDF 文本丢掉了"这个连字符原本是断字符还是复合词的
# 一部分"的信息。删连字符能把 "recommen-\ndation" 修成 "recommendation"，但会把
# "middle-\nincome" 毁成 "middleincome" —— 实测这份 WHO 指南里就有 2 处，而
# 医学文本里跨行复合词（low-income / point-of-care / first-line / evidence-based）
# 远比断字词常见。**造出原文不存在的词是 verbatim 违规，留下可辨认的排版伪影不是**，
# 所以宁可保留连字符（同"宁可空手不喂垃圾""宁可留空不猜"的一贯取舍）。
# 续词大小写不限：Steven-\nJohnson 这类专有名词同样只需消掉换行。
_DEHYPHEN = re.compile(r"(\w)-[ \t]*\n[ \t]*(\w)")

ANCHOR = re.compile(
    r"^[ \t]*(Recommendation\s+\d+[a-z]?|Recommendations?\s+\d+[a-z]?|"
    r"Good practice statement|No recommendation)\b[ \t.:]*", re.I | re.M)
# 推荐块的收尾标记："(Strong recommendation, moderate certainty evidence)"
_GRADE_CLOSE = re.compile(
    r"\((?=[^)]*\brecommendation\b)(?=[^)]*\b(?:evidence|certainty|quality)\b)[^)]*\)",
    re.I)


# 块内编号子条目："Recommendations 1. …… 2. …… 3. ……"（WHO 结核指南体例：一个
# "Recommendations" 标题下挂多条，每条自带无括号的 GRADE 尾巴）。前面不允许是
# 数字或点，免得把 "3.5." 这类小节号或小数当成条目编号。
_SUBITEM = re.compile(r"(?<![\d.])(\d{1,2})\.\s+(?=[A-Z“\"])")
# 推荐条目之后的附属段落 —— 属于整节而不属于任何单条推荐，切分时截掉，
# 否则末条推荐会把一整页 Remarks 吞进 statement 里。
_TRAILER = re.compile(r"\b(Remarks?|Justification|Rationale|Subgroup considerations|"
                      r"Implementation considerations|Monitoring and evaluation|"
                      r"Research priorities)\b\s*[:•\-]?", re.I)

# 连字（ligature）丢失的特征词：PDF 里 fi/fl/ft/ffi 合字在文本抽取时整体消失，
# "software"→"soware"、"specific"→"specic"。这类文本**不能摄入** —— 逐字保存
# 的前提是那些字确实是原文的字；把错字写进 normative 库，既污染检索也让
# provenance.verbatim 变成假声明。（实测 WHO CAD-for-TB 政策简报就是这种。）
_LIGATURE_LOSS = re.compile(
    r"\b(?:soware|specic|signicant|identied|dierent|eective|ecacy|ecient|"
    r"classiation|conrmed|benet|nding|dicult|sucient|rst-line|proles?|"
    r"noti(?:ed|cation)|stratied|quantied|veried)\b", re.I)


# 推荐性表述的情态标志。用途是**排除**，不是查找 —— 候选条目已由结构锚点确定，
# 这里只把明显不是推荐的candidates剔掉（如页眉 "Recommendations" + 页码 89 撞出来的
# 定义段落 "…is defined as a test where most reagents are enclosed…"）。方向与被
# 禁止的"关键词抓句"相反：抓句是拿关键词去正文里找推荐，会把转述别家指南的句子
# 抓进来；这里是对已定位的条目做否定筛查，不会引入新的来源不明条目。
_MODAL = re.compile(r"\b(should|should not|may|must|recommend\w*|suggest\w*|"
                    r"is advised|are advised|not be used|be offered)\b", re.I)


# WHO 母婴健康类指南（产程照护、产前照护）用的是**另一套分级**，不是 GRADE 的
# strong/conditional：括号里直接写 (Recommended) / (Not recommended) /
# (Context-specific recommendation)。
#
# 必须认它，而且是**安全关键**：不认的话 "Not recommended" 条目的强度字段是空的，
# 一条"不推荐做 X"在下游看起来就跟一条中性推荐一样 —— 比没有分级更危险。
#
# 按 §6d「刻意不做跨源分级归一化」，这里保留它自己的取值（who_recommended /
# who_not_recommended / who_context_specific），**不映射成 strong/conditional**：
# 两套体系的方法学含义不同，归一化会抹掉差异。
_WHO_MNH = [
    (re.compile(r"\(\s*not\s+recommended[^)]*\)", re.I), "who_not_recommended"),
    (re.compile(r"\(\s*context-specific\s+recommendation[^)]*\)", re.I), "who_context_specific"),
    (re.compile(r"\(\s*recommended\s+only\s+in\s+the\s+context[^)]*\)", re.I),
     "who_research_context_only"),
    (re.compile(r"\(\s*recommended\s*\)", re.I), "who_recommended"),
]


def who_mnh_strength(stmt: str) -> str | None:
    """WHO 母婴健康体例的推荐分级。**否定式先匹配** —— "(Not recommended)" 里
    含有 "recommended"，顺序反了会把"不推荐"读成"推荐"。"""
    for pat, val in _WHO_MNH:
        if pat.search(stmt):
            return val
    return None


_LABEL_PREFIX = re.compile(
    r"^\s*(?:Recommendations?\s*\d*[a-z]?|Good practice statement|No recommendation)"
    r"\s*[.:]?\s*", re.I)


def _strip_label(stmt: str) -> str:
    """剥掉条目开头的锚点标签，再去判"这段像不像推荐"。

    **不剥就会被前缀自己骗过**：`_MODAL` 里的 `recommend\\w*` 会命中标签里的
    "Recommendations" 一词，于是任何以该标签开头的段落（包括页眉残留带出来的
    定义段落）都被认成推荐。§6e 在表格分节小标题上踩过完全相同的坑
    （"Recommendations: Clinical variables…" 被当成推荐），教训是同一条：
    **判推荐动词必须在去掉标签前缀之后做。**
    """
    return _LABEL_PREFIX.sub("", stmt, count=1)


def text_quality(text: str) -> tuple[bool, str]:
    """连字丢失检测。返回 (可用, 说明)。"""
    hits = _LIGATURE_LOSS.findall(text)
    uniq = {h.lower() for h in hits}
    if len(uniq) >= 3:
        return False, (f"PDF 文本抽取丢失连字（fi/fl/ft 合字），检出 {len(uniq)} 类畸变词 "
                       f"{sorted(uniq)[:6]} 共 {len(hits)} 处 —— 逐字摘录会把错字写进库，"
                       f"该文档需改用 PDF 版面解析（如 MinerU）后才能摄入")
    return True, "未检出连字丢失"


def clean_text(raw: str) -> str:
    """接合跨行连字符 → 去目录行/页码行。**先接合再按行处理**，否则删掉页码行会
    让不相邻的两半被误接。"""
    t = _DEHYPHEN.sub(r"\1-\2", raw)
    keep = [ln for ln in t.split("\n")
            if not _PAGE_LINE.match(ln) and not _TOC_LINE.search(ln)]
    return "\n".join(keep)


def _norm(s: str) -> str:
    return " ".join(s.split())


_SENT_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"])")


def _first_sentences(block: str, n: int = 2) -> str:
    """取块的前 n 句。用于没有 GRADE 收尾标记、块边界会一路延伸的锚点类型。"""
    parts = _SENT_END.split(_norm(block))
    return " ".join(parts[:n]) if parts else _norm(block)


def _context_before(text: str, pos: int) -> str | None:
    """块前最近的一个"像小节标题"的行 —— 用于填 context（这条推荐回答的临床问题）。

    判据保守：短、不以句号收尾、不是另一个锚点。取不到就留空 —— **宁可留空不猜**，
    context 会被下游当作"这条推荐的临床问题"展示，编一个比没有更糟。
    """
    for ln in reversed(text[:pos].split("\n")[-6:]):
        s = ln.strip()
        if not s or len(s) > 90 or s.endswith((".", ";", ",")):
            continue
        if ANCHOR.match(s) or _GRADE_CLOSE.search(s):
            continue
        if sum(c.isdigit() for c in s) > len(s) / 3:
            continue
        return _norm(s)
    return None


MAX_BLOCK = 4000        # 推荐节的有效长度上限（3 条推荐 + 余量），兜底用
MAX_REC_NUMBER = 40     # 超过此编号且无 GRADE 的锚点，判为页眉+页码残留


def _trim_block(block: str) -> str:
    """把锚点块收缩到"推荐条目本身"的范围。

    块边界只能取到下一个锚点，而 WHO 指南在推荐之后还有 Remarks、Justification、
    证据表、参考文献 —— 实测结核指南单块长达 9.7 万字符，里面 77 个编号绝大多数是
    正文列表而非推荐。**不收缩就没法安全切分**：要么按编号切出满是噪声的 77 条，
    要么（GRADE 判据兜住后）整块只算一条、把真正的第 2、3 条推荐丢掉。

    `Remarks` 这类小标题是原文给的语义边界，比长度可靠，优先用它；没有时才用
    MAX_BLOCK 兜底。
    """
    tr = _TRAILER.search(block)
    end = tr.start() if tr else len(block)
    return block[:min(end, MAX_BLOCK)]


def split_numbered(block: str, label: str) -> list[str]:
    """把 "Recommendations 1. …… 2. …… 3. ……" 切成一条一条。

    **为什么必须切**：这些子条目各自带 GRADE 尾巴（"Conditional recommendation,
    moderate certainty of evidence"，且 WHO 结核指南这一体例**不加括号**，所以
    _GRADE_CLOSE 截不住）。不切开时 parse_grade 会在整块里搜索，抓到的是**别条
    推荐的等级** —— 实测把一条 moderate 的推荐标成了 high。同 §6d
    split_statements 的教训：**绑错强度比不给更糟**。

    尾部 Remarks/Justification 等附属段落属于整节而非单条，一并截掉。
    """
    # **不能先剥掉 label** —— ANCHOR 把 "Recommendations 1" 整体当标签，剥掉它
    # 就把第一条推荐的编号一起剥了，切分从第 2 条起，第 1 条静默丢失。
    # 直接在整块上找编号：单条体例（RHD 的 "Recommendation 1 Children, …"）
    # 编号后没有点，_SUBITEM 天然不匹配，不会误切。
    body = block
    marks = list(_SUBITEM.finditer(body))
    if len(marks) < 2:
        return [_norm(block)]
    out, bounds = [], [m.start() for m in marks] + [len(body)]
    for i, m in enumerate(marks):
        seg = body[m.end():bounds[i + 1]]
        tr = _TRAILER.search(seg)
        if tr:
            seg = seg[:tr.start()]
        seg = _norm(seg)
        if len(seg) >= MIN_LEN:
            out.append(seg)
    if not out:
        return [_norm(block)]
    # **只有当子条目多数自带 GRADE 时才承认这是"一节多条推荐"。**
    # 否则切开的是推荐正文里的普通编号列表（剂量/疗程/适用条件枚举）——实测
    # RHD 指南被这样切碎成 149 条（正确值 31）。判据用 GRADE 而不是长度或个数：
    # WHO 的多条推荐体例里每条都自带强度，普通枚举没有。
    graded = sum(1 for s in out if parse_grade(s)["recommendation_strength"])
    if graded < max(2, int(len(out) * 0.6)):
        return [_norm(block)]
    return out


def extract(raw: str) -> dict:
    """编号锚点抽取。返回与 guideline_fetch.extract() 同形状的结果。"""
    text = clean_text(raw)
    tq_ok, tq_note = text_quality(text)
    if not tq_ok:
        return {"ok": False, "strategy": None, "recommendations": [],
                "yields": {"rec_numbered": 0}, "anchors": 0,
                "blocker": "text_quality", "reason": tq_note}
    anchors = [(m.start(), m.end(), m.group(1)) for m in ANCHOR.finditer(text)]
    if not anchors:
        return {"ok": False, "strategy": None, "recommendations": [],
                "yields": {"rec_numbered": 0}, "anchors": 0,
                "reason": "全文中没有 Recommendation N / Good practice statement "
                          "这类编号锚点 —— 该文档的推荐未以可识别结构呈现，不做"
                          "关键词抓句降级（会把转述别家指南的句子记成本文推荐）"}

    bounds = [s for s, _, _ in anchors] + [len(text)]
    seen: dict[str, int] = {}
    recs: list[dict] = []
    dropped_pagehdr = dropped_nonrec = 0
    for i, (s, e, label) in enumerate(anchors):
        _n = re.search(r"(\d+)", label)
        anchor_no = int(_n.group(1)) if _n else None
        label_n = _norm(label)
        ctx = _context_before(text, s)
        is_norec = bool(re.match(r"no recommendation", label, re.I))
        # **切分必须在截断之前**：块尾截断（GRADE 收尾 / 取前两句）会把一个锚点下
        # 的第 2、3 条推荐直接砍掉 —— 实测 WHO 结核指南因此只剩每节第一条。
        segs = split_numbered(_trim_block(_norm(text[s:bounds[i + 1]])), label_n)
        if len(segs) == 1:
            # 单条：块边界一路到下一个锚点，须自行收尾。
            # 坑 3：块尾粘连下一节标题 —— 有 GRADE 尾括号就截到括号止；
            # 没有 GRADE 收尾的锚点（No recommendation / Good practice statement）
            # 截不住，实测 RHD 有一块长达 5.2 万字符吞掉整章正文，按句子收尾。
            m = _GRADE_CLOSE.search(segs[0])
            segs = [segs[0][:m.end()] if m else _first_sentences(segs[0], 2)]
        else:
            # 已按编号切开：各自再按自己的 GRADE 尾括号收尾（无括号体例保持原样，
            # 边界已由下一条的编号界定）。
            segs = [(lambda mm, sg: sg[:mm.end()] if mm else sg)(_GRADE_CLOSE.search(sg), sg)
                    for sg in segs]
        for stmt in segs:
            if len(stmt) < MIN_LEN:
                continue
            # 坑 2：编号跨章重复（执行摘要 + 正文），去重键用正文哈希而非编号
            body = _norm(stmt[len(label_n):] if stmt.startswith(label_n) else stmt).lower()
            key = hashlib.md5(body.encode()).hexdigest()
            if key in seen:
                continue
            grade = parse_grade(stmt)
            if not grade["recommendation_strength"]:
                grade["recommendation_strength"] = who_mnh_strength(stmt)
            # 页眉残留："Recommendations" 是章标题、紧跟的数字其实是页码，撞成一个
            # 锚点后会把定义段落（"…is defined as a test where most reagents are
            # enclosed…"）当成推荐。真实推荐编号极少超过 MAX_REC_NUMBER，且这类
            # 残留必然没有 GRADE —— 两个条件同时成立才拒，避免误伤真有几十条推荐
            # 的合并指南。被拒的计数返回，不静默丢弃。
            if (not is_norec and not grade["recommendation_strength"]
                    and anchor_no and anchor_no > MAX_REC_NUMBER):
                dropped_pagehdr += 1
                continue
            # 既无 GRADE 强度、又没有任何推荐性情态动词的，不是推荐条目。
            # **只看开头一段**：推荐的情态动词必然在句子主干里，而条目正文可长达
            # 数千字，全文搜 may/should 会被无关段落骗过 —— 实测页眉残留带出的
            # 定义段落（"…is defined as a test where…"）就是靠后文一个 may 混进来的。
            if not is_norec and not grade["recommendation_strength"] \
                    and not _MODAL.search(_strip_label(stmt)[:300]):
                dropped_nonrec += 1
                continue
            seen[key] = 1
            if is_norec:
                # "WHO 无法作出推荐"的条目**不得带推荐强度**：正文里的
                # "…does not recommend either for or against…" 会被 _STRENGTH
                # 误判成 strong，而"无法推荐"却标着强推荐是自相矛盾的，
                # 下游会把它当一条正常推荐用。同 USPSTF I 级：证据不足是结论
                # 本身，不是一个弱一点的推荐。
                grade = {"recommendation_strength": None, "grade_letter": None,
                         "evidence_certainty": None}
            recs.append({
                "statement": stmt,
                "context": ctx,
                "section": label_n,
                "recommendation_strength": grade["recommendation_strength"],
                "grade_letter": grade["grade_letter"],
                "evidence_certainty": grade["evidence_certainty"],
                "agreement": None,
                # WHO"无法作出推荐"条目：与 USPSTF I 级（证据不足）同性质，审稿价值
                # 尤高（官方都说证据不足的领域，论文若称临床价值正该追问），单独标记
                # 以免被下游当成一条普通推荐。
                "is_no_recommendation": is_norec,
            })

    dropped = {"page_header": dropped_pagehdr, "not_a_recommendation": dropped_nonrec}
    if len(recs) < MIN_YIELD:
        return {"ok": False, "strategy": "rec_numbered", "recommendations": recs,
                "yields": {"rec_numbered": len(recs)}, "anchors": len(anchors),
                "dropped": dropped,
                "reason": f"编号锚点 {len(anchors)} 个但只抽出 {len(recs)} 条"
                          f"（去重/长度过滤后），少于 {MIN_YIELD} 条视为结构没抽对"}
    return {"ok": True, "strategy": "rec_numbered", "recommendations": recs,
            "yields": {"rec_numbered": len(recs)}, "anchors": len(anchors),
            "dropped": dropped, "reason": None}


def yield_audit(res: dict) -> dict:
    """抽全性核查 —— §6e 那道门。

    `>=3 条` 挡不住**静默截断**（ESPNIC 那次错误实现抽 10 条、正确实现 41 条，
    两个都过门都不报错）。这里拿锚点数当上界对账：锚点是"该是一条推荐的位置"，
    抽出数远低于它就说明块被过滤器吃掉了。

    注意锚点数天然高于抽出数（执行摘要与正文重复、TOC 残留），所以阈值取得比
    §6e 的 0.6 宽松，并把重复量单列出来供人判断，而不是直接判失败。
    """
    a, n = res.get("anchors") or 0, len(res.get("recommendations") or [])
    ratio = (n / a) if a else 0.0
    flags = []
    if a and ratio < 0.25:
        flags.append(f"抽出/锚点 = {n}/{a} = {ratio:.2f}，偏低 —— 可能有整段推荐被"
                     f"过滤器吃掉（也可能只是执行摘要与正文重复度高），须人工比对原文")
    return {"anchors": a, "extracted": n, "coverage_ratio": round(ratio, 3), "flags": flags}


def probe(uuid: str, cache_dir: str | None = None) -> dict:
    """单条目端到端探查：元数据 → 许可 → TEXT → 抽取 → 抽全性。"""
    it = _get(f"{IRIS}/core/items/{uuid}")
    title, rights = it.get("name"), _meta(it, "dc.rights")
    ok, why = license_ok(rights)
    out = {"uuid": uuid, "title": title, "rights": rights, "license_ok": ok,
           "license_note": why, "date_issued": _meta(it, "dc.date.issued")}
    if not ok:
        out["blocker"] = "license"
        return out
    bm = bundles(uuid)
    bs, note = pick_text(bm)
    out["text_note"] = note
    if not bs:
        out["blocker"] = "no_fulltext"
        return out
    res = extract(fetch_text(bs["url"], cache_dir, key=uuid))
    out["extract"] = res
    out["yield_audit"] = yield_audit(res)
    if not res["ok"]:
        # 成因分开记：结构问题（推荐散在正文里）与文本质量问题（连字丢失）的
        # 处置完全不同 —— 前者换文档，后者换解析器。混为一类会让缺口报告误导。
        out["blocker"] = res.get("blocker") or "structure"
    return out
