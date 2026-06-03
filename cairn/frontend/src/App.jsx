import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  Bell,
  Bot,
  CheckCheck,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock,
  Download,
  Eye,
  EyeOff,
  FileText,
  Folder,
  History,
  Home,
  Inbox,
  KeyRound,
  Loader2,
  Lock,
  LogOut,
  Monitor,
  Moon,
  Network,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Save,
  Search,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Square,
  Sun,
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
      window.location.hash = "#/dashboard";
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
  const [confirmState, setConfirmState] = useState(null);
  const [theme, setTheme] = useState(() => {
    if (typeof window === "undefined") return "light";
    return window.localStorage.getItem("rabbit-theme") === "dark" ? "dark" : "light";
  });
  const runAction = useAsyncAction(setToast);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    window.localStorage.setItem("rabbit-theme", theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => (prev === "dark" ? "light" : "dark"));
  }, []);

  // Promise-based confirm: pages call confirmAction(...) and await a boolean.
  const confirmAction = useCallback(
    (options) =>
      new Promise((resolve) => {
        setConfirmState({ options: options || {}, resolve });
      }),
    [],
  );

  const resolveConfirm = useCallback(
    (result) => {
      setConfirmState((current) => {
        if (current) current.resolve(result);
        return null;
      });
    },
    [],
  );

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
        theme={theme}
        onToggleTheme={toggleTheme}
        onLogout={logout}
        onPassword={() => setPasswordOpen(true)}
        onSettings={() => setSettingsOpen(true)}
        setToast={setToast}
      />
      <main className="app-main">
        {route.page === "project" ? (
          <ProjectWorkspace
            projectId={route.projectId}
            runAction={runAction}
            setToast={setToast}
            confirmAction={confirmAction}
          />
        ) : route.page === "vulnerabilities" ? (
          <VulnerabilitiesPage route={route} runAction={runAction} setToast={setToast} confirmAction={confirmAction} />
        ) : route.page === "workers" ? (
          <WorkersPage runAction={runAction} setToast={setToast} confirmAction={confirmAction} />
        ) : route.page === "templates" ? (
          <TemplatesPage runAction={runAction} setToast={setToast} confirmAction={confirmAction} />
        ) : route.page === "audit" ? (
          <AuditPage setToast={setToast} />
        ) : route.page === "projects" ? (
          <ProjectsPage runAction={runAction} setToast={setToast} confirmAction={confirmAction} />
        ) : (
          <DashboardPage runAction={runAction} setToast={setToast} />
        )}
      </main>
      {passwordOpen && <PasswordModal onClose={() => setPasswordOpen(false)} runAction={runAction} />}
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} runAction={runAction} />}
      {confirmState && (
        <ConfirmModal
          {...confirmState.options}
          onConfirm={() => resolveConfirm(true)}
          onCancel={() => resolveConfirm(false)}
        />
      )}
      {toast && <Toast toast={toast} onClose={() => setToast(null)} />}
    </div>
  );
}

