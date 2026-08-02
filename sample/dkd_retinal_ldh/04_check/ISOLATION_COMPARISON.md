# 隔离 vs 非隔离核查：同一张卡、同一套 prompt、同一篇论文

**这是 `STATUS_2026-07-31.md` §5 待决 B 要的那个数。** 2026-08-01 实跑。
产物：`isolated/*.yaml`（6 份）；对照物：`../04_check/adversarial_check.yaml`（7-31，非隔离）。

## 1. 怎么跑的

| | 非隔离（7-31） | 隔离（8-01） |
|---|---|---|
| 上下文 | 与填卡同一个上下文 | **每次调用一个新进程**（`extract.py stage3 --backend claude-cli`） |
| 材料 | 执行者能看到填卡时的全部推理 | 白名单装配：三条硬约束 + stage3 指令 + **一张**卡 + 论文全文 + SPEC |
| 切分 | 七问一次跑完，两张卡合并成一份报告 | **6 次独立调用**：2 张卡 × {`1,2,3,5,6`｜`4`｜`7`} |
| `context_isolated` | `false` | `true`，**由 extract.py 写入**，非执行者自报 |

按 `prompts/stage3_adversarial_check.md`，第 4、7 问必须单独调用——非隔离那次虽然标了
「← 按 prompt 单独跑」，但那是同一个上下文里的"另起一段"，不是另一次调用。

## 2. 结果

### 非隔离（7-31）：7 条，两张卡合并

1 high / 2 medium / 1 low / 1 cannot_determine / 2 pass。**第 4 问判 pass。**

### 隔离（8-01）：45 条

| 调用 | pass | issue | cannot_determine |
|---|---|---|---|
| `claim_1_q12356` | 3 | 6（m2 l4） | 1 |
| `claim_1_q4` | 1 | 2（l2） | 0 |
| `claim_1_q7` | 6 | 1（l1） | 1 |
| `claim_2_q12356` | 0 | **10（h1 m7 l2）** | 1 |
| `claim_2_q4` | 4 | 1（l1） | 0 |
| `claim_2_q7` | 7 | 0 | 1 |
| **合计** | **21** | **20（h1 m9 l10）** | **4** |

**issue 从 4 条变成 20 条。** 但条数不是重点，下面三条才是。

## 3. 三个结论

### 3.1 隔离挑出了非隔离判 pass 的那一问，而它正是本项目的核心

非隔离把**第 4 问（性能提升当患者获益）判成 pass**。隔离后两张卡各自都判出 issue：

- `claim_1`：`demonstrated_effect` **只报敏感度不报特异度**。而卡声称的获益是"分流谁去做
  确证化验"——分流的代价恰恰由特异度承担（同一操作点 47·6% vs 38·8%，p=0·072 **不显著**；
  外部集低至 31·7%）。只报单向就是把双向权衡呈现成流程获益。
- `claim_1`：论文断言 `cost-effective`，全文**无任何成本或成本-效果分析**，`benefit_gap` 没记。
  论文还恰恰以"nor shown to be cost-effective"为由否定过前人工作——同一判据对己方未执行。
- `claim_2`：`claimed_benefit` 写"减少**不必要的**侵入性肾活检"，原文只说
  `minimise the need for invasive kidney biopsy`。**"不必要的"是卡自己加的定语**，
  它预设了论文未证明的那一步（能识别哪些活检可免）。

同一个上下文里，核查者刚写完 `benefit_gap`，再问它"`benefit_gap` 写对了吗"，它答"对"。

### 3.2 隔离不只是**加**发现，它还**推翻**了非隔离的最高级发现

非隔离那条唯一的 **high** 说：`comparator` 把自建算法对照与**文献引用的**尿试纸敏感度并列，
"论文并未在同一人群里与试纸做头对头比较"。

隔离运行判 `claim_1` 第 3 问为 **pass**，理由是论文做过这个比较。核对原文，隔离运行是对的：

> Methods：`We also compared the performance of the DeepDKD system and urine dipstick protein test in DKD screening (appendix p 29).`
> Results：`Among two external validation datasets (NDSP and PUDM), the sensitivity of DeepDKD (77⋅8% in both datasets) was substantially higher than that of the urine dipstick protein test (19⋅8% vs 0%).`

**主文里有头对头的数字。** 非隔离那次抓的 43·6%–69·4% 是 Introduction 引自文献 10–12 的
背景数字，不是本文的对照结果——它把背景段的数字当成了卡里那个对照的来源。

→ **`STATUS_2026-07-31.md` §5 待决 C（拆 `comparator` / `comparator_literature_only`）
的前提不成立**，那是一条 schema 改动，影响所有卡，不应基于一条被推翻的发现去做。
（`claim_2` 的 comparator 另有问题，但性质不同：那里把**参考标准**肾活检写成了对照臂，
论文对 claim_2 做过统计比较的只有算法对算法。见 `isolated/claim_2_q12356.yaml` Q3。）

### 3.3 隔离运行之间"抓到什么"稳定，"抓到多少 / 判多重"不稳定

`q4` 与 `q7` 各跑过**两次机制不同的隔离运行**（一次子上下文、一次 `claude -p` 新进程）：

| | 一致性 |
|---|---|
| `claim_2_q7` | **逐条一致**：七个事实全 pass，落在同一条 `cannot_determine`（外部集参考标准） |
| `claim_1_q7` | 核心一致、严重度不一致：都独立指出 `prospective × different_site` 交叉误读，一次 medium 一次 low |
| `claim_2_q4` | 核心一致、覆盖面不一致：都抓到"不必要的"这个多余定语；一次另报 3 条，一次只报 1 条 |
| `claim_2_q12356` | 都独立指出 `population.age_group` 的 note **借用了 claim_1 的队列年龄**（主文表只覆盖 claim_1 的十个队列），一次判 high 一次判 medium |

→ **评测时能当指标的是"发现的集合"，不是条数和严重度。**

## 4. 一处装配时抓到的真实泄漏（隔离的边界比想象窄）

第一版把 `prompts/README.md` 整个塞进阶段三的 bundle。README 第 8 行拿 **LDH 那篇**
当反例讲流程事故（"阶段一的 `paper_overview` 根本没产出"）——**那正是被审的这篇论文**。
核查者读到会先入为主。已改成只截取"三条硬约束"之后的部分。

> 隔离不是"别把上一阶段的产物给它"，是**别把任何关于这篇论文的场外信息给它**。
> 这条只有在把材料真正物化成一个文件、再去 grep 它的时候才会被发现。

## 5. 模型自报的隔离状态不可信（两个方向都错）

六次运行全部跑在独立进程里。其中三次在输出正文里自报了 `context_isolated`：
两次 `true`、**一次 `false`**（`claim_1_q12356`）。它无从知道自己被怎么调起来。

→ `extract.py land()` 现在把正文里的该字段**剥掉**，并在头部留痕记下它自报了什么。
留两个来源不同、可能矛盾的同名字段，下游读哪个都是错。

## 6. 给待决 B 的答复

**要隔离，而且要按问题子集拆开调用。** 代价是 6 次调用而不是 1 次（本篇约 89k 字符/次，
`claude -p` 单次 2–5 分钟）。评测时至少对 gold 那几篇跑隔离；**报告里必须记
`context_isolated`，且该字段只能由程序写。**
