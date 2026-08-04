"""external_standard 记录 schema (对应清单 §5)。

外部证据通道的统一存储契约：每条外部标准都带完整 provenance，
其中 predates_paper_submission 是硬字段——论文投稿后才出现的指南
可用于"今天能否部署"评价，但不能用来指责作者违背当时尚不存在的标准。
"""
from __future__ import annotations
import hashlib
import json, os, tempfile
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

DOCUMENT_TYPES = {
    # §5 规范集
    "guideline", "consensus", "hta", "regulatory",
    "systematic_review", "registry", "terminology",
    # 工程扩展：发现层文献 / 流行病学统计 (source_role 会标明其非规范性)
    "literature", "epidemiology",
    # 策展摄入层：报告规范清单 / 偏倚评估工具 / 证据框架 (reporting_tool 角色)
    "reporting_guideline", "bias_tool", "evidence_framework",
}
SOURCE_ROLES = {
    "normative", "evidence_synthesis", "regulatory", "registry",
    "terminology", "epidemiology", "reporting_tool", "discovery",
}
ACCESS_CLASSES = {"A", "B", "C"}
# 八个审查模块 (须与 clinical_sources.yaml: module_routing 的键一致)
REVIEW_MODULES = {
    "clinical_question", "population_validity", "reference_standard",
    "comparator_baseline", "endpoint_utility", "generalization",
    "safety_harm_equity", "workflow_deployment",
}
ALIGNMENT_VERDICTS = {
    "supported", "partial", "missing", "contradicted", "not_applicable",
    "cannot_determine", "post_submission_only",
}
TEMPORAL_STATUSES = {"pre_submission", "post_submission_only"}


@dataclass
class ExternalStandard:
    # --- 身份 ---
    source_id: str                      # 对应 clinical_sources.yaml 的 source id
    issuing_body: str
    document_type: str                  # DOCUMENT_TYPES 之一
    title: str
    canonical_url: str
    # --- 时间 (predates_* 依赖) ---
    version_or_publication_date: Optional[str] = None   # ISO yyyy-mm-dd
    effective_date: Optional[str] = None
    retrieved_date: Optional[str] = None
    # --- 适用范围 ---
    region: Optional[str] = None
    target_population: Optional[str] = None
    care_setting: Optional[str] = None
    intended_use_or_decision_point: Optional[str] = None
    # --- 规范内容 ---
    recommendation_or_requirement: Optional[str] = None
    recommendation_strength: Optional[str] = None
    evidence_certainty: Optional[str] = None
    comparator: Optional[str] = None
    endpoint_or_threshold: Optional[str] = None
    # --- 引用溯源 ---
    passage: Optional[str] = None
    section_page_table: Optional[str] = None
    # --- 治理 ---
    license: Optional[str] = None
    machine_access: Optional[str] = None   # api|xml|ftp|html|pdf|manual
    source_quality: Optional[str] = None
    source_role: Optional[str] = None
    tier: Optional[int] = None
    # --- 硬门控 ---
    predates_paper_submission: str = "unknown"   # "true"|"false"|"unknown"
    notes: Optional[str] = None
    # 结构化查询上下文 (PICO/Intended-Use)，便于审计"为什么检到这条"
    query_context: dict = field(default_factory=dict)
    # 本条记录服务哪些审查模块。留空 = 不限定，由路由层按源角色分配（API 连接器的默认行为）；
    # 非空 = 记录自己声明（策展条目用，让清单条目精确落到对应模块而非全模块散射）。
    modules: list = field(default_factory=list)

    def validate(self) -> list[str]:
        errs = []
        for m in self.modules:
            if m not in REVIEW_MODULES:
                errs.append(f"modules 含未知模块 '{m}'")
        if self.document_type not in DOCUMENT_TYPES:
            errs.append(f"document_type '{self.document_type}' 不在 {sorted(DOCUMENT_TYPES)}")
        if self.source_role and self.source_role not in SOURCE_ROLES:
            errs.append(f"source_role '{self.source_role}' 非法")
        if self.predates_paper_submission not in {"true", "false", "unknown"}:
            errs.append("predates_paper_submission 必须是 true/false/unknown")
        for f in ("source_id", "issuing_body", "title", "canonical_url"):
            if not getattr(self, f):
                errs.append(f"缺必填字段 {f}")
        return errs

    def to_dict(self) -> dict:
        d = asdict(self)
        # query_context 里下划线前缀的键（如 `_card` 原卡）只在进程内传递，不落盘——
        # 否则每条记录都带一份完整 Claim Card 副本。
        qc = d.get("query_context")
        if isinstance(qc, dict):
            d["query_context"] = {k: v for k, v in qc.items() if not k.startswith("_")}
        return d