function TopNav({ route, user, theme, onToggleTheme, onLogout, onPassword, onSettings, setToast }) {
  const mainNav = [
    ["projects", "项目", Folder],
    ["vulnerabilities", "漏洞报告", AlertTriangle],
    ["workers", "工作节点", Monitor],
    ["templates", "模板", FileText],
  ];
  const activeNav = mainNav.find(([key]) => route.page === key || (key === "projects" && route.page === "project"));
  const sectionLabel =
    route.page === "dashboard"
      ? "仪表盘"
      : route.page === "audit"
        ? "审计日志"
        : activeNav?.[1] || APP_NAME;
  const reportSubnav = [
    ["overview", "报告总览"],
    ["critical", "严重漏洞"],
    ["high", "高危漏洞"],
    ["medium", "中危漏洞"],
    ["low", "低危漏洞"],
    ["confirmed", "已确认漏洞"],
    ["ignored", "已忽略漏洞"],
    ["export-records", "导出记录"],
  ];
  const activeView = route.page === "vulnerabilities" ? route.view || "overview" : null;
  return (
    <>
      <header className="top-utility">
        <button className="brand" type="button" onClick={() => go("#/dashboard")}>
          <span className="brand-mark">
            <img src="/static/rabbit-icon.png" alt="Rabbit" />
          </span>
          <span>{APP_NAME}</span>
        </button>
        <div className="top-section-label">{sectionLabel}</div>
        <GlobalSearch setToast={setToast} />
        <div className="nav-actions">
          <NotificationBell setToast={setToast} />
          <button
            className="icon-button"
            type="button"
            onClick={onToggleTheme}
            aria-label={theme === "dark" ? "切换为浅色模式" : "切换为深色模式"}
            title={theme === "dark" ? "浅色模式" : "深色模式"}
          >
            {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </button>
          <button className="user-chip" type="button" onClick={onPassword} title="修改密码">
            <span className="user-avatar">{(user.username || "U").slice(0, 1).toUpperCase()}</span>
            <span className="user-name">{user.username}</span>
          </button>
          <button className="icon-button" type="button" onClick={onLogout} aria-label="退出登录" title="退出登录">
            <LogOut size={16} />
          </button>
        </div>
      </header>
      <aside className="top-nav">
        <nav className="nav-tabs" aria-label="主导航">
          <button
            className={cn("nav-tab", route.page === "dashboard" && "active")}
            type="button"
            onClick={() => go("#/dashboard")}
          >
            <Home size={17} />
            首页
          </button>
          {mainNav.map(([key, label, Icon]) => {
            const active = route.page === key || (key === "projects" && route.page === "project");
            return (
              <div key={key} className={cn("nav-group", active && "active")}>
                <button
                  className={cn("nav-tab", active && "active")}
                  type="button"
                  onClick={() => go(key === "projects" ? "#/projects" : `#/${key}`)}
                >
                  <Icon size={17} />
                  {label}
                  {key === "vulnerabilities" && <ChevronDown className="nav-caret" size={14} />}
                </button>
                {key === "vulnerabilities" && active && (
                  <div className="sub-nav">
                    {reportSubnav.map(([view, label]) => (
                      <button
                        key={view}
                        className={cn(activeView === view && "active")}
                        type="button"
                        onClick={() => go(view === "overview" ? "#/vulnerabilities" : `#/vulnerabilities/${view}`)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
          <button
            className={cn("nav-tab", route.page === "audit" && "active")}
            type="button"
            onClick={() => go("#/audit")}
          >
            <History size={17} />
            审计日志
          </button>
          <button className="nav-tab" type="button" onClick={onSettings}>
            <Settings size={17} />
            系统设置
          </button>
        </nav>
        <div className="sidebar-foot">
          <FileText size={15} />
          <span>默认工作区</span>
        </div>
      </aside>
    </>
  );
}

function GlobalSearch({ setToast }) {
  const [term, setTerm] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState({ vulnerabilities: [], projects: [] });
  const wrapRef = useRef(null);
  const cacheRef = useRef(null);

  // Lazily fetch the full vulnerability + project lists once, then filter
  // client-side as the user types. Kept lightweight: one fetch per session,
  // refreshed only when the dropdown is opened after being closed a while.
  const ensureData = useCallback(async () => {
    if (cacheRef.current && Date.now() - cacheRef.current.at < 60000) return cacheRef.current.payload;
    const [vulns, projects] = await Promise.all([
      apiRequest("/api/vulnerabilities").catch(() => []),
      apiRequest("/projects").catch(() => []),
    ]);
    const payload = {
      vulnerabilities: Array.isArray(vulns) ? vulns : [],
      projects: Array.isArray(projects) ? projects : [],
    };
    cacheRef.current = { at: Date.now(), payload };
    return payload;
  }, []);

  useEffect(() => {
    const value = term.trim().toLowerCase();
    if (!value) {
      setData({ vulnerabilities: [], projects: [] });
      setOpen(false);
      return undefined;
    }
    let cancelled = false;
    setLoading(true);
    const timer = window.setTimeout(async () => {
      try {
        const payload = await ensureData();
        if (cancelled) return;
        const vulnerabilities = payload.vulnerabilities
          .filter((item) =>
            [item.title, item.fact_id, item.project_name, item.project_id, item.description]
              .filter(Boolean)
              .some((field) => String(field).toLowerCase().includes(value)),
          )
          .slice(0, 6);
        const projects = payload.projects
          .filter((item) =>
            [item.title, item.id, item.goal, item.origin]
              .filter(Boolean)
              .some((field) => String(field).toLowerCase().includes(value)),
          )
          .slice(0, 5);
        setData({ vulnerabilities, projects });
        setOpen(true);
      } catch (error) {
        if (!cancelled && setToast) setToast({ type: "danger", message: error.message || "搜索失败" });
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 220);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [term, ensureData, setToast]);

  useEffect(() => {
    const onClick = (event) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const reset = () => {
    setTerm("");
    setOpen(false);
  };

  const goVuln = (vuln) => {
    reset();
    go(`#/vulnerabilities?q=${encodeURIComponent(vuln.title || vuln.fact_id || "")}`);
  };

  const goProject = (project) => {
    reset();
    go(`#/projects/${project.id}`);
  };

  const hasResults = data.vulnerabilities.length > 0 || data.projects.length > 0;

  return (
    <div className="global-search-wrap" ref={wrapRef}>
      <label className="global-search">
        <Search size={15} />
        <input
          value={term}
          placeholder="搜索漏洞标题、编号、项目、标签..."
          aria-label="全局搜索"
          onChange={(event) => setTerm(event.target.value)}
          onFocus={() => term.trim() && setOpen(true)}
          onKeyDown={(event) => {
            if (event.key === "Escape") reset();
          }}
        />
        {term && (
          <button type="button" className="global-search-clear" onClick={reset} aria-label="清空搜索">
            <X size={14} />
          </button>
        )}
      </label>
      {open && term.trim() && (
        <div className="search-dropdown">
          {loading && !hasResults ? (
            <div className="search-empty">
              <Loader2 className="spin" size={16} />
              <span>搜索中...</span>
            </div>
          ) : !hasResults ? (
            <div className="search-empty">
              <Search size={16} />
              <span>未找到匹配结果</span>
            </div>
          ) : (
            <>
              {data.vulnerabilities.length > 0 && (
                <section className="search-section">
                  <header>漏洞</header>
                  {data.vulnerabilities.map((vuln) => {
                    const meta = SEVERITY_META[vuln.severity] || SEVERITY_META.low;
                    return (
                      <button key={`v-${vuln.id}`} type="button" className="search-item" onClick={() => goVuln(vuln)}>
                        <span className={cn("search-dot", vuln.severity)} />
                        <span className="search-item-main">
                          <strong>{clampText(vuln.title, 42)}</strong>
                          <small>
                            {meta.label} · {vuln.fact_id} · {vuln.project_name}
                          </small>
                        </span>
                      </button>
                    );
                  })}
                </section>
              )}
              {data.projects.length > 0 && (
                <section className="search-section">
                  <header>项目</header>
                  {data.projects.map((project) => (
                    <button key={`p-${project.id}`} type="button" className="search-item" onClick={() => goProject(project)}>
                      <span className="search-icon">
                        <Folder size={15} />
                      </span>
                      <span className="search-item-main">
                        <strong>{clampText(project.title, 42)}</strong>
                        <small>{project.id}</small>
                      </span>
                    </button>
                  ))}
                </section>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function NotificationBell({ setToast }) {
  const [count, setCount] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const wrapRef = useRef(null);

  const loadCount = useCallback(async () => {
    try {
      const res = await apiRequest("/api/notifications/unread-count");
      setCount(Number(res?.count) || 0);
    } catch {
      // Silent: the badge simply stays at its last value.
    }
  }, []);

  useEffect(() => {
    loadCount();
    const timer = window.setInterval(loadCount, 10000);
    return () => window.clearInterval(timer);
  }, [loadCount]);

  useEffect(() => {
    const onClick = (event) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const list = await apiRequest("/api/notifications?limit=50");
      const items = Array.isArray(list) ? list : [];
      setItems(items);
      // Mark the just-shown unread notifications as read, then refresh the badge.
      const unreadIds = items.filter((item) => !item.read).map((item) => item.id);
      if (unreadIds.length) {
        await apiRequest("/api/notifications/read", { method: "POST", body: { ids: unreadIds } }).catch(() => {});
        setItems((prev) => prev.map((item) => ({ ...item, read: true })));
      }
      await loadCount();
    } catch (error) {
      if (setToast) setToast({ type: "danger", message: error.message || "通知加载失败" });
    } finally {
      setLoading(false);
    }
  }, [setToast, loadCount]);

  const togglePanel = async () => {
    const next = !open;
    setOpen(next);
    if (next) await loadList();
  };

  const markAllRead = async () => {
    try {
      const res = await apiRequest("/api/notifications/read", { method: "POST" });
      setCount(Number(res?.count) || 0);
      setItems((prev) => prev.map((item) => ({ ...item, read: true })));
    } catch (error) {
      if (setToast) setToast({ type: "danger", message: error.message || "操作失败" });
    }
  };

  const clearAll = async () => {
    try {
      await apiRequest("/api/notifications", { method: "DELETE" });
      setItems([]);
      setCount(0);
    } catch (error) {
      if (setToast) setToast({ type: "danger", message: error.message || "操作失败" });
    }
  };

  const openNotification = (item) => {
    if (item.link) {
      setOpen(false);
      go(item.link);
    }
  };

  return (
    <div className="notification-wrap" ref={wrapRef}>
      <button
        className="icon-button notification-button"
        type="button"
        onClick={togglePanel}
        aria-label="通知"
        title="通知"
      >
        <Bell size={16} />
        {count > 0 && <span className="notification-dot">{count > 99 ? "99+" : count}</span>}
      </button>
      {open && (
        <div className="notification-panel">
          <header className="notification-panel-head">
            <strong>通知</strong>
            <div className="notification-actions">
              <button type="button" onClick={markAllRead} disabled={!items.length}>
                <CheckCheck size={14} />
                全部已读
              </button>
              <button type="button" className="danger" onClick={clearAll} disabled={!items.length}>
                <Trash2 size={14} />
                清空
              </button>
            </div>
          </header>
          <div className="notification-list">
            {loading ? (
              <div className="search-empty">
                <Loader2 className="spin" size={16} />
                <span>加载中...</span>
              </div>
            ) : items.length === 0 ? (
              <div className="search-empty">
                <Inbox size={18} />
                <span>暂无通知</span>
              </div>
            ) : (
              items.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={cn("notification-item", !item.read && "unread", item.link && "linked")}
                  onClick={() => openNotification(item)}
                >
                  <span className={cn("notification-level", item.level || "info")} />
                  <span className="notification-item-main">
                    <strong>{item.title}</strong>
                    {item.body && <p>{clampText(item.body, 90)}</p>}
                    <small>{formatTime(item.created_at)}</small>
                  </span>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ConfirmModal({ title = "确认操作", message, tone = "default", confirmLabel = "确认", cancelLabel = "取消", onConfirm, onCancel }) {
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal-card confirm-card" role="dialog" aria-modal="true">
        <div className="confirm-body">
          <span className={cn("confirm-icon", tone)}>
            {tone === "danger" ? <AlertTriangle size={22} /> : <AlertCircle size={22} />}
          </span>
          <div className="confirm-text">
            <h2>{title}</h2>
            {message && <p>{message}</p>}
          </div>
        </div>
        <div className="modal-footer">
          <button className="ghost-button" type="button" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            className={cn("primary-button compact", tone === "danger" && "danger")}
            type="button"
            onClick={onConfirm}
            autoFocus
          >
            {confirmLabel}
          </button>
        </div>
      </section>
    </div>
  );
}

function AuditPage({ setToast }) {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    try {
      const list = await apiRequest("/api/audit?limit=100");
      setEntries(Array.isArray(list) ? list : []);
    } catch (error) {
      if (!silent) setToast({ type: "danger", message: error.message || "审计日志加载失败" });
    } finally {
      if (!silent) setLoading(false);
    }
  }, [setToast]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => load({ silent: true }), 10000);
    return () => window.clearInterval(timer);
  }, [load]);

  const actionTone = (action) => {
    const value = String(action || "");
    if (/delete|remove|clear|disable/i.test(value)) return "danger";
    if (/create|add|export|complete|enable/i.test(value)) return "success";
    if (/update|status|reopen|edit|patch/i.test(value)) return "info";
    return "muted";
  };

  return (
    <>
      <PageHeader
        icon={History}
        title="审计日志"
        subtitle="记录关键操作的时间、对象与详情"
        actions={
          <button className="ghost-button" type="button" onClick={() => load()}>
            <RefreshCw size={18} />
            刷新
          </button>
        }
      />
      <section className="content-wrap vulnerability-report-page">
        <article className="vuln-table-card audit-card">
          <header className="vuln-table-title">
            <div>
              <h2>操作记录</h2>
              <p>最近 100 条关键操作，每 10 秒自动刷新</p>
            </div>
            <span className="status-pill">
              <span className="dot success" />
              <span>{entries.length} 条</span>
            </span>
          </header>
          <div className="audit-head">
            <span>时间</span>
            <span>操作</span>
            <span>摘要</span>
            <span>对象</span>
            <span>操作者</span>
          </div>
          <div className="audit-body">
            {loading ? (
              <EmptyState icon={Loader2} title="正在加载审计日志" />
            ) : entries.length === 0 ? (
              <EmptyState icon={History} title="暂无审计记录" subtitle="关键操作发生后会在这里留痕。" />
            ) : (
              entries.map((entry) => (
                <div className="audit-row" key={entry.id}>
                  <time>{formatTime(entry.created_at)}</time>
                  <span>
                    <Badge tone={actionTone(entry.action)}>{entry.action}</Badge>
                  </span>
                  <div className="audit-summary-cell">
                    <strong title={entry.summary}>{entry.summary}</strong>
                    {entry.detail && <span title={entry.detail}>{clampText(entry.detail, 80)}</span>}
                  </div>
                  <code title={`${entry.target_type || ""} ${entry.target_id || ""}`.trim()}>
                    {entry.target_type ? `${entry.target_type}${entry.target_id ? `:${entry.target_id}` : ""}` : "-"}
                  </code>
                  <span className="audit-actor">{entry.actor || "-"}</span>
                </div>
              ))
            )}
          </div>
        </article>
      </section>
    </>
  );
}

function AuthPage({ onAuthed, setToast }) {
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({ username: "", password: "", confirm_password: "", captcha_answer: "" });
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
    if (mode === "register" && form.password !== form.confirm_password) {
      setToast({ type: "warning", message: "两次输入的密码不一致" });
      return;
    }
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
      <section className="auth-card auth-split-card">
        <aside className="auth-showcase" aria-hidden="true">
          <div className="auth-brand">
            <span className="auth-brand-logo">
              <img src="/static/rabbit-icon.png" alt="" />
            </span>
            <strong>{APP_NAME}</strong>
          </div>
          <div className="auth-hero-visual">
            <span className="cube cube-a" />
            <span className="cube cube-b" />
            <span className="cube cube-c" />
            <span className="orbit-line orbit-a" />
            <span className="orbit-line orbit-b" />
            <img src="/static/rabbit-icon.png" alt="" />
            <span className="shield-badge">
              <CheckCircle2 size={34} />
            </span>
          </div>
          <div className="auth-copy">
            <h2>
              持续安全探索
              <br />
              让攻击<span>无处遁形</span>
            </h2>
            <p>Rabbit 帮助安全团队自动化资产发现、漏洞验证和攻击面管理，让安全探索更清晰。</p>
          </div>
          <div className="auth-capabilities">
            <span>
              <ShieldAlert size={16} />
              资产发现
            </span>
            <span>
              <CheckCircle2 size={16} />
              漏洞验证
            </span>
            <span>
              <Network size={16} />
              攻击面管理
            </span>
          </div>
        </aside>

        <main className="auth-form-panel">
          {mode === "register" && (
            <button className="auth-back" type="button" onClick={() => setMode("login")}>
              <ArrowLeft size={16} />
              返回登录
            </button>
          )}
          <div className="auth-title align-left">
            <h1>{mode === "login" ? "欢迎回来" : "创建账户"} <span>👋</span></h1>
            <p>{mode === "login" ? "登录以继续安全探索工作流" : "加入 Rabbit 安全探索平台"}</p>
          </div>
          {mode === "login" && (
            <div className="segmented auth-login-tabs">
              <button className="active" type="button">
                账号登录
              </button>
              <button
                type="button"
                onClick={() => setToast({ type: "info", message: "手机号验证码登录暂未接入，先使用账号登录。" })}
              >
                手机号登录
              </button>
            </div>
          )}
          <form className="stack-form auth-stack-form" onSubmit={submit}>
            <label>
              <span>用户名</span>
              <input
                autoComplete="username"
                value={form.username}
                onChange={(event) => setForm({ ...form, username: event.target.value })}
                placeholder="请输入用户名"
              />
            </label>
            <label>
              <span>密码</span>
              <input
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                type="password"
                value={form.password}
                onChange={(event) => setForm({ ...form, password: event.target.value })}
                placeholder={mode === "login" ? "请输入密码" : "请设置密码"}
              />
            </label>
            {mode === "register" && (
              <label>
                <span>确认密码</span>
                <input
                  autoComplete="new-password"
                  type="password"
                  value={form.confirm_password}
                  onChange={(event) => setForm({ ...form, confirm_password: event.target.value })}
                  placeholder="请再次输入密码"
                />
              </label>
            )}
            <label>
              <span>验证码</span>
              <div className="captcha-row">
                <input
                  value={form.captcha_answer}
                  onChange={(event) => setForm({ ...form, captcha_answer: event.target.value })}
                  placeholder="请输入计算结果"
                />
                <button className="captcha-chip" type="button" onClick={loadCaptcha}>
                  {captcha?.question || "刷新"}
                  <RefreshCw size={15} />
                </button>
              </div>
            </label>
            <button className="primary-button auth-submit" type="submit" disabled={loading}>
              {loading ? <Loader2 className="spin" size={18} /> : <Lock size={18} />}
              {mode === "login" ? "登录" : "注册账号"}
            </button>
          </form>
          <p className="auth-switch">
            {mode === "login" ? "还没有账号？" : "已有账号？"}
            <button type="button" onClick={() => setMode(mode === "login" ? "register" : "login")}>
              {mode === "login" ? "立即注册" : "返回登录"}
            </button>
          </p>
        </main>
      </section>
    </div>
  );
}

function PageHeader({ icon: Icon, title, subtitle, actions, compact = false }) {
  return (
    <section className={cn("page-header", compact && "compact-report-header")}>
      <div className="page-title">
        {Icon && (
          <span className="page-icon">
            <Icon size={28} />
          </span>
        )}
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

function DashboardPage({ runAction, setToast }) {
  const [vulnerabilities, setVulnerabilities] = useState([]);
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [newOpen, setNewOpen] = useState(false);

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    try {
      const [vulnList, projectList] = await Promise.all([
        apiRequest("/api/vulnerabilities"),
        apiRequest("/projects"),
      ]);
      setVulnerabilities(Array.isArray(vulnList) ? vulnList : []);
      setProjects(Array.isArray(projectList) ? projectList : []);
      setLastUpdated(new Date());
    } catch (error) {
      if (!silent) setToast({ type: "danger", message: error.message || "仪表盘数据加载失败" });
    } finally {
      if (!silent) setLoading(false);
    }
  }, [setToast]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => load({ silent: true }), 8000);
    return () => window.clearInterval(timer);
  }, [load]);

  const severitySummary = useMemo(() => summarizeSeverity(vulnerabilities), [vulnerabilities]);
  const statusDistribution = useMemo(() => buildStatusDistribution(vulnerabilities), [vulnerabilities]);
  const trendData = useMemo(() => buildVulnerabilityTrend(vulnerabilities), [vulnerabilities]);
  const recentActivity = useMemo(
    () =>
      [...vulnerabilities]
        .sort((a, b) => String(b.discovered_at || "").localeCompare(String(a.discovered_at || "")))
        .slice(0, 6),
    [vulnerabilities],
  );

  const projectCounts = useMemo(() => {
    const total = projects.length;
    const active = projects.filter((project) => project.status === "active").length;
    const completed = projects.filter((project) => project.status === "completed").length;
    return { total, active, completed };
  }, [projects]);

  const quickActions = [
    { key: "new", label: "新建项目", desc: "定义起点、目标和提示", icon: Plus, onClick: () => setNewOpen(true) },
    { key: "vulns", label: "漏洞报告", desc: "查看与管理漏洞", icon: AlertTriangle, onClick: () => go("#/vulnerabilities") },
    { key: "workers", label: "工作节点", desc: "状态与模型配置", icon: Monitor, onClick: () => go("#/workers") },
    { key: "templates", label: "模板", desc: "复用项目模板", icon: FileText, onClick: () => go("#/templates") },
  ];

  return (
    <>
      <PageHeader
        icon={Home}
        title="仪表盘"
        subtitle="安全探索全局概览：漏洞态势、趋势与最近活动"
        actions={
          <>
            <div className="status-pill">
              <span className="dot success" />
              <span>{lastUpdated ? `更新于 ${lastUpdated.toLocaleTimeString("zh-CN")}` : "待更新"}</span>
            </div>
            <button className="ghost-button" type="button" onClick={() => load()}>
              <RefreshCw size={18} />
              刷新
            </button>
          </>
        }
      />
      <section className="content-wrap vulnerability-report-page">
        {loading ? (
          <EmptyState icon={Loader2} title="正在加载仪表盘" />
        ) : (
          <>
            <div className="metric-grid severity">
              {["critical", "high", "medium", "low"].map((level) => (
                <MetricCard
                  key={level}
                  label={`${SEVERITY_META[level].label}漏洞`}
                  value={severitySummary[level] || 0}
                  tone={level}
                />
              ))}
              <MetricCard label="已确认" value={statusDistribution.confirmed || 0} tone="success" />
            </div>
            <div className="metric-grid">
              <MetricCard label="全部项目" value={projectCounts.total} tone="info" />
              <MetricCard label="运行中项目" value={projectCounts.active} tone="success" />
              <MetricCard label="已完成项目" value={projectCounts.completed} tone="muted" />
              <MetricCard label="漏洞总数" value={statusDistribution.total || 0} tone="info" />
            </div>
            <div className="dashboard-grid">
              <VulnerabilityTrend data={trendData} />
              <VulnerabilityStatusDistribution data={statusDistribution} />
            </div>
            <section className="vuln-analysis-card dashboard-activity-card">
              <header>
                <h3>最近活动</h3>
                <span>最新发现</span>
              </header>
              {recentActivity.length === 0 ? (
                <p className="analysis-empty">暂无漏洞活动</p>
              ) : (
                <div className="recent-vuln-list">
                  {recentActivity.map((item) => {
                    const meta = SEVERITY_META[item.severity] || SEVERITY_META.low;
                    return (
                      <article key={`activity-${item.id}`}>
                        <span className={cn("activity-dot", item.severity)} />
                        <div>
                          <strong title={item.title}>{clampText(item.title, 48)}</strong>
                          <p>
                            {meta.label} · {item.project_name} · {formatTime(item.discovered_at)}
                          </p>
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </section>
            <section className="vuln-analysis-card dashboard-quick-card">
              <header>
                <h3>快捷操作</h3>
                <span>常用入口</span>
              </header>
              <div className="quick-action-grid">
                {quickActions.map((action) => (
                  <button
                    key={action.key}
                    className={cn("quick-action", action.key)}
                    type="button"
                    onClick={action.onClick}
                  >
                    <span className="quick-action-chip">
                      <action.icon size={20} />
                    </span>
                    <span className="quick-action-text">
                      <strong>{action.label}</strong>
                      <small>{action.desc}</small>
                    </span>
                    <ChevronRight size={18} />
                  </button>
                ))}
              </div>
            </section>
          </>
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

function ProjectsPage({ runAction, setToast, confirmAction }) {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newOpen, setNewOpen] = useState(false);
  const [reopenTarget, setReopenTarget] = useState(null);

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
    const ok = await confirmAction({
      title: "删除项目",
      message: `确认删除项目「${project.title}」？此操作不可恢复。`,
      tone: "danger",
      confirmLabel: "删除",
    });
    if (!ok) return;
    await runAction("项目已删除", () => apiRequest(`/projects/${project.id}`, { method: "DELETE" }));
    await load();
  };

  const updateStatus = async (project, status) => {
    await runAction("项目状态已更新", () =>
      apiRequest(`/projects/${project.id}/status`, { method: "PUT", body: { status } }),
    );
    await load();
  };

  const reopenProject = (project) => {
    setReopenTarget(project);
  };

  const submitReopen = async (description) => {
    const project = reopenTarget;
    if (!project) return;
    await runAction("项目已重新打开", () =>
      apiRequest(`/projects/${project.id}/reopen`, {
        method: "POST",
        body: { description, creator: HUMAN_WORKER },
      }),
    );
    setReopenTarget(null);
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
      {reopenTarget && (
        <TextActionModal
          title={`重新打开「${reopenTarget.title}」`}
          label="重新打开原因"
          placeholder="补充验证或重新探索"
          defaultValue="补充验证或重新探索"
          submitLabel="重新打开"
          onClose={() => setReopenTarget(null)}
          onSubmit={submitReopen}
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

function ProjectWorkspace({ projectId, runAction, setToast, confirmAction }) {
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

  const updateTitle = () => {
    setModal("title");
  };

  const submitTitle = async (title) => {
    if (!title || title === project.title) {
      setModal(null);
      return;
    }
    await runAction("项目名称已更新", () =>
      apiRequest(`/projects/${project.id}/title`, { method: "PUT", body: { title } }),
    );
    setModal(null);
    await load();
  };

  const updateStatus = async (status) => {
    await runAction("项目状态已更新", () =>
      apiRequest(`/projects/${project.id}/status`, { method: "PUT", body: { status } }),
    );
    await load();
  };

  const deleteProject = async () => {
    const ok = await confirmAction({
      title: "删除项目",
      message: `确认删除项目「${project.title}」？此操作不可恢复。`,
      tone: "danger",
      confirmLabel: "删除",
    });
    if (!ok) return;
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
      {modal === "title" && (
        <TextActionModal
          title="重命名项目"
          label="项目名称"
          multiline={false}
          defaultValue={project.title}
          submitLabel="保存名称"
          onClose={() => setModal(null)}
          onSubmit={submitTitle}
        />
      )}
    </>
  );
}

function GraphCanvas({ detail, selected, onSelect, layout }) {
  const containerRef = useRef(null);
  const cyRef = useRef(null);

  const elements = useMemo(() => {
    const compactLabel = (value, length = 72) => clampText(String(value || ""), length);
    const nodes = detail.facts.map((fact) => ({
      data: {
        id: fact.id,
        label: fact.id === "origin" ? "起点" : fact.id === "goal" ? "目标" : `${fact.id}: ${compactLabel(fact.description)}`,
      },
      classes: cn("fact-node", fact.id),
    }));
    const intentNodes = detail.intents.map((intent) => ({
      data: {
        id: intent.id,
        label: `${intent.id}: ${compactLabel(intent.description)}`,
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
      minZoom: 0.35,
      maxZoom: 2.2,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "font-family": "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
            "font-size": 15,
            "font-weight": 620,
            color: "#fff",
            "text-wrap": "wrap",
            "text-max-width": 190,
            "text-valign": "center",
            "text-halign": "center",
            width: 196,
            height: 72,
            shape: "round-rectangle",
            "background-color": "#007aff",
            "background-opacity": 0.96,
            "border-width": 0,
            "shadow-blur": 16,
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
            height: 104,
            "background-color": "#34c759",
            "font-size": 26,
          },
        },
        {
          selector: ".goal",
          style: {
            width: 220,
            height: 104,
            "background-color": "#ff6b6b",
            opacity: 0.88,
            "font-size": 26,
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
            "font-size": 12,
            color: "#334155",
            "text-background-color": "#fff",
            "text-background-opacity": 0.9,
            "text-background-padding": 4,
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
        ? { name: "dagre", rankDir: "TB", nodeSep: 75, rankSep: 125, fit: true, padding: 44 }
        : { name: layout, fit: true, padding: 54 };
    cy.layout(options).run();
    window.setTimeout(() => {
      if (!cyRef.current) return;
      const currentZoom = cyRef.current.zoom();
      if (currentZoom < 0.72) {
        cyRef.current.zoom({ level: 0.72, renderedPosition: { x: cyRef.current.width() / 2, y: cyRef.current.height() / 2 } });
        cyRef.current.center();
      }
    }, 80);
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

function TextActionModal({
  title,
  label,
  onClose,
  onSubmit,
  defaultValue = "",
  multiline = true,
  placeholder,
  submitLabel = "保存",
}) {
  const [text, setText] = useState(defaultValue);
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
          {multiline ? (
            <textarea
              rows={6}
              value={text}
              placeholder={placeholder}
              onChange={(event) => setText(event.target.value)}
              autoFocus
              required
            />
          ) : (
            <input
              value={text}
              placeholder={placeholder}
              onChange={(event) => setText(event.target.value)}
              autoFocus
              required
            />
          )}
        </label>
        <div className="modal-footer">
          <button className="ghost-button" type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary-button compact" type="submit" disabled={saving}>
            {saving ? <Loader2 className="spin" size={18} /> : <Save size={18} />}
            {submitLabel}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function VulnerabilitiesPage({ route, runAction, setToast, confirmAction }) {
  const view = route?.view || "overview";
  const severityViews = { critical: "严重漏洞", high: "高危漏洞", medium: "中危漏洞", low: "低危漏洞" };
  const statusViews = { confirmed: "已确认漏洞", ignored: "已忽略漏洞" };
  const viewTitle =
    view === "export-records"
      ? "导出记录"
      : severityViews[view] || statusViews[view] || "报告总览";

  const [vulnerabilities, setVulnerabilities] = useState([]);
  const [projects, setProjects] = useState([]);
  const [filters, setFilters] = useState({ severity: "", project_id: "", status: "", search: route?.search || "", date_from: "", date_to: "" });
  const [expandedVulns, setExpandedVulns] = useState({});
  const [selectedIds, setSelectedIds] = useState([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  // The left sub-nav drives a severity or status filter via the URL view. The
  // in-page filter selects (project/search) still compose on top of it.
  const viewSeverity = severityViews[view] ? view : "";
  const viewStatus = statusViews[view] ? view : "";

  const query = useMemo(() => {
    const params = new URLSearchParams();
    const severity = viewSeverity || filters.severity;
    const status = viewStatus || filters.status;
    if (severity) params.set("severity", severity);
    if (filters.project_id) params.set("project_id", filters.project_id);
    if (status) params.set("status", status);
    const suffix = params.toString();
    return suffix ? `?${suffix}` : "";
  }, [filters.project_id, filters.severity, filters.status, viewSeverity, viewStatus]);

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    try {
      const [list, projectList] = await Promise.all([
        apiRequest(`/api/vulnerabilities${query}`),
        apiRequest("/projects"),
      ]);
      setVulnerabilities(list);
      setProjects(projectList);
    } catch (error) {
      if (!silent) setToast({ type: "danger", message: error.message || "漏洞报告加载失败" });
    } finally {
      if (!silent) setLoading(false);
    }
  }, [query, setToast]);

  useEffect(() => {
    load();
  }, [load]);

  // When the global search navigates here with a ?q= term, reflect it in the
  // in-page search filter even if this page is already mounted.
  useEffect(() => {
    if (route?.search !== undefined) {
      setFilters((prev) => (prev.search === route.search ? prev : { ...prev, search: route.search || "" }));
    }
  }, [route?.search]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      load({ silent: true });
    }, 8000);
    return () => window.clearInterval(timer);
  }, [load]);

  const refresh = async () => {
    await runAction("漏洞报告已刷新", () => apiRequest("/api/vulnerabilities/refresh", { method: "POST" }));
    await load();
  };

  const exportMd = async ({ selected = [], title = "vulnerabilities" }) => {
    if (!selected.length) {
      setToast({ type: "warning", message: "请先选择要导出的漏洞" });
      return;
    }
    const params = new URLSearchParams({ format: "md" });
    params.set("vulnerability_ids", selected.join(","));
    await runAction("MD 报告已生成", () => downloadFromApi(`/api/vulnerabilities/export?${params}`, `${title}.md`));
  };

  const updateVulnerabilityStatus = async (vuln, status) => {
    const label = status === "ignored" ? "漏洞已标记为忽略" : "漏洞已恢复为已确认";
    await runAction(label, () =>
      apiRequest(`/api/vulnerabilities/${encodeURIComponent(vuln.id)}/status`, {
        method: "PATCH",
        body: { status },
      }),
    );
    await load();
  };

  const visibleVulnerabilities = useMemo(() => {
    const term = filters.search.trim().toLowerCase();
    const from = filters.date_from;
    const to = filters.date_to;
    return vulnerabilities.filter((item) => {
      if (term) {
        const matched = [item.title, item.description, item.project_name, item.project_id, item.fact_id, item.source_worker]
          .filter(Boolean)
          .some((value) => String(value).toLowerCase().includes(term));
        if (!matched) return false;
      }
      const day = String(item.discovered_at || "").slice(0, 10);
      if (from && day && day < from) return false;
      if (to && day && day > to) return false;
      return true;
    });
  }, [filters.search, filters.date_from, filters.date_to, vulnerabilities]);

  const filteredProjectCount = useMemo(
    () => new Set(visibleVulnerabilities.map((item) => item.project_id)).size,
    [visibleVulnerabilities],
  );
  const filteredVulnCount = visibleVulnerabilities.length;
  const totalPages = Math.max(1, Math.ceil(filteredVulnCount / pageSize));
  const currentPage = Math.min(page, totalPages);
  const pagedVulnerabilities = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return visibleVulnerabilities.slice(start, start + pageSize);
  }, [visibleVulnerabilities, currentPage, pageSize]);
  const pageStart = filteredVulnCount === 0 ? 0 : (currentPage - 1) * pageSize + 1;
  const pageEnd = Math.min(currentPage * pageSize, filteredVulnCount);
  const pageNumbers = useMemo(() => buildPageNumbers(currentPage, totalPages), [currentPage, totalPages]);
  const visibleSummary = useMemo(() => summarizeSeverity(visibleVulnerabilities), [visibleVulnerabilities]);
  const statusDistribution = useMemo(() => buildStatusDistribution(visibleVulnerabilities), [visibleVulnerabilities]);
  const visibleIds = useMemo(() => visibleVulnerabilities.map((item) => item.id), [visibleVulnerabilities]);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.includes(id));
  const severityTop = useMemo(() => buildSeverityTop(visibleVulnerabilities), [visibleVulnerabilities]);
  const trendData = useMemo(() => buildVulnerabilityTrend(visibleVulnerabilities), [visibleVulnerabilities]);

  useEffect(() => {
    setSelectedIds((prev) => prev.filter((id) => visibleIds.includes(id)));
  }, [visibleIds]);

  // Reset to the first page whenever the result set or page size changes.
  useEffect(() => {
    setPage(1);
  }, [query, filters.search, filters.date_from, filters.date_to, pageSize]);

  // Keep the current page within bounds if the total shrinks.
  useEffect(() => {
    setPage((prev) => Math.min(prev, totalPages));
  }, [totalPages]);

  const toggleSelected = (id) => {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
  };

  const toggleVisibleSelected = () => {
    setSelectedIds(allVisibleSelected ? [] : visibleIds);
  };

  if (view === "export-records") {
    return <ExportRecordsView setToast={setToast} confirmAction={confirmAction} />;
  }

  return (
    <>
      <PageHeader
        compact
        title={`漏洞报告 / ${viewTitle}`}
        subtitle="查看和管理所有漏洞报告，跟踪漏洞处理进度和状态"
        actions={
          <button
            className="ghost-button report-export-button"
            type="button"
            disabled={!selectedIds.length}
            onClick={() => exportMd({ selected: selectedIds, title: "rabbit-vulnerabilities" })}
          >
            <Download size={18} />
            导出报告
          </button>
        }
      />
      <section className="content-wrap vulnerability-report-page">
        <div className="metric-grid severity">
          {["critical", "high", "medium", "low"].map((level) => (
            <MetricCard key={level} label={`${SEVERITY_META[level].label}漏洞`} value={visibleSummary[level] || 0} tone={level} />
          ))}
          <MetricCard label="已确认" value={statusDistribution.confirmed || 0} tone="success" />
        </div>
        <div className="report-filter-bar">
          <label className="filter-search">
            <Search size={15} />
            <input
              value={filters.search}
              onChange={(event) => setFilters({ ...filters, search: event.target.value })}
              placeholder="搜索漏洞标题、编号、组件、标签..."
            />
          </label>
          <label className="filter-field">
            <span>项目</span>
            <select value={filters.project_id} onChange={(event) => setFilters({ ...filters, project_id: event.target.value })}>
              <option value="">全部项目</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.title}
                </option>
              ))}
            </select>
          </label>
          <label className="filter-field">
            <span>严重程度</span>
            <select
              value={viewSeverity || filters.severity}
              disabled={!!viewSeverity}
              onChange={(event) => setFilters({ ...filters, severity: event.target.value })}
            >
              <option value="">全部</option>
              {Object.entries(SEVERITY_META).map(([key, meta]) => (
                <option key={key} value={key}>
                  {meta.label}
                </option>
              ))}
            </select>
          </label>
          <label className="filter-field">
            <span>状态</span>
            <select
              value={viewStatus || filters.status}
              disabled={!!viewStatus}
              onChange={(event) => setFilters({ ...filters, status: event.target.value })}
            >
              <option value="">全部状态</option>
              <option value="confirmed">已确认</option>
              <option value="ignored">已忽略</option>
            </select>
          </label>
          <label className="filter-field date-field">
            <span>发现时间</span>
            <div className="date-range-control">
              <input
                type="date"
                value={filters.date_from}
                max={filters.date_to || undefined}
                onChange={(event) => setFilters({ ...filters, date_from: event.target.value })}
              />
              <span>→</span>
              <input
                type="date"
                value={filters.date_to}
                min={filters.date_from || undefined}
                onChange={(event) => setFilters({ ...filters, date_to: event.target.value })}
              />
            </div>
          </label>
          <button
            className="ghost-button compact filter-reset"
            type="button"
            onClick={() => setFilters({ severity: "", project_id: "", status: "", search: "", date_from: "", date_to: "" })}
          >
            <X size={15} />
            重置
          </button>
        </div>
        {loading ? (
          <EmptyState icon={Loader2} title="正在分析漏洞报告" />
        ) : visibleVulnerabilities.length === 0 ? (
          <EmptyState icon={CheckCircle2} title="没有匹配的漏洞" subtitle="当前筛选范围内尚未发现漏洞。" />
        ) : (
          <div className="vuln-report-grid">
            <article className="vuln-table-card">
              <header className="vuln-table-title">
                <div>
                  <h2>漏洞列表</h2>
                  <p>点击单条漏洞查看证据、过程和证明数据包</p>
                </div>
                <button className="ghost-button compact" type="button" disabled={!visibleIds.length} onClick={toggleVisibleSelected}>
                  {allVisibleSelected ? "取消全选" : "全选当前"}
                </button>
              </header>
              <div className="vuln-table-head">
                <span />
                <span>漏洞名称</span>
                <span>所属项目</span>
                <span>严重度</span>
                <span>状态</span>
                <span>发现时间</span>
                <span>操作</span>
              </div>
              <div className="vuln-table-body">
                {pagedVulnerabilities.map((vuln) => (
                  <VulnerabilityItem
                    key={vuln.id}
                    vuln={vuln}
                    selected={selectedIds.includes(vuln.id)}
                    onSelect={() => toggleSelected(vuln.id)}
                    expanded={!!expandedVulns[vuln.id]}
                    onToggle={() => setExpandedVulns({ ...expandedVulns, [vuln.id]: !expandedVulns[vuln.id] })}
                    onExport={() => exportMd({ selected: [vuln.id], title: `${vuln.project_id}-${vuln.fact_id}` })}
                    onStatusChange={(status) => updateVulnerabilityStatus(vuln, status)}
                  />
                ))}
              </div>
              <footer className="vuln-table-footer">
                <span>
                  共 {filteredVulnCount} 条{filteredVulnCount > 0 ? ` · 第 ${pageStart}-${pageEnd} 条` : ""}
                </span>
                <label className="pagination-size">
                  <select value={pageSize} onChange={(event) => setPageSize(Number(event.target.value))}>
                    {[10, 20, 50, 100].map((size) => (
                      <option key={size} value={size}>
                        {size} 条/页
                      </option>
                    ))}
                  </select>
                </label>
                <div className="pagination">
                  <button
                    type="button"
                    aria-label="上一页"
                    disabled={currentPage <= 1}
                    onClick={() => setPage((prev) => Math.max(1, prev - 1))}
                  >
                    <ChevronRight size={14} />
                  </button>
                  {pageNumbers.map((item, index) =>
                    item === "..." ? (
                      <span key={`gap-${index}`}>...</span>
                    ) : (
                      <button
                        key={item}
                        className={cn(item === currentPage && "active")}
                        type="button"
                        onClick={() => setPage(item)}
                      >
                        {item}
                      </button>
                    ),
                  )}
                  <button
                    type="button"
                    aria-label="下一页"
                    disabled={currentPage >= totalPages}
                    onClick={() => setPage((prev) => Math.min(totalPages, prev + 1))}
                  >
                    <ChevronRight size={14} />
                  </button>
                </div>
              </footer>
            </article>
            <aside className="vuln-side-panels">
              <VulnerabilityTrend data={trendData} />
              <SeverityTopList items={severityTop} />
              <VulnerabilityStatusDistribution data={statusDistribution} />
            </aside>
          </div>
        )}
      </section>
    </>
  );
}

function ExportRecordsView({ setToast, confirmAction }) {
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    try {
      const list = await apiRequest("/api/vulnerabilities/export-records");
      setRecords(Array.isArray(list) ? list : []);
    } catch (error) {
      if (!silent) setToast({ type: "danger", message: error.message || "导出记录加载失败" });
    } finally {
      if (!silent) setLoading(false);
    }
  }, [setToast]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => load({ silent: true }), 8000);
    return () => window.clearInterval(timer);
  }, [load]);

  const formatLabels = { md: "Markdown", markdown: "Markdown", json: "JSON", csv: "CSV", pdf: "PDF", docx: "Word", word: "Word" };

  const redownload = async (record) => {
    setBusyId(record.id);
    try {
      const params = new URLSearchParams();
      params.set("format", record.format || "md");
      if (record.project_id) params.set("project_id", record.project_id);
      if (record.severity) params.set("severity", record.severity);
      if (record.status) params.set("status", record.status);
      await downloadFromApi(`/api/vulnerabilities/export?${params}`, record.filename);
      setToast({ type: "success", message: "已重新导出报告" });
      await load({ silent: true });
    } catch (error) {
      setToast({ type: "danger", message: error.message || "重新导出失败" });
    } finally {
      setBusyId(null);
    }
  };

  const removeRecord = async (record) => {
    const ok = await confirmAction({
      title: "删除导出记录",
      message: `确认删除「${record.filename}」这条导出记录？`,
      tone: "danger",
      confirmLabel: "删除",
    });
    if (!ok) return;
    try {
      await apiRequest(`/api/vulnerabilities/export-records/${record.id}`, { method: "DELETE" });
      setToast({ type: "success", message: "记录已删除" });
      await load({ silent: true });
    } catch (error) {
      setToast({ type: "danger", message: error.message || "删除失败" });
    }
  };

  const clearAll = async () => {
    const ok = await confirmAction({
      title: "清空导出记录",
      message: "确认清空全部导出记录？此操作不可撤销。",
      tone: "danger",
      confirmLabel: "清空",
    });
    if (!ok) return;
    try {
      await apiRequest("/api/vulnerabilities/export-records", { method: "DELETE" });
      setToast({ type: "success", message: "导出记录已清空" });
      await load({ silent: true });
    } catch (error) {
      setToast({ type: "danger", message: error.message || "清空失败" });
    }
  };

  return (
    <>
      <PageHeader
        compact
        title="漏洞报告 / 导出记录"
        subtitle="查看历史导出操作，包括导出范围、格式和时间"
      />
      <section className="content-wrap vulnerability-report-page">
        <article className="vuln-table-card export-records-card">
          <header className="vuln-table-title">
            <div>
              <h2>导出记录</h2>
              <p>每次导出漏洞报告都会在此留痕</p>
            </div>
            <div className="button-row">
              <button
                className="ghost-button compact danger"
                type="button"
                disabled={!records.length}
                onClick={clearAll}
              >
                <Trash2 size={15} />
                清空记录
              </button>
              <button className="ghost-button compact" type="button" onClick={() => load()}>
                <RefreshCw size={15} />
                刷新
              </button>
            </div>
          </header>
          <div className="export-records-head">
            <span>导出时间</span>
            <span>范围</span>
            <span>格式</span>
            <span>漏洞数</span>
            <span>文件名</span>
            <span>操作</span>
          </div>
          <div className="export-records-body">
            {loading ? (
              <EmptyState icon={Loader2} title="正在加载导出记录" />
            ) : records.length === 0 ? (
              <EmptyState icon={Download} title="暂无导出记录" subtitle="在漏洞列表中导出报告后，记录会显示在这里。" />
            ) : (
              records.map((record) => (
                <div className="export-records-row" key={record.id}>
                  <time>{formatTime(record.created_at)}</time>
                  <div className="export-scope-cell">
                    <strong>{record.scope}</strong>
                    {record.project_name && <span>{record.project_name}</span>}
                  </div>
                  <span><Badge tone="info">{formatLabels[record.format] || record.format.toUpperCase()}</Badge></span>
                  <span className="export-count">{record.vulnerability_count}</span>
                  <code title={record.filename}>{record.filename}</code>
                  <div className="button-row export-record-actions">
                    <button
                      className="table-action"
                      type="button"
                      title="重新导出"
                      aria-label="重新导出"
                      disabled={busyId === record.id}
                      onClick={() => redownload(record)}
                    >
                      {busyId === record.id ? <Loader2 className="spin" size={15} /> : <Download size={15} />}
                    </button>
                    <button
                      className="table-action danger"
                      type="button"
                      title="删除记录"
                      aria-label="删除记录"
                      onClick={() => removeRecord(record)}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </article>
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

function buildVulnerabilityTrend(items) {
  // Build a fixed 7-day calendar window ending today (local time), so the
  // chart always shows 近 7 天 even on days with no findings.
  const dayKeys = [];
  const byKey = new Map();
  const now = new Date();
  for (let offset = 6; offset >= 0; offset -= 1) {
    const day = new Date(now.getFullYear(), now.getMonth(), now.getDate() - offset);
    const key = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
    dayKeys.push(key);
    byKey.set(key, {
      date: key,
      label: `${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`,
      total: 0,
      critical: 0,
      high: 0,
      medium: 0,
      low: 0,
    });
  }
  items.forEach((item) => {
    const key = String(item.discovered_at || "").slice(0, 10);
    const entry = byKey.get(key);
    if (!entry) return;
    entry.total += 1;
    if (entry[item.severity] !== undefined) entry[item.severity] += 1;
  });
  return dayKeys.map((key) => byKey.get(key));
}

function buildSeverityTop(items, limit = 5) {
  const rank = { critical: 0, high: 1, medium: 2, low: 3 };
  return [...items]
    .sort((a, b) => {
      const byRank = (rank[a.severity] ?? 9) - (rank[b.severity] ?? 9);
      if (byRank !== 0) return byRank;
      return String(b.discovered_at || "").localeCompare(String(a.discovered_at || ""));
    })
    .slice(0, limit);
}

function buildPageNumbers(current, total) {
  // Compact pager: always show first/last, current ±1, with ellipsis gaps.
  if (total <= 7) {
    return Array.from({ length: total }, (_, index) => index + 1);
  }
  const pages = new Set([1, total, current, current - 1, current + 1]);
  const sorted = [...pages].filter((page) => page >= 1 && page <= total).sort((a, b) => a - b);
  const result = [];
  let prev = 0;
  for (const page of sorted) {
    if (page - prev > 1) result.push("...");
    result.push(page);
    prev = page;
  }
  return result;
}

function buildStatusDistribution(items) {
  return items.reduce(
    (acc, item) => {
      if (item.status === "ignored") acc.ignored += 1;
      else acc.confirmed += 1;
      acc.total += 1;
      return acc;
    },
    { total: 0, confirmed: 0, ignored: 0 },
  );
}

function VulnerabilityTrend({ data }) {
  const series = [
    ["critical", "严重", "#ff375f"],
    ["high", "高危", "#ff7a1a"],
    ["medium", "中危", "#f5b700"],
    ["low", "低危", "#0a84ff"],
  ];
  const hasData = data.some((item) => item.total > 0);
  const max = Math.max(1, ...data.flatMap((item) => series.map(([key]) => item[key] || 0)));
  const width = 280;
  const height = 120;
  const padX = 6;
  const innerW = width - padX * 2;
  const pointX = (index) => (data.length <= 1 ? width / 2 : padX + (index / (data.length - 1)) * innerW);
  const pointY = (value) => height - ((value || 0) / max) * (height - 18) - 9;
  const toPoints = (key) => data.map((item, index) => `${pointX(index)},${pointY(item[key])}`).join(" ");
  return (
    <section className="vuln-analysis-card">
      <header>
        <h3>漏洞趋势</h3>
        <span>近 7 天</span>
      </header>
      {!hasData ? (
        <p className="analysis-empty">近 7 天暂无新发现</p>
      ) : (
        <>
          <svg className="trend-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="漏洞趋势">
            {series.map(([key, label, color]) => (
              <polyline
                key={key}
                points={toPoints(key)}
                fill="none"
                stroke={color}
                strokeWidth="2.4"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-label={label}
              />
            ))}
            {data.map((item, index) =>
              series.map(([key, label, color]) => (
                <circle
                  key={`${item.date}-${key}`}
                  cx={pointX(index)}
                  cy={pointY(item[key])}
                  r="2.8"
                  fill={color}
                  aria-label={`${item.date} ${label} ${item[key] || 0}`}
                />
              )),
            )}
          </svg>
          <div className="trend-axis">
            {data.map((item) => (
              <span key={`axis-${item.date}`}>{item.label}</span>
            ))}
          </div>
          <div className="trend-legend">
            {series.map(([key, label, color]) => (
              <span key={key}>
                <i style={{ background: color }} />
                <strong>{data.reduce((sum, item) => sum + (item[key] || 0), 0)}</strong>
                {label}
              </span>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function SeverityTopList({ items }) {
  return (
    <section className="vuln-analysis-card severity-top-card">
      <header>
        <h3>严重漏洞 TOP 5</h3>
        <span>{items.length} 条</span>
      </header>
      <div className="severity-top-list">
        {items.length === 0 ? (
          <p className="analysis-empty">暂无漏洞</p>
        ) : (
          items.map((item, index) => {
            const meta = SEVERITY_META[item.severity] || SEVERITY_META.low;
            return (
              <article key={`top-${item.id}`}>
                <span className="severity-top-rank">{index + 1}</span>
                <span className={cn("status-badge", meta.tone)}>{meta.label}</span>
                <strong title={item.title}>{clampText(item.title, 30)}</strong>
                <code>{item.fact_id}</code>
              </article>
            );
          })
        )}
      </div>
    </section>
  );
}

function VulnerabilityStatusDistribution({ data }) {
  const total = Math.max(0, data.total || 0);
  const confirmed = data.confirmed || 0;
  const ignored = data.ignored || 0;
  const radius = 36;
  const circumference = 2 * Math.PI * radius;
  const confirmedLength = total ? (confirmed / total) * circumference : 0;
  const ignoredLength = total ? (ignored / total) * circumference : 0;
  return (
    <section className="vuln-analysis-card status-distribution-card">
      <header>
        <h3>状态分布</h3>
        <span>实时数据</span>
      </header>
      {total === 0 ? (
        <p className="analysis-empty">暂无状态数据</p>
      ) : (
        <div className="status-distribution">
          <svg viewBox="0 0 96 96" role="img" aria-label="漏洞状态分布">
            <circle className="donut-track" cx="48" cy="48" r={radius} />
            <circle
              className="donut-segment confirmed"
              cx="48"
              cy="48"
              r={radius}
              strokeDasharray={`${confirmedLength} ${circumference - confirmedLength}`}
            />
            <circle
              className="donut-segment ignored"
              cx="48"
              cy="48"
              r={radius}
              strokeDasharray={`${ignoredLength} ${circumference - ignoredLength}`}
              strokeDashoffset={-confirmedLength}
            />
            <text x="48" y="45" textAnchor="middle">
              {total}
            </text>
            <text x="48" y="59" textAnchor="middle" className="donut-caption">
              总计
            </text>
          </svg>
          <div className="status-distribution-list">
            <span>
              <i className="status-dot confirmed" />
              已确认
              <strong>{confirmed}</strong>
            </span>
            <span>
              <i className="status-dot ignored" />
              已忽略
              <strong>{ignored}</strong>
            </span>
          </div>
        </div>
      )}
    </section>
  );
}

function VulnerabilityItem({ vuln, selected, onSelect, expanded, onToggle, onExport, onStatusChange }) {
  const meta = SEVERITY_META[vuln.severity] || SEVERITY_META.low;
  const ignored = vuln.status === "ignored";
  return (
    <article className={cn("vuln-table-item", ignored && "ignored")}>
      <div className="vuln-table-row">
        <label className="vuln-select">
          <input type="checkbox" checked={selected} onChange={onSelect} />
        </label>
        <div className="vuln-name-cell">
          <div className="vuln-meta">
            <span>{vuln.fact_id}</span>
          </div>
          <h3>{vuln.title}</h3>
        </div>
        <div className="vuln-project-cell">
          <strong>{vuln.project_name}</strong>
          <span>{vuln.project_id}</span>
        </div>
        <div>
          <Badge tone={meta.tone}>{meta.label}</Badge>
        </div>
        <div>
          <Badge tone={ignored ? "muted" : "success"}>{ignored ? "已忽略" : "已确认"}</Badge>
        </div>
        <time>{formatTime(vuln.discovered_at)}</time>
        <div className="button-row">
          <button
            className={cn("table-action", ignored ? "success" : "warning")}
            type="button"
            title={ignored ? "恢复确认" : "设为忽略"}
            aria-label={ignored ? "恢复确认" : "设为忽略"}
            onClick={() => onStatusChange(ignored ? "confirmed" : "ignored")}
          >
            {ignored ? <CheckCircle2 size={16} /> : <X size={16} />}
          </button>
          <button className="table-action" type="button" onClick={onExport} title="导出当前漏洞" aria-label="导出当前漏洞">
            <Download size={16} />
          </button>
          <button className="table-action" type="button" onClick={onToggle} title="查看详情" aria-label="查看详情">
            {expanded ? <ChevronDown size={16} /> : <Eye size={16} />}
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

function WorkersPage({ runAction, setToast, confirmAction }) {
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

  // Summary counts derived from the real worker status array (idle/busy/offline/
  // disabled). "在线" = enabled and reachable (idle or busy); we never fabricate
  // metrics that the API does not expose.
  const workerCounts = useMemo(() => {
    const counts = { total: visibleWorkers.length, online: 0, running: 0, offline: 0, tasks: 0 };
    for (const worker of visibleWorkers) {
      const status = statusByName.get(worker.name) || worker;
      const effective = status.status || (worker.enabled === false ? "disabled" : "offline");
      if (effective === "busy") counts.running += 1;
      if (effective === "idle" || effective === "busy") counts.online += 1;
      else counts.offline += 1;
      counts.tasks += status.tasks_completed || 0;
    }
    return counts;
  }, [visibleWorkers, statusByName]);

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
    if (!config) return;
    const ok = await confirmAction({
      title: "删除工作节点",
      message: `确认删除 Worker「${worker.name}」？`,
      tone: "danger",
      confirmLabel: "删除",
    });
    if (!ok) return;
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
          <>
            <div className="metric-grid">
              <MetricCard label="在线" value={workerCounts.online} tone="success" />
              <MetricCard label="离线" value={workerCounts.offline} tone="muted" />
              <MetricCard label="运行中" value={workerCounts.running} tone="info" />
              <MetricCard label="任务数" value={workerCounts.tasks} tone="info" />
            </div>
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
          </>
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
      PI_PROVIDER_API: "openai-completions",
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
      PI_BASE_URL: "http://10.2.8.77:3000/v1",
      PI_API_KEY: "",
      PI_PROVIDER_API: "openai-completions",
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

function TemplatesPage({ runAction, setToast, confirmAction }) {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newTemplate, setNewTemplate] = useState(false);
  const [projectTemplate, setProjectTemplate] = useState(null);
  const [category, setCategory] = useState("all");

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
    const ok = await confirmAction({
      title: "删除模板",
      message: `确认删除模板「${template.title}」？`,
      tone: "danger",
      confirmLabel: "删除",
    });
    if (!ok) return;
    await runAction("模板已删除", () => apiRequest(`/api/templates/${template.id}`, { method: "DELETE" }));
    await load();
  };

  const categoryCounts = useMemo(() => {
    const counts = {};
    for (const template of templates) {
      const key = templateCategory(template);
      counts[key] = (counts[key] || 0) + 1;
    }
    return counts;
  }, [templates]);

  const visibleTemplates = useMemo(
    () => (category === "all" ? templates : templates.filter((template) => templateCategory(template) === category)),
    [templates, category],
  );

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
        <div className="template-tabs" role="tablist" aria-label="模板分类">
          {TEMPLATE_CATEGORIES.map((tab) => {
            const count = tab.key === "all" ? templates.length : categoryCounts[tab.key] || 0;
            return (
              <button
                key={tab.key}
                type="button"
                role="tab"
                aria-selected={category === tab.key}
                className={cn("template-tab", category === tab.key && "active")}
                onClick={() => setCategory(tab.key)}
              >
                {tab.label}
                <span className="template-tab-count">{count}</span>
              </button>
            );
          })}
        </div>
        {loading ? (
          <EmptyState icon={Loader2} title="正在加载模板" />
        ) : visibleTemplates.length === 0 ? (
          <EmptyState
            icon={FileText}
            title="该分类下暂无模板"
            subtitle="切换分类查看其他模板，或新建一个自定义模板。"
            action={
              <button className="primary-button compact" type="button" onClick={() => setNewTemplate(true)}>
                <Plus size={16} />
                新建模板
              </button>
            }
          />
        ) : (
          <div className="template-grid">
            {visibleTemplates.map((template) => {
              const catKey = templateCategory(template);
              const catMeta = TEMPLATE_CATEGORY_META[catKey] || TEMPLATE_CATEGORY_META.custom;
              const hintCount = template.hints?.length || 0;
              return (
                <article className="template-card" key={template.id}>
                  <header>
                    <span className="template-icon">
                      <FileText size={20} />
                    </span>
                    <div className="template-heading">
                      <h3>{template.title}</h3>
                      <div className="template-badges">
                        <Badge tone={catMeta.tone}>{catMeta.label}</Badge>
                        <Badge tone={template.is_builtin ? "info" : "success"}>
                          {template.is_builtin ? "内置" : "自定义"}
                        </Badge>
                      </div>
                    </div>
                  </header>
                  <p className="template-description">{template.goal}</p>
                  <div className="template-section">
                    <span>起点</span>
                    <p>{template.origin}</p>
                  </div>
                  <div className="template-foot">
                    <span className="template-meta">
                      <Sparkles size={14} />
                      {hintCount ? `${hintCount} 条提示` : "无初始提示"}
                    </span>
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
                  </div>
                </article>
              );
            })}
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

const TEMPLATE_CATEGORIES = [
  { key: "all", label: "全部模板" },
  { key: "web", label: "Web渗透" },
  { key: "internal", label: "内网渗透" },
  { key: "recon", label: "信息收集" },
  { key: "ctf", label: "CTF挑战" },
  { key: "custom", label: "自定义" },
];

const TEMPLATE_CATEGORY_META = {
  web: { label: "Web渗透", tone: "info" },
  internal: { label: "内网渗透", tone: "high" },
  recon: { label: "信息收集", tone: "medium" },
  ctf: { label: "CTF挑战", tone: "critical" },
  custom: { label: "自定义", tone: "success" },
};

// Presentational-only category grouping derived from the template's own text.
// Templates have no category field from the API, so this never fabricates data;
// it only buckets a template for the filter tabs and badge.
function templateCategory(template) {
  if (!template.is_builtin) return "custom";
  const text = `${template.title || ""} ${template.origin || ""} ${template.goal || ""}`;
  if (/CTF/i.test(text)) return "ctf";
  if (/web/i.test(text)) return "web";
  if (text.includes("内网")) return "internal";
  if (text.includes("外网") || text.includes("信息收集") || text.includes("侦察")) return "recon";
  return "web";
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
  const [form, setForm] = useState({ current_password: "", new_password: "", confirm_password: "" });
  const [show, setShow] = useState({ current: false, next: false });
  const [saving, setSaving] = useState(false);

  const rules = [
    { key: "len", label: "至少 8 个字符", ok: form.new_password.length >= 8 },
    { key: "case", label: "包含大小写字母", ok: /[a-z]/.test(form.new_password) && /[A-Z]/.test(form.new_password) },
    { key: "num", label: "包含数字", ok: /\d/.test(form.new_password) },
    { key: "sym", label: "包含特殊字符", ok: /[^A-Za-z0-9]/.test(form.new_password) },
  ];
  const allOk = rules.every((rule) => rule.ok);
  const matched = form.confirm_password.length > 0 && form.new_password === form.confirm_password;
  const canSubmit = !!form.current_password && allOk && matched && !saving;

  const submit = async (event) => {
    event.preventDefault();
    if (!canSubmit) return;
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
    <Modal title="修改密码" subtitle="为账号设置一个更安全的新密码" onClose={onClose}>
      <form className="stack-form modal-body password-form" onSubmit={submit}>
        <label>
          <span>当前密码</span>
          <div className="input-affix">
            <KeyRound size={16} className="affix-icon" />
            <input
              type={show.current ? "text" : "password"}
              value={form.current_password}
              onChange={(event) => setForm({ ...form, current_password: event.target.value })}
              placeholder="请输入当前密码"
              autoComplete="current-password"
              required
            />
            <button type="button" className="affix-toggle" onClick={() => setShow({ ...show, current: !show.current })} aria-label="显示/隐藏密码">
              {show.current ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </label>
        <label>
          <span>新密码</span>
          <div className="input-affix">
            <Lock size={16} className="affix-icon" />
            <input
              type={show.next ? "text" : "password"}
              value={form.new_password}
              onChange={(event) => setForm({ ...form, new_password: event.target.value })}
              placeholder="请输入新密码"
              autoComplete="new-password"
              required
            />
            <button type="button" className="affix-toggle" onClick={() => setShow({ ...show, next: !show.next })} aria-label="显示/隐藏密码">
              {show.next ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </label>
        <label>
          <span>确认新密码</span>
          <div className="input-affix">
            <Lock size={16} className="affix-icon" />
            <input
              type={show.next ? "text" : "password"}
              value={form.confirm_password}
              onChange={(event) => setForm({ ...form, confirm_password: event.target.value })}
              placeholder="请再次输入新密码"
              autoComplete="new-password"
              required
            />
          </div>
          {form.confirm_password.length > 0 && !matched && <small className="field-hint danger">两次输入的密码不一致</small>}
        </label>
        <ul className="password-rules">
          {rules.map((rule) => (
            <li key={rule.key} className={cn(rule.ok && "ok")}>
              {rule.ok ? <CheckCircle2 size={14} /> : <Circle size={14} />}
              {rule.label}
            </li>
          ))}
        </ul>
        <div className="modal-footer">
          <button className="ghost-button" type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary-button compact" type="submit" disabled={!canSubmit}>
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
            <small className="field-hint">意图在被回收前允许等待的最长时间。</small>
          </label>
          <label>
            <span>Reason 超时（秒）</span>
            <input
              type="number"
              min="5"
              value={settings.reason_timeout}
              onChange={(event) => setSettings({ ...settings, reason_timeout: Number(event.target.value) })}
            />
            <small className="field-hint">Reason 阶段在判定超时前的最长执行时间。</small>
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
