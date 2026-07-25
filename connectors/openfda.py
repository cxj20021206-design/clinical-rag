"""openFDA 连接器 (无 key)。

用途：监管语境 —— 定义"监管认可的预期用途与已知风险"，喂
reference_standard / safety_harm_equity / generalization / workflow_deployment 模块。

--- 2026-07-25 重写：从药品库改接器械库 ---
旧实现只连了 `drug/label`（药品标签），按 `indications_and_usage:"<病种>"` 检索。
对一篇"病房脓毒症早期预警 AI"的论文，实测检回 **Silver Sulfadiazine（磺胺嘧啶银，
二三度烧伤创面外用抗菌乳膏）**——只因标签里出现 wound sepsis 这个词。
审的是医疗 AI 软件，该查的是**器械**库，不是药品库。

现在按价值从高到低查三个端点：

1. `device/classification` —— **最高价值**。FDA 对每类器械的法定定义，`definition`
   字段就是官方**预期用途**原文。例："Lung Computed Tomography System,
   Computer-Aided Detection"(Class II, 21 CFR 892.2050) 定义为
   "To assist radiologists in the review of ... and highlight potential nodules
   **that the radiologist should review**"。这句话能直接支撑"本文声称可独立出报告，
   超出该类产品监管定位"这种意见。
2. `device/510k` —— 同类产品的**已获批先例**（谁、什么时候、什么产品码获批）。
   例：脓毒症卡检出 Bayesian Health Sepsis Flagging Device、Sepsis ImmunoScore。
3. `drug/label` —— 仅当卡片明确涉及**用药决策**时才查，且排在最后。否则纯噪声。

predates：510k 按 `decision_date`（可作检索条件），classification 无发布日 → unknown。
"""
from __future__ import annotations
import requests
from base import (Connector, UA, clean_text, keywords,
                  NON_CLINICAL, GENERIC_CLINICAL)
from schema import ExternalStandard, compute_predates

CLASSIFICATION = "https://api.fda.gov/device/classification.json"
DEVICE_510K = "https://api.fda.gov/device/510k.json"
DRUG_LABEL = "https://api.fda.gov/drug/label.json"

# 卡片的 model_output / intended_use 用词 → FDA 器械产品类别词汇。
# 论文说"结节检出"，FDA 那边叫 "computer aided detection"；不做这层翻译就查不到。
CATEGORY_VOCAB = [
    (("detect", "detection", "nodule", "lesion", "finding", "segmentation"),
     "computer aided detection"),
    (("triage", "prioriti", "notification", "alert", "warning", "flag"),
     "triage"),
    (("risk", "score", "predict", "prognos", "stratif"),
     "risk assessment"),
]
# 只有涉及用药决策时才值得查药品标签
DRUG_HINTS = ("drug", "antibiotic", "therapy", "treatment", "dose", "dosing",
              "medication", "prescri", "regimen")


