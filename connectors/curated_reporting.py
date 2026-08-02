"""策展摄入层：报告规范清单 / 偏倚评估工具 (source_role = reporting_tool)。

与 API 连接器的根本区别：这些文档**内容固定、与具体疾病无关**，不需要检索。
它们按"研究设计 + 证据阶段"决定是否适用——所以本模块的核心不是查询翻译，而是
**适用性门控 (applicability gating)**。

铁律（与 C0-C4 分级同源）：一份清单不适用时必须**说明理由并记录**，而不是静默返回空。
否则系统会对一篇 C1 回顾性研究提出"你没做随机对照试验"这类越级要求。

条目全部来自 curated/reporting_tools/*.yaml（人工策展、带完整 provenance 与许可标注）。
"""
from __future__ import annotations
import os, glob, re, yaml
from base import Connector
from schema import ExternalStandard, compute_predates

CURATED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "curated", "reporting_tools")

# 从 Claim Card 推断研究设计用的关键词。
#
# ⚠️ 这些词全部按**词首边界**匹配，不是裸子串（`_kw_hit`）。2026-08-01 实测：
# 裸子串下 `"ct"` 命中了 `"chara`**`ct`**`ers"`，把一篇**血糖时序**论文判成
# `medical_imaging_ai`，于是 CLAIM 影像清单被错误启用、报成 🕳（适用但零摄入
# ＝真缺口）→ **凭空撑大缺口报告**。更要命的是 `"predi`**`ct`**`ion"` 同样含 ct，
# 意味着几乎任何预测模型论文都会中招。
# 这与 §6e 的 `injury` 命中 `acute kidney injury`、§6d 的 `ultrasound` 命中儿科
# 超声指南是同一类错误：**短词当判别信号必须先划边界**。
#
# 词尾刻意不封边界：这些是**词干**（`imag` 要吃下 imaging/images，
# `histolog` 要吃下 histology/histological）。只有词首需要边界。
_IMAGING_KW = ("ct", "mri", "x-ray", "xray", "radiograph", "ultrasound", "sonograph",
               "patholog", "histolog", "whole slide", "wsi", "fundus", "oct",
               "mammograph", "endoscop", "dermoscop", "imag", "scan")
_PREDICT_KW = ("risk", "probabilit", "predict", "prognos", "classif", "detect",
               "diagnos", "grade", "grading", "score", "staging", "segmentation")
_DIAG_KW = ("detect", "diagnos", "classif", "screen")
_TRIAL_KW = ("trial", "rct", "randomis", "randomiz")


def _kw_hit(text: str, keywords) -> bool:
    """边界匹配。`text` 须已小写。表里两类词，判据不同：

    - **长度 ≤3 = 缩写**（ct / mri / oct / wsi / rct）→ **首尾都封边界**。
      只封词首不够：`oct` 会命中 `October`。缩写在文本里必然独立成词。
    - **长度 ≥4 = 词干**（imag / histolog / predict / randomis）→ **只封词首**，
      词尾要留给 imaging/images、histology/histological、randomised/randomized。

    残留风险（已知、未处理）：`scan` 会命中 `scandinavian`。四字母以上的词干
    继续封词尾就会丢掉全部屈折形式，代价更大；真撞上再单列。
    """
    for k in keywords:
        tail = r"(?![a-z0-9])" if len(k) <= 3 else ""
        if re.search(r"(?<![a-z0-9])" + re.escape(k) + tail, text):
            return True
    return False


def load_curated(directory: str = CURATED_DIR) -> list[dict]:
    docs = []
    for path in sorted(glob.glob(os.path.join(directory, "*.yaml"))):
        d = yaml.safe_load(open(path))
        d["_path"] = os.path.relpath(path, os.path.dirname(directory))
        docs.append(d)
    return docs


def infer_study_designs(card: dict) -> set[str]:
    """Claim Card → 研究设计集合。卡里显式声明 study_design 时以声明为准。

    注意：clinical_trial_protocol 永不推断——方案是另一种文档，误判会引入大量
    不适用条目（"你没写数据监查计划"）。必须由卡显式声明。
    """
    declared = card.get("study_design")
    if declared:
        return set(declared) if isinstance(declared, (list, set)) else {declared}

    stage = (card.get("evidence_stage") or "").strip().upper()
    inp = (card.get("model_input") or "").lower()
    out = (card.get("model_output") or "").lower()
    dep = " ".join(str(card.get(k) or "") for k in
                   ("deployment_claim_level", "intended_use", "care_setting")).lower()

    designs: set[str] = set()
    if _kw_hit(inp, _IMAGING_KW):
        designs.add("medical_imaging_ai")
    if _kw_hit(out, _PREDICT_KW):
        designs.add("prediction_model_development")
        if stage in ("C2", "C3", "C4"):
            designs.add("prediction_model_validation")
    if _kw_hit(out, _DIAG_KW):
        designs.add("diagnostic_test_accuracy")
    if stage == "C3":
        designs |= {"early_clinical_evaluation", "prospective_deployment"}
    if stage == "C4":
        designs.add("early_clinical_evaluation")
        if _kw_hit(dep, _TRIAL_KW):
            designs.add("randomised_controlled_trial")
    return designs


