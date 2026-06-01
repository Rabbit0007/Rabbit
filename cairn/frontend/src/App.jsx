import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Download,
  Eye,
  FileText,
  Folder,
  KeyRound,
  Loader2,
  Lock,
  LogOut,
  Monitor,
  Network,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Save,
  Settings,
  ShieldAlert,
  Sparkles,
  Square,
  Trash2,
  User,
  X,
} from "lucide-react";
import { apiRequest, downloadFromApi } from "./api";
import {
  SEVERITY_META,
  STATUS_META,
  TASK_TYPES,
  clampText,
  cn,
  formatTime,
  go,
  groupBy,
  parseHash,
  parseHintLines,
  relativeHeartbeat,
} from "./utils";

try {
  cytoscape.use(dagre);
} catch {
  // Vite HMR may register the extension more than once.
}

const APP_NAME = "Rabbit";
const HUMAN_WORKER = "Human";
const SECRET_MASK = "********";

function useRoute() {
  const [route, setRoute] = useState(parseHash);

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    if (!window.location.hash) {
      window.location.hash = "#/projects";
    }
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return route;
}

function useAsyncAction(setToast) {
  return useCallback(
    async (label, action) => {
      try {
        const result = await action();
        if (label) setToast({ type: "success", message: label });
        return result;
      } catch (error) {
        if (error?.status === 401) {
          setToast({ type: "danger", message: "登录状态已失效，请重新登录" });
        } else {
          setToast({ type: "danger", message: error.message || "操作失败" });
        }
        throw error;
      }
    },
    [setToast],
  );
}

export default function App() {
  const route = useRoute();
  const [user, setUser] = useState(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [toast, setToast] = useState(null);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const runAction = useAsyncAction(setToast);

  const loadUser = useCallback(async () => {
    try {
      const me = await apiRequest("/api/auth/me");
      setUser(me);
    } catch {
      setUser(null);
    } finally {
      setAuthChecked(true);
    }
  }, []);

  useEffect(() => {
    loadUser();
  }, [loadUser]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(null), 4200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const logout = async () => {
    await runAction(null, () => apiRequest("/api/auth/logout", { method: "POST" }));
    setUser(null);
  };

  if (!authChecked) {
    return (
      <div className="boot-screen">
        <Loader2 className="spin" size={28} />
        <span>正在载入 Rabbit</span>
      </div>
    );
  }

  if (!user) {
    return <AuthPage onAuthed={loadUser} setToast={setToast} />;
  }

  return (
    <div className="app-shell">
      <TopNav
        route={route}
        user={user}
        onLogout={logout}
        onPassword={() => setPasswordOpen(true)}
        onSettings={() => setSettingsOpen(true)}
      />
      <main className="app-main">
        {route.page === "project" ? (
          <ProjectWorkspace projectId={route.projectId} runAction={runAction} setToast={setToast} />
        ) : route.page === "vulnerabilities" ? (
          <VulnerabilitiesPage runAction={runAction} setToast={setToast} />
        ) : route.page === "workers" ? (
          <WorkersPage runAction={runAction} setToast={setToast} />
        ) : route.page === "templates" ? (
          <TemplatesPage runAction={runAction} setToast={setToast} />
        ) : (
          <ProjectsPage runAction={runAction} setToast={setToast} />
        )}
      </main>
      {passwordOpen && <PasswordModal onClose={() => setPasswordOpen(false)} runAction={runAction} />}
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} runAction={runAction} />}
      {toast && <Toast toast={toast} onClose={() => setToast(null)} />}
    </div>
  );
}

function TopNav({ route, user, onLogout, onPassword, onSettings }) {
  const nav = [
    ["projects", "项目"],
    ["vulnerabilities", "漏洞报告"],
    ["workers", "工作节点"],
    ["templates", "模板"],
  ];

  return (
    <header className="top-nav">
      <button className="brand" type="button" onClick={() => go("#/projects")}>
        <span className="brand-mark">
          <img src="/static/rabbit-icon.png" alt="Rabbit" />
        </span>
        <span>{APP_NAME}</span>
      </button>
      <nav className="nav-tabs" aria-label="主导航">
        {nav.map(([key, label]) => {
          const active = route.page === key || (key === "projects" && route.page === "project");
          return (
            <button
              key={key}
              className={cn("nav-tab", active && "active")}
              type="button"
              onClick={() => go(key === "projects" ? "#/projects" : `#/${key}`)}
            >
              {label}
            </button>
          );
        })}
      </nav>
      <div className="nav-actions">
        <span className="user-chip">
          <User size={17} />
          {user.username}
        </span>
        <button className="ghost-button" type="button" onClick={onPassword}>
          <KeyRound size={17} />
          修改密码
        </button>
        <button className="ghost-button" type="button" onClick={onSettings}>
          <Settings size={17} />
        </button>
        <button className="ghost-button" type="button" onClick={onLogout}>
          <LogOut size={17} />
          退出登录
        </button>
      </div>
    </header>
  );
}

