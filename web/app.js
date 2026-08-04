const STEPS = [
  ["source", "论文输入", "PDF 与元数据"],
  ["parse", "01 · 解析", "PDF → 可定位文本"],
  ["overview", "02 · 全文概览", "主张、队列、对照"],
  ["cards", "03 · Claim Card", "结构化临床主张"],
  ["check", "04 · 反方核查", "引文硬门与挑错"],
  ["retrieval", "05 · 外部检索", "ExternalStandard"],
  ["review", "06 · 审稿输出", "对齐与审稿建议"],
];
let runs = [], run, step = 0;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));
const link = (label, href) => href ? `<a class="artifact" href="${href}" target="_blank" rel="noreferrer">↗ ${esc(label)}</a>` : "";

function artifacts() {
  const base = [link("00_source · metadata.yaml", run.metadata), link("论文 PDF", run.pdf)];
  if (run.parse_notes) base.push(link("01_parse · parse_notes.md", run.parse_notes));
  if (run.overview) base.push(link("02_overview · paper_overview.yaml", run.overview.file));
  run.cards.forEach(x => base.push(link(`03_cards · ${x.name}.yaml`, x.file)));
  run.checks.forEach(x => base.push(link("04_check · 核查结果", x)));
  run.retrieval.forEach(x => base.push(link("05_retrieval · retrieved.jsonl", x)));
  if (run.retrieval_summary) base.push(link("05_retrieval · summary.md", run.retrieval_summary));
  if (run.review) base.push(link("06_review_output · review_notes.md", run.review));
  $("artifact-links").innerHTML = base.join("");
}

function renderStepList() {
  $("step-list").innerHTML = STEPS.map((x, i) => `<button class="step ${i===step?"active":""}" data-step="${i}"><span class="number">${i+1}</span><span>${x[1]}<small>${x[2]}</small></span></button>`).join("");
  document.querySelectorAll("[data-step]").forEach(b => b.onclick = () => { step = +b.dataset.step; render(); });
}

function cards() { return run.cards.map(c => `<section class="card"><h4>${esc(c.claim_id)} · ${esc(c.condition)}</h4><div class="tags"><span class="tag">${esc(c.task)}</span><span class="tag">${esc(c.population)}</span><span class="tag">${esc(c.stage)}</span><span class="tag">${esc(c.care_setting)}</span></div><p><b>预期用途：</b>${esc(c.intended_use)}</p><p><b>论文声称：</b>${esc(c.claimed_benefit)}</p><small>provenance 字段：${c.provenance_fields} 条</small></section>`).join(""); }

async function retrievalView() {
  const files = run.retrieval;
  if (!files.length) return `<div class="callout">该样例没有保存检索 JSONL。</div>`;
  const rows = await Promise.all(files.map(async file => (await fetch(file)).text()));
  const all = rows.flatMap(t => t.trim().split("\n").filter(Boolean).map(JSON.parse));
  const counts = {}; all.forEach(x => counts[x.source_id] = (counts[x.source_id] || 0) + 1);
  const sources = Object.entries(counts).map(([k,v]) => `<tr><td>${esc(k)}</td><td>${v}</td></tr>`).join("");
  const examples = all.filter(x => x.recommendation_or_requirement).slice(0, 3).map(x => `<section class="card"><h4>${esc(x.title)}</h4><p>${esc(x.recommendation_or_requirement)}</p><div class="tags"><span class="tag">${esc(x.source_role)}</span><span class="tag">Tier ${esc(x.tier)}</span><span class="tag">predates=${esc(x.predates_paper_submission)}</span></div></section>`).join("");
  return `<h2>外部标准检索</h2><p class="lead">Claim Card 决定检索和适用性；每条记录最终统一为 <code>ExternalStandard</code>。</p><div class="grid"><div class="metric"><strong>${all.length}</strong><span>保存的外部记录</span></div><div class="metric"><strong>${Object.keys(counts).length}</strong><span>命中来源</span></div></div><h3>按来源</h3><table class="table"><tr><th>source_id</th><th>记录数</th></tr>${sources}</table><h3>可作为外部要求的条目示例</h3>${examples || `<div class="callout">该 run 没有可直接作为规范要求的条文；这也是有效结果，系统不得用 discovery 题录冒充指南条文。</div>`}`;
}

