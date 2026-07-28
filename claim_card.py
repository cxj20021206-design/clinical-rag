"""Clinical Claim Card 的分层结构、校验门禁与兼容视图。

## 为什么要分层（2026-07-28 的实测结论）

原来的卡是一层扁平自由文本，每个字段同时干两件互相冲突的事：既当门控的匹配依据，
又当内容描述。于是"把卡写详细"不但不能减少误判，还会**翻转门控**，而且是静默的。
三个实测反例（都用当时库里的真实指南跑出来）：

  ① disease="acute kidney injury in critical illness"（比 "acute kidney injury" 详细）
     → 因 `critical illness` 命中 **ESPNIC 新生儿 POCUS 指南**；target_population 写
     "ICU patients" 不含 adult 字样，人群门控没兜住 → 成人 AKI 论文被要求对齐新生儿
     床旁超声指南。**简陋卡反而不会。**
  ② disease="acute ischemic stroke, excluding hemorrhagic stroke and congenital heart
     disease" → `congenital heart` 命中 ESPNIC。卡里明写"排除"，系统当成"包含"
     —— 词面匹配读不出否定。
  ③ intended_use="automated grading to triage referrals to ophthalmology"
     → `triage` 命中急重症词表，USPSTF 被整个拦下 → 一篇糖网**筛查**论文因为把决策
     写清楚了，反而拿不到筛查指南。

所以本模块把卡切成三层，规矩只有一条：**只有 gating 层参与准入**。

  gating      少、受控（枚举/受控词表），决定"哪份指南适用"。
  descriptive 多、详细、自由文本，供下游模型阅读与相关度排序，**不参与准入**。
              "把论文里涉及医学的内容都写进去"就写在这一层的 clinical_context。
  provenance  每个字段从论文哪句话来；**区分"论文没写"(absent) 与"没抽出来"
              (not_extracted)** —— 前者是审稿发现，后者是系统故障，混为一谈会让
              "弃权/缺口报告率"这个指标失去意义（见 docs/RELATED_WORK.md §2）。

## 兼容

`load_card()` 同时接受新式分层卡与旧式扁平卡；`ClaimCard.legacy` 输出一份扁平字典，
键名与旧卡一致，所有连接器无需改动即可继续工作。区别在于扁平视图里的**门控键由
gating 层生成**（例如 `disease_or_condition` 只取 `condition.primary.label`，
不再夹带合并症语境），这正是上面 ① 那类误判的修复点。
"""
from __future__ import annotations

import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "connectors"))
from base import NON_CLINICAL                                   # noqa: E402

# --------------------------------------------------------------------------
# 受控枚举。刻意小而粗——枚举的价值在于**可判定**，不在于表达力；
# 表达力交给 descriptive 层。
# --------------------------------------------------------------------------
AGE_GROUPS = ("neonate", "infant", "child", "adolescent", "adult", "older_adult", "mixed")

# 场景枚举 → 指南 scope.care_settings 里的常见说法。setting_note 做的是词重叠，
# 枚举必须落回自然语言才对得上（不然 "icu" 与 "intensive care unit" 永远不重叠）。
CARE_SETTINGS = {
    "icu": "intensive care unit",
    "nicu": "neonatal intensive care unit",
    "picu": "paediatric intensive care unit",
    "emergency_department": "emergency department",
    "inpatient_ward": "inpatient ward",
    "primary_care": "primary care",
    "outpatient_specialty": "outpatient specialty clinic",
    "community": "community",
    "prehospital": "prehospital",
    "perioperative": "perioperative",
    "home": "home",
}

# 临床任务枚举。它取代了"扫 intended_use 里有没有 triage/screening 这类词"的脆弱做法
# —— 上面反例 ③ 就是被 intended_use 里一个 triage 翻掉的。
CLINICAL_TASKS = ("screening", "prevention", "diagnosis", "triage", "prognostication",
                  "treatment_selection", "monitoring", "risk_stratification",
                  "documentation")
EVIDENCE_STAGES = ("C0", "C1", "C2", "C3", "C4")

# 研究设计**沿用报告清单层已有的词表**（curated/reporting_tools/*.yaml 的
# applicability.study_designs），不另起一套——`infer_study_designs` 里"卡里显式声明
# 时以声明为准"是**整体替换**推断结果的，词表对不上会让所有清单静默失效
# （实测：自造 retrospective_development 等值后，肺癌卡从 105 条掉到 15 条）。
# 留空 = 按证据阶段/模态推断（推荐）；只有 clinical_trial_protocol 必须显式声明。
STUDY_DESIGNS = ("medical_imaging_ai", "prediction_model_development",
                 "prediction_model_validation", "diagnostic_test_accuracy",
                 "early_clinical_evaluation", "prospective_deployment",
                 "randomised_controlled_trial", "clinical_trial_protocol",
                 "human_factors_study")

