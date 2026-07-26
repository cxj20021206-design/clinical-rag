"""USPSTF 取数层：抓索引 + 解析单条推荐页 → 结构化 dict。

与 API 连接器分开的原因：USPSTF 没有 API，内容靠网页抓取；而且 108 条推荐内容固定、
不随论文变化，所以走"抓一次存本地"的策展路线（第三条腿），不是每次检索都联网。

许可（2026-07-25 核对 https://www.uspreventiveservicestaskforce.org/uspstf/recommendation-topics/copyright-notice）：
  允许 "reproduce, redistribute, publicly display, and incorporate USPSTF work into
  other materials"，**但必须 without any changes**；禁止收费再分发与营利用途；
  引用时须注明 USPSTF 网页为出处。版权声明中**未提及** AI/文本挖掘限制
  （对比 NICE 明令 AI 用途需另行许可 → NICE 不可行，见 docs/RELATED_WORK.md §2）。
  本模块因此逐字保存推荐原文，不改写、不摘要，并强制记录 canonical_url。
"""
from __future__ import annotations
import gzip
import html as htmllib
import re
import time
import urllib.request

BASE = "https://www.uspreventiveservicestaskforce.org"
INDEX = BASE + "/uspstf/topic_search_results?topic_status=P"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

# USPSTF 推荐等级 → 净收益确定性。取自 USPSTF Grade Definitions，用于填 evidence_certainty。
# I 级("证据不足")在审稿里价值最高：官方都认为证据不足的领域，论文若声称临床价值即应追问。
GRADE_CERTAINTY = {
    "A": ("high", "高确定性，净收益大"),
    "B": ("moderate", "高确定性净收益中等，或中等确定性净收益中至大"),
    "C": ("moderate", "中等确定性净收益小；应按个体情况选择性提供"),
    "D": ("moderate", "中等/高确定性无净收益或弊大于利；不建议提供"),
    "I": ("insufficient", "证据不足，无法评估收益与危害的平衡"),
}


def _get(url: str, retries: int = 3) -> str:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            r = urllib.request.urlopen(req, timeout=60)
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", "replace")
        except Exception as e:                      # 网络抖动重试
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def _strip_tags(s: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|div|li|h\d|tr)>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


# ---------- 索引 ----------

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_HREF = re.compile(r"href='(/uspstf/recommendation/[^']+)'")


def fetch_index(max_pages: int = 12, delay: float = 1.0) -> list[dict]:
    """抓全部分页的推荐索引。表头固定为
    Status | Type | Year | Topic Name | Age Group | Grade | Category。"""
    out, seen = [], set()
    for page in range(1, max_pages + 1):
        url = INDEX if page == 1 else f"{INDEX}&PAGE={page}"
        h = _get(url)
        n_before = len(out)
        for row in _ROW.findall(h):
            cells = _CELL.findall(row)
            if len(cells) < 7:
                continue
            m = _HREF.search(cells[3])
            if not m:
                continue
            slug = m.group(1)
            if slug in seen:
                continue
            seen.add(slug)
            out.append({
                "status": _strip_tags(cells[0]),
                "type": _strip_tags(cells[1]),          # Screening / Preventive Medication / Counseling
                "year": _strip_tags(cells[2]),
                "title": _strip_tags(cells[3]),
                "age_group": _strip_tags(cells[4]),
                "grades": [g.strip() for g in _strip_tags(cells[5]).split(",") if g.strip()],
                "category": _strip_tags(cells[6]),
                "url": BASE + slug,
                "slug": slug.rsplit("/", 1)[-1],
            })
        if len(out) == n_before:                        # 本页无新条目 → 到底
            break
        time.sleep(delay)
    return out


# ---------- 单条推荐 ----------

_DATE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})")
_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}


def _iso_date(text: str) -> str | None:
    m = _DATE.search(text)
    if not m:
        return None
    return f"{m.group(3)}-{_MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"


def _sections(text: str) -> dict:
    """按 USPSTF 固定分区标题切正文。各推荐页分区一致，见 RELATED_WORK.md §USPSTF。"""
    heads = ["Recommendation Summary", "Clinician Summary", "Patient Summary",
             "Full Recommendation", "Importance", "Practice Considerations",
             "Supporting Evidence", "Recommendations of Others",
             "Update of Previous USPSTF Recommendation",
             "USPSTF Assessment of Magnitude of Net Benefit", "Rationale", "Discussion"]
    idx = []
    for h in heads:
        m = re.search(r"^\s*" + re.escape(h) + r"\s*$", text, re.M | re.I)
        if m:
            idx.append((m.start(), m.end(), h))
    idx.sort()
    out = {}
    for i, (s, e, name) in enumerate(idx):
        end = idx[i + 1][0] if i + 1 < len(idx) else len(text)
        body = text[e:end].strip()
        if body:
            out[name] = body
    return out


