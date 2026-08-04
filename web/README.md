# clinical-rag 演示网页

这是一个**静态流程可视化页面**：它读取 `sample/*` 里已经跑好的中间产物，供讲解时展示，
不需要数据库、登录、GPU 调度或网页端 LLM 调用。

```bash
# 在仓库根目录
python3 web/build_demo_data.py
python3 -m http.server 8000
# 浏览器打开 http://localhost:8000/web/
```

每次更新 `sample/` 中的 demo run 后，重新运行 `build_demo_data.py`。页面的数据结构在
`web/data/runs.json`；原始 YAML / JSONL / Markdown / PDF 仍保留在 sample 目录，右栏可直接打开。

它是“已跑流程的播放器”，不是“上传任意 PDF 并在线跑完整 pipeline”的产品。后者需要另行接
GPU 解析、LLM backend、后台 job 与用户数据隔离，不在本阶段范围内。
