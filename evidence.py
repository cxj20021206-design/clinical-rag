"""证据核查层 —— 把"模型说这句话来自论文"变成程序可判定的事实。

## 为什么这一层必须是程序,而且必须是硬门

抽卡器是 LLM,LLM 会**改写引文**:调整标点、补全缩写、把 PDF 抽取错乱的字母拼回
正确单词。这些改写读起来毫无破绽——人工确认层看不出来,只有 `str.find` 看得出来。
本项目的 `verbatim: true` 是对外声明"这句话逐字来自原文"(§6f 为了不违反它,宁可
放弃主题极相关的 WHO 结核 CAD 政策),抽卡这一侧必须有对等的强制。

规则:**每条 quote 必须能在解析文本里字面定位;定位不到 → 该字段判 not_extracted
(硬错误,不得进入检索)**。not_extracted 是系统故障不是审稿发现(见 claim_card.py)。

## 两档归一化是实测逼出来的,不是设计推演

2026-07-31 拿 6 张 gold 卡的 27 条人工引文对 `corpus/text/` 实测:

| 归一化 | 定位成功 |
|---|---|
| 仅折叠空白 | 20/27 |
| 第一档:Unicode 破折号/撇号/空格族 + 跨行断字缝合 | 23/27 |
| 第二档:只保留字母数字 | **25/27** |

三类 PDF 抽取伪影,`corpus/README.md` 那句"实测零连字丢失"只覆盖了其中一类:

1. **Unicode 同形字符**——`children aged 1−59 months` 里的 `−` 是 U+2212 数学减号
   (不是连字符也不是 en dash),`59 months` 之间是 U+2009 窄空格;`Alzheimerʼs` 用的是
   U+02BC。模型输出时几乎必然写成 ASCII 形式。→ 第一档处理。
2. **字间空格污染**——cardiac MRI 那篇有整段被抽成 `h yp er tr ophic c ar-\ndi om yo path`
   (PDF 字距渲染)。这一类**第一档救不了**:模型读到这种文本会自行拼回
   `hypertrophic cardiomyopathy` 输出,而那个字符串在原文里根本不存在。
   → 只能靠第二档(丢掉所有非字母数字后比对)。
3. **人工/模型自行省略**——剩下 2 条失败的是 gold 卡自己写了
   `"(sensitivity 55.5% ...) for identification"`,用省略号删掉了中间内容。
   **这已经不是逐字引用**,两档都不该救,只能改卡。已于 2026-07-31 修正为完整原文。
   → 由此定下规则:**quote 内禁止省略号;要引两段就给两条 quote**。

第二档为什么不危险:它只用于**确认这句话在原文里存在**,不用于展示。落盘的 quote
始终是模型给的原样字符串,程序一个字节不改(与 §6f"不删连字"同一条铁律:造出原文
没有的词是 verbatim 违规,留下可辨认伪影不是)。

## 页码由这一层给,不让模型填

模型填页码基本必错,而错页码是"看起来最像正确"的那种错。做法:文档读取层保留
分页,本层逐页定位,页码是**定位的副产物**而不是模型的一个输出字段。
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass

# 各字符族 → ASCII 代表。刻意只做"同形归一",不做同义替换。
_DASHES = "‐‑‒–—―−⁃－"
_APOSTS = "‘’ʼ′´‵"
_QUOTES = "“”″«»"
_SPACES = "              　​"

_RE_DASH = re.compile(f"[{_DASHES}]")
_RE_APOS = re.compile(f"[{_APOSTS}]")
_RE_QUOT = re.compile(f"[{_QUOTES}]")
_RE_SPACE = re.compile(f"[{_SPACES}]")
_RE_HYPHEN_BREAK = re.compile(r"-\s*\n\s*")
_RE_WS = re.compile(r"\s+")
_RE_NONALNUM = re.compile(r"[^a-z0-9]")


def normalize(s: str, aggressive: bool = False) -> str:
    """第一档(默认)/第二档(aggressive)归一化。**只用于比对,不改写落盘内容。**"""
    s = unicodedata.normalize("NFKC", s)
    s = _RE_DASH.sub("-", s)
    s = _RE_APOS.sub("'", s)
    s = _RE_QUOT.sub('"', s)
    s = _RE_SPACE.sub(" ", s)
    s = _RE_HYPHEN_BREAK.sub("", s)        # 跨行断字缝合(仅比对副本,原文不动)
    s = _RE_WS.sub(" ", s).lower().strip()
    return _RE_NONALNUM.sub("", s) if aggressive else s


ELLIPSIS = re.compile(r"\.\.\.|…")

# MinerU `*_content_list.json` 里**承载正文的键**。
#
# 2026-08-01 实测教训：初版只读 `("text", "table_body", "table_caption", "img_caption")`，
# 而 MinerU 3.x 实际用的是 `image_caption`（**不是 `img_caption`**）、`chart_caption`、
# `chart_footnote`、`content`、`table_footnote`、`code_body`、`list_items`……
# 后果：模型读的 `.md` 里有图注，核验读的 content_list 子集里没有 →
# **合法引文被判 `not_extracted`（硬错误）**，等于要求回去重填一张本来正确的卡。
# 这与"拿 MinerU 抽卡却用 pypdf 核验"是同一类错误，只是发生在 MinerU 内部。
#
# 因此改成白名单 + **未知键告警**：宁可吵，也不要再有一次悄悄变窄。
_MINERU_TEXT = frozenset({
    "text", "content",
    "table_body", "table_caption", "table_footnote",
    "image_caption", "image_footnote", "img_caption",
    "chart_caption", "chart_footnote",
    "code_caption", "code_body", "code_footnote",
    "list_items",
})
# 明确的非正文键：结构/坐标/路径。列出来是为了让"未知键"告警只对真正的新键响。
_MINERU_NONTEXT = frozenset({
    "type", "sub_type", "bbox", "page_idx", "img_path", "text_level", "text_format",
    "image_path", "table_path", "index", "score", "angle", "lines",
})


@dataclass
class Located:
    found: bool
    tier: str | None = None        # "exact" | "normalized" | "alnum"
    page: int | None = None        # 1-based;跨页时为 None
    reason: str | None = None      # 未定位时的成因,直接进缺口报告

    def as_dict(self) -> dict:
        d = {"verified": self.found}
        if self.tier:
            d["match_tier"] = self.tier
        if self.page:
            d["page"] = self.page
        if self.reason:
            d["reason"] = self.reason
        return d


class SourceDoc:
    """论文的解析产物:分页文本 + 两档归一化索引。

    分页是**这一层的输入**,不是从扁平 txt 里猜出来的 —— 文档读取层
    (PDF/Word/HTML)必须把页边界一并交出来,否则页码无从谈起。
    """

    def __init__(self, pages: list[str], source: str = ""):
        self.pages = pages
        self.source = source
        self.full = "\n".join(pages)
        self._n1 = [normalize(p) for p in pages]
        self._n2 = [normalize(p, aggressive=True) for p in pages]
        self._f1 = normalize(self.full)
        self._f2 = normalize(self.full, aggressive=True)

    @classmethod
    def from_pdf(cls, path: str) -> "SourceDoc":
        import pypdf
        r = pypdf.PdfReader(path)
        return cls([(p.extract_text() or "") for p in r.pages], source=path)

    @classmethod
    def from_text(cls, path: str) -> "SourceDoc":
        """扁平文本(无分页信息)。页码一律为 None —— **不猜页码**。"""
        return cls([open(path, encoding="utf-8", errors="replace").read()], source=path)

    @classmethod
    def from_mineru(cls, content_list_json: str) -> "SourceDoc":
        """MinerU 版面解析产物(`*_content_list.json`)。**分页保留,且含表格内容。**

        为什么必须有这个入口(2026-07-31 实测):同一篇 PDF,表格里的
        `Number of participants with diabetes` 与各数据集平均年龄 `66.05`
        **在 pypdf 文本里根本不存在**——该 PDF 的表格被抽成逐字符逆序
        (`Sex`→`xeS`、`(27·04%)`→`)%40⋅72(`),pdfplumber 也一样。MinerU 的版面模型
        把它还原成结构化 HTML 表格。

        由此定下一条规矩:**`provenance.source` 必须指向抽卡时实际读的那份解析产物。**
        拿 MinerU 抽卡、却用 pypdf 文本核验,表格来源的引文会被判定位失败 →
        标成 `not_extracted`(硬错误)→ **把解析层的能力差异误报成抽取故障**。
        """
        import json
        blocks = json.load(open(content_list_json, encoding="utf-8"))
        pages: dict[int, list[str]] = {}
        unknown: set[str] = set()
        for b in blocks:
            idx = int(b.get("page_idx") or 0)
            for key, v in b.items():
                if key in _MINERU_NONTEXT:
                    continue
                if key not in _MINERU_TEXT:
                    unknown.add(key)          # 见下方 _MINERU_TEXT 的注释
                    continue
                if not v:
                    continue
                if isinstance(v, list):
                    v = " ".join(str(x) for x in v)
                # 表格是 HTML,剥标签只为比对;落盘引文仍由卡自己保存原样
                pages.setdefault(idx, []).append(re.sub(r"<[^>]+>", " ", str(v)))
        if unknown:
            # 不静默:MinerU 换 schema 时必须看得见,否则又是一次"核验语料悄悄变窄"。
            print(f"  [warn] {os.path.basename(content_list_json)}: 未识别的块键 "
                  f"{sorted(unknown)} —— 若它们含正文,核验语料就比模型读到的窄，"
                  f"合法引文会被误判为 not_extracted。请补进 evidence._MINERU_TEXT")
        if not pages:
            raise ValueError(f"{content_list_json} 里没有可用文本块")
        ordered = [" ".join(pages.get(i, [])) for i in range(max(pages) + 1)]
        return cls(ordered, source=content_list_json)

    @property
    def paginated(self) -> bool:
        return len(self.pages) > 1

    def locate(self, quote: str) -> Located:
        q = (quote or "").strip()
        if not q:
            return Located(False, reason="quote 为空")
        if ELLIPSIS.search(q):
            # 省略号一律拒绝:它意味着模型删掉了中间内容,剩下的字符串在原文里不连续,
            # 定位成功与否都不能证明"这句话是原文说的"。要引两段请给两条 quote。
            return Located(False, reason="quote 内含省略号 —— 逐字引用不得省略,请拆成多条 quote")
        if len(normalize(q, aggressive=True)) < 12:
            # 过短的引文定位成功也没有证据价值("adults" 在任何论文里都能找到)。
            return Located(False, reason="quote 过短(归一化后 <12 字符),不足以作为出处")

        for tier, hays, full in (("normalized", self._n1, self._f1),
                                 ("alnum", self._n2, self._f2)):
            nq = normalize(q, aggressive=(tier == "alnum"))
            for i, h in enumerate(hays):
                if nq in h:
                    tier_out = "exact" if (tier == "normalized" and q in self.full) else tier
                    return Located(True, tier_out,
                                   page=(i + 1) if self.paginated else None)
            if nq in full:      # 跨页
                return Located(True, tier, page=None)
        return Located(False, reason="在解析文本中定位不到 —— 引文可能被改写,"
                                     "或该内容不在提供给模型的材料里(见 input_coverage)")


def verify_card(doc: dict, src: SourceDoc) -> dict:
    """核验一张卡的全部 provenance 引文。

    返回 {field: Located}。调用方(抽卡器)负责把未通过的字段改标 not_extracted ——
    本模块只判定,不改卡:改卡是策略,判定是事实,两者分开才能审计。
    """
    fields = ((doc.get("provenance") or {}).get("fields") or {})
    out = {}
    for key, val in fields.items():
        q = (val or {}).get("quote")
        if not q:
            continue                      # 无 quote 的字段由 validate_card 的 status 规则管
        out[key] = src.locate(q)
    return out


def main():
    import argparse
    import glob
    import os
    import yaml
    ap = argparse.ArgumentParser(description="核验 Claim Card 的引文能否在原文里字面定位")
    ap.add_argument("cards", nargs="+")
    ap.add_argument("--text-dir", default="corpus/text",
                    help="扁平文本目录(按卡文件名同名 .txt 查找);优先用 provenance.source")
    a = ap.parse_args()

    cards = [p for pat in a.cards for p in sorted(glob.glob(pat))]
    tot = ok = 0
    for p in cards:
        doc = yaml.safe_load(open(p))
        doc = doc.get("claim_card", doc)
        slug = os.path.basename(p).rsplit(".", 1)[0]
        src_path = ((doc.get("provenance") or {}).get("source") or
                    os.path.join(a.text_dir, slug + ".txt"))
        if not os.path.exists(src_path):
            print(f"\n=== {slug} ===\n  ⚠️ 找不到原文 {src_path},跳过")
            continue
        low = src_path.lower()
        if low.endswith("_content_list.json") or low.endswith("_content_list_v2.json"):
            src = SourceDoc.from_mineru(src_path)     # 版面解析，含表格，保留分页
        elif low.endswith(".pdf"):
            src = SourceDoc.from_pdf(src_path)
        else:
            src = SourceDoc.from_text(src_path)
        print(f"\n=== {slug} ===  ({len(src.pages)} 页 / {len(src.full)} 字符)")
        for key, r in verify_card(doc, src).items():
            tot += 1
            ok += r.found
            mark = f"OK[{r.tier}]" if r.found else "**MISS**"
            page = f" p.{r.page}" if r.page else ""
            print(f"  {mark:18s}{page:6s} {key:26s} {r.reason or ''}")
    print(f"\n定位成功 {ok}/{tot}")
    return 0 if ok == tot else 1


if __name__ == "__main__":
    raise SystemExit(main())
