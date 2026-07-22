# clinical-rag —— MedAI 审稿的外部临床证据通道

独立项目。唯一职责：给定一篇论文的 **Clinical Claim Card**（PICO / intended-use / 证据阶段），
从分层的权威临床源检索"现实医学世界的标准"，返回带完整 provenance 的 `external_standard` 记录，
写入 Claim–Evidence Graph 的**外部侧**。

> 设计与决策的完整说明见 [`docs/DESIGN.md`](docs/DESIGN.md)。

## 铁律
- 外部源只定义论文**应该证明什么**；**绝不**替论文证明它"实际做到了什么"（那是内部原文核验，属另一子系统）。
- 每条外部证据保存完整溯源；`predates_paper_submission` 是硬字段——投稿后才出现的指南可评价"今天能否部署"，但不能指责作者违背当时尚不存在的标准。
- 只用开放/免费且许可允许的内容（access_class A/B/C 逐文档核对）。排除清单见 `clinical_sources.yaml: exclusions`。
- **国内源（NHC/NMPA/CDE/CMDE/ChiCTR）已剔除**（无 API/不易访问，2026-07-22 老师决定），记录在 `clinical_sources.yaml: deferred_sources` 备日后需要。

## 快速开始
```bash
cd /work/hdd/bgkq/xchen48/clinical-rag
python3 retrieve.py --claim examples/claim_card_lung_ct.yaml --per-source 3
# 输出：按 8 个审查模块分组的外部标准 + 无连接器角色(待策展)提示；结果写 store/retrieved.jsonl
```
指定模块：`--modules comparator_baseline endpoint_utility`

## 结构
```
clinical_sources.yaml   源注册表：41 个源 + 角色/地域/access_class/machine_access
                        + module_routing(8模块) + retrieval_order + exclusions + deferred_sources
schema.py               ExternalStandard 记录契约(§5) + validate + compute_predates + 原子写
retrieve.py             路由层：Claim Card → 模块路由 → 连接器(各调一次) → 分配到模块 → 写 store
connectors/
  base.py               连接器接口
  clinicaltrials.py     ClinicalTrials.gov v2   (registry；comparator/endpoint/population)
  europepmc.py          Europe PMC              (discovery；找系统综述/指南/文献，全模块补充)
  who_gho.py            WHO GHO OData           (epidemiology；疾病负担/unmet need)
  openfda.py            openFDA                 (regulatory；药品标签适应证/警示)
examples/               示例 Clinical Claim Card
store/                  检索记录缓存(jsonl，带日期 + query_context)
docs/DESIGN.md          完整设计文档
```

## 两条腿（刻意区分）
1. **干净 API 直连**（背景/发现证据，✅ 已建）：CT.gov / Europe PMC / WHO GHO / openFDA（无 key）。
   注册表里另有 4 个无 key Class-A 可加：pubmed_eutils / pmc_oa / crossref / mesh。
2. **规范指南策展摄入**（最高价值、最不 API 化，⏭ 待建）：WHO/NICE/USPSTF/学会指南多为 Class B PDF、
   许可敏感 → 按 Claim Card 小批策展摄入，不做通用爬虫。路由时这些角色会被标为"待策展"缺口。

## 状态（2026-07-22）
- ✅ 端到端跑通：注册表 / schema / 原子写 / 4 个连接器 / predates 门控 / 路由层 / 示例 Claim Card。
- ✅ 实测：肺癌筛查 Claim Card → CT.gov 出真实 comparator(AI vs 放射科医生)+endpoint，Europe PMC 出相关文献，
  投稿后文献被 `predates=false` 正确标记。
- ⏭ 待建（见 DESIGN.md §未来）：规范指南策展摄入层、更多无 key 连接器、Claim Card 自动抽取器(上游/内部)、
  与内部原文核验子系统对接成 Claim–Evidence Graph。
