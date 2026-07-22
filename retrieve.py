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

# 已实现的连接器：source_id -> 实例
CONNECTORS = {
    "clinicaltrials_gov": ClinicalTrialsConnector(),
    "europepmc": EuropePMCConnector(),
    "who_gho": WHOGHOConnector(),
    "openfda": OpenFDAConnector(),
}


def load_registry(path=None):
    return yaml.safe_load(open(path or os.path.join(HERE, "clinical_sources.yaml")))


def claim_to_query(card: dict) -> dict:
    """Clinical Claim Card → 连接器统一 query_context。"""
    return {
        "condition": card.get("disease_or_condition", ""),
        "intervention": card.get("intended_use") or card.get("model_output", ""),
        "population": card.get("target_population", ""),
        "outcome": card.get("claimed_benefit", ""),
        "setting": card.get("care_setting", ""),
        "region": card.get("region", ""),
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
    # 3) 结果分配到所有相关模块 (一条记录可跨模块)
    for module in modules:
        gaps[module] = roles_without_connector(registry, module, routing)
        for sid in module_sources[module]:
            results[module].extend(cache.get(sid, []))
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

    all_recs = []
    print("\n=== 检索结果 (按模块) ===")
    for module in (args.modules or registry["module_routing"].keys()):
        recs = results.get(module, [])
        gap = sorted(gaps.get(module, []))
        print(f"\n[{module}]  命中 {len(recs)} 条" +
              (f"  ⚠️无连接器角色(待策展): {gap}" if gap else ""))
        for r in recs[:3]:
            pre = r.predates_paper_submission
            flag = "" if pre == "true" else f"  <predates={pre}>"
            print(f"   - {r.source_id}: {(r.title or '')[:64]}{flag}")
        all_recs.extend(recs)

    # dedup 后统一写盘
    uniq = {(r.source_id, r.canonical_url): r for r in all_recs}
    atomic_write_jsonl(list(uniq.values()), args.out)
    errs = [e for r in uniq.values() for e in r.validate()]
    print(f"\n=== 写入 {args.out} ({len(uniq)} 条去重记录)；schema 错误: {errs or '无'} ===")


if __name__ == "__main__":
    main()