def check_applicability(doc: dict, card: dict) -> tuple[bool, str]:
    """返回 (是否适用, 理由)。理由在两种情况下都要写清楚。"""
    app = doc.get("applicability") or {}
    name = doc.get("variant") or doc.get("name") or doc.get("source_id")
    stage = (card.get("evidence_stage") or "").strip().upper()
    ok_stages = set(app.get("evidence_stages") or [])
    designs = infer_study_designs(card)
    ok_designs = set(app.get("study_designs") or [])

    if ok_stages and stage and stage not in ok_stages:
        return False, (f"证据阶段不匹配：论文为 {stage}，{name} 适用于 "
                       f"{sorted(ok_stages)}。对 {stage} 论文套用本清单属越级要求。")
    if ok_designs and not (designs & ok_designs):
        return False, (f"研究设计不匹配：从 Claim Card 推断为 {sorted(designs) or '无法判定'}，"
                       f"{name} 适用于 {sorted(ok_designs)}。")
    if not designs and ok_designs:
        return False, f"无法从 Claim Card 判定研究设计，未启用 {name}（宁可不提，不可乱提）。"
    return True, (f"适用：研究设计 {sorted(designs & ok_designs) or sorted(designs)}，"
                  f"证据阶段 {stage or '未声明'}。")


def applicability_report(card: dict, directory: str = CURATED_DIR) -> list[dict]:
    """给 CLI / 上游用：每份清单是否启用、为什么。不适用的理由必须可见。"""
    out = []
    for doc in load_curated(directory):
        ok, reason = check_applicability(doc, card)
        prov = doc.get("provenance") or {}
        out.append({
            "source_id": doc.get("source_id"),
            "name": doc.get("variant") or doc.get("name"),
            "applicable": ok,
            "reason": reason,
            "n_items": len(doc.get("items") or []),
            "completeness": prov.get("completeness"),
            "completeness_note": prov.get("completeness_note"),
            "publication_date": prov.get("publication_date"),
        })
    return out


class CuratedReportingConnector(Connector):
    """一个注册表 source_id 对应一个实例；一个 source_id 可含多份文档 (CONSORT/SPIRIT)。"""
    source_role = "reporting_tool"
    machine_access = "curated"

    def __init__(self, source_id: str, docs: list[dict]):
        self.source_id = source_id
        self.docs = docs
        self.issuing_body = docs[0].get("issuing_body", "")
        self.tier = docs[0].get("tier")

    def search(self, query_context, submission_date=None, limit=5):
        """query_context 需含 Claim Card 的门控字段 (evidence_stage/model_input/...)。

        limit 被**有意忽略**：清单是一个完整要求集，截断等于静默丢弃要求，
        与本项目"缺口必须显式"的原则冲突。
        """
        card = query_context.get("_card") or query_context
        recs: list[ExternalStandard] = []
        for doc in self.docs:
            ok, reason = check_applicability(doc, card)
            if not ok:
                continue
            prov = doc.get("provenance") or {}
            app = doc.get("applicability") or {}
            name = doc.get("variant") or doc.get("name")
            pub = prov.get("publication_date")
            predates = compute_predates(pub, submission_date)
            base_note = doc.get("_note") or ""
            for item in (doc.get("items") or []):
                notes = [f"适用性判定：{reason}"]
                if app.get("note"):
                    notes.append(f"使用约束：{app['note'].strip()}")
                if prov.get("completeness") != "full":
                    notes.append(f"⚠️摄入完整性={prov.get('completeness')}："
                                 f"{(prov.get('completeness_note') or '').strip()}")
                if base_note:
                    notes.append(base_note)
                recs.append(ExternalStandard(
                    source_id=self.source_id,
                    issuing_body=doc.get("issuing_body", ""),
                    document_type=doc.get("document_type", "reporting_guideline"),
                    title=f"{name} item {item['id']}"
                          + (f" ({item['section']})" if item.get("section") else ""),
                    canonical_url=prov.get("canonical_url", ""),
                    version_or_publication_date=pub,
                    retrieved_date=self.today(),
                    region=doc.get("region"),
                    intended_use_or_decision_point=app.get("applies_when"),
                    recommendation_or_requirement=item.get("text"),
                    passage=item.get("text"),
                    section_page_table=f"{name} item {item['id']}",
                    license=prov.get("license"),
                    machine_access=self.machine_access,
                    source_role=self.source_role,
                    tier=doc.get("tier"),
                    predates_paper_submission=predates,
                    notes=" | ".join(notes),
                    modules=list(item.get("modules") or []),
                    query_context={k: v for k, v in query_context.items()
                                   if not k.startswith("_")},
                ))
        return recs


def build_connectors(directory: str = CURATED_DIR) -> dict:
    """source_id -> CuratedReportingConnector"""
    by_id: dict[str, list[dict]] = {}
    for doc in load_curated(directory):
        by_id.setdefault(doc["source_id"], []).append(doc)
    return {sid: CuratedReportingConnector(sid, docs) for sid, docs in by_id.items()}
