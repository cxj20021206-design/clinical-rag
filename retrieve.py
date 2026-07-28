"""clinical-rag 检索路由层。

输入：Clinical Claim Card (PICO/intended-use) + 论文投稿日 + 目标审查模块。
流程：Claim Card → module_routing(源角色) → 有连接器的源 → 检索 →
      dedup → 按模块标记 → 写 store → 返回 external_standard 记录。

外部只回答"应该证明什么"。规范指南层(normative/evidence_synthesis/reporting_tool/
terminology)暂无自动连接器，需按 Claim Card 策展摄入，路由时会明确标出缺口。
"""
from __future__ import annotations
import os, sys, json, yaml, argparse
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "connectors"))
from schema import ExternalStandard, atomic_write_jsonl  # noqa: E402
from clinicaltrials import ClinicalTrialsConnector       # noqa: E402
from europepmc import EuropePMCConnector                 # noqa: E402
from who_gho import WHOGHOConnector                       # noqa: E402
from openfda import OpenFDAConnector                       # noqa: E402
from curated_reporting import build_connectors, applicability_report  # noqa: E402
from uspstf import USPSTFConnector                          # noqa: E402
from curated_guidelines import CuratedGuidelinesConnector    # noqa: E402

# 已实现的连接器：source_id -> 实例
CONNECTORS = {
    # ① API 直连（在线检索，结果随 Claim Card 变化）
    "clinicaltrials_gov": ClinicalTrialsConnector(),
    "europepmc": EuropePMCConnector(),
    "who_gho": WHOGHOConnector(),
    "openfda": OpenFDAConnector(),
}
# ② 策展摄入·报告规范清单（离线固定内容，按研究设计/证据阶段做适用性门控）
CONNECTORS.update(build_connectors())
# ③ 策展摄入·临床指南 (normative)。按病种+场景门控，与 ② 同构但依据不同。
#    USPSTF = 预防/筛查；学会与国家 CPG = 急重症/治疗。两者互补，缺一边就有整类论文没人管。
_uspstf = USPSTFConnector()
if _uspstf.available():
    CONNECTORS["uspstf"] = _uspstf
_cpg = CuratedGuidelinesConnector()
if _cpg.available():
    CONNECTORS["society_guidelines"] = _cpg


def load_registry(path=None):
    return yaml.safe_load(open(path or os.path.join(HERE, "clinical_sources.yaml")))


def claim_to_query(card: dict) -> dict:
    """Clinical Claim Card → 连接器统一 query_context。

    前六个键给 API 连接器做查询翻译；后四个是策展层做适用性门控用的（证据阶段/
    研究设计/输入输出模态），API 连接器忽略即可。`_card` 传原卡，下划线前缀不落盘。
    """
    return {
        "condition": card.get("disease_or_condition", ""),
        "intervention": card.get("intended_use") or card.get("model_output", ""),
        "population": card.get("target_population", ""),
        "outcome": card.get("claimed_benefit", ""),
        "setting": card.get("care_setting", ""),
        "region": card.get("region", ""),
        # 门控字段
        "evidence_stage": card.get("evidence_stage", ""),
        "model_input": card.get("model_input", ""),
        "model_output": card.get("model_output", ""),
        "deployment_claim_level": card.get("deployment_claim_level", ""),
        "_card": card,
    }


def sources_for_module(registry, module, routing):
    """某模块相关的源：源自声明服务该模块 OR 角色被 module_routing 命中 OR
    是 discovery(文献发现，对所有模块作补充)。"""
    roles = set(routing.get(module, []))
    out = []
    for s in registry["sources"]:
        role = s.get("source_role")
        if module in (s.get("modules") or []) or role in roles or role == "discovery":
            out.append(s["id"])
    return out


def roles_without_connector(registry, module, routing):
    """module_routing 里点名、但没有任何带连接器源的角色 → 待策展缺口。"""
    gaps = set()
    for role in routing.get(module, []):
        srcs = [s["id"] for s in registry["sources"] if s.get("source_role") == role]
        if not any(sid in CONNECTORS for sid in srcs):
            gaps.add(role)
    return gaps