# Recommendation Summary 分区的权威三列表：Population | Recommendation | Grade。
# 必须用这张表而非正则扫句子——(a) 并非所有推荐都以 "The USPSTF recommends" 开头
# （如前列腺 C 级那条以 "For men aged 55 to 69 years..." 开头，正则会整条漏掉）；
# (b) 一条推荐常含多个亚组分级（前列腺 C+D、结直肠 A+B+C），只有这张表能把
# 人群—推荐—等级正确绑定。绑错推荐强度比不给更糟。
_SUMMARY_TABLE = re.compile(
    r"<table[^>]*>\s*<thead>.*?Population.*?Recommendation.*?Grade.*?</thead>(.*?)</table>",
    re.S | re.I)
_GRADE_IN_ROW = re.compile(r"class='[^']*grade([ABCDI])\b", re.I)


def _parse_summary_table(h: str) -> list[dict]:
    """→ [{population, recommendation, grade}]，推荐原文逐字。"""
    m = _SUMMARY_TABLE.search(h)
    if not m:
        return []
    rows = []
    for row in _ROW.findall(m.group(1)):
        cells = _CELL.findall(row)
        if len(cells) < 3:
            continue
        g = _GRADE_IN_ROW.search(row)
        grade = (g.group(1).upper() if g
                 else (_strip_tags(cells[2]).strip().upper() or None))
        if grade not in GRADE_CERTAINTY:
            grade = None
        rows.append({
            "population": _strip_tags(cells[0]),
            "recommendation": _strip_tags(cells[1]),   # 逐字，不改写
            "grade": grade,
        })
    return rows


# 页面状态：USPSTF 用标题前缀标注已停用/已转交的主题，这类页面**没有** Recommendation
# Summary 表（如 "Inactive: Chronic Kidney Disease: Screening"、
# "Referred: Immunizations for Children" —— 免疫接种已转交 ACIP）。
# 这不是解析失败，是该主题确实没有现行推荐，须如实记录而非当作缺陷。
_STATUS = re.compile(r"^\s*(Inactive|Referred|In Progress|Draft)\s*:", re.I)


def parse_recommendation(url: str) -> dict:
    """单条推荐页 → dict。推荐原文逐字保留（许可要求 without any changes）。"""
    h = _get(url)
    text = _strip_tags(h)
    secs = _sections(text)

    m = _STATUS.match(text)
    status = m.group(1).lower().replace(" ", "_") if m else "active"

    recommendations = _parse_summary_table(h)
    statements = [r["recommendation"] for r in recommendations]
    grades = sorted({r["grade"] for r in recommendations if r["grade"]})

    # 发布日在页头 "Final Recommendation Statement | <标题> | March 09, 2021"，
    # 不在正文分区内；直接扫全文会撞上参考文献里的日期，故锚定该标题后 300 字。
    anchor = re.search(r"Final Recommendation Statement", text)
    date_scope = text[anchor.start():anchor.start() + 300] if anchor else text[:1500]

    return {
        "url": url,
        "status": status,                     # active / inactive / referred / in_progress / draft
        "publication_date": _iso_date(date_scope),
        "recommendations": recommendations,   # 人群—推荐—等级 已绑定
        "statements": statements,
        "grades_on_page": grades,
        "sections": {k: v for k, v in secs.items()},
        "raw_len": len(text),
    }


if __name__ == "__main__":           # 冒烟测试
    import json, sys
    if len(sys.argv) > 1 and sys.argv[1] == "index":
        rows = fetch_index()
        print(f"索引 {len(rows)} 条")
        for r in rows[:5]:
            print(" ", r["year"], r["grades"], r["title"][:60])
    else:
        d = parse_recommendation(BASE + "/uspstf/recommendation/lung-cancer-screening")
        print(json.dumps({k: v for k, v in d.items() if k != "sections"},
                         ensure_ascii=False, indent=2)[:1500])
        print("分区:", list(d["sections"]))