function AuthPage({ onAuthed, setToast }) {
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({ username: "", password: "", captcha_answer: "" });
  const [captcha, setCaptcha] = useState(null);
  const [loading, setLoading] = useState(false);

  const loadCaptcha = useCallback(async () => {
    const data = await apiRequest("/api/auth/captcha");
    setCaptcha(data);
    setForm((prev) => ({ ...prev, captcha_answer: "" }));
  }, []);

  useEffect(() => {
    loadCaptcha().catch((error) => setToast({ type: "danger", message: error.message }));
  }, [loadCaptcha, setToast]);

  const submit = async (event) => {
    event.preventDefault();
    setLoading(true);
    try {
      await apiRequest(`/api/auth/${mode === "login" ? "login" : "register"}`, {
        method: "POST",
        body: {
          username: form.username,
          password: form.password,
          captcha_id: captcha?.captcha_id,
          captcha_answer: form.captcha_answer,
        },
      });
      await onAuthed();
    } catch (error) {
      setToast({ type: "danger", message: error.message || "认证失败" });
      await loadCaptcha().catch(() => {});
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-shell">
      <div className="auth-art" aria-hidden="true">
        <span className="map-node node-a" />
        <span className="map-node node-b" />
        <span className="map-node node-c" />
        <span className="map-node node-d" />
        <span className="map-line line-a" />
        <span className="map-line line-b" />
        <span className="map-line line-c" />
      </div>
      <section className="auth-card">
        <div className="auth-title">
          <span className="auth-logo">
            <img src="/static/rabbit-icon.png" alt="Rabbit" />
          </span>
          <h1>{APP_NAME}</h1>
          <p>登录以继续安全探索工作流</p>
        </div>
        <div className="segmented">
          <button className={cn(mode === "login" && "active")} type="button" onClick={() => setMode("login")}>
            登录
          </button>
          <button className={cn(mode === "register" && "active")} type="button" onClick={() => setMode("register")}>
            注册
          </button>
        </div>
        <form className="stack-form" onSubmit={submit}>
          <label>
            <span>用户名</span>
            <input
              autoComplete="username"
              value={form.username}
              onChange={(event) => setForm({ ...form, username: event.target.value })}
              placeholder="admin"
            />
          </label>
          <label>
            <span>密码</span>
            <input
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              type="password"
              value={form.password}
              onChange={(event) => setForm({ ...form, password: event.target.value })}
              placeholder="输入密码"
            />
          </label>
          <label>
            <span>验证码</span>
            <div className="captcha-row">
              <input
                value={form.captcha_answer}
                onChange={(event) => setForm({ ...form, captcha_answer: event.target.value })}
                placeholder="计算结果"
              />
              <button className="captcha-chip" type="button" onClick={loadCaptcha}>
                {captcha?.question || "刷新"}
              </button>
            </div>
          </label>
          <button className="primary-button" type="submit" disabled={loading}>
            {loading ? <Loader2 className="spin" size={18} /> : <Lock size={18} />}
            {mode === "login" ? "登录" : "创建账号"}
          </button>
        </form>
      </section>
    </div>
  );
}

function PageHeader({ icon: Icon, title, subtitle, actions }) {
  return (
    <section className="page-header">
      <div className="page-title">
        <span className="page-icon">
          <Icon size={28} />
        </span>
        <div>
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </div>
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </section>
  );
}

function Toast({ toast, onClose }) {
  return (
    <div className={cn("toast", toast.type || "info")}>
      <span>{toast.message}</span>
      <button type="button" onClick={onClose}>
        <X size={16} />
      </button>
    </div>
  );
}

function Modal({ title, subtitle, children, onClose, wide = false }) {
  return (
    <div className="modal-backdrop" role="presentation">
      <section className={cn("modal-card", wide && "wide")} role="dialog" aria-modal="true">
        <header className="modal-header">
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button className="icon-button" type="button" onClick={onClose}>
            <X size={18} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}

function EmptyState({ icon: Icon = Sparkles, title, subtitle, action }) {
  return (
    <div className="empty-state">
      <Icon size={42} />
      <h3>{title}</h3>
      {subtitle && <p>{subtitle}</p>}
      {action}
    </div>
  );
}

function Badge({ tone = "muted", children }) {
  return <span className={cn("badge", tone)}>{children}</span>;
}

function MiniStat({ label, value }) {
  return (
    <div className="mini-stat">
      <span>{label}</span>
      <strong>{value ?? "-"}</strong>
    </div>
  );
}

function ProjectsPage({ runAction, setToast }) {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newOpen, setNewOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setProjects(await apiRequest("/projects"));
    } catch (error) {
      setToast({ type: "danger", message: error.message || "项目加载失败" });
    } finally {
      setLoading(false);
    }
  }, [setToast]);

  useEffect(() => {
    load();
  }, [load]);

  const counts = useMemo(() => {
    const total = projects.length;
    const active = projects.filter((project) => project.status === "active").length;
    const completed = projects.filter((project) => project.status === "completed").length;
    const stopped = projects.filter((project) => project.status === "stopped").length;
    return { total, active, completed, stopped };
  }, [projects]);

  const deleteProject = async (project) => {
    if (!window.confirm(`确认删除项目 ${project.title}？`)) return;
    await runAction("项目已删除", () => apiRequest(`/projects/${project.id}`, { method: "DELETE" }));
    await load();
  };

  const updateStatus = async (project, status) => {
    await runAction("项目状态已更新", () =>
      apiRequest(`/projects/${project.id}/status`, { method: "PUT", body: { status } }),
    );
    await load();
  };

  const reopenProject = async (project) => {
    const description = window.prompt("重新打开原因", "补充验证或重新探索");
    if (!description) return;
    await runAction("项目已重新打开", () =>
      apiRequest(`/projects/${project.id}/reopen`, {
        method: "POST",
        body: { description, creator: HUMAN_WORKER },
      }),
    );
    await load();
  };

  return (
    <>
      <PageHeader
        icon={Network}
        title="项目"
        subtitle="面向目标的事实图探索工作区"
        actions={
          <>
            <div className="status-pill">
              <span className="dot success" />
              <span>{counts.active} 个运行中</span>
            </div>
            <button className="primary-outline" type="button" onClick={() => setNewOpen(true)}>
              <Plus size={18} />
              新建项目
            </button>
            <button className="ghost-button" type="button" onClick={load}>
              <RefreshCw size={18} />
              刷新
            </button>
          </>
        }
      />
      <section className="content-wrap">
        <div className="metric-grid">
          <MetricCard label="全部项目" value={counts.total} tone="info" />
          <MetricCard label="运行中" value={counts.active} tone="success" />
          <MetricCard label="已完成" value={counts.completed} tone="muted" />
          <MetricCard label="已停止" value={counts.stopped} tone="warning" />
        </div>
        {loading ? (
          <EmptyState icon={Loader2} title="正在加载项目" />
        ) : projects.length === 0 ? (
          <EmptyState
            icon={Folder}
            title="还没有项目"
            subtitle="创建一个项目后，Rabbit 会围绕起点、目标和提示生成事实图。"
            action={
              <button className="primary-button compact" type="button" onClick={() => setNewOpen(true)}>
                <Plus size={18} />
                新建项目
              </button>
            }
          />
        ) : (
          <div className="project-grid">
            {projects.map((project) => (
              <ProjectCard
                key={project.id}
                project={project}
                onDelete={() => deleteProject(project)}
                onStop={() => updateStatus(project, "stopped")}
                onStart={() => updateStatus(project, "active")}
                onReopen={() => reopenProject(project)}
              />
            ))}
          </div>
        )}
      </section>
      {newOpen && (
        <NewProjectModal
          onClose={() => setNewOpen(false)}
          onCreated={(projectId) => {
            setNewOpen(false);
            go(`#/projects/${projectId}`);
          }}
          runAction={runAction}
        />
      )}
    </>
  );
}

function MetricCard({ label, value, tone }) {
  return (
    <div className={cn("metric-card", tone)}>
      <span className="metric-dot" />
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
    </div>
  );
}

function ProjectCard({ project, onDelete, onStop, onStart, onReopen }) {
  const status = STATUS_META[project.status] || STATUS_META.active;
  return (
    <article className="project-card">
      <button className="project-open" type="button" onClick={() => go(`#/projects/${project.id}`)}>
        <span className="project-folder">
          <Folder size={24} />
        </span>
        <span className="project-main">
          <span className="project-title-row">
            <strong>{project.title}</strong>
            <Badge tone={status.tone}>{status.label}</Badge>
          </span>
          <span className="project-sub">
            {project.id} · 创建于 {formatTime(project.created_at)}
          </span>
        </span>
      </button>
      <div className="project-stats">
        <MiniStat label="事实" value={project.fact_count} />
        <MiniStat label="意图" value={project.intent_count} />
        <MiniStat label="工作中" value={project.working_intent_count} />
      </div>
      {project.reason && (
        <div className="reason-strip">
          <Activity size={16} />
          <span>{project.reason.worker}</span>
          <span>{project.reason.trigger}</span>
        </div>
      )}
      <div className="card-actions">
        <button className="ghost-button compact" type="button" onClick={() => go(`#/projects/${project.id}`)}>
          <Eye size={16} />
          打开
        </button>
        {project.status === "active" && (
          <button className="ghost-button compact warning" type="button" onClick={onStop}>
            <Square size={16} />
            停止
          </button>
        )}
        {project.status === "stopped" && (
          <button className="ghost-button compact" type="button" onClick={onStart}>
            <Play size={16} />
            继续
          </button>
        )}
        {project.status === "completed" && (
          <button className="ghost-button compact" type="button" onClick={onReopen}>
            <RefreshCw size={16} />
            重新打开
          </button>
        )}
        <button className="ghost-button compact danger" type="button" onClick={onDelete}>
          <Trash2 size={16} />
          删除
        </button>
      </div>
    </article>
  );
}