class OpenFDAConnector(Connector):
    source_id = "openfda"
    issuing_body = "US FDA (openFDA)"
    source_role = "regulatory"
    tier = 3
    machine_access = "api"

    # ---------- 工具 ----------
    @staticmethod
    def _iso(ymd):
        """openFDA 日期常为 'YYYYMMDD'。"""
        if ymd and len(ymd) == 8 and str(ymd).isdigit():
            return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
        return ymd

    def _get(self, url, params) -> list[dict]:
        """openFDA 无命中时返回 404（不是空数组），必须当正常情况处理。"""
        try:
            r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=45)
            if r.status_code == 404:
                return []
            r.raise_for_status()
            return r.json().get("results", [])
        except Exception as e:
            print(f"  [openfda] {url.rsplit('/', 1)[-1]} 失败: {str(e)[:70]}")
            return []

    @staticmethod
    def _categories(qc) -> list[str]:
        text = " ".join(str(qc.get(k, "")) for k in
                        ("model_output", "intended_use", "intervention",
                         "clinical_decision_affected")).lower()
        return [term for hints, term in CATEGORY_VOCAB if any(h in text for h in hints)]

    # ---------- 主入口 ----------
    def search(self, query_context, submission_date=None, limit=5):
        cond = query_context.get("condition") or query_context.get("population", "")
        cond_terms = [t for t in keywords(cond, max_terms=4) if t not in NON_CLINICAL]
        if not cond_terms:
            # 病种字段里没有任何临床词（纯方法学论文，如"representation learning
            # benchmark"）→ 本来就没有对应的监管品类，空手比硬凑更诚实。
            return []
        # 短语落空后的降级顺序：非泛化词优先、再长词优先。
        # 否则 "diabetic retinopathy" 会先用 diabetic 去查，检回"糖尿病针头废弃盒"。
        fallback_terms = sorted(cond_terms,
                                key=lambda t: (t in GENERIC_CLINICAL, -len(t)))
        out: list[ExternalStandard] = []
        seen: set = set()

        def add(rec, key):
            if key in seen or len(out) >= limit:
                return False
            seen.add(key)
            out.append(rec)
            return True

        # --- 1) 器械分类：官方预期用途定义 ---
        # 先按产品类别词查（"结节检出"→"computer aided detection"），再按与病种的
        # 契合度排序。给分类留一半配额，另一半留给 510(k) 先例，否则分类会把它挤光。
        cls_quota = max(1, limit // 2)
        cond_phrase = clean_text(cond)
        # 两路查：① 病种本身（"diabetic retinopathy" → Diabetic Retinopathy Detection
        # Device, 21 CFR 886.1100）② 产品类别词。只查类别词会给视网膜病变论文返回
        # "结肠CT计算机辅助检测"——类别对了但器官不对。
        queries = [q for q in ([cond_phrase] + fallback_terms[:2] +
                               self._categories(query_context)) if q]
        cands, seen_pc = [], set()
        for q in queries:
            for d in self._get(CLASSIFICATION, {"search": f'device_name:"{q}"', "limit": 10}):
                pc = d.get("product_code")
                if pc in seen_pc:
                    continue
                seen_pc.add(pc)
                name = str(d.get("device_name", "")).lower()
                cands.append((name, name + " " + str(d.get("definition", "")).lower(), d))

        # 按病种契合度排序：命中在 device_name 里权重加倍；泛化医学词（cancer/
        # screening/risk 这类满库都是的）大幅降权。否则 "lung cancer screening" 里的
        # cancer 会让"遗传性肿瘤易感基因测序"压过"肺部CT计算机辅助检测"。
        # 不用池内 IDF——查询词本身会污染池子（拿 lung 查就会灌进一堆 lung 条目，
        # 反而把 lung 的权重压低），是反的。
        # 还要按**功能**加分，否则"电场肿瘤治疗仪(非小细胞肺癌)"会压过"肺部CT
        # 计算机辅助检测"——两者都带 lung+cancer，但前者是治疗器械，本文做的是检出。
        cats = self._categories(query_context)
        cat_words = {w for c in cats for w in c.split() if w not in ("computer", "aided")}
        # 模态也参与打分：卡说 low-dose chest CT，那"肺部CT计算机辅助检测"就该压过
        # "肺纤维化影像转诊软件"（两者都是 lung + 影像软件，但只有前者是 CT）。
        mod_words = {w for w in keywords(query_context.get("model_input", ""), max_terms=4)
                     if w not in NON_CLINICAL}

        def _w(t):
            return 0.25 if t in GENERIC_CLINICAL else 1.0
        scored = []
        for name, blob, d in cands:
            sc = sum(_w(t) * (2.0 if t in name else 1.0) for t in cond_terms if t in blob)
            if cat_words and any(w in blob for w in cat_words):
                sc += 2.0          # 功能对得上（检出/分诊/风险评估）
            if mod_words and any(w in blob for w in mod_words):
                sc += 1.0          # 模态对得上（CT / X 线 / 眼底 ...）
            scored.append((sc, name, d))
        scored.sort(key=lambda x: -x[0])
        best = scored[0][0] if scored else 0.0

        top_codes: list[str] = []          # 供 510(k) 按产品码精确回查
        for score, _name, d in scored:
            if score == 0:
                break   # 一条与病种沾边的分类都没有 → 宁可不给，也不返回器官都不对的类别
            if d.get("product_code") and d["product_code"] not in top_codes:
                top_codes.append(d["product_code"])
            if len(out) >= cls_quota:
                continue                   # 配额满了仍继续收产品码，只是不再产出记录
            defn = (d.get("definition") or "").strip()
            if not defn:
                continue
            name = d.get("device_name", "")
            reg = d.get("regulation_number") or ""
            klass = d.get("device_class") or ""
            add(ExternalStandard(
                source_id=self.source_id, issuing_body=self.issuing_body,
                document_type="regulatory",
                title=f"FDA 器械分类: {name}",
                canonical_url=("https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/"
                               f"cfcfr/CFRSearch.cfm?FR={reg}" if reg
                               else "https://open.fda.gov/apis/device/classification/"),
                version_or_publication_date=None, retrieved_date=self.today(),
                region="US",
                intended_use_or_decision_point="FDA 法定预期用途 (device classification)",
                recommendation_or_requirement=defn,
                passage=f"[Class {klass} · 21 CFR {reg} · product code "
                        f"{d.get('product_code','')}] {defn}"[:1000],
                section_page_table=f"21 CFR {reg}" if reg else d.get("product_code", ""),
                source_role=self.source_role, tier=self.tier,
                machine_access=self.machine_access, license="openFDA (US gov)",
                source_quality="regulatory_classification",
                predates_paper_submission="unknown",   # 分类条目无发布日
                notes="FDA 对该类器械的法定预期用途定义；本文若声称超出此范围需额外证据",
                query_context=query_context,
            ), ("cls", d.get("product_code"), reg))

        # --- 2) 510(k)：同类已获批先例（predates 作检索条件） ---
        date_clause = ""
        if submission_date:
            d0 = str(submission_date)[:10].replace("-", "")
            date_clause = f" AND decision_date:[19760101 TO {d0}]"
        # **优先按产品码回查**——用上一步分类命中的 product_code，这是 FDA 自己的
        # 数据模型：分类给品类，510(k) 给该品类下已获批的具体产品。
        # PIB → IDx-DR / EyeArt / iPredict-DR；OEB → syngo.CT Lung CAD / AVIEW Lung
        # Nodule CAD。按器械名瞎猜则会把 "diabetic" 检成"糖尿病针头废弃盒"。
        # 产品码落空再退到病种短语、最后退到单词。openFDA 无命中返 404，_get 已按空处理。
        queries_510k = ([f'product_code:"{c}"' for c in top_codes[:3]] +
                        [f'device_name:"{t}"' for t in
                         ([cond_phrase] if cond_phrase else []) + fallback_terms[:2]])
        for q in queries_510k:
            if len(out) >= limit:
                break
            for d in self._get(DEVICE_510K,
                               {"search": f'{q}{date_clause}', "limit": 5}):
                of = d.get("openfda", {}) or {}
                k = d.get("k_number", "")
                dec = d.get("decision_date")
                name = d.get("device_name") or of.get("device_name") or ""
                add(ExternalStandard(
                    source_id=self.source_id, issuing_body=self.issuing_body,
                    document_type="regulatory",
                    title=f"FDA 510(k) 获批先例: {name}",
                    canonical_url=("https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/"
                                   f"cfpmn/pmn.cfm?ID={k}"),
                    version_or_publication_date=dec, retrieved_date=self.today(),
                    region="US",
                    intended_use_or_decision_point="510(k) 实质等同获批",
                    recommendation_or_requirement=None,  # 获批记录不是"要求"
                    passage=f"[{k} · {d.get('applicant','')} · {dec}] {name}"
                            f" (Class {of.get('device_class','?')}, "
                            f"21 CFR {of.get('regulation_number','?')})",
                    section_page_table=k,
                    source_role=self.source_role, tier=self.tier,
                    machine_access=self.machine_access, license="openFDA (US gov)",
                    source_quality="regulatory_clearance",
                    predates_paper_submission=compute_predates(dec, submission_date),
                    notes="同类产品的监管先例；获批≠疗效证明，也≠本文方法已获批",
                    query_context=query_context,
                ), ("510k", k))

        # --- 3) 药品标签：仅当卡片涉及用药决策 ---
        card_text = " ".join(str(v) for v in query_context.values()
                             if isinstance(v, str)).lower()
        if len(out) < limit and any(h in card_text for h in DRUG_HINTS):
            for d in self._get(DRUG_LABEL,
                               {"search": f'indications_and_usage:"{clean_text(cond)}"',
                                "limit": 3}):
                of = d.get("openfda", {}) or {}
                name = (of.get("brand_name") or of.get("generic_name") or ["(unknown)"])[0]
                eff = self._iso(d.get("effective_time"))
                ind = " ".join(d.get("indications_and_usage", []) or [])[:800]
                warn = " ".join(d.get("warnings", []) or [])[:400]
                add(ExternalStandard(
                    source_id=self.source_id, issuing_body=self.issuing_body,
                    document_type="regulatory",
                    title=f"FDA 药品标签: {name}",
                    canonical_url="https://open.fda.gov/apis/drug/label/",
                    version_or_publication_date=eff, retrieved_date=self.today(),
                    region="US",
                    intended_use_or_decision_point="approved indication",
                    recommendation_or_requirement=ind or None,
                    passage=(ind + (" | WARNINGS: " + warn if warn else ""))[:1000] or None,
                    section_page_table="indications_and_usage",
                    source_role=self.source_role, tier=self.tier,
                    machine_access=self.machine_access, license="openFDA (US gov)",
                    source_quality="regulatory_label",
                    predates_paper_submission=compute_predates(eff, submission_date),
                    notes="仅因本卡涉及用药决策才检索；自发不良事件≠因果",
                    query_context=query_context,
                ), ("drug", name))
        return out[:limit]
