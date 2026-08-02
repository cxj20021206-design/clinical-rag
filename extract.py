"""抽卡流程 driver —— 阶段 ②③⑥ 的程序侧。

## 这个文件补的是什么口子

`prompts/` 那四个文件是**指令**,`EXTRACTOR_DESIGN.md` 是**设计**,中间缺的是**执行**:
2026-07-31 第一次抽 LDH 那篇时,是执行者(Claude)照着框架临场做的——阶段一的
`paper_overview` 根本没产出、阶段三的七问一条没跑,而两张卡**通过了全部程序校验、
27/27 引文定位成功,从外面看不出任何问题**。

结论不是"下次注意",是**流程里凡是靠人记得去做的环节,迟早会漏**。所以:

| 靠什么保证 | 之前 | 现在 |
|---|---|---|
| 阶段不被跳过 | 执行者自觉 | `stage2` 找不到 `paper_overview` 直接拒绝跑 |
| 阶段三换上下文 | prompt 里写"必须新开会话" | **新开一个 `claude -p` 进程**,`context_isolated` 由本程序写入卡外的 manifest,不让执行者自己声明 |
| 第 4/7 问单独调用 | prompt 里写"必须单独一次调用" | 一次 `stage3` 调用 = 一个问题子集 = 一个进程,默认拆成 `1,2,3,5,6` / `4` / `7` 三次 |
| 阶段三看不到阶段二的推理 | prompt 里写"不给你,也不该要" | **白名单装配**:stage3 的 bundle 组装函数根本不读 02_overview/,物理上没有可泄漏的东西 |

## 为什么是 bundle + 可插拔执行,而不是直接调 API

本仓库没有任何 LLM 调用代码,环境里也没有 `anthropic` 包和 API key。与其写一段没跑过的
API 代码,不如把"一次调用所需的全部输入"物化成一个自包含的 **bundle**(`request.md` +
`manifest.yaml`),再由后端去执行:

- `--backend bundle`(默认):只装配,不执行。人可以把 `request.md` 贴进任何会话。
- `--backend claude-cli`:`claude -p < request.md` —— **新进程 = 新上下文**,阶段三要的
  隔离在这里是操作系统给的,不是靠模型自律。
- `--backend exec --exec-cmd "..."`:任意外部命令,收 bundle 目录、写 `response.txt`。

无论走哪个后端,产出都要过同一道 ③④ 硬门(`evidence.verify_card` +
`claim_card.validate_card`),不过就退非零并要求**回阶段二重填**——按
`prompts/README.md`:不要手工改卡去迁就校验,那是把故障洗成"论文没写"。

## 一个刻意的不对称:模型读 md,程序核验 content_list.json

两者是**同一次 MinerU 解析**的两种序列化(本程序强制它们同目录,见 `_parse_paths`)。
模型读 md 是因为它可读;核验用 content_list.json 是因为它带 `page_idx`,而页码必须由
程序反查(模型填页码基本必错,且错页码是"最像正确"的那种错)。manifest 里
`model_read` 与 `verified_against` 两个字段分别记录,不合并成一个 `source`——
它们的用途不同,合并就说不清"卡上的 source 到底指谁"。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys

import yaml

import claim_card
import evidence

ROOT = os.path.dirname(os.path.abspath(__file__))

# 每个阶段允许看见的材料。**白名单,不是黑名单**——加新材料要显式改这里,
# 而不是"忘了排除"就泄漏进去。
STAGE_SPEC = {
    "1": {
        "prompt": "prompts/stage1_overview.md",
        "out_dir": "02_overview",
        "needs": [],                       # 前置产物
        "extra_docs": [],
    },
    "2": {
        "prompt": "prompts/stage2_fill_card.md",
        "out_dir": "03_cards",
        "needs": ["02_overview/paper_overview.yaml"],
        "extra_docs": ["docs/CARD_EXTRACTION_SPEC.md"],
    },
    "3": {
        "prompt": "prompts/stage3_adversarial_check.md",
        "out_dir": "04_check",
        "needs": [],                       # ← 刻意为空:阶段三不得看见阶段一/二的产物
        # extra_docs 刻意为空。CARD_EXTRACTION_SPEC.md 是从 6 篇真实论文归纳出来的,
        # **逐条点名那 6 篇**并在 §12 给出它们跑出来的结果。喂给阶段三 =
        # 被审论文若是其中一篇,核查者先拿到了标准答案(与 ctg_fetal 那次循环论证同型)。
        # 阶段三真正需要的那几条填卡约定,已以不点名论文的形式写进 prompts/stage3 §「填卡约定」。
        "extra_docs": [],
    },
}

# 阶段三的默认切分:第 4、7 问必须单独一次调用(prompts/stage3 §「单独跑的两问」)。
STAGE3_SPLITS = ["1,2,3,5,6", "4", "7"]


# ---------------------------------------------------------------- run 目录

def _parse_paths(run: str) -> tuple[str, str]:
    """定位 MinerU 解析产物,返回 (给模型读的 md, 给程序核验的 content_list.json)。

    强制两者同目录:拿 A 次解析抽卡、用 B 次解析核验,引文会被判定位失败 →
    标成 not_extracted(硬错误)→ 把解析层差异误报成抽取故障(2026-07-31 实测)。
    """
    base = os.path.join(run, "01_parse")
    md = cl = None
    for dirpath, _, files in os.walk(base):
        for f in files:
            if f.endswith("_content_list.json"):
                cl = os.path.join(dirpath, f)
            elif f.endswith(".md") and not f.startswith("parse_notes"):
                md = os.path.join(dirpath, f)
    if not md or not cl:
        sys.exit(f"[错误] {base} 下找不到成对的 MinerU 产物(*.md + *_content_list.json)。\n"
                 f"        解析必须先跑,且必须上 GPU 节点(登录节点会被 BrokenProcessPool 杀)。")
    if os.path.dirname(md) != os.path.dirname(cl):
        sys.exit(f"[错误] md 与 content_list.json 不在同一次解析产物里:\n  {md}\n  {cl}")
    return md, cl


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


STAGE3_SAFE = ("<!-- STAGE3_SAFE_BEGIN", "<!-- STAGE3_SAFE_END -->")


def _readme_for(stage: str) -> str:
    """阶段三只拿 README 里被 STAGE3_SAFE 标记圈起来的那一段,不拿全文。

    README 的开头与文末都含**关于具体论文的场外信息**(流程事故复盘拿被审的那篇当反例;
    附录列规则来历时会点名论文)。核查者读到会先入为主。这不是阶段二的推理泄漏,
    但同样是场外信息,按 §「阶段三必须换上下文」的精神一并挡掉。

    **按标记切,不按标题名切。** 2026-08-01 之前这里 find 的是标题字符串
    "## 三条贯穿所有阶段的硬约束";改标题(三条→五条)会让它找不到,然后**默默退回整份
    README**——泄漏原样放回来且不报错。同一类静默失效本项目已记过多次(§6d 表格抽取
    10 冒充 41、§6e 去重塌成每份 1 条),这里改成**找不到就硬退出**。
    """
    txt = _read(os.path.join(ROOT, "prompts/README.md"))
    if stage != "3":
        return txt
    b, e = txt.find(STAGE3_SAFE[0]), txt.find(STAGE3_SAFE[1])
    if b < 0 or e < 0 or e <= b:
        sys.exit(f"[错误] prompts/README.md 里找不到成对的 {STAGE3_SAFE[0]}…{STAGE3_SAFE[1]} 标记。\n"
                 f"        阶段三只能装配标记之间的内容;宁可停下,也不能退回整份 README —— \n"
                 f"        README 的开头与附录含关于具体论文的场外信息,会破坏阶段三的隔离。")
    seg = txt[txt.find("-->", b) + 3: e]
    return seg.strip()


def _audit_stage3_leak(instructions: str) -> list[str]:
    """扫阶段三**指令段**里有没有混进关于某篇具体论文的场外信息。

    只扫指令段(卡与论文全文之前的部分):卡和论文本来就该出现在那里。

    判据是机械的 —— 指令段不得提到语料库里任何一篇论文的标识:
    `corpus/gold/*.yaml` 的名字、`sample/*` 的 run 目录名、语料目录名。理由:
    这些名字只会出现在"举例说明"里,而举的例子若正好是被审的那篇,核查者就先拿到了
    答案(与 ctg_fetal 那次循环论证同型);即使不是那篇,也是无关的场外信息。

    2026-08-01 实测抓到两处真泄漏:`docs/CARD_EXTRACTION_SPEC.md` 逐条点名 6 篇 gold
    论文却被当作"字段判据"喂进来;卡的头部注释写了它属不属于 gold 语料。
    两处都是人工 grep 才发现的 —— 所以改成装配时自动扫。
    """
    tokens = set()
    for p in glob.glob(os.path.join(ROOT, "corpus/gold/*.yaml")):
        tokens.add(os.path.basename(p)[:-5])
    for p in glob.glob(os.path.join(ROOT, "sample/*")):
        if os.path.isdir(p):
            tokens.add(os.path.basename(p))
    tokens |= {"corpus/gold", "corpus/text", "corpus/extracted"}
    low = instructions.lower()
    return sorted(t for t in tokens if t and t.lower() in low)


def _claim_constraints(overview_path: str, claim: str) -> str:
    """把阶段一登记的**队列白名单**与**未取到的材料**摘成显式约束,附在阶段二的 request 里。

    阶段二本来就能读到整份 paper_overview,但"白名单藏在一份大 YAML 里"和
    "白名单被单独列出来"对模型不是一回事 —— 后者才是 prompts/stage2 §0 纪律一、二
    可执行的形态。**摘录是纯机械动作**(按 claim_id 取 uses_cohorts / unavailable_content),
    判断仍在模型那边。

    顺带堵一个此前无人负责的洞:`--claim` 打错字时,原来只会照常装配、照常出卡,
    卡里 claim_id 是错的而流程一路绿灯。现在直接退出。
    """
    try:
        ov = (yaml.safe_load(_read(overview_path)) or {}).get("paper_overview") or {}
    except Exception as e:                       # 阶段一产物坏了要当场停,不要带病往下走
        sys.exit(f"[错误] 读不动 {overview_path}: {e}")

    cands = ov.get("claim_candidates") or []
    ids = [c.get("claim_id") for c in cands]
    hit = next((c for c in cands if c.get("claim_id") == claim), None)
    if hit is None:
        sys.exit(f"[错误] 阶段一的 paper_overview 里没有 claim_id={claim!r}。\n"
                 f"        已登记的是:{ids}\n"
                 f"        要么 --claim 打错了,要么阶段一漏登记了这个主张 —— 两种都得先修。")

    allow = list(hit.get("uses_cohorts") or [])
    by_id = {c.get("cohort_id"): c for c in (ov.get("cohorts") or [])}
    unknown = [c for c in allow if c not in by_id]
    if unknown:
        sys.exit(f"[错误] claim {claim} 的 uses_cohorts 引用了未登记的队列 {unknown}。\n"
                 f"        cohorts 里有的是:{sorted(k for k in by_id if k)}")

    lines = ["\n\n---\n\n## 本卡可用的队列(白名单 —— 阶段一 `uses_cohorts`)\n",
             "本卡的任何人群特征、任何性能数字、任何场景描述,**只能来自下列队列**。",
             "清单之外的队列即使论文里有、数字即使真实,也不得用来填本卡"
             "(prompts/stage2 §0 纪律一)。\n"]
    for cid in allow:
        c = by_id[cid]
        bits = [f"purpose={c.get('purpose')}", f"n={c.get('n')}"]
        if c.get("subgroup_restrictions"):
            bits.append(f"**亚组限制**={c['subgroup_restrictions']}")
        if c.get("characteristics_locator"):
            bits.append(f"人群特征在={c['characteristics_locator']}")
        lines.append(f"- `{cid}` — {c.get('source') or ''}  ({'; '.join(str(b) for b in bits)})")
    if not allow:
        lines.append("- (阶段一没给本主张登记任何队列 —— 涉及队列的字段一律 `not_extracted`)")

    blocked = [u for u in (ov.get("unavailable_content") or [])
               if not u.get("affects_claims") or claim in (u.get("affects_claims") or [])]
    if blocked:
        lines.append("\n### 材料未给全的部分(禁令 —— 阶段一 `unavailable_content`)\n")
        lines.append("落在下列部分里的内容一律标 `not_extracted`,"
                     "**不得用主文里别的队列的表来代替**(§0 纪律二)。\n")
        for u in blocked:
            lines.append(f"- {u.get('part')} {u.get('pages') or ''} — {u.get('contains') or ''}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- bundle 装配

def build_bundle(run: str, stage: str, label: str, card: str | None = None,
                 claim: str | None = None, questions: str | None = None) -> str:
    """把一次调用所需的全部输入物化成 bundle 目录,返回该目录路径。

    **隔离在这里发生**:函数只往 request 里放 STAGE_SPEC 白名单允许的东西。
    阶段三的 needs 为空,所以 02_overview/ 与既有 04_check/ 连读都不会被读。
    """
    spec = STAGE_SPEC[stage]
    md, content_list = _parse_paths(run)
    bundle = os.path.join(run, "_bundles", f"stage{stage}_{label}")
    os.makedirs(bundle, exist_ok=True)

    parts = [_readme_for(stage),
             "\n\n---\n\n",
             _read(os.path.join(ROOT, spec["prompt"]))]

    included, excluded = [], []

    if stage == "3":
        if not card:
            sys.exit("[错误] stage3 必须用 --card 指定待核查的卡")
        qs = questions or "1,2,3,4,5,6,7"
        parts.append(f"\n\n---\n\n## 本次运行只回答第 {qs} 问\n\n"
                     f"其余问题由别的隔离运行负责,**不要作答**。\n"
                     f"输出只给 YAML(`findings:` 下的条目),不要写文件,不要额外解说。\n")
        # 泄漏审计要在**加卡与论文之前**做:扫的是指令段,卡和全文本来就该在里面。
        leak = _audit_stage3_leak("".join(parts))
        if leak:
            sys.exit(f"[错误] 阶段三的指令段里提到了语料库中某些论文的标识:{leak}\n"
                     f"        指令段不得含任何关于具体论文的场外信息 —— 举的例子若正好是被审的\n"
                     f"        那篇,核查者就先拿到了答案。请把那些例子改成不点名的表述,\n"
                     f"        或把该文档移出 STAGE_SPEC['3']['extra_docs']。")
        parts.append(f"\n\n---\n\n## 待核查的卡\n\n```yaml\n{_read(card)}\n```\n")
        included.append({"path": os.path.relpath(card, ROOT), "role": "card_under_review"})
        # 显式记下"按设计被排除的东西",供审计。不记录 = 事后无法证明确实没给。
        excluded = [
            {"path": "02_overview/paper_overview.yaml", "why": "阶段一产物 —— 同血统偏好"},
            {"path": "04_check/*", "why": "既有核查结论 —— 会锚定本次判断"},
            {"path": "prompts/stage2_fill_card.md", "why": "填卡指令 —— 会诱导复述填卡推理"},
            {"path": "prompts/README.md(STAGE3_SAFE 标记之外的全部)",
             "why": "开头的流程事故复盘与文末的『规则的来历』会点名具体论文 —— "
                    "关于某篇论文的场外信息,只给标记之间那段"},
            {"path": "docs/CARD_EXTRACTION_SPEC.md",
             "why": "逐条点名归纳它的那几篇论文并给出其结果 —— 被审论文若在其中即循环论证;"
                    "所需约定已以不点名形式写进 prompts/stage3 §填卡约定"},
        ]
    else:
        for need in spec["needs"]:
            p = os.path.join(run, need)
            if not os.path.exists(p):
                sys.exit(f"[错误] 阶段{stage} 需要前置产物 {need},但它不存在。\n"
                         f"        阶段不能跳:先跑 `extract.py stage{int(stage)-1} --run {run}`。")
            parts.append(f"\n\n---\n\n## 前置产物 `{need}`\n\n```yaml\n{_read(p)}\n```\n")
            included.append({"path": need, "role": "prior_stage_output"})
        if stage == "2":
            if not claim:
                sys.exit("[错误] stage2 必须用 --claim 指定要填哪个 claim_id(一次只填一张卡)")
            parts.append(f"\n\n---\n\n## 本次要填的主张\n\n`claim_id: {claim}`。"
                         f"一次只填这一个,不要把其他主张混进来。\n"
                         f"`provenance.source` 填:`{os.path.relpath(content_list, ROOT)}`\n")
            parts.append(_claim_constraints(os.path.join(run, spec["needs"][0]), claim))

    for doc in spec["extra_docs"]:
        parts.append(f"\n\n---\n\n## 字段判据 `{doc}`\n\n{_read(os.path.join(ROOT, doc))}\n")
        included.append({"path": doc, "role": "spec"})

    paper = _read(md)
    parts.append(f"\n\n---\n\n## 论文全文(MinerU 版面解析产物)\n\n{paper}\n")
    included.append({"path": os.path.relpath(md, ROOT), "role": "paper_full_text",
                     "chars": len(paper)})

    request = "".join(parts)
    with open(os.path.join(bundle, "request.md"), "w", encoding="utf-8") as f:
        f.write(request)

    manifest = {
        "stage": stage,
        "label": label,
        "questions": questions,
        "model_read": os.path.relpath(md, ROOT),
        "verified_against": os.path.relpath(content_list, ROOT),
        "included": included,
        "excluded_by_design": excluded,
        "request_chars": len(request),
        # 由本程序写入,不接受执行者自报。阶段三只有走独立进程才为 true。
        "context_isolated": None,
        "backend": None,
    }
    with open(os.path.join(bundle, "manifest.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(manifest, f, allow_unicode=True, sort_keys=False)
    return bundle


# ---------------------------------------------------------------- 执行后端

_FENCE = re.compile(r"```(?:yaml|yml)?\s*\n(.*?)```", re.S)

# 模型常在输出里自报 context_isolated。它**猜不准**:2026-08-01 六次独立进程运行中,
# 三次自报了该字段,其中 claim_1_q12356 明明跑在新进程里却自报 false,另两次自报 true。
# 它无从知道自己被怎么调起来的。该字段由 land() 写进头部注释,正文里的一律剥掉 ——
# 留两个来源不同、可能矛盾的同名字段,下游读哪个都是错。
_SELF_ISO = re.compile(r"^[^\S\n]*context_isolated\s*:\s*(\S+)[^\S\n]*$\n?", re.M)


def _extract_yaml(text: str) -> str:
    """从模型输出里取 YAML。有围栏取围栏内最长的一段,没有就原样返回。"""
    blocks = _FENCE.findall(text)
    return max(blocks, key=len).strip() + "\n" if blocks else text.strip() + "\n"


def run_backend(bundle: str, backend: str, exec_cmd: str | None,
                model: str | None) -> tuple[str | None, bool]:
    """执行 bundle,返回 (模型输出文本 或 None, 是否为独立上下文)。"""
    req = os.path.join(bundle, "request.md")
    if backend == "bundle":
        return None, False
    if backend == "claude-cli":
        cmd = ["claude", "-p"]
        if model:
            cmd += ["--model", model]
        # 新进程 = 新上下文。阶段三要的独立性由这里保证,不靠模型自律。
        r = subprocess.run(cmd, stdin=open(req, encoding="utf-8"),
                           capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"[错误] claude CLI 退出码 {r.returncode}\n{r.stderr[-2000:]}")
        return r.stdout, True
    if backend == "exec":
        if not exec_cmd:
            sys.exit("[错误] --backend exec 需要 --exec-cmd")
        r = subprocess.run(exec_cmd, shell=True, cwd=bundle,
                           capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"[错误] 外部命令退出码 {r.returncode}\n{r.stderr[-2000:]}")
        resp = os.path.join(bundle, "response.txt")
        return (_read(resp) if os.path.exists(resp) else r.stdout), True
    sys.exit(f"[错误] 未知后端 {backend}")


def _stamp_manifest(bundle: str, **kw) -> None:
    p = os.path.join(bundle, "manifest.yaml")
    m = yaml.safe_load(_read(p))
    m.update(kw)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(m, f, allow_unicode=True, sort_keys=False)


# ---------------------------------------------------------------- ③④ 硬门

def gate(card_path: str, content_list: str) -> int:
    """③ 引文核验 + ④ 规则校验。顺序不能反,理由见 EXTRACTOR_DESIGN §2。"""
    doc = yaml.safe_load(_read(card_path))
    doc = doc.get("claim_card", doc)
    src = evidence.SourceDoc.from_mineru(content_list)

    bad = []
    results = evidence.verify_card(doc, src)
    print(f"\n③ 引文核验  ({len(src.pages)} 页 / {len(src.full)} 字符)")
    for key, r in results.items():
        mark = f"OK[{r.tier}]" if r.found else "**MISS**"
        page = f" p.{r.page}" if r.page else ""
        print(f"   {mark:18s}{page:6s} {key:28s} {r.reason or ''}")
        if not r.found:
            bad.append(key)
    ok = len(results) - len(bad)
    print(f"   定位成功 {ok}/{len(results)}")

    errs, warns = claim_card.validate_card(doc)
    print("\n④ 规则校验")
    for e in errs:
        print(f"   ERROR   {e}")
    for w in warns:
        print(f"   WARN    {w}")
    if not errs and not warns:
        print("   零错零警告")

    if bad or errs:
        print(f"\n✗ 不通过。**回阶段二重填**(`extract.py stage2 --run ... --claim ...`),"
              f"\n  不要手工改卡去迁就校验 —— 那是把故障洗成\"论文没写\"(prompts/README.md)。")
        if bad:
            print(f"  定位不到的字段应改标 not_extracted(系统故障,不是审稿发现):{', '.join(bad)}")
        return 1
    print("\n✓ 通过 ③④,卡可进入检索")
    return 0


# ---------------------------------------------------------------- 子命令

def cmd_stage(a) -> int:
    run = a.run.rstrip("/")
    stage = a.stage
    if stage == "3":
        splits = [a.questions] if a.questions else STAGE3_SPLITS
        cards = a.card if isinstance(a.card, list) else [a.card]
        rc = 0
        for card in cards:
            slug = os.path.basename(card).rsplit(".", 1)[0]
            for qs in splits:
                label = f"{slug}_q{qs.replace(',', '')}"
                rc |= _one(run, stage, label, a, card=card, questions=qs)
        return rc
    label = a.claim if stage == "2" else "overview"
    return _one(run, stage, label, a, claim=a.claim)


def _one(run, stage, label, a, card=None, claim=None, questions=None) -> int:
    bundle = build_bundle(run, stage, label, card=card, claim=claim, questions=questions)
    print(f"\n=== 阶段{stage} / {label} ===\n  bundle: {bundle}")
    out, isolated = run_backend(bundle, a.backend, a.exec_cmd, a.model)
    _stamp_manifest(bundle, backend=a.backend,
                    context_isolated=(isolated if stage == "3" else None))

    if out is None:
        print(f"  已装配,未执行(--backend bundle)。下一步二选一:\n"
              f"    claude -p < {bundle}/request.md > {bundle}/response.txt\n"
              f"    或把 request.md 贴进一个**新会话**,把回答存成 response.txt\n"
              f"  然后:python3 extract.py land --run {run} --stage {stage} "
              f"--bundle {bundle} --out <目标文件>")
        if stage == "3":
            print("  ⚠️ 手工执行时 context_isolated 保持 false —— 本程序无法证明你换了上下文")
        return 0

    with open(os.path.join(bundle, "response.txt"), "w", encoding="utf-8") as f:
        f.write(out)
    return land(run, stage, bundle, a.out, label)


def land(run: str, stage: str, bundle: str, out: str | None, label: str = "") -> int:
    """把模型输出落盘到 run 的标准位置,阶段二额外过 ③④ 硬门。"""
    man = yaml.safe_load(_read(os.path.join(bundle, "manifest.yaml")))
    body = _extract_yaml(_read(os.path.join(bundle, "response.txt")))
    self_claim = _SELF_ISO.search(body)
    if self_claim:
        body = _SELF_ISO.sub("", body)
    try:
        yaml.safe_load(body)
    except yaml.YAMLError as e:
        sys.exit(f"[错误] 模型输出不是合法 YAML:{e}")

    out_dir = os.path.join(run, STAGE_SPEC[stage]["out_dir"])
    os.makedirs(out_dir, exist_ok=True)
    if not out:
        name = {"1": "paper_overview.yaml",
                "2": f"{man.get('label') or label}.yaml",
                "3": f"{man.get('label') or label}.yaml"}[stage]
        out = os.path.join(out_dir, name)

    header = ""
    if stage == "3":
        # 三值,不是布尔:
        #   true     —— 本程序开的独立进程,隔离是操作系统给的
        #   asserted —— 调用方在 driver 之外安排了隔离(如子上下文),本程序**没有证明**
        #   false    —— 同上下文,报告只能当下界
        # 把 asserted 折叠进 true 就等于让调用方自报隔离,正是这一层要防的事。
        assert_by = man.get("isolation_asserted_by")
        iso = "true" if man.get("context_isolated") else ("asserted" if assert_by else "false")
        header = (f"# 阶段三产物 —— 按 prompts/stage3_adversarial_check.md 执行。\n"
                  f"# context_isolated: {iso}  ← 由 extract.py 写入,非执行者自报\n"
                  f"# 问题子集:{man.get('questions')}   后端:{man.get('backend')}\n")
        if iso == "asserted":
            header += (f"# ⚠️ 隔离由 driver 之外安排({assert_by}):材料白名单与新上下文都成立,\n"
                       f"#    但本程序未能验证,证据力低于 true 一档。\n")
        elif iso == "false":
            header += ("# ⚠️ 非隔离运行,本报告只能当**下界**:挑出的问题是真的,"
                       "没挑出的不能算没有。\n")
        if self_claim:
            # 留痕而不是静默删:自报与实况不符本身就是"别信模型自报"的证据。
            header += (f"# 注:模型在正文里自报 context_isolated: {self_claim.group(1)}"
                       f"(已剥除)。它无从知道自己被怎么调起来,以上以程序判定为准。\n")
    with open(out, "w", encoding="utf-8") as f:
        f.write(header + body)
    print(f"  → {out}")

    if stage == "2":
        return gate(out, os.path.join(ROOT, man["verified_against"]))
    return 0


def cmd_land(a) -> int:
    if a.assert_isolated:
        # 人把 request.md 贴进新会话时,隔离确实成立但本程序无法证明。
        # 记成 asserted 而不是 true —— 证据力低一档,且要写清是谁担保的。
        _stamp_manifest(a.bundle, isolation_asserted_by=a.assert_isolated)
    return land(a.run.rstrip("/"), a.stage, a.bundle, a.out)


# ------------------------------------------------- 投稿日：会议截止日机械补入

def stamp_date(run: str, cards: list[str] | None = None, dry: bool = False) -> int:
    """把 venue_deadlines.yaml 里该届的截止日补进卡（阶梯第 5 档）。

    **纯机械活，没有任何推断**：会议名与年份读自运维方写的 `00_source/metadata.yaml`，
    日期读自运维方维护的 `venue_deadlines.yaml`，本函数只做查表与写入。
    模型不参与，也不应该参与——它猜出来的日期看起来跟真的一模一样。

    只补 `submission_date` 为空的卡。论文自带 Received 时那是每篇各自的真实日期，
    优先级高于"全会议一个常数"。
    """
    meta_path = os.path.join(run, "00_source", "metadata.yaml")
    if not os.path.exists(meta_path):
        sys.exit(f"[错误] 缺 {meta_path} —— 会议名与年份是运维方提供的 run 级元数据，"
                 f"不在论文里，也不许由模型推断")
    meta = (yaml.safe_load(open(meta_path, encoding="utf-8")) or {}).get("source_paper") or {}
    venue, year = meta.get("venue"), meta.get("year")
    date, ed, why = claim_card.resolve_venue_deadline(venue, year)
    if not date:
        sys.exit(f"[错误] 查不到 {venue} {year} 的截止日：{why}\n"
                 f"        请人对着官方 CFP 往 venue_deadlines.yaml 里加一届"
                 f"（见该文件头「怎么加一届」）。**不要让模型填这个日期。**")
    print(f"{venue} {year} → {date}（{why}）")
    if not ed.get("verified_by"):
        print("  ⚠️ 该条目 verified_by 留空 —— 尚未有人对照官方 CFP 核实，"
              "下游报告不得声称此日期已核实")

    cards = cards or sorted(
        os.path.join(run, "03_cards", f) for f in os.listdir(os.path.join(run, "03_cards"))
        if f.endswith((".yaml", ".yml")))
    n = 0
    for path in cards:
        raw = open(path, encoding="utf-8").read()
        doc = yaml.safe_load(raw) or {}
        cc = doc.get("claim_card", doc)
        cur = cc.get("submission_date")
        cur_basis = (cc.get("submission_date_source") or {}).get("basis")
        # 顺序要紧：**先判 basis 再判有没有日期**。卡上已是 venue_deadline 档的日期，
        # 其来源就是本表而非论文，表一订正它就成了孤儿——而 validate_card 的逐字比对
        # 又不许手工改卡，两头堵死。这条分支就是留给"表被订正"的唯一出口。
        if cur_basis == "venue_deadline":
            if str(cur)[:10] == date:
                print(f"  [跳过] {os.path.basename(path)}: 已补过且与表一致")
                continue
            out, hit = [], False
            for line in raw.splitlines():
                if not hit and line.strip().startswith("submission_date:"):
                    ind = line[: len(line) - len(line.lstrip())]
                    out += [
                        f"{ind}# ↓ 由 `extract.py stamp-date` 依 venue_deadlines.yaml 订正：",
                        f"{ind}#   {cur} → {date}（{why}）。",
                        f"{ind}#   旧值同样取自该表而非论文，故随表更新，不算改动抽卡产物。",
                        f'{ind}submission_date: "{date}"',
                    ]
                    hit = True
                    continue
                out.append(line)
            if not hit:
                print(f"  [跳过] {os.path.basename(path)}: 未定位到 submission_date 行")
                continue
            if dry:
                print(f"  [dry-run] 会把 {os.path.basename(path)} 的 {cur} 订正为 {date}")
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(out) + "\n")
                print(f"  [订正] {os.path.basename(path)}: {cur} → {date}")
            n += 1
            continue
        if cur:
            print(f"  [跳过] {os.path.basename(path)}: 已有 submission_date="
                  f"{cur!r}（论文自带的日期优先）")
            continue
        # 行级改写而非 yaml.dump：卡里的注释是承载信息的（每格为什么这么填），
        # dump 一遍会全部丢掉。
        out, hit = [], False
        for line in raw.splitlines():
            stripped = line.strip()
            if not hit and (stripped.startswith("submission_date:")):
                ind = line[: len(line) - len(line.lstrip())]
                out += [
                    f"{ind}# ↓ 以下三项由 `extract.py stamp-date` 机械补入，不是抽卡产物。",
                    f"{ind}#   论文自身无 Received/Accepted/检索截止日（阶段一正确停在第 4 档），",
                    f"{ind}#   日期来自 venue_deadlines.yaml 这一**论文之外**的运维元数据。",
                    f"{ind}#   校验时会拿它与该表逐字比对，手工改这里会直接报错。",
                    f"{ind}#   上方抽卡时写的「这会让 validate_card 硬报错」一段保留作历史——",
                    f"{ind}#   那时第 5 档还不存在，其描述在当时是对的。",
                    f'{ind}submission_date: "{date}"',
                    f"{ind}submission_date_source:",
                    f"{ind}  basis: venue_deadline",
                    f"{ind}  venue: {venue}",
                    f"{ind}  year: {year}",
                    f"{ind}  note: >-",
                    f"{ind}    {why}。取更早的一档作保守下界：日期越早，被判 predates=true 的",
                    f"{ind}    指南越少，越不会拿作者当时看不到的标准要求他。",
                    f"{ind}    粗糙之处：这是全会议一个常数，不是本篇各自的投稿时刻。",
                ]
                hit = True
                continue
            out.append(line)
        if not hit:
            print(f"  [跳过] {os.path.basename(path)}: 未定位到 submission_date 行")
            continue
        if dry:
            print(f"  [dry-run] 会改 {path}")
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(out) + "\n")
            print(f"  ✓ {path}")
        n += 1
    print(f"\n补入 {n} 张卡。接着跑 `python3 extract.py verify --run {run}` 过硬门。")
    return 0


def cmd_stamp_date(a) -> int:
    return stamp_date(a.run.rstrip("/"), a.cards, a.dry_run)


def cmd_verify(a) -> int:
    run = a.run.rstrip("/")
    _, content_list = _parse_paths(run)
    cards = a.cards or sorted(
        os.path.join(run, "03_cards", f) for f in os.listdir(os.path.join(run, "03_cards"))
        if f.endswith((".yaml", ".yml")))
    rc = 0
    for c in cards:
        print(f"\n=== {os.path.basename(c)} ===")
        rc |= gate(c, content_list)
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(
        description="抽卡流程 driver:装配 bundle → 执行 → 落盘 → 过 ③④ 硬门")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("stage1", "stage2", "stage3"):
        p = sub.add_parser(name, help=f"阶段{name[-1]}")
        p.add_argument("--run", required=True, help="run 目录(含 01_parse/)")
        p.add_argument("--backend", default="bundle",
                       choices=["bundle", "claude-cli", "exec"],
                       help="bundle=只装配;claude-cli=新进程执行(阶段三的隔离靠它)")
        p.add_argument("--exec-cmd", help="--backend exec 时的外部命令")
        p.add_argument("--model", help="传给 claude CLI 的 --model")
        p.add_argument("--out", help="落盘路径(默认按阶段放进 run 的标准子目录)")
        if name == "stage2":
            p.add_argument("--claim", required=True, help="要填的 claim_id,一次一张卡")
        if name == "stage3":
            p.add_argument("--card", required=True, nargs="+", help="待核查的卡")
            p.add_argument("--questions",
                           help=f"只跑这几问;默认按 {STAGE3_SPLITS} 拆成三次独立调用")
        p.set_defaults(func=cmd_stage, stage=name[-1],
                       claim=None, card=None, questions=None)

    p = sub.add_parser("land", help="把已有的 response.txt 落盘并过硬门")
    p.add_argument("--run", required=True)
    p.add_argument("--stage", required=True, choices=["1", "2", "3"])
    p.add_argument("--bundle", required=True)
    p.add_argument("--out")
    p.add_argument("--assert-isolated", metavar="谁担保的",
                   help="阶段三:在 driver 之外安排了隔离(如贴进新会话)。"
                        "记成 asserted 而非 true —— 本程序未证明")
    p.set_defaults(func=cmd_land)

    p = sub.add_parser("stamp-date",
                       help="会议论文:把 venue_deadlines.yaml 里该届截止日补进卡(纯查表)")
    p.add_argument("--run", required=True)
    p.add_argument("--cards", nargs="*")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_stamp_date)

    p = sub.add_parser("verify", help="只跑 ③④ 硬门")
    p.add_argument("--run", required=True)
    p.add_argument("--cards", nargs="*")
    p.set_defaults(func=cmd_verify)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