def stable_external_standard_id(record: dict) -> str:
    """外部条目的稳定身份，不依赖一次检索中的排序或截断标题。

    同一来源文档的不同推荐条目必须不同，故 section_page_table 进入指纹；发布日期也进入，
    让指南换版不会静默覆盖旧版。缺少字段仍生成稳定 id，但调用方应保留原始 URL 供人复核。
    """
    bits = [str(record.get(k) or "") for k in
            ("source_id", "canonical_url", "section_page_table", "version_or_publication_date")]
    digest = hashlib.sha256("\x1f".join(bits).encode("utf-8")).hexdigest()[:20]
    return f"ext_{digest}"


@dataclass
class PaperEvidence:
    """论文内的一条、可逐字定位的事实证据。

    它不是模型的摘要；quote 必须来自解析产物，verification 记录定位层的匹配结果。
    """
    evidence_id: str
    quote: str
    source: str
    section: Optional[str] = None
    page: Optional[int] = None
    match_tier: Optional[str] = None
    input_part: Optional[str] = "main_text"

    def validate(self) -> list[str]:
        errs = []
        for f in ("evidence_id", "quote", "source"):
            if not getattr(self, f):
                errs.append(f"PaperEvidence 缺必填字段 {f}")
        if self.page is not None and (not isinstance(self.page, int) or self.page < 1):
            errs.append("PaperEvidence.page 必须为正整数或 null")
        if self.match_tier and self.match_tier not in {"exact", "normalized", "alnum"}:
            errs.append("PaperEvidence.match_tier 非法")
        return errs

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Alignment:
    """一条外部标准与一篇论文 claim 的可审计对齐结果。"""
    alignment_id: str
    claim_id: str
    external_standard_id: str
    external_standard_title: str
    external_standard_url: str
    verdict: str
    reason: str
    temporal_status: str
    evidence_search_audit: dict
    paper_evidence: list[PaperEvidence] = field(default_factory=list)
    clinical_review: dict = field(default_factory=dict)
    schema_version: int = 1

    def validate(self) -> list[str]:
        errs = []
        for f in ("alignment_id", "claim_id", "external_standard_id", "external_standard_title",
                  "external_standard_url", "reason"):
            if not getattr(self, f):
                errs.append(f"Alignment 缺必填字段 {f}")
        if self.verdict not in ALIGNMENT_VERDICTS:
            errs.append(f"Alignment.verdict={self.verdict!r} 非法")
        if self.temporal_status not in TEMPORAL_STATUSES:
            errs.append(f"Alignment.temporal_status={self.temporal_status!r} 非法")
        if self.temporal_status == "post_submission_only" and self.verdict != "post_submission_only":
            errs.append("投稿后标准只能使用 verdict=post_submission_only")
        if self.verdict in {"supported", "partial", "contradicted"} and not self.paper_evidence:
            errs.append(f"Alignment {self.verdict} 必须带论文证据")
        if self.verdict == "missing" and not self.evidence_search_audit.get("missing_allowed"):
            errs.append("材料覆盖不完整或搜索未穷尽时，不得使用 verdict=missing")
        for ev in self.paper_evidence:
            errs.extend(ev.validate())
        if self.verdict != "not_applicable":
            for key in ("dimension", "concern", "clinical_importance", "author_request",
                        "acceptable_response"):
                if not str(self.clinical_review.get(key) or "").strip():
                    errs.append(f"Alignment.clinical_review 缺 {key}")
        return errs

    def to_dict(self) -> dict:
        d = asdict(self)
        d["paper_evidence"] = [x.to_dict() for x in self.paper_evidence]
        return d


def compute_predates(pub_date: Optional[str], submission_date: Optional[str]) -> str:
    """规范发布日 vs 论文投稿日。发布 <= 投稿 → 'true'(投稿时已存在)。"""
    if not pub_date or not submission_date:
        return "unknown"
    try:
        return "true" if date.fromisoformat(pub_date[:10]) <= date.fromisoformat(submission_date[:10]) else "false"
    except ValueError:
        return "unknown"


def atomic_write_jsonl(records: list[ExternalStandard], path: str) -> None:
    """原子写一批记录到 jsonl（temp + os.replace）。"""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            for r in records:
                f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