def retrieve(card: dict, submission_date: str | None,
             modules: list[str] | None = None, per_source: int = 4,
             registry=None):
    registry = registry or load_registry()
    routing = registry["module_routing"]
    modules = modules or list(routing.keys())
    qctx = claim_to_query(card)

    results = defaultdict(list)        # module -> [ExternalStandard]
    gaps = defaultdict(set)            # module -> {待策展角色}

    # 1) 每模块相关且有连接器的源
    module_sources = {m: [sid for sid in sources_for_module(registry, m, routing)
                          if sid in CONNECTORS] for m in modules}
    # 2) 每个连接器只调一次 (缓存)
    cache: dict[str, list] = {}
    for sid in {s for sids in module_sources.values() for s in sids}:
        try:
            cache[sid] = CONNECTORS[sid].search(qctx, submission_date, limit=per_source)
        except Exception as e:
            print(f"  [warn] {sid} 检索异常: {str(e)[:80]}")
            cache[sid] = []
    # 3) 结果分配到相关模块。记录若自带 modules 声明 (策展条目)，只落到声明的模块；
    #    留空的 (API 连接器) 沿用原行为：按源角色散射到所有相关模块。
    for module in modules:
        gaps[module] = roles_without_connector(registry, module, routing)
        for sid in module_sources[module]:
            for rec in cache.get(sid, []):
                if not rec.modules or module in rec.modules:
                    results[module].append(rec)
    return results, gaps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claim", required=True, help="Clinical Claim Card YAML 路径")
    ap.add_argument("--out", default=os.path.join(HERE, "store", "retrieved.jsonl"))
    ap.add_argument("--modules", nargs="*", default=None)
    ap.add_argument("--per-source", type=int, default=4)
    args = ap.parse_args()

    card = yaml.safe_load(open(args.claim))
    card = card.get("clinical_claim", card)  # 兼容带/不带顶层键
    submission = card.get("submission_date")
    registry = load_registry()

    results, gaps = retrieve(card, submission, args.modules, args.per_source, registry)

    # 策展清单的适用性判定：启用了哪些、以及**没启用哪些和为什么**
    print("\n=== 报告规范清单适用性 (策展层) ===")
    for a in applicability_report(card):
        if not a["applicable"]:
            mark = "⛔"                      # 不适用——理由必须打印，防止越级要求
        elif a["n_items"] == 0:
            mark = "🕳"                      # 适用但一条都没摄入 = 真缺口，不能显示成已覆盖
        elif a["completeness"] != "full":
            mark = "🟡"                      # 适用但摄入不完整
        else:
            mark = "✅"
        print(f" {mark} {a['name']:18s} ({a['n_items']:3d} 条, 完整性={a['completeness']}"
              f", 发布 {a['publication_date']})")
        print(f"      {a['reason']}")
        if a["applicable"] and a["completeness"] != "full":
            print(f"      ⚠️ {(a['completeness_note'] or '').strip()[:170]}")
        if a["applicable"] and submission and a["publication_date"] and \
                a["publication_date"] > submission:
            print(f"      ⏱ 发布于投稿日 {submission} 之后 → predates=false，"
                  f"可用于'今天能否部署'，不得据此指责作者。")

    # 临床指南层 (normative) 的适用性判定：同样要求不适用时说明理由
    print("\n=== 临床指南适用性 (normative 策展层) ===")
    if "uspstf" in CONNECTORS:
        rep = CONNECTORS["uspstf"].report(card)
        print(f" {'✅' if rep['applicable'] else '⛔'} USPSTF ({rep['n_recs']} 条推荐)")
        print(f"      {rep['reason']}")
        for t in rep["topics"]:
            print(f"      · {t['title'][:66]}  Grade={t['grades']} ({t['date']})")
    else:
        print(" 🕳 USPSTF 未摄入 — 先跑 python3 connectors/uspstf_ingest.py")

    if "society_guidelines" in CONNECTORS:
        for g in CONNECTORS["society_guidelines"].report(card):
            mark = "✅" if g["applicable"] else "⛔"
            if g.get("curation_level") == "auto":      # 自动扩库产物必须自报身份
                mark += "🤖" + ("⚠️" if g.get("needs_review") else "")
            print(f" {mark} {(g['name'] or g['slug'])[:34]:34s} "
                  f"({g['n_recs']}/{g['n_total']} 条推荐, {g['publication_date']}, "
                  f"{g['issuing_body'][:34]})")
            print(f"      {g['reason']}")
            if g["setting_note"]:
                print(f"      {g['setting_note']}")
            if g["applicable"] and submission and g["publication_date"] and \
                    g["publication_date"] > submission:
                print(f"      ⏱ 发布于投稿日 {submission} 之后 → predates=false，"
                      f"不得据此指责作者。")
    else:
        print(" 🕳 学会/国家 CPG 未摄入 — 先跑 python3 connectors/guideline_ingest.py")
    print("    ⚠️ 其余 normative 源 (WHO IRIS / VA-DoD) 仍待策展；"
          "NICE 因 AI 用途许可禁令不可行，见 docs/RELATED_WORK.md §2。"
          "未摄入的具体指南与原因见 curated/guidelines/manifest.yaml: deferred。")

    all_recs = []
    print("\n=== 检索结果 (按模块) ===")
    for module in (args.modules or registry["module_routing"].keys()):
        recs = results.get(module, [])
        gap = sorted(gaps.get(module, []))
        by_src = defaultdict(int)
        for r in recs:
            by_src[r.source_id] += 1
        # normative 仍是缺口（发现层只有题录+摘要，拿不到条文原文），但发现层检出的
        # 指南/共识可直接当作"待策展摄入"的工作清单，所以顺带报数。
        cand = [r for r in recs if r.document_type in ("guideline", "consensus")]
        gap_line = ""
        if gap:
            gap_line = f"\n   ⚠️无连接器角色(待策展): {gap}"
            if "normative" in gap and cand:
                gap_line += f" — 发现层已检出 {len(cand)} 篇候选指南/共识可供策展"
        print(f"\n[{module}]  命中 {len(recs)} 条 {dict(by_src)}" + gap_line)
        for r in recs[:3]:
            pre = r.predates_paper_submission
            flag = "" if pre == "true" else f"  <predates={pre}>"
            print(f"   - {r.source_id}: {(r.title or '')[:64]}{flag}")
        all_recs.extend(recs)

    # dedup 后统一写盘。键含 section_page_table——同一份清单的不同条目共用 canonical_url，
    # 只按 (source_id, url) 去重会把整份清单塌成一条。
    uniq = {(r.source_id, r.canonical_url, r.section_page_table): r for r in all_recs}
    atomic_write_jsonl(list(uniq.values()), args.out)
    errs = [e for r in uniq.values() for e in r.validate()]
    print(f"\n=== 写入 {args.out} ({len(uniq)} 条去重记录)；schema 错误: {errs or '无'} ===")


if __name__ == "__main__":
    main()