function NewProjectModal({ onClose, onCreated, runAction, initial = null }) {
  const [form, setForm] = useState({
    title: initial?.title || "",
    origin: initial?.origin || "",
    goal: initial?.goal || "",
    hints: initial?.hints?.map((hint) => hint.content).join("\n") || "",
  });
  const [saving, setSaving] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      const detail = await runAction("项目已创建", () =>
        apiRequest("/projects", {
          method: "POST",
          body: {
            title: form.title,
            origin: form.origin,
            goal: form.goal,
            hints: parseHintLines(form.hints, HUMAN_WORKER),
          },
        }),
      );
      onCreated(detail.project.id);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="新建项目" subtitle="只需要定义起点、目标和必要提示，Worker 会围绕事实图推进。" onClose={onClose} wide>
      <form className="stack-form modal-body" onSubmit={submit}>
        <label>
          <span>项目名称</span>
          <input value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} required />
        </label>
        <div className="two-col">
          <label>
            <span>起点</span>
            <textarea
              value={form.origin}
              onChange={(event) => setForm({ ...form, origin: event.target.value })}
              rows={6}
              required
            />
          </label>
          <label>
            <span>目标</span>
            <textarea
              value={form.goal}
              onChange={(event) => setForm({ ...form, goal: event.target.value })}
              rows={6}
              required
            />
          </label>
        </div>
        <label>
          <span>初始提示</span>
          <textarea
            value={form.hints}
            onChange={(event) => setForm({ ...form, hints: event.target.value })}
            rows={4}
            placeholder="每行一条提示"
          />
        </label>
        <div className="modal-footer">
          <button className="ghost-button" type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary-button compact" type="submit" disabled={saving}>
            {saving ? <Loader2 className="spin" size={18} /> : <Plus size={18} />}
            创建项目
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ProjectWorkspace({ projectId, runAction, setToast }) {
  const [detail, setDetail] = useState(null);
  const [timeline, setTimeline] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [tab, setTab] = useState("details");
  const [modal, setModal] = useState(null);
  const [layout, setLayout] = useState("dagre");

  const load = useCallback(async () => {
    try {
      const [project, events] = await Promise.all([
        apiRequest(`/projects/${projectId}`),
        apiRequest(`/api/projects/${projectId}/timeline`).catch(() => []),
      ]);
      setDetail(project);
      setTimeline(events);
    } catch (error) {
      setToast({ type: "danger", message: error.message || "项目加载失败" });
    } finally {
      setLoading(false);
    }
  }, [projectId, setToast]);

  useEffect(() => {
    setLoading(true);
    setSelected(null);
    load();
  }, [load]);

  useEffect(() => {
    if (detail?.project?.status !== "active") return undefined;
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [detail?.project?.status, load]);

  const project = detail?.project;
  const facts = detail?.facts || [];
  const intents = detail?.intents || [];
  const selectedFactIds = selected?.type === "fact" ? [selected.id] : facts.length ? ["origin"] : [];
  const selectedIntent = selected?.type === "intent" ? intents.find((intent) => intent.id === selected.id) : null;

  const updateTitle = async () => {
    const title = window.prompt("项目名称", project.title);
    if (!title || title === project.title) return;
    await runAction("项目名称已更新", () =>
      apiRequest(`/projects/${project.id}/title`, { method: "PUT", body: { title } }),
    );
    await load();
  };

  const updateStatus = async (status) => {
    await runAction("项目状态已更新", () =>
      apiRequest(`/projects/${project.id}/status`, { method: "PUT", body: { status } }),
    );
    await load();
  };

  const deleteProject = async () => {
    if (!window.confirm(`确认删除项目 ${project.title}？`)) return;
    await runAction("项目已删除", () => apiRequest(`/projects/${project.id}`, { method: "DELETE" }));
    go("#/projects");
  };

  const exportProject = async (format) => {
    await runAction(null, () => downloadFromApi(`/projects/${project.id}/export?format=${format}`, `${project.id}.${format}`));
  };

  if (loading) {
    return <EmptyState icon={Loader2} title="正在载入图谱" />;
  }

  if (!detail) {
    return (
      <EmptyState
        icon={AlertTriangle}
        title="项目不可用"
        action={
          <button className="primary-outline" type="button" onClick={() => go("#/projects")}>
            返回项目
          </button>
        }
      />
    );
  }

  const status = STATUS_META[project.status] || STATUS_META.active;

  return (
    <>
      <section className="workspace-header">
        <button className="icon-button" type="button" onClick={() => go("#/projects")}>
          <ArrowLeft size={20} />
        </button>
        <div className="workspace-title">
          <span>{project.id}</span>
          <button type="button" onClick={updateTitle}>
            {project.title}
          </button>
          <Badge tone={status.tone}>{status.label}</Badge>
        </div>
        <div className="workspace-actions">
          <div className="status-pill">
            <span>{facts.length} 个事实</span>
            <span>{intents.length} 个意图</span>
          </div>
          <button className="ghost-button compact" type="button" onClick={() => exportProject("yaml")}>
            <Download size={16} />
            YAML
          </button>
          <button className="ghost-button compact" type="button" onClick={() => exportProject("timeline")}>
            <Clock size={16} />
            时间线
          </button>
          {project.status === "active" ? (
            <button className="ghost-button compact warning" type="button" onClick={() => updateStatus("stopped")}>
              <Square size={16} />
              停止
            </button>
          ) : project.status === "stopped" ? (
            <button className="ghost-button compact" type="button" onClick={() => updateStatus("active")}>
              <Play size={16} />
              继续
            </button>
          ) : (
            <button className="ghost-button compact" type="button" onClick={() => setModal("reopen")}>
              <RefreshCw size={16} />
              重新打开
            </button>
          )}
          <button className="ghost-button compact danger" type="button" onClick={deleteProject}>
            <Trash2 size={16} />
            删除
          </button>
          <button className="ghost-button compact" type="button" onClick={load}>
            <RefreshCw size={16} />
            刷新
          </button>
        </div>
      </section>
      <section className="workspace-layout">
        <div className="graph-panel">
          <div className="graph-toolbar floating">
            <select value={layout} onChange={(event) => setLayout(event.target.value)}>
              <option value="dagre">Dagre</option>
              <option value="breadthfirst">层级</option>
              <option value="circle">环形</option>
              <option value="grid">网格</option>
            </select>
          </div>
          <div className="graph-actions floating right">
            <button className="primary-outline compact" type="button" onClick={() => setModal("intent")}>
              <Plus size={16} />
              意图
            </button>
            <button
              className="primary-outline compact success"
              type="button"
              onClick={() => setModal("conclude")}
              disabled={!selectedIntent || selectedIntent.to}
            >
              <CheckCircle2 size={16} />
              完成
            </button>
            <button className="primary-outline compact warning" type="button" onClick={() => setModal("hint")}>
              <Sparkles size={16} />
              提示
            </button>
            <button className="primary-outline compact" type="button" onClick={() => setModal("complete")}>
              <ShieldAlert size={16} />
              总结
            </button>
          </div>
          <GraphCanvas detail={detail} selected={selected} onSelect={setSelected} layout={layout} />
        </div>
        <Inspector
          detail={detail}
          selected={selected}
          setSelected={setSelected}
          tab={tab}
          setTab={setTab}
          timeline={timeline}
          onRefresh={load}
          runAction={runAction}
        />
      </section>
      {modal === "intent" && (
        <IntentModal
          title="新增探索意图"
          fromIds={selectedFactIds}
          facts={facts}
          onClose={() => setModal(null)}
          onSubmit={async (payload) => {
            await runAction("意图已创建", () =>
              apiRequest(`/projects/${project.id}/intents`, { method: "POST", body: payload }),
            );
            setModal(null);
            await load();
          }}
        />
      )}
      {modal === "conclude" && selectedIntent && (
        <TextActionModal
          title={`完成意图 ${selectedIntent.id}`}
          label="产出事实"
          onClose={() => setModal(null)}
          onSubmit={async (description) => {
            await runAction("意图已完成", () =>
              apiRequest(`/projects/${project.id}/intents/${selectedIntent.id}/conclude`, {
                method: "POST",
                body: { worker: selectedIntent.worker || HUMAN_WORKER, description },
              }),
            );
            setModal(null);
            await load();
          }}
        />
      )}
      {modal === "hint" && (
        <TextActionModal
          title="添加项目提示"
          label="提示内容"
          onClose={() => setModal(null)}
          onSubmit={async (content) => {
            await runAction("提示已添加", () =>
              apiRequest(`/projects/${project.id}/hints`, { method: "POST", body: { content, creator: HUMAN_WORKER } }),
            );
            setModal(null);
            await load();
          }}
        />
      )}
      {modal === "complete" && (
        <TextActionModal
          title="总结项目"
          label="总结结论"
          onClose={() => setModal(null)}
          onSubmit={async (description) => {
            await runAction("项目已完成", () =>
              apiRequest(`/projects/${project.id}/complete`, {
                method: "POST",
                body: { from: selectedFactIds, worker: HUMAN_WORKER, description },
              }),
            );
            setModal(null);
            await load();
          }}
        />
      )}
      {modal === "reopen" && (
        <TextActionModal
          title="重新打开项目"
          label="重新打开原因"
          onClose={() => setModal(null)}
          onSubmit={async (description) => {
            await runAction("项目已重新打开", () =>
              apiRequest(`/projects/${project.id}/reopen`, {
                method: "POST",
                body: { creator: HUMAN_WORKER, description },
              }),
            );
            setModal(null);
            await load();
          }}
        />
      )}
    </>
  );
}

function GraphCanvas({ detail, selected, onSelect, layout }) {
  const containerRef = useRef(null);
  const cyRef = useRef(null);

  const elements = useMemo(() => {
    const nodes = detail.facts.map((fact) => ({
      data: { id: fact.id, label: fact.id === "origin" ? "起点" : fact.id === "goal" ? "目标" : `${fact.id}: ${fact.description}` },
      classes: cn("fact-node", fact.id),
    }));
    const intentNodes = detail.intents.map((intent) => ({
      data: {
        id: intent.id,
        label: `${intent.id}: ${intent.description}`,
      },
      classes: cn("intent-node", intent.to ? "done" : intent.worker ? "claimed" : "open"),
    }));
    const edges = [];
    detail.intents.forEach((intent) => {
      (intent.from || []).forEach((source) => {
        edges.push({
          data: { id: `${source}-${intent.id}`, source, target: intent.id, label: "触发" },
          classes: "source-edge",
        });
      });
      if (intent.to) {
        edges.push({
          data: { id: `${intent.id}-${intent.to}`, source: intent.id, target: intent.to, label: "产出" },
          classes: "result-edge",
        });
      }
    });
    return [...nodes, ...intentNodes, ...edges];
  }, [detail]);

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      wheelSensitivity: 0.18,
      minZoom: 0.2,
      maxZoom: 2.2,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "font-family": "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
            "font-size": 12,
            "font-weight": 700,
            color: "#fff",
            "text-wrap": "wrap",
            "text-max-width": 150,
            "text-valign": "center",
            "text-halign": "center",
            width: 150,
            height: 58,
            shape: "round-rectangle",
            "background-color": "#007aff",
            "border-width": 0,
            "shadow-blur": 18,
            "shadow-color": "rgba(0, 122, 255, .22)",
            "shadow-opacity": 1,
          },
        },
        {
          selector: ".fact-node",
          style: {
            "background-color": "#0a84ff",
          },
        },
        {
          selector: ".origin",
          style: {
            width: 220,
            height: 118,
            "background-color": "#34c759",
            "font-size": 24,
          },
        },
        {
          selector: ".goal",
          style: {
            width: 220,
            height: 118,
            "background-color": "#ff6b6b",
            opacity: 0.75,
            "font-size": 24,
          },
        },
        {
          selector: ".intent-node.open",
          style: {
            "background-color": "#ff9f0a",
          },
        },
        {
          selector: ".intent-node.claimed",
          style: {
            "background-color": "#5856d6",
          },
        },
        {
          selector: ".intent-node.done",
          style: {
            "background-color": "#007aff",
          },
        },
        {
          selector: "edge",
          style: {
            width: 2,
            "curve-style": "bezier",
            "target-arrow-shape": "triangle",
            "line-color": "#7ee0c5",
            "target-arrow-color": "#7ee0c5",
            label: "data(label)",
            "font-size": 10,
            color: "#6b7280",
            "text-background-color": "#fff",
            "text-background-opacity": 0.75,
            "text-background-padding": 2,
          },
        },
        {
          selector: ".source-edge",
          style: {
            "line-style": "dashed",
          },
        },
        {
          selector: ":selected",
          style: {
            "border-width": 5,
            "border-color": "#ffffff",
            "shadow-blur": 30,
            "shadow-color": "rgba(0, 122, 255, .36)",
          },
        },
      ],
    });
    cyRef.current = cy;
    cy.on("tap", "node", (event) => {
      const node = event.target;
      const id = node.id();
      const type = detail.facts.some((fact) => fact.id === id) ? "fact" : "intent";
      onSelect({ type, id });
    });
    cy.on("tap", (event) => {
      if (event.target === cy) onSelect(null);
    });
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [elements, detail.facts, onSelect]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const options =
      layout === "dagre"
        ? { name: "dagre", rankDir: "TB", nodeSep: 55, rankSep: 90, fit: true, padding: 90 }
        : { name: layout, fit: true, padding: 90 };
    cy.layout(options).run();
  }, [layout, elements]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().unselect();
    if (selected?.id) {
      cy.getElementById(selected.id).select();
    }
  }, [selected]);

  return <div className="graph-canvas" ref={containerRef} />;
}