# age_group → 供人群门控识别的词（curated_guidelines._PED / _ADULT 认这些词）。
# "mixed" 刻意不产出任何标记：人群门控只在**明确冲突**时硬拦，留白 = 不拦，是安全侧。
_AGE_WORDS = {
    "neonate": "neonate newborn", "infant": "infant", "child": "child paediatric",
    "adolescent": "adolescent", "adult": "adult", "older_adult": "older adult elderly",
    "mixed": "",
}

# condition.primary.label 里不该出现的东西。病种字段被污染时所有门控会**静默失效**
# （`disease_or_condition: "AI-assisted low-dose CT interpretation"` 抽不出任何判别词，
# 不报错、不命中、看起来一切正常），与 ESPNIC 那次"沉默的截断"同类，所以做成硬错误。
_TASK_WORDS = {"screening", "screen", "triage", "prognostication", "prognosis",
               "monitoring", "prediction", "predicting", "grading", "stratification"}
_MODALITY_WORDS = {"ct", "mri", "ultrasound", "sonography", "radiograph", "radiography",
                   "x-ray", "xray", "imaging", "ecg", "ekg", "eeg", "echocardiography",
                   "fundus", "photograph", "histopathology", "slide", "ehr"}
_MODIFIER_WORDS = {"high-risk", "low-risk", "low-dose", "high-dose", "ai-assisted",
                   "assisted", "automated", "computer-aided", "annual", "biennial"}


class CardError(ValueError):
    pass


# --------------------------------------------------------------------------
# 校验门禁：抽卡器（LLM）上线前必须先有它
# --------------------------------------------------------------------------
def validate_card(doc: dict) -> tuple[list[str], list[str]]:
    """返回 (errors, warnings)。errors 非空 = 不得进入检索。"""
    errs, warns = [], []
    g = doc.get("gating") or {}
    cond = (g.get("condition") or {}).get("primary") or {}
    label = str(cond.get("label") or "").strip()

    if not label:
        errs.append("gating.condition.primary.label 缺失 —— 没有病种就没有门控依据")
    else:
        toks = {t.strip(".,;/:") for t in label.lower().replace("/", " ").split()}
        for group, name in ((NON_CLINICAL, "ML 术语"), (_MODALITY_WORDS, "影像/数据模态词"),
                            (_TASK_WORDS, "任务词"), (_MODIFIER_WORDS, "人群/技术修饰词")):
            bad = sorted(toks & group)
            if bad:
                errs.append(
                    f"gating.condition.primary.label 混入{name} {bad} —— 病种字段被污染时"
                    f"所有门控会静默失效。任务词写 gating.clinical_task，模态写 "
                    f"descriptive.model_input，修饰词写 descriptive。")

    pop = g.get("population") or {}
    if pop.get("age_group") not in AGE_GROUPS:
        errs.append(f"gating.population.age_group={pop.get('age_group')!r} 不在受控枚举 {AGE_GROUPS}")
    if g.get("care_setting") not in CARE_SETTINGS:
        errs.append(f"gating.care_setting={g.get('care_setting')!r} 不在受控枚举 {sorted(CARE_SETTINGS)}")
    if g.get("clinical_task") not in CLINICAL_TASKS:
        errs.append(f"gating.clinical_task={g.get('clinical_task')!r} 不在受控枚举 {CLINICAL_TASKS}")
    if g.get("evidence_stage") not in EVIDENCE_STAGES:
        errs.append(f"gating.evidence_stage={g.get('evidence_stage')!r} 不在 {EVIDENCE_STAGES}")
    sd = g.get("study_design") or []
    sd = [sd] if isinstance(sd, str) else list(sd)
    bad_sd = [x for x in sd if x not in STUDY_DESIGNS]
    if bad_sd:
        errs.append(f"gating.study_design {bad_sd} 不在报告清单层的词表 {STUDY_DESIGNS} 内 —— "
                    f"显式声明会**整体替换**推断结果，词表对不上等于把所有清单静默关掉")
    if not doc.get("submission_date"):
        errs.append("submission_date 缺失 —— predates 门控是全系统唯一的硬门控，不能没有它")

    # provenance：合成示例卡可豁免，真实抽取的卡必须给出处
    prov = doc.get("provenance") or {}
    if prov.get("source") != "synthetic_example":
        for key in ("condition.primary", "population.age_group", "care_setting", "clinical_task"):
            p = prov.get(key) or {}
            if not (p.get("quote") or p.get("status")):
                errs.append(f"provenance[{key}] 缺失 —— 抽卡器会幻觉，gating 字段必须可回溯到原文")
    d = doc.get("descriptive") or {}
    for k in ("comparator", "claimed_benefit", "intended_use"):
        if not d.get(k) and k not in (doc.get("absent_fields") or []):
            warns.append(f"descriptive.{k} 为空且未登记在 absent_fields —— "
                         f"「论文没写」(absent) 是审稿发现，「没抽出来」(not_extracted) 是故障，"
                         f"不区分会让缺口报告失真")
    excl = ((g.get("condition") or {}).get("excluded")) or []
    if excl and not isinstance(excl, list):
        errs.append("gating.condition.excluded 必须是列表")
    return errs, warns