async function content() {
  const kind = STEPS[step][0];
  if (kind === "source") return `<h2>论文输入</h2><p class="lead">一个 run 从一篇论文及其元数据开始。网页读取的是已经保存的输入，不会重新上传或处理文件。</p>${run.pdf ? `<iframe class="pdf" src="${run.pdf}"></iframe>` : `<div class="callout">本样例未随仓库保存 PDF；仍可展示其余已保存的解析与审稿产物。</div>`}`;
  if (kind === "parse") return `<h2>PDF → 可定位的解析文本</h2><p class="lead">解析不是普通预处理：后面的每个论文 quote 都必须能回到同一份解析产物与页码。</p><div class="grid"><div class="metric"><strong>MinerU</strong><span>版面解析优先</span></div><div class="metric"><strong>表格/图注</strong><span>保留供后续核验</span></div><div class="metric"><strong>逐字定位</strong><span>防止模型改写引文</span></div></div>${run.parse_notes ? `<h3>本 run 的解析记录</h3><pre class="raw" id="raw-preview">正在读取…</pre>` : ``}`;
  if (kind === "overview") { const o = run.overview; return o ? `<h2>全文概览：先理解，再填卡</h2><p class="lead">这一阶段识别独立临床主张、可用队列、参考标准与比较对象，避免把不同 claim 的证据混在一起。</p><div class="grid"><div class="metric"><strong>${o.n_cohorts}</strong><span>识别的队列</span></div><div class="metric"><strong>${o.claims.length}</strong><span>独立临床主张</span></div></div><h3>主张候选</h3>${o.claims.map(x=>`<section class="card"><b>${esc(x.id)}</b><p>${esc(x.label)}</p></section>`).join("")}` : `<div class="callout">该 run 缺少 overview 产物。</div>`; }
  if (kind === "cards") return `<h2>Clinical Claim Card</h2><p class="lead">每个独立临床主张单独成卡；只有 gating 层进入“哪份指南适用”的准入判断，细节保留在 descriptive 层。</p>${cards()}`;
  if (kind === "check") return `<h2>核验与反方检查</h2><p class="lead">程序先逐字定位 Card 的 provenance quote，再做受控字段校验；独立模型上下文随后专门寻找 Card 对论文的误读。</p><div class="grid"><div class="metric"><strong>引用核验</strong><span>quote 必须回到解析原文</span></div><div class="metric"><strong>门控校验</strong><span>病种/人群/任务/日期</span></div><div class="metric"><strong>反方核查</strong><span>不替 Card 自我辩护</span></div></div><h3>本 run 的核查产物</h3>${run.checks.map(x=>`<p>${link(x.split("/").pop(),x)}</p>`).join("")}`;
  if (kind === "retrieval") return await retrievalView();
  return `<h2>审稿建议与证据边界</h2><p class="lead">正式目标是：外部标准与论文原文形成 Alignment，再输出可追溯审稿建议。本仓库的既有样例 review notes 是该流程的人工总结；新阶段四可将其逐步自动化。</p><div class="callout"><b>审稿意见不能只凭检索结果产生。</b>它必须能指出：哪条外部标准适用、论文哪句原文支持或缺失、该标准是否早于投稿，以及作者如何回应。</div>${run.review ? `<pre class="raw" id="review-preview">正在读取…</pre>` : ``}`;
}

async function loadPreview(id, file) { if (!file) return; const el=$(id); if (el) el.textContent=await (await fetch(file)).text(); }
async function render() { renderStepList(); artifacts(); $("run-title").innerHTML=`${esc(run.title)} <span>${esc(run.citation)}</span>`; $("content").innerHTML=await content(); if (step===1) loadPreview("raw-preview",run.parse_notes); if(step===6) loadPreview("review-preview",run.review); $("previous").disabled=step===0; $("next").disabled=step===STEPS.length-1; }

async function start() { const data=await (await fetch("data/runs.json")).json(); runs=data.runs; const select=$("run-select"); select.innerHTML=runs.map((r,i)=>`<option value="${i}">${esc(r.title)}</option>`).join(""); select.onchange=()=>{run=runs[+select.value];step=0;render();}; $("previous").onclick=()=>{if(step){step--;render();}}; $("next").onclick=()=>{if(step<STEPS.length-1){step++;render();}}; run=runs[0]; render(); }
start().catch(err => { $("content").innerHTML=`<div class="callout">无法加载演示数据：${esc(err.message)}。请从仓库根目录启动静态服务器，而不是直接双击 HTML 文件。</div>`; });