function Inspector({ detail, selected, setSelected, tab, setTab, timeline, onRefresh, runAction }) {
  const facts = detail.facts;
  const intents = detail.intents;
  const fact = selected?.type === "fact" ? facts.find((item) => item.id === selected.id) : null;
  const intent = selected?.type === "intent" ? intents.find((item) => item.id === selected.id) : null;
  const tabs = [
    ["details", "详情", null],
    ["hints", "提示", detail.hints.length],
    ["logs", "日志", intents.length],
    ["timeline", "时间线", timeline.length],
  ];

  const claimIntent = async () => {
    if (!intent) return;
    await runAction("意图已认领", () =>
      apiRequest(`/projects/${detail.project.id}/intents/${intent.id}/heartbeat`, {
        method: "POST",
        body: { worker: HUMAN_WORKER },
      }),
    );
    onRefresh();
  };

  const releaseIntent = async () => {
    if (!intent) return;
    await runAction("意图已释放", () =>
      apiRequest(`/projects/${detail.project.id}/intents/${intent.id}/release`, {
        method: "POST",
        body: { worker: intent.worker || HUMAN_WORKER },
      }),
    );
    onRefresh();
  };

  return (
    <aside className="inspector">
      <div className="inspector-tabs">
        {tabs.map(([key, label, count]) => (
          <button key={key} className={cn(tab === key && "active")} type="button" onClick={() => setTab(key)}>
            {label}
            {count !== null && <span>{count}</span>}
          </button>
        ))}
      </div>
      <div className="inspector-body">
        {tab === "details" && (
          <>
            {!selected && (
              <div className="detail-card">
                <span>项目</span>
                <h3>{detail.project.title}</h3>
                <p>{detail.project.id}</p>
                <div className="detail-grid">
                  <MiniStat label="事实" value={facts.length} />
                  <MiniStat label="意图" value={intents.length} />
                </div>
              </div>
            )}
            {fact && (
              <div className="detail-card">
                <span>事实</span>
                <h3>{fact.id}</h3>
                <p>{fact.description}</p>
              </div>
            )}
            {intent && (
              <div className="detail-card">
                <span>意图</span>
                <h3>{intent.id}</h3>
                <p>{intent.description}</p>
                <div className="detail-meta">
                  <span>来源：{(intent.from || []).join(", ")}</span>
                  <span>产出：{intent.to || "未完成"}</span>
                  <span>创建者：{intent.creator}</span>
                  <span>Worker：{intent.worker || "未认领"}</span>
                </div>
                {!intent.to && (
                  <div className="button-row">
                    {!intent.worker ? (
                      <button className="primary-outline compact" type="button" onClick={claimIntent}>
                        <Activity size={16} />
                        认领
                      </button>
                    ) : (
                      <button className="ghost-button compact" type="button" onClick={releaseIntent}>
                        <Pause size={16} />
                        释放
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}
          </>
        )}
        {tab === "hints" && (
          <div className="timeline-list">
            {detail.hints.length === 0 ? (
              <EmptyState title="暂无提示" />
            ) : (
              detail.hints.map((hint) => (
                <article className="timeline-item" key={hint.id}>
                  <span>{hint.id}</span>
                  <p>{hint.content}</p>
                  <small>
                    {hint.creator} · {formatTime(hint.created_at)}
                  </small>
                </article>
              ))
            )}
          </div>
        )}
        {tab === "logs" && (
          <div className="timeline-list">
            {intents.map((item) => (
              <button
                className="timeline-item clickable"
                key={item.id}
                type="button"
                onClick={() => {
                  setSelected({ type: "intent", id: item.id });
                  setTab("details");
                }}
              >
                <span>{item.id}</span>
                <p>{item.description}</p>
                <small>{item.worker || item.creator}</small>
              </button>
            ))}
          </div>
        )}
        {tab === "timeline" && (
          <div className="timeline-list">
            {timeline.length === 0 ? (
              <EmptyState title="暂无时间线" />
            ) : (
              timeline.map((event) => (
                <button
                  className="timeline-item clickable"
                  key={event.id}
                  type="button"
                  onClick={() => event.node_id && setSelected({ type: event.node_id.startsWith("i") ? "intent" : "fact", id: event.node_id })}
                >
                  <span>{event.event_type}</span>
                  <p>{event.description}</p>
                  <small>
                    {formatTime(event.timestamp)} {event.actor ? `· ${event.actor}` : ""}
                  </small>
                </button>
              ))
            )}
          </div>
        )}
      </div>
    </aside>
  );
}

function IntentModal({ fromIds, facts, onClose, onSubmit }) {
  const [form, setForm] = useState({
    from: fromIds,
    description: "",
    worker: "",
  });
  const [saving, setSaving] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      await onSubmit({
        from: form.from,
        description: form.description,
        creator: HUMAN_WORKER,
        worker: form.worker || null,
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="新增探索意图" onClose={onClose}>
      <form className="stack-form modal-body" onSubmit={submit}>
        <label>
          <span>来源事实</span>
          <select
            multiple
            value={form.from}
            onChange={(event) =>
              setForm({ ...form, from: Array.from(event.target.selectedOptions).map((option) => option.value) })
            }
          >
            {facts.map((fact) => (
              <option key={fact.id} value={fact.id}>
                {fact.id} · {clampText(fact.description, 60)}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>意图描述</span>
          <textarea
            rows={5}
            value={form.description}
            onChange={(event) => setForm({ ...form, description: event.target.value })}
            required
          />
        </label>
        <label>
          <span>直接认领给 Worker（可选）</span>
          <input value={form.worker} onChange={(event) => setForm({ ...form, worker: event.target.value })} />
        </label>
        <div className="modal-footer">
          <button className="ghost-button" type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary-button compact" type="submit" disabled={saving || !form.from.length}>
            {saving ? <Loader2 className="spin" size={18} /> : <Plus size={18} />}
            保存
          </button>
        </div>
      </form>
    </Modal>
  );
}

function TextActionModal({ title, label, onClose, onSubmit }) {
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      await onSubmit(text);
    } finally {
      setSaving(false);
    }
  };
  return (
    <Modal title={title} onClose={onClose}>
      <form className="stack-form modal-body" onSubmit={submit}>
        <label>
          <span>{label}</span>
          <textarea rows={6} value={text} onChange={(event) => setText(event.target.value)} required />
        </label>
        <div className="modal-footer">
          <button className="ghost-button" type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary-button compact" type="submit" disabled={saving}>
            {saving ? <Loader2 className="spin" size={18} /> : <Save size={18} />}
            保存
          </button>
        </div>
      </form>
    </Modal>
  );
}

function VulnerabilitiesPage({ runAction, setToast }) {
  const [vulnerabilities, setVulnerabilities] = useState([]);
  const [summary, setSummary] = useState({ critical: 0, high: 0, medium: 0, low: 0 });
  const [projects, setProjects] = useState([]);
  const [filters, setFilters] = useState({ severity: "", project_id: "" });
  const [expandedProjects, setExpandedProjects] = useState({});
  const [expandedVulns, setExpandedVulns] = useState({});
  const [loading, setLoading] = useState(true);

  const query = useMemo(() => {
    const params = new URLSearchParams();
    if (filters.severity) params.set("severity", filters.severity);
    if (filters.project_id) params.set("project_id", filters.project_id);
    const suffix = params.toString();
    return suffix ? `?${suffix}` : "";
  }, [filters]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, counts, projectList] = await Promise.all([
        apiRequest(`/api/vulnerabilities${query}`),
        apiRequest("/api/vulnerabilities/summary"),
        apiRequest("/projects"),
      ]);
      setVulnerabilities(list);
      setSummary(counts);
      setProjects(projectList);
      setExpandedProjects((prev) => {
        if (Object.keys(prev).length) return prev;
        return Object.fromEntries(list.map((item) => [item.project_id, true]));
      });
    } catch (error) {
      setToast({ type: "danger", message: error.message || "漏洞报告加载失败" });
    } finally {
      setLoading(false);
    }
  }, [query, setToast]);

  useEffect(() => {
    load();
  }, [load]);

  const refresh = async () => {
    await runAction("漏洞报告已刷新", () => apiRequest("/api/vulnerabilities/refresh", { method: "POST" }));
    await load();
  };

  const exportMd = async ({ projectId, vulnerabilityId, title }) => {
    const params = new URLSearchParams({ format: "md" });
    if (filters.severity) params.set("severity", filters.severity);
    if (projectId) params.set("project_id", projectId);
    if (vulnerabilityId) params.set("vulnerability_id", vulnerabilityId);
    await runAction(null, () => downloadFromApi(`/api/vulnerabilities/export?${params}`, `${title || "vulnerabilities"}.md`));
  };

  const groups = useMemo(() => {
    return Array.from(groupBy(vulnerabilities, (item) => item.project_id).entries()).map(([projectId, items]) => ({
      projectId,
      projectName: items[0]?.project_name || projectId,
      items,
      counts: summarizeSeverity(items),
      latest: items.map((item) => item.discovered_at).sort().at(-1),
    }));
  }, [vulnerabilities]);

  const filteredProjectCount = groups.length;
  const filteredVulnCount = vulnerabilities.length;

  return (
    <>
      <PageHeader
        icon={AlertTriangle}
        title="漏洞报告"
        subtitle="按项目归档漏洞、证明数据包和漏洞浮现过程"
        actions={
          <>
            <button className="ghost-button" type="button" onClick={refresh}>
              <RefreshCw size={18} />
              刷新
            </button>
          </>
        }
      />
      <section className="content-wrap">
        <div className="metric-grid severity">
          {["critical", "high", "medium", "low"].map((level) => (
            <MetricCard key={level} label={SEVERITY_META[level].label} value={summary[level] || 0} tone={level} />
          ))}
        </div>
        <div className="filter-panel">
          <label>
            <span>严重程度</span>
            <select value={filters.severity} onChange={(event) => setFilters({ ...filters, severity: event.target.value })}>
              <option value="">全部严重程度</option>
              {Object.entries(SEVERITY_META).map(([key, meta]) => (
                <option key={key} value={key}>
                  {meta.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>项目</span>
            <select value={filters.project_id} onChange={(event) => setFilters({ ...filters, project_id: event.target.value })}>
              <option value="">全部项目</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.title} ({project.id})
                </option>
              ))}
            </select>
          </label>
          {(filters.severity || filters.project_id) && (
            <button className="ghost-button compact" type="button" onClick={() => setFilters({ severity: "", project_id: "" })}>
              <X size={16} />
              清除筛选
            </button>
          )}
          <div className="result-count">
            {filteredProjectCount} 个项目 / {filteredVulnCount} 条漏洞
          </div>
        </div>
        {loading ? (
          <EmptyState icon={Loader2} title="正在分析漏洞报告" />
        ) : vulnerabilities.length === 0 ? (
          <EmptyState icon={CheckCircle2} title="没有匹配的漏洞" subtitle="当前筛选范围内尚未发现漏洞。" />
        ) : (
          <div className="folder-list">
            {groups.map((group) => {
              const expanded = expandedProjects[group.projectId] ?? true;
              return (
                <article className="project-folder-card" key={group.projectId}>
                  <div className="folder-header">
                    <button
                      className="folder-toggle-main"
                      type="button"
                      onClick={() => setExpandedProjects({ ...expandedProjects, [group.projectId]: !expanded })}
                    >
                      <span className="project-folder large">
                        <Folder size={28} />
                      </span>
                      <span className="folder-title">
                        <strong>{group.projectName}</strong>
                        <span>
                          {group.projectId} · {group.items.length} 条漏洞 · 最新发现 {formatTime(group.latest)}
                        </span>
                      </span>
                    </button>
                    <span className="severity-row">
                      {Object.entries(group.counts)
                        .filter(([, count]) => count > 0)
                        .map(([level, count]) => (
                          <Badge key={level} tone={SEVERITY_META[level].tone}>
                            {SEVERITY_META[level].label} {count}
                          </Badge>
                        ))}
                    </span>
                    <button
                      className="ghost-button compact"
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        exportMd({ projectId: group.projectId, title: group.projectId });
                      }}
                    >
                      <Download size={16} />
                      导出 MD
                    </button>
                    <button
                      className="icon-button"
                      type="button"
                      onClick={() => setExpandedProjects({ ...expandedProjects, [group.projectId]: !expanded })}
                    >
                      {expanded ? <ChevronDown size={20} /> : <ChevronRight size={20} />}
                    </button>
                  </div>
                  {expanded && (
                    <div className="vuln-list">
                      {group.items.map((vuln) => (
                        <VulnerabilityItem
                          key={vuln.id}
                          vuln={vuln}
                          expanded={!!expandedVulns[vuln.id]}
                          onToggle={() => setExpandedVulns({ ...expandedVulns, [vuln.id]: !expandedVulns[vuln.id] })}
                          onExport={() => exportMd({ vulnerabilityId: vuln.id, title: `${vuln.project_id}-${vuln.fact_id}` })}
                        />
                      ))}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </section>
    </>
  );
}

function summarizeSeverity(items) {
  return items.reduce(
    (acc, item) => {
      acc[item.severity] = (acc[item.severity] || 0) + 1;
      return acc;
    },
    { critical: 0, high: 0, medium: 0, low: 0 },
  );
}

function VulnerabilityItem({ vuln, expanded, onToggle, onExport }) {
  const meta = SEVERITY_META[vuln.severity] || SEVERITY_META.low;
  return (
    <article className="vuln-item">
      <div className="vuln-summary">
        <div>
          <div className="vuln-meta">
            <Badge tone={meta.tone}>{meta.label}</Badge>
            <span>{vuln.fact_id}</span>
            <span>{formatTime(vuln.discovered_at)}</span>
          </div>
          <h3>{vuln.title}</h3>
          <p>{clampText(vuln.description, 220)}</p>
        </div>
        <div className="button-row">
          <button className="ghost-button compact" type="button" onClick={onExport}>
            <Download size={16} />
            导出
          </button>
          <button className="ghost-button compact" type="button" onClick={onToggle}>
            {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            查看详情
          </button>
        </div>
      </div>
      {expanded && (
        <div className="vuln-detail">
          <div className="detail-grid cards">
            <InfoBox label="项目来源" value={`${vuln.project_name} (${vuln.project_id})`} />
            <InfoBox label="确认事实" value={vuln.fact_id} />
            <InfoBox label="来源意图" value={vuln.source_intent_id || "未记录"} />
            <InfoBox label="工作节点" value={vuln.source_worker || "未记录"} />
          </div>
          <section>
            <h4>完整描述</h4>
            <p className="soft-box">{vuln.description}</p>
          </section>
          <section>
            <h4>关键证据</h4>
            <div className="evidence-list">
              {(vuln.evidence?.length ? vuln.evidence : ["未记录"]).map((item, index) => (
                <p key={`${item}-${index}`}>{item}</p>
              ))}
            </div>
          </section>
          <section>
            <h4>漏洞证明数据包</h4>
            <div className="packet-list">
              {(vuln.proof_packets || []).length === 0 ? (
                <p className="soft-box">未记录证明数据包。</p>
              ) : (
                vuln.proof_packets.map((packet, index) => (
                  <article className="packet-card" key={`${packet.title}-${index}`}>
                    <strong>{packet.title || `证明 ${index + 1}`}</strong>
                    <span>请求数据包</span>
                    <pre>{packet.request || "未记录"}</pre>
                    <span>响应/回显</span>
                    <pre>{packet.response || "未记录"}</pre>
                    {packet.note && <p>{packet.note}</p>}
                  </article>
                ))
              )}
            </div>
          </section>
          <section>
            <h4>漏洞浮现过程</h4>
            <div className="process-list">
              {(vuln.process || []).map((step, index) => (
                <article className="process-step" key={`${step.id}-${index}`}>
                  <span>{index + 1}</span>
                  <div>
                    <strong>
                      {step.label || step.type || "过程"} {step.id || ""}
                    </strong>
                    <p>{step.description || "无描述"}</p>
                    {(step.worker || step.time) && (
                      <small>
                        {step.worker || ""} {step.time ? `· ${formatTime(step.time)}` : ""}
                      </small>
                    )}
                  </div>
                </article>
              ))}
            </div>
          </section>
        </div>
      )}
    </article>
  );
}

function InfoBox({ label, value }) {
  return (
    <div className="info-box">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function WorkersPage({ runAction, setToast }) {
  const [workers, setWorkers] = useState([]);
  const [config, setConfig] = useState(null);
  const [history, setHistory] = useState({});
  const [expanded, setExpanded] = useState({});
  const [editor, setEditor] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [statusList, configPayload] = await Promise.all([
        apiRequest("/api/workers").catch((error) => {
          setToast({ type: "warning", message: error.message || "工作节点状态暂不可用" });
          return [];
        }),
        apiRequest("/api/workers/config").catch((error) => {
          setToast({ type: "warning", message: error.message || "Worker 配置暂不可用" });
          return null;
        }),
      ]);
      setWorkers(statusList);
      setConfig(configPayload);
      setLastUpdated(new Date());
    } finally {
      setLoading(false);
    }
  }, [setToast]);

  useEffect(() => {
    load();
  }, [load]);

  const statusByName = useMemo(() => new Map(workers.map((worker) => [worker.name, worker])), [workers]);
  const visibleWorkers = useMemo(() => {
    const configured = config?.workers || [];
    const names = new Set(configured.map((worker) => worker.name));
    const statusOnly = workers.filter((worker) => !names.has(worker.name)).map((worker) => ({ ...worker, env: {} }));
    return [...configured, ...statusOnly];
  }, [config, workers]);

  const saveWorkers = async (nextWorkers, label = "Worker 配置已保存") => {
    const updated = await runAction(label, () =>
      apiRequest("/api/workers/config", { method: "PUT", body: { workers: nextWorkers } }),
    );
    setConfig(updated);
    await load();
  };

  const setEnabled = async (worker, enabled) => {
    if (!config) return;
    const next = config.workers.map((item) => (item.name === worker.name ? { ...item, enabled } : item));
    await saveWorkers(next, enabled ? "Worker 已启用" : "Worker 已关闭");
  };

  const testWorker = async (worker) => {
    const source = config?.workers?.find((item) => item.name === worker.name) || worker;
    const result = await runAction(null, () =>
      apiRequest("/api/workers/config/test", { method: "POST", body: { worker: normalizeWorkerForSave(source) } }),
    );
    setToast({
      type: result.ok ? "success" : "danger",
      message: result.ok ? `${result.worker_name} 连通性正常` : `${result.worker_name} 测试失败：${result.preview || result.stderr_preview}`,
    });
  };

  const loadHistory = async (workerName) => {
    if (history[workerName]) return;
    const rows = await runAction(null, () => apiRequest(`/api/workers/${encodeURIComponent(workerName)}/history`));
    setHistory((prev) => ({ ...prev, [workerName]: rows }));
  };

  const toggleHistory = async (workerName) => {
    const open = !expanded[workerName];
    setExpanded((prev) => ({ ...prev, [workerName]: open }));
    if (open) await loadHistory(workerName);
  };

  const saveEditor = async (worker) => {
    if (!config) return;
    const normalized = normalizeWorkerForSave(worker);
    const exists = config.workers.some((item) => item.name === normalized.name);
    const next = exists
      ? config.workers.map((item) => (item.name === normalized.name ? normalized : item))
      : [...config.workers, normalized];
    await saveWorkers(next, exists ? "Worker 已更新" : "Worker 已新增");
    setEditor(null);
  };

  const deleteWorker = async (worker) => {
    if (!config || !window.confirm(`确认删除 Worker ${worker.name}？`)) return;
    await saveWorkers(config.workers.filter((item) => item.name !== worker.name), "Worker 已删除");
    setEditor(null);
  };

  return (
    <>
      <PageHeader
        icon={Monitor}
        title="工作节点"
        subtitle="实时状态、模型配置、任务历史与健康检查"
        actions={
          <>
            <button className="primary-outline" type="button" disabled={!config} onClick={() => setEditor(defaultWorkerDraft(config?.workers || []))}>
              <Plus size={18} />
              新增 Worker
            </button>
            <div className="status-pill">
              <span className="dot success" />
              <span>{lastUpdated ? `更新于 ${lastUpdated.toLocaleTimeString("zh-CN")}` : "待更新"}</span>
            </div>
            <button className="ghost-button" type="button" onClick={load}>
              <RefreshCw size={18} />
              刷新
            </button>
          </>
        }
      />
      <section className="content-wrap">
        {loading ? (
          <EmptyState icon={Loader2} title="正在读取工作节点" />
        ) : visibleWorkers.length === 0 ? (
          <EmptyState
            icon={Bot}
            title="暂无 Worker"
            subtitle="新增 Worker 后，调度器会按优先级和并发配置分配任务。"
            action={
              <button className="primary-button compact" type="button" disabled={!config} onClick={() => setEditor(defaultWorkerDraft([]))}>
                <Plus size={18} />
                新增 Worker
              </button>
            }
          />
        ) : (
          <div className="worker-grid">
            {visibleWorkers.map((worker) => {
              const status = statusByName.get(worker.name) || worker;
              const statusMeta = STATUS_META[status.status || (worker.enabled === false ? "disabled" : "offline")] || STATUS_META.offline;
              return (
                <article className="worker-card" key={worker.name}>
                  <header>
                    <span className={cn("worker-dot", statusMeta.tone)} />
                    <div>
                      <h3>{worker.name}</h3>
                      <p>
                        {worker.type} · {workerModelLabel(worker)}
                      </p>
                    </div>
                    <Badge tone={statusMeta.tone}>{statusMeta.label}</Badge>
                  </header>
                  {status.current_task && <p className="current-task">{status.current_task}</p>}
                  <div className="project-stats">
                    <MiniStat label="任务数" value={status.tasks_completed ?? 0} />
                    <MiniStat label="平均" value={status.avg_duration_seconds ? `${status.avg_duration_seconds}s` : "-"} />
                    <MiniStat label="心跳" value={relativeHeartbeat(status.last_heartbeat_seconds_ago)} />
                  </div>
                  <div className="card-actions">
                    <button
                      className={cn("ghost-button compact", worker.enabled === false ? "" : "warning")}
                      type="button"
                      disabled={!config}
                      onClick={() => setEnabled(worker, worker.enabled === false)}
                    >
                      {worker.enabled === false ? <Play size={16} /> : <Pause size={16} />}
                      {worker.enabled === false ? "启用" : "关闭"}
                    </button>
                    <button className="ghost-button compact" type="button" disabled={!config} onClick={() => testWorker(worker)}>
                      <Activity size={16} />
                      测试
                    </button>
                    <button className="ghost-button compact" type="button" disabled={!config} onClick={() => setEditor(worker)}>
                      <Settings size={16} />
                      编辑
                    </button>
                  </div>
                  <button className="history-toggle" type="button" onClick={() => toggleHistory(worker.name)}>
                    {expanded[worker.name] ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
                    {expanded[worker.name] ? "隐藏任务历史" : "显示任务历史"}
                  </button>
                  {expanded[worker.name] && (
                    <div className="history-list">
                      {(history[worker.name] || []).length === 0 ? (
                        <p>该工作节点暂无任务历史记录。</p>
                      ) : (
                        history[worker.name].map((row, index) => (
                          <article key={`${row.started_at}-${index}`}>
                            <strong>{row.task_type}</strong>
                            <span>{row.description}</span>
                            <small>
                              {row.project_name} · {formatTime(row.started_at)} · {row.outcome}
                            </small>
                          </article>
                        ))
                      )}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </section>
      {editor && (
        <WorkerEditor
          worker={editor}
          onClose={() => setEditor(null)}
          onSave={saveEditor}
          onDelete={config?.workers?.some((item) => item.name === editor.name) ? () => deleteWorker(editor) : null}
        />
      )}
    </>
  );
}

function workerModelLabel(worker) {
  const env = worker.env || {};
  return env.ANTHROPIC_MODEL || env.CODEX_MODEL || env.PI_MODEL || "未配置模型";
}

function defaultWorkerDraft(existingWorkers) {
  let index = 1;
  const names = new Set(existingWorkers.map((worker) => worker.name));
  while (names.has(`worker_local_${index}`)) index += 1;
  return {
    name: `worker_local_${index}`,
    type: "pi",
    enabled: true,
    task_types: [...TASK_TYPES],
    max_running: 1,
    priority: 0,
    env: {
      PI_MODEL: "",
      PI_BASE_URL: "",
      PI_API_KEY: "",
      PI_PROVIDER_API: "openai-chat-completions",
    },
    secret_env_keys: ["PI_API_KEY"],
  };
}

function normalizeWorkerForSave(worker) {
  return {
    name: worker.name.trim(),
    type: worker.type,
    enabled: worker.enabled !== false,
    task_types: worker.task_types?.length ? worker.task_types : [...TASK_TYPES],
    max_running: Number(worker.max_running) || 1,
    priority: Number(worker.priority) || 0,
    env: Object.fromEntries(Object.entries(worker.env || {}).map(([key, value]) => [key, String(value ?? "")])),
    secret_env_keys: worker.secret_env_keys || [],
  };
}

const WORKER_PRESETS = [
  {
    id: "pi-openai-chat",
    label: "Pi · OpenAI Chat",
    type: "pi",
    env: {
      PI_MODEL: "deepseekv4",
      PI_BASE_URL: "http://10.2.8.77:3000/v1/chat/completions",
      PI_API_KEY: "",
      PI_PROVIDER_API: "openai-chat-completions",
    },
  },
  {
    id: "claude-code",
    label: "Claude Code · Anthropic",
    type: "claudecode",
    env: {
      ANTHROPIC_MODEL: "claude-3-5-sonnet-latest",
      ANTHROPIC_BASE_URL: "https://api.anthropic.com",
      ANTHROPIC_AUTH_TOKEN: "",
    },
  },
  {
    id: "codex",
    label: "Codex · Responses API",
    type: "codex",
    env: {
      CODEX_MODEL: "gpt-5",
      CODEX_BASE_URL: "https://api.openai.com/v1",
      OPENAI_API_KEY: "",
    },
  },
  {
    id: "mock",
    label: "Mock",
    type: "mock",
    env: {},
  },
];

function WorkerEditor({ worker, onClose, onSave, onDelete }) {
  const [draft, setDraft] = useState(() => JSON.parse(JSON.stringify(worker)));
  const [saving, setSaving] = useState(false);
  const envKeys = useMemo(() => workerEnvKeys(draft.type), [draft.type]);

  const applyPreset = (presetId) => {
    const preset = WORKER_PRESETS.find((item) => item.id === presetId);
    if (!preset) return;
    setDraft((prev) => ({
      ...prev,
      type: preset.type,
      env: { ...preset.env },
      secret_env_keys: Object.keys(preset.env).filter((key) => /KEY|TOKEN|SECRET/i.test(key)),
    }));
  };

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      await onSave(draft);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="Worker 配置" subtitle="保存后由调度器验证并热更新，失败时原配置保持不变。" onClose={onClose} wide>
      <form className="stack-form modal-body" onSubmit={submit}>
        <div className="two-col tight">
          <label>
            <span>快速模板</span>
            <select defaultValue="" onChange={(event) => applyPreset(event.target.value)}>
              <option value="" disabled>
                选择模型模板
              </option>
              {WORKER_PRESETS.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.label}
                </option>
              ))}
            </select>
          </label>
          <label className="switch-line">
            <span>启用状态</span>
            <button
              className={cn("switch", draft.enabled !== false && "on")}
              type="button"
              onClick={() => setDraft({ ...draft, enabled: draft.enabled === false })}
            >
              <span />
            </button>
          </label>
        </div>
        <div className="two-col tight">
          <label>
            <span>名称</span>
            <input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} required />
          </label>
          <label>
            <span>类型</span>
            <select
              value={draft.type}
              onChange={(event) => {
                const type = event.target.value;
                setDraft({ ...draft, type, env: defaultEnvForType(type), secret_env_keys: workerEnvKeys(type).filter((key) => /KEY|TOKEN|SECRET/i.test(key)) });
              }}
            >
              <option value="pi">pi</option>
              <option value="claudecode">claudecode</option>
              <option value="codex">codex</option>
              <option value="mock">mock</option>
            </select>
          </label>
        </div>
        <div className="three-col">
          <label>
            <span>最大并发</span>
            <input
              type="number"
              min="1"
              value={draft.max_running}
              onChange={(event) => setDraft({ ...draft, max_running: event.target.value })}
            />
          </label>
          <label>
            <span>优先级</span>
            <input
              type="number"
              min="0"
              value={draft.priority}
              onChange={(event) => setDraft({ ...draft, priority: event.target.value })}
            />
          </label>
          <label>
            <span>任务类型</span>
            <div className="checkbox-row">
              {TASK_TYPES.map((type) => (
                <label key={type}>
                  <input
                    type="checkbox"
                    checked={draft.task_types?.includes(type)}
                    onChange={(event) => {
                      const next = event.target.checked
                        ? [...(draft.task_types || []), type]
                        : (draft.task_types || []).filter((item) => item !== type);
                      setDraft({ ...draft, task_types: next });
                    }}
                  />
                  {type}
                </label>
              ))}
            </div>
          </label>
        </div>
        <div className="env-grid">
          {envKeys.map((key) => {
            const secret = /KEY|TOKEN|SECRET/i.test(key);
            return (
              <label key={key}>
                <span>{key}</span>
                <input
                  type={secret ? "password" : "text"}
                  value={draft.env?.[key] ?? ""}
                  placeholder={secret ? SECRET_MASK : ""}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      env: { ...(draft.env || {}), [key]: event.target.value },
                      secret_env_keys: secret
                        ? Array.from(new Set([...(draft.secret_env_keys || []), key]))
                        : draft.secret_env_keys || [],
                    })
                  }
                  required={draft.type !== "mock"}
                />
              </label>
            );
          })}
        </div>
        <div className="modal-footer split">
          <div>
            {onDelete && (
              <button className="ghost-button danger" type="button" onClick={onDelete}>
                <Trash2 size={16} />
                删除 Worker
              </button>
            )}
          </div>
          <div className="button-row">
            <button className="ghost-button" type="button" onClick={onClose}>
              取消
            </button>
            <button className="primary-button compact" type="submit" disabled={saving}>
              {saving ? <Loader2 className="spin" size={18} /> : <Save size={18} />}
              保存配置
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
}

function workerEnvKeys(type) {
  if (type === "claudecode") return ["ANTHROPIC_MODEL", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"];
  if (type === "codex") return ["CODEX_MODEL", "CODEX_BASE_URL", "OPENAI_API_KEY"];
  if (type === "pi") return ["PI_MODEL", "PI_BASE_URL", "PI_API_KEY", "PI_PROVIDER_API", "PI_MODEL_CONTEXT_WINDOW"];
  return [];
}

function defaultEnvForType(type) {
  const preset = WORKER_PRESETS.find((item) => item.type === type);
  return { ...(preset?.env || {}) };
}

function TemplatesPage({ runAction, setToast }) {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newTemplate, setNewTemplate] = useState(false);
  const [projectTemplate, setProjectTemplate] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setTemplates(await apiRequest("/api/templates"));
    } catch (error) {
      setToast({ type: "danger", message: error.message || "模板加载失败" });
    } finally {
      setLoading(false);
    }
  }, [setToast]);

  useEffect(() => {
    load();
  }, [load]);

  const deleteTemplate = async (template) => {
    if (!window.confirm(`确认删除模板 ${template.title}？`)) return;
    await runAction("模板已删除", () => apiRequest(`/api/templates/${template.id}`, { method: "DELETE" }));
    await load();
  };

  return (
    <>
      <PageHeader
        icon={FileText}
        title="模板"
        subtitle="把常用目标、起点和提示保存成可复用项目模板"
        actions={
          <>
            <button className="primary-outline" type="button" onClick={() => setNewTemplate(true)}>
              <Plus size={18} />
              新建模板
            </button>
            <button className="ghost-button" type="button" onClick={load}>
              <RefreshCw size={18} />
              刷新
            </button>
          </>
        }
      />
      <section className="content-wrap">
        {loading ? (
          <EmptyState icon={Loader2} title="正在加载模板" />
        ) : (
          <div className="template-grid">
            {templates.map((template) => (
              <article className="template-card" key={template.id}>
                <header>
                  <Badge tone={template.is_builtin ? "info" : "success"}>{template.is_builtin ? "内置" : "自定义"}</Badge>
                  <h3>{template.title}</h3>
                </header>
                <div className="template-section">
                  <span>起点</span>
                  <p>{template.origin}</p>
                </div>
                <div className="template-section">
                  <span>目标</span>
                  <p>{template.goal}</p>
                </div>
                <div className="template-section">
                  <span>提示</span>
                  <p>{template.hints?.length ? `${template.hints.length} 条提示` : "无初始提示"}</p>
                </div>
                <div className="card-actions">
                  <button className="primary-outline compact" type="button" onClick={() => setProjectTemplate(template)}>
                    <Plus size={16} />
                    使用模板
                  </button>
                  {!template.is_builtin && (
                    <button className="ghost-button compact danger" type="button" onClick={() => deleteTemplate(template)}>
                      <Trash2 size={16} />
                      删除
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
      {newTemplate && (
        <TemplateEditor
          onClose={() => setNewTemplate(false)}
          onSave={async (payload) => {
            await runAction("模板已创建", () => apiRequest("/api/templates", { method: "POST", body: payload }));
            setNewTemplate(false);
            await load();
          }}
        />
      )}
      {projectTemplate && (
        <NewProjectModal
          initial={projectTemplate}
          runAction={runAction}
          onClose={() => setProjectTemplate(null)}
          onCreated={(projectId) => {
            setProjectTemplate(null);
            go(`#/projects/${projectId}`);
          }}
        />
      )}
    </>
  );
}

function TemplateEditor({ onClose, onSave }) {
  const [form, setForm] = useState({ title: "", origin: "", goal: "", hints: "" });
  const [saving, setSaving] = useState(false);
  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      await onSave({
        title: form.title,
        origin: form.origin,
        goal: form.goal,
        hints: parseHintLines(form.hints, HUMAN_WORKER),
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="新建模板" onClose={onClose} wide>
      <form className="stack-form modal-body" onSubmit={submit}>
        <label>
          <span>模板名称</span>
          <input value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} required />
        </label>
        <div className="two-col">
          <label>
            <span>起点</span>
            <textarea rows={6} value={form.origin} onChange={(event) => setForm({ ...form, origin: event.target.value })} required />
          </label>
          <label>
            <span>目标</span>
            <textarea rows={6} value={form.goal} onChange={(event) => setForm({ ...form, goal: event.target.value })} required />
          </label>
        </div>
        <label>
          <span>提示</span>
          <textarea rows={4} value={form.hints} onChange={(event) => setForm({ ...form, hints: event.target.value })} />
        </label>
        <div className="modal-footer">
          <button className="ghost-button" type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary-button compact" type="submit" disabled={saving}>
            {saving ? <Loader2 className="spin" size={18} /> : <Save size={18} />}
            保存模板
          </button>
        </div>
      </form>
    </Modal>
  );
}

function PasswordModal({ onClose, runAction }) {
  const [form, setForm] = useState({ current_password: "", new_password: "" });
  const [saving, setSaving] = useState(false);
  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      await runAction("密码已修改", () =>
        apiRequest("/api/auth/password", {
          method: "PUT",
          body: {
            current_password: form.current_password,
            new_password: form.new_password,
          },
        }),
      );
      onClose();
    } finally {
      setSaving(false);
    }
  };
  return (
    <Modal title="修改密码" subtitle="新密码需要包含大小写字母、数字和特殊字符。" onClose={onClose}>
      <form className="stack-form modal-body" onSubmit={submit}>
        <label>
          <span>当前密码</span>
          <input
            type="password"
            value={form.current_password}
            onChange={(event) => setForm({ ...form, current_password: event.target.value })}
            required
          />
        </label>
        <label>
          <span>新密码</span>
          <input
            type="password"
            value={form.new_password}
            onChange={(event) => setForm({ ...form, new_password: event.target.value })}
            required
          />
        </label>
        <div className="modal-footer">
          <button className="ghost-button" type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary-button compact" type="submit" disabled={saving}>
            {saving ? <Loader2 className="spin" size={18} /> : <Save size={18} />}
            保存
          </button>
        </div>
      </form>
    </Modal>
  );
}

function SettingsModal({ onClose, runAction }) {
  const [settings, setSettings] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiRequest("/settings").then(setSettings).catch(() => setSettings({ intent_timeout: 60, reason_timeout: 60 }));
  }, []);

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      await runAction("设置已保存", () => apiRequest("/settings", { method: "PUT", body: settings }));
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="调度设置" subtitle="仅调整超时参数，不改变项目和 Worker 数据。" onClose={onClose}>
      {!settings ? (
        <EmptyState icon={Loader2} title="正在读取设置" />
      ) : (
        <form className="stack-form modal-body" onSubmit={submit}>
          <label>
            <span>意图超时（秒）</span>
            <input
              type="number"
              min="5"
              value={settings.intent_timeout}
              onChange={(event) => setSettings({ ...settings, intent_timeout: Number(event.target.value) })}
            />
          </label>
          <label>
            <span>Reason 超时（秒）</span>
            <input
              type="number"
              min="5"
              value={settings.reason_timeout}
              onChange={(event) => setSettings({ ...settings, reason_timeout: Number(event.target.value) })}
            />
          </label>
          <div className="modal-footer">
            <button className="ghost-button" type="button" onClick={onClose}>
              取消
            </button>
            <button className="primary-button compact" type="submit" disabled={saving}>
              {saving ? <Loader2 className="spin" size={18} /> : <Save size={18} />}
              保存
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}
