#!/bin/bash
#SBATCH --job-name=mineru_2papers
#SBATCH --account=bgkq-delta-gpu
#SBATCH --partition=gpuA100x4
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=48g
#SBATCH --gpus=1
#SBATCH --time=01:30:00
#SBATCH --output=/work/hdd/bgkq/Code/clinical-rag/sample/_parse_job_%j.log
# ---------------------------------------------------------------------------
# 01_parse —— 把两篇样例论文的 PDF 交给 MinerU 做版面解析。
#
# 为什么必须上 GPU 节点（2026-07-31 实测，见 dkd 样例的 parse_notes.md）：
#   登录节点跑不动 —— CPU worker 被 BrokenProcessPool 杀掉；默认 vlm 后端还会
#   尝试起本地服务并超时。必须 `-b pipeline -d cuda`。
#
# 为什么不是 pypdf 就够：extract.py 硬性要求成对的 *.md + *_content_list.json
#   （后者带 page_idx，页码必须由程序给而不是让模型填）；且表格内容在 pypdf 文本里
#   可能**根本不存在**。
#
# 参考成本：15 页 / 12 分钟 / 1×A100（含模型加载）。本次 25 + 12 = 37 页。
# ---------------------------------------------------------------------------
set -u

ENV=/work/hdd/bgkq/xchen48/conda_envs/mineru
ROOT=/work/hdd/bgkq/Code/clinical-rag/sample

export PATH="$ENV/bin:$PATH"
export MINERU_MODEL_SOURCE=huggingface
export HF_HOME=/u/xchen48/.cache/huggingface

echo "=== node=$(hostname) start=$(date) ==="
"$ENV/bin/mineru" --version || true

for run in nature_med_apol1_kidney iclr2024_pediatric_hypoglycemia; do
  src="$ROOT/$run/00_source"
  out="$ROOT/$run/01_parse"
  pdf=$(ls "$src"/*.pdf | head -1)
  echo "--- [$run] $(basename "$pdf") -> $out  ($(date +%H:%M:%S)) ---"
  "$ENV/bin/mineru" -p "$pdf" -o "$out" -b pipeline -d cuda -l en
  echo "--- [$run] mineru exit=$? ($(date +%H:%M:%S)) ---"
  find "$out" -name '*.md' -o -name '*_content_list.json' | sed "s|$ROOT/||"
done

echo "=== end=$(date) ==="
