"""连接器基类：把某个外部源的原始返回映射成 ExternalStandard 记录。

另含各连接器共用的查询词处理（清洗/抽词/复数展开）。Claim Card 的字段是自由文本
（"AI-assisted low-dose CT interpretation"），直接丢给检索接口会退化成松散匹配，
所以统一在这里抽成可控的关键词。
"""
from __future__ import annotations
import re
import sys, os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from schema import ExternalStandard  # noqa: E402

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

# 功能词 + 空泛技术/临床词。留在 OR 子句里会让约束近乎失效。
STOPWORDS = {
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


# ML 术语不是临床病种，绝不能拿去查临床/监管库：拿 "learning" 查器械库会检回
# "Deep Learning Image Reconstruction"，一篇表征学习基准论文因此被配上 FDA 器械先例。
NON_CLINICAL = {
    "learning", "machine", "deep", "artificial", "intelligence", "neural",
    "network", "networks", "algorithm", "algorithms", "representation",
    "benchmark", "embedding", "transformer", "foundation", "training",
    "dataset", "datasets", "classification", "regression", "supervised",
    "unsupervised", "multimodal", "pretrained", "finetuning",
}
# 泛化医学词：能匹配上但几乎没有判别力（满库都是）。用它们当判别信号会出现
# "lung cancer screening" 检回乳腺癌筛查指标这种事——匹配上的是 cancer，不是 lung。
GENERIC_CLINICAL = {
    "cancer", "disease", "diseases", "screening", "risk", "detection",
    "imaging", "test", "tests", "assay", "adult", "adults", "disorder",
}


def clean_text(s) -> str:
    """去掉会破坏各家查询语法的字符（Lucene / OData / openFDA 通用取交集）。"""
    return re.sub(r'["\\:()\[\]{}^~?*\']', " ", str(s or "")).strip()


def keywords(*texts, exclude=None, max_terms: int = 8) -> list[str]:
    """自由文本 → 关键词（保留连字符：low-dose / ai-assisted）。

    `exclude` 传上层已经作强制约束的词——否则会出现 病种="lung cancer screening"
    而人群约束是 (lung OR cancer OR screening) 这种情况：凡命中病种的必然命中人群，
    约束等于没加。
    """
    exclude = exclude or set()
    out: list[str] = []
    for t in texts:
        for tok in clean_text(t).lower().split():
            tok = tok.strip(".,;/")
            if len(tok) < 2 or tok in STOPWORDS or tok in exclude or tok in out:
                continue
            out.append(tok)
            if len(out) >= max_terms:
                return out
    return out


def expand_plural(terms: list[str]) -> list[str]:
    """adult ↔ adults：卡里写单数、文献写复数（或反之）不该漏掉。"""
    out = list(terms)
    for t in terms:
        alt = t[:-1] if t.endswith("s") else t + "s"
        if len(alt) >= 3 and alt not in out:
            out.append(alt)
    return out


class Connector:
    source_id: str = "base"
    issuing_body: str = ""
    source_role: str = ""
    tier: int | None = None
    machine_access: str = "api"

    def search(self, query_context: dict, submission_date: str | None = None,
               limit: int = 5) -> list[ExternalStandard]:
        """query_context: 结构化 PICO/Intended-Use (population/condition/
        intervention/comparator/outcome/setting/region)。返回 ExternalStandard 列表。"""
        raise NotImplementedError

    @staticmethod
    def today() -> str:
        return date.today().isoformat()