# --------------------------------------------------------------------------
# 卡对象与兼容视图
# --------------------------------------------------------------------------
class ClaimCard:
    def __init__(self, doc: dict, path: str | None = None, legacy_input: bool = False):
        self.doc = doc
        self.path = path
        self.legacy_input = legacy_input

    @property
    def gating(self) -> dict:
        return self.doc.get("gating") or {}

    @property
    def descriptive(self) -> dict:
        return self.doc.get("descriptive") or {}

    @property
    def legacy(self) -> dict:
        """扁平视图：键名与旧卡一致，所有连接器无需改动。

        **门控键只由 gating 层生成**，这是与旧卡的关键差别：
        `disease_or_condition` 只取 `condition.primary.label`，合并症语境
        (`comorbid_context`) 与排除病种 (`excluded`) 各走各的键，不再混进病种字段
        —— 反例 ① 的修复点。
        """
        if self.legacy_input:
            return dict(self.doc)
        g, d = self.gating, self.descriptive
        cond = (g.get("condition") or {})
        primary = cond.get("primary") or {}
        pop = g.get("population") or {}
        age = pop.get("age_group") or "mixed"
        special = " ".join(pop.get("special") or [])
        out = {
            # —— 门控键（受控）——
            "disease_or_condition": primary.get("label") or "",
            "excluded_conditions": [str(x).lower() for x in (cond.get("excluded") or [])],
            "comorbid_context": cond.get("comorbid_context") or [],
            "condition_subtype": (cond.get("qualifiers") or {}).get("subtype") or "",
            "target_population": " ".join(x for x in (_AGE_WORDS.get(age, ""), special) if x),
            "care_setting": CARE_SETTINGS.get(g.get("care_setting"), g.get("care_setting") or ""),
            "clinical_task": g.get("clinical_task") or "",
            "evidence_stage": g.get("evidence_stage") or "",
            # 留空必须落成"没声明"，不能落成空串——curated_reporting 用真值判断，
            # 空串虽然也是假值，但列表形态更贴合它 set(declared) 的用法
            "study_design": g.get("study_design") or None,
            "region": self.doc.get("region") or "",
            "submission_date": self.doc.get("submission_date") or "",
            # —— 描述键（自由文本，只供检索扩展与相关度排序）——
            "population_description": d.get("target_population") or "",
        }
        for k in ("intended_use", "intended_user", "model_input", "model_output",
                  "clinical_decision_affected", "comparator", "claimed_benefit",
                  "claimed_harm_reduction", "deployment_claim_level", "clinical_context"):
            out[k] = d.get(k) or ""
        return out


def load_card(src, strict: bool = True) -> ClaimCard:
    """读卡。同时接受新式分层卡与旧式扁平卡。

    旧卡（顶层 `clinical_claim`，字段全扁平）原样透传并给出提示——它们的门控行为
    与改造前完全一致，方便逐张迁移时做 A/B 回归。
    """
    doc = yaml.safe_load(open(src)) if isinstance(src, str) else dict(src)
    path = src if isinstance(src, str) else None
    if "claim_card" in doc:
        doc = doc["claim_card"]
    if "gating" not in doc:                       # 旧式扁平卡
        flat = doc.get("clinical_claim", doc)
        return ClaimCard(flat, path, legacy_input=True)
    errs, warns = validate_card(doc)
    for w in warns:
        print(f"  [card warn] {w}")
    if errs:
        msg = "Claim Card 未通过校验门禁：\n  - " + "\n  - ".join(errs)
        if strict:
            raise CardError(msg)
        print(msg)
    return ClaimCard(doc, path)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="校验 Claim Card 并打印其门控视图")
    ap.add_argument("cards", nargs="+")
    a = ap.parse_args()
    for p in a.cards:
        print(f"\n=== {p} ===")
        try:
            c = load_card(p, strict=False)
        except Exception as e:
            print(f"  ✗ {type(e).__name__}: {e}")
            continue
        if c.legacy_input:
            print("  ⚠️ 旧式扁平卡（未分层）—— 门控行为同改造前，建议迁移")
        lg = c.legacy
        for k in ("disease_or_condition", "excluded_conditions", "comorbid_context",
                  "target_population", "care_setting", "clinical_task",
                  "evidence_stage", "study_design", "submission_date"):
            print(f"  {k:24s} {lg.get(k)!r}")


if __name__ == "__main__":
    main()
