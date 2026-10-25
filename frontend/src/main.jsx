import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...(options.headers || {}) },
  });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "请求未能完成，请稍后重试。");
  return payload;
}

function navigate(path) {
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function useRoute() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const handle = () => setPath(window.location.pathname);
    window.addEventListener("popstate", handle);
    return () => window.removeEventListener("popstate", handle);
  }, []);
  return path;
}

const agentDescriptions = {
  "流程编排 Agent": "拆解本轮核查目标与检索问题",
  "材料抽取 Agent": "从上传材料抽取关键事实",
  "证据检索 Agent": "定位可回溯的材料证据",
  "风险审阅 Agent": "输出有证据支撑的风险结论",
};

function cn(...values) { return values.filter(Boolean).join(" "); }
function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}
function formatSize(value) {
  if (!value) return "0 KB";
  return value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(value / 1024)} KB`;
}
function labelForState(value) {
  return ({ queued: "排队中", running: "处理中", ready: "已就绪", completed: "已完成", failed: "失败", open: "待处理", confirmed: "已确认", resolved: "已解决" })[value] || value || "未知";
}

function StatePill({ state }) { return <span className={cn("state-pill", state)}>{labelForState(state)}</span>; }

function Logo() {
  return <button className="brand" onClick={() => navigate("/")} aria-label="返回项目列表"><span className="brand-mark">B</span><span>Bond<span className="brand-accent">.AI</span></span></button>;
}

function Shell({ children, projectName }) {
  return <div className="app-shell">
    <header className="topbar"><Logo /><div className="topbar-center"><span className="status-light" />公司债智能尽调工作台</div><div className="topbar-right"><span className="cloud-dot">●</span> Evidence-first</div></header>
    <main>{projectName && <div className="crumb"><button onClick={() => navigate("/")}>项目空间</button><span>/</span><span>{projectName}</span></div>}{children}</main>
    <footer>仅用于尽调辅助核查 · 结论须由项目人员复核 · 所有风险均应回溯至原始材料</footer>
  </div>;
}

function Notice({ message, onClose }) {
  if (!message) return null;
  return <div className="toast"><span>!</span><p>{message}</p><button onClick={onClose}>×</button></div>;
}

function Home() {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [summary, setSummary] = useState("");

  const load = async () => {
    try { setLoading(true); setProjects((await api("/api/projects")).projects); } catch (err) { setError(err.message); } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);
  const createProject = async (event) => {
    event.preventDefault();
    try {
      setCreating(true);
      const result = await api("/api/projects", { method: "POST", body: JSON.stringify({ name, material_summary: summary }) });
      navigate(`/projects/${result.project.id}`);
    } catch (err) { setError(err.message); } finally { setCreating(false); }
  };
  const createDemo = async () => {
    try {
      setCreating(true);
      const result = await api("/api/projects/demo", { method: "POST", body: JSON.stringify({}) });
      navigate(`/projects/${result.project.id}`);
    } catch (err) { setError(err.message); } finally { setCreating(false); }
  };

  return <Shell>
    <Notice message={error} onClose={() => setError("")} />
    <section className="home-hero">
      <div className="hero-copy"><p className="kicker">AI DUE DILIGENCE WORKBENCH</p><h1>把公司债材料<br /><em>变成可追溯的判断。</em></h1><p className="hero-text">上传募集说明书、财报与担保材料。四个协作 Agent 在项目边界内抽取事实、检索证据、审阅风险。</p><div className="hero-tags"><span>OCR</span><span>Hybrid RAG</span><span>Multi-Agent</span><span>Evidence trace</span></div></div>
      <form className="new-project card" onSubmit={createProject}><div className="card-title"><span className="step">01</span><div><p>NEW WORKSPACE</p><h2>创建尽调项目</h2></div></div><label>项目名称<input required maxLength="160" value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：XX 集团 2026 年公司债" /></label><label>项目说明（可选）<textarea maxLength="2000" value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="记录材料来源或本轮核查重点" rows="3" /></label><button className="primary-button" disabled={creating}>{creating ? "正在创建…" : "创建并上传材料"}<span>→</span></button><button type="button" className="text-button" onClick={createDemo} disabled={creating}>加载脱敏试用材料 <span>↗</span></button></form>
    </section>
    <section className="section-heading"><div><p className="kicker">WORKSPACES</p><h2>你的项目</h2></div><span>{projects.length} 个项目</span></section>
    {loading ? <div className="loading-card"><div className="spinner" />正在读取项目…</div> : projects.length ? <div className="project-grid">{projects.map((project, index) => <button key={project.id} className="project-card" onClick={() => navigate(`/projects/${project.id}`)}><span className="project-index">{String(index + 1).padStart(2, "0")}</span><div><h3>{project.name}</h3><p>{project.material_summary || "尚未添加项目说明"}</p></div><div className="project-meta"><span>最近活动 {formatDate(project.last_active_at)}</span><span className="go-arrow">→</span></div></button>)}</div> : <div className="empty-state card"><div className="empty-icon">◇</div><h3>从一个项目开始</h3><p>创建项目并上传 PDF 或 DOCX 材料，系统会建立独立的证据链与项目记忆。</p></div>}
  </Shell>;
}

function Project({ projectId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef(null);
  const [sourceNote, setSourceNote] = useState("");
  const [question, setQuestion] = useState("");

  const load = async () => {
    try { setData(await api(`/api/projects/${projectId}`)); } catch (err) { setError(err.message); }
  };
  useEffect(() => { load(); }, [projectId]);
  const active = useMemo(() => data?.jobs?.some((job) => ["queued", "running"].includes(job.status)), [data]);
  useEffect(() => { if (!active) return; const timer = setInterval(load, 2200); return () => clearInterval(timer); }, [active, projectId]);
  const action = async (callback, refresh = true) => { try { setBusy(true); await callback(); if (refresh) await load(); } catch (err) { setError(err.message); } finally { setBusy(false); } };
  const upload = async (event) => {
    event.preventDefault(); const file = fileRef.current?.files?.[0]; if (!file) return setError("请先选择一个 PDF 或 DOCX 文件。");
    const form = new FormData(); form.set("file", file); form.set("source_note", sourceNote || "用户上传");
    await action(() => api(`/api/projects/${projectId}/documents`, { method: "POST", body: form }));
    fileRef.current.value = ""; setSourceNote("");
  };
  const deleteProject = () => { if (window.confirm("删除整个项目会清除材料、向量和审阅记录，确定继续吗？")) action(async () => { await api(`/api/projects/${projectId}`, { method: "DELETE" }); navigate("/"); }, false); };
  const runReview = () => action(() => api(`/api/projects/${projectId}/reviews`, { method: "POST", body: JSON.stringify({}) }));
  const ask = async (event) => { event.preventDefault(); if (!question.trim()) return; await action(async () => { await api(`/api/projects/${projectId}/questions`, { method: "POST", body: JSON.stringify({ question }) }); setQuestion(""); }); };

  if (!data) return <Shell><div className="loading-card"><div className="spinner" />正在加载项目…</div><Notice message={error} onClose={() => setError("")} /></Shell>;
  const readyCount = data.documents.filter((item) => item.status === "ready").length;
  const canReview = data.documents.length > 0 && readyCount === data.documents.length && !active;
  // Jobs are retained for auditability. Surface only the newest failure so an
  // old, already superseded run does not make a successfully completed review
  // look broken.
  const failure = data.jobs[0]?.status === "failed" ? data.jobs[0] : null;
  return <Shell projectName={data.project.name}>
    <Notice message={error || failure?.error} onClose={() => setError("")} />
    <section className="project-hero"><div><p className="kicker">PROJECT WORKSPACE</p><h1>{data.project.name}</h1><p>{data.project.material_summary || "本项目尚未填写背景说明。"}</p></div><div className="project-actions"><button className="secondary-button" onClick={deleteProject} disabled={busy}>删除项目</button><button className="primary-button" onClick={runReview} disabled={!canReview || busy}>{active ? "任务处理中…" : "运行多 Agent 审阅"}<span>→</span></button></div></section>
    <section className="metrics"><Metric label="项目材料" value={data.documents.length} sub="PDF / DOCX" /><Metric label="可检索材料" value={readyCount} sub="已建立向量索引" /><Metric label="最新风险项" value={data.findings.length} sub={data.review?.status === "completed" ? "审阅已完成" : "等待审阅"} /><Metric label="项目记忆" value={data.notes.length} sub="人工确认与追问" /></section>
    {active && <div className="running-banner"><span className="pulse" />正在处理材料。页面会自动更新，请勿重复提交。</div>}
    <section className="workspace-grid">
      <div className="stack"><section className="panel upload-panel"><PanelHeading number="01" title="材料中心" caption="MATERIAL LIBRARY" /><form onSubmit={upload}><label className="drop-zone"><input ref={fileRef} type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" /><span className="upload-symbol">↑</span><strong>选择或拖入材料</strong><small>仅支持 PDF、DOCX · 单文件不超过 20 MB</small></label><input value={sourceNote} maxLength="500" onChange={(e) => setSourceNote(e.target.value)} placeholder="来源说明，例如：发行人提供的募集说明书" /><button className="dark-button" disabled={busy}>上传并建立索引 <span>→</span></button></form><DocumentList documents={data.documents} onDelete={(id) => { if (window.confirm("删除此文件及其向量索引？")) action(() => api(`/api/projects/${projectId}/documents/${id}`, { method: "DELETE" })); }} busy={busy} /></section>
      <section className="panel"><PanelHeading number="02" title="审阅轨迹" caption="AGENT TRACE" /><div className="agent-trace">{["流程编排 Agent", "材料抽取 Agent", "证据检索 Agent", "风险审阅 Agent"].map((name, index) => { const agent = data.agents.find((item) => item.agent_name === name); return <div className="agent-row" key={name}><span className={cn("agent-node", agent?.status || "idle")}>{index + 1}</span><div><strong>{name}</strong><p>{agentDescriptions[name]}</p>{agent?.error && <small className="agent-error">{agent.error}</small>}</div><div className="agent-state">{agent ? <><StatePill state={agent.status} />{agent.duration_ms ? <small>{agent.duration_ms} ms</small> : null}</> : <span className="muted">等待任务</span>}</div></div>; })}</div></section></div>
      <section className="panel project-memory"><PanelHeading number="03" title="项目记忆与追问" caption="PROJECT MEMORY" /><p className="panel-intro">提问只会检索当前项目的材料与已保存的人工确认，无法访问其他项目。</p><form className="question-form" onSubmit={ask}><input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="例如：担保人的代偿能力是否有充分材料支持？" /><button className="icon-button" disabled={busy || !question.trim()}>↑</button></form><div className="note-list">{data.notes.length ? data.notes.map((note, index) => <article key={`${note.created_at}-${index}`} className={cn("note", note.kind)}><span>{note.kind === "question" ? "Q" : "A"}</span><div><small>{note.kind === "question" ? "项目追问" : "基于材料的回答"} · {formatDate(note.created_at)}</small><p>{note.content}</p></div></article>) : <p className="muted">尚无项目记忆。完成审阅后可以在此追问。</p>}</div></section>
    </section>
    <section className="result-section"><div className="section-heading"><div><p className="kicker">04 · EVIDENCE REVIEW</p><h2>风险审阅结果</h2></div>{data.review && <StatePill state={data.review.status} />}</div>{data.review?.summary && <div className="summary-card"><span>本轮结论</span><p>{data.review.summary}</p></div>}{data.findings.length ? <div className="finding-grid">{data.findings.map((finding) => <FindingCard key={finding.id} finding={finding} update={(payload) => action(() => api(`/api/findings/${finding.id}`, { method: "PATCH", body: JSON.stringify(payload) }))} />)}</div> : <div className="empty-state card"><div className="empty-icon">◌</div><h3>{data.review?.status === "failed" ? "本轮审阅未完成" : "等待风险结论"}</h3><p>{data.review?.status === "failed" ? "请查看上方任务错误信息，修正后可重新发起审阅。" : "材料均显示“已就绪”后，点击“运行多 Agent 审阅”。"}</p></div>}</section>
  </Shell>;
}

function Metric({ label, value, sub }) { return <article className="metric"><span>{label}</span><strong>{value}</strong><small>{sub}</small></article>; }
function PanelHeading({ number, title, caption }) { return <div className="panel-heading"><span className="step">{number}</span><div><p>{caption}</p><h2>{title}</h2></div></div>; }
function DocumentList({ documents, onDelete, busy }) { return <div className="document-list">{documents.length ? documents.map((document) => <article key={document.id}><div className="file-type">{document.file_name.split(".").pop()?.toUpperCase()}</div><div className="document-main"><strong>{document.file_name}</strong><small>{document.source_note} · {formatSize(document.size_bytes)}</small>{document.status === "failed" && <em>{document.extraction_error}</em>}</div><StatePill state={document.status} /><button className="delete-file" disabled={busy || ["queued", "running"].includes(document.status)} onClick={() => onDelete(document.id)} title="删除文件">×</button></article>) : <p className="muted doc-empty">尚未上传材料</p>}</div>; }
function FindingCard({ finding, update }) { const [status, setStatus] = useState(finding.status); const [note, setNote] = useState(finding.review_note || ""); return <article className={cn("finding-card", finding.severity)}><div className="finding-top"><span className="severity">{finding.severity}风险</span><span className={cn("change", finding.change_status)}>{finding.change_status}</span></div><h3>{finding.title}</h3><code>{finding.rule_code}</code><blockquote>{finding.evidence}</blockquote><p className="recommendation"><strong>建议</strong>{finding.recommendation}</p><p className="confidence">置信度 {Math.round((finding.confidence || 0) * 100)}%</p>{finding.citations.map((citation, index) => <details key={index}><summary>证据 · {citation.file_name} · {citation.location}</summary><p>{citation.excerpt}</p></details>)}<div className="finding-form"><select value={status} onChange={(e) => setStatus(e.target.value)}><option value="open">待处理</option><option value="confirmed">已确认</option><option value="resolved">已解决</option></select><input value={note} onChange={(e) => setNote(e.target.value)} placeholder="人工备注" /><button onClick={() => update({ status, note })}>保存</button></div></article>; }

function App() {
  const path = useRoute();
  const match = path.match(/^\/projects\/([0-9a-f-]+)$/i);
  return match ? <Project projectId={match[1]} /> : <Home />;
}

createRoot(document.getElementById("root")).render(<App />);
