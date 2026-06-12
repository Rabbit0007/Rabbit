import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";
import elk from "cytoscape-elk";
import klay from "cytoscape-klay";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  Bot,
  CalendarDays,
  CheckCheck,
  CheckCircle2,
  ChevronLeft,
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
  KeyRound,
  Loader2,
  Lock,
  LayoutGrid,
  List,
  LogOut,
  Monitor,
  MoreVertical,
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
  Tag,
  Trash2,
  User,
  X,
} from "lucide-react";
import { apiRequest, downloadFromApi } from "./api";
import NotificationBell from "./components/notifications/NotificationBell";
import {
  Badge,
  ConfirmModal,
  EmptyState,
  MetricCard,
  MiniStat,
  Modal,
  PageHeader,
  ProjectSummaryCard,
  Toast,
  VulnerabilitySummaryCard,
} from "./components/ui";
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
  cytoscape.use(elk);
  cytoscape.use(klay);
} catch {
  // Vite HMR may register the extension more than once.
}

const APP_NAME = "Rabbit";
const HUMAN_WORKER = "Human";
const SECRET_MASK = "********";
const VULN_FOCUS_STORAGE_KEY = "rabbit:vuln-focus";
const PROJECT_PRESET_STORAGE_KEY = "rabbit:project-preset";
const WORKER_PRESET_STORAGE_KEY = "rabbit:worker-preset";
const VULN_FILTERS_STORAGE_KEY = "rabbit:vuln-filters";

function queueVulnerabilityFocus(id, hash = "#/vulnerabilities") {
  if (typeof window !== "undefined" && id) {
    window.sessionStorage.setItem(VULN_FOCUS_STORAGE_KEY, String(id));
  }
  go(hash);
}

function queueRoutePreset(storageKey, payload, hash) {
  if (typeof window !== "undefined") {
    window.sessionStorage.setItem(storageKey, JSON.stringify(payload || {}));
  }
  go(hash);
}

function consumeRoutePreset(storageKey) {
  if (typeof window === "undefined") return null;
  const raw = window.sessionStorage.getItem(storageKey);
  if (!raw) return null;
  window.sessionStorage.removeItem(storageKey);
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

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
    { title: null, items: [["overview", "报告总览"]] },
    {
      title: "按严重程度",
      items: [
        ["critical", "严重漏洞"],
        ["high", "高危漏洞"],
        ["medium", "中危漏洞"],
        ["low", "低危漏洞"],
      ],
    },
    {
      title: "按处理状态",
      items: [
        ["confirmed", "已确认漏洞"],
        ["ignored", "已忽略漏洞"],
      ],
    },
    { title: null, items: [["export-records", "导出记录"]] },
  ];
  const vulnRouteActive = route.page === "vulnerabilities";
  const [reportExpanded, setReportExpanded] = useState(vulnRouteActive);
  const [reportManual, setReportManual] = useState(false);
  const activeView = route.page === "vulnerabilities" ? route.view || "overview" : null;
  const searchPlaceholder =
    route.page === "projects" || route.page === "project"
      ? "搜索项目名称、编号、负责人、标签..."
      : "搜索漏洞标题、编号、项目、标签...";

  useEffect(() => {
    if (vulnRouteActive) {
      if (!reportManual) setReportExpanded(true);
      return;
    }
    setReportExpanded(false);
    setReportManual(false);
  }, [reportManual, vulnRouteActive]);

  const toggleVulnerabilityNav = () => {
    if (!vulnRouteActive) {
      setReportManual(false);
      setReportExpanded(true);
      go("#/vulnerabilities");
      return;
    }
    setReportManual(true);
    setReportExpanded((prev) => !prev);
  };

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
        <GlobalSearch setToast={setToast} placeholder={searchPlaceholder} shortcutLabel="⌘K" />
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
              <div key={key} className={cn("nav-group", active && "active", key === "vulnerabilities" && reportExpanded && "expanded")}>
                <button
                  className={cn("nav-tab", active && key !== "vulnerabilities" && "active", key === "vulnerabilities" && active && "module-open")}
                  type="button"
                  onClick={() => (key === "vulnerabilities" ? toggleVulnerabilityNav() : go(key === "projects" ? "#/projects" : `#/${key}`))}
                >
                  <Icon size={17} />
                  {label}
                  {key === "vulnerabilities" && <ChevronDown className="nav-caret" size={14} />}
                </button>
                {key === "vulnerabilities" && reportExpanded && (
                  <div className="sub-nav">
                    {reportSubnav.map((group, index) => (
                      <div key={group.title || `group-${index}`} className="sub-nav-group">
                        {group.title && <span className="sub-nav-group-label">{group.title}</span>}
                        <div className="sub-nav-list">
                          {group.items.map(([view, label]) => (
                            <button
                              key={view}
                              className={cn(activeView === view && "active")}
                              type="button"
                              onClick={() => go(view === "overview" ? "#/vulnerabilities" : `#/vulnerabilities/${view}`)}
                            >
                              <span className="sub-nav-indicator" />
                              {label}
                            </button>
                          ))}
                        </div>
                      </div>
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

function GlobalSearch({ setToast, placeholder = "搜索漏洞标题、编号、项目、标签...", shortcutLabel = "" }) {
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
          placeholder={placeholder}
          aria-label="全局搜索"
          onChange={(event) => setTerm(event.target.value)}
          onFocus={() => term.trim() && setOpen(true)}
          onKeyDown={(event) => {
            if (event.key === "Escape") reset();
          }}
        />
        {!term && shortcutLabel && <span className="global-search-shortcut">{shortcutLabel}</span>}
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
  const [captchaLoading, setCaptchaLoading] = useState(false);
  const [authMessage, setAuthMessage] = useState(null);
  const [failedAttempts, setFailedAttempts] = useState(0);
  const [cooldownUntil, setCooldownUntil] = useState(0);
  const [clock, setClock] = useState(Date.now());

  const loadCaptcha = useCallback(async () => {
    setCaptchaLoading(true);
    try {
      const data = await apiRequest("/api/auth/captcha");
      setCaptcha(data);
      setForm((prev) => ({ ...prev, captcha_answer: "" }));
    } catch (error) {
      setCaptcha(null);
      throw error;
    } finally {
      setCaptchaLoading(false);
    }
  }, []);

  useEffect(() => {
    loadCaptcha().catch((error) => setToast({ type: "danger", message: error.message }));
  }, [loadCaptcha, setToast]);

  useEffect(() => {
    if (!cooldownUntil) return undefined;
    const timer = window.setInterval(() => setClock(Date.now()), 300);
    return () => window.clearInterval(timer);
  }, [cooldownUntil]);

  const isRegister = mode === "register";
  const cooldownSeconds = Math.max(0, Math.ceil((cooldownUntil - clock) / 1000));
  const passwordsMismatch = isRegister && form.confirm_password.length > 0 && form.password !== form.confirm_password;
  const requiredFilled =
    form.username.trim() &&
    form.password &&
    form.captcha_answer.trim() &&
    (!isRegister || form.confirm_password);
  const canSubmit = Boolean(
    requiredFilled && captcha?.captcha_id && !passwordsMismatch && !loading && !captchaLoading && cooldownSeconds === 0,
  );
  const submitLabel = loading
    ? isRegister
      ? "正在注册..."
      : "正在登录..."
    : cooldownSeconds > 0
      ? `${cooldownSeconds} 秒后重试`
      : isRegister
        ? "注册账号"
        : "登录";

  const updateField = (key, value) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    if (key === "username") {
      setFailedAttempts(0);
      setCooldownUntil(0);
    }
    setAuthMessage(null);
  };

  const switchMode = (nextMode) => {
    setMode(nextMode);
    setAuthMessage(null);
    setLoading(false);
    setFailedAttempts(0);
    setCooldownUntil(0);
    setForm((prev) => ({ ...prev, password: "", confirm_password: "", captcha_answer: "" }));
    loadCaptcha().catch(() => {});
  };

  const describeAuthError = (error) => {
    if (error?.status === 400 && String(error.message || "").includes("验证码")) {
      return { type: "warning", title: "验证码无效", body: error.message };
    }
    if (error?.status === 401) {
      return {
        type: "danger",
        title: "账号或密码错误",
        body: "请检查账号、密码和验证码。为安全起见，不区分账号是否存在。",
      };
    }
    if (error?.status === 409 && isRegister) {
      return { type: "warning", title: "用户名不可用", body: "这个用户名已被占用，请换一个再试。" };
    }
    if (error?.status === 422) {
      return {
        type: "warning",
        title: "输入格式不符合要求",
        body: isRegister ? "用户名需 3-32 个字符，密码长度至少 8 位。" : "请填写用户名、密码和验证码。",
      };
    }
    if (error?.status === 429) {
      return {
        type: "danger",
        title: "登录尝试过多",
        body: "该账号短时间内失败次数过多，服务端已临时限制登录。请 15 分钟后再试。",
      };
    }
    return { type: "danger", title: "认证请求失败", body: "服务暂时没有返回有效结果，请检查网络或稍后重试。" };
  };

  const submit = async (event) => {
    event.preventDefault();
    if (loading) return;
    if (!captcha?.captcha_id) {
      setAuthMessage({ type: "warning", title: "验证码未就绪", body: "请先刷新验证码后再提交。" });
      return;
    }
    if (passwordsMismatch) {
      setAuthMessage({ type: "warning", title: "两次密码不一致", body: "请重新确认要设置的密码。" });
      return;
    }
    setAuthMessage({ type: "info", title: isRegister ? "正在创建账户" : "正在验证身份", body: "请稍候，不要重复提交。" });
    setLoading(true);
    try {
      await apiRequest(`/api/auth/${isRegister ? "register" : "login"}`, {
        method: "POST",
        body: {
          username: form.username.trim(),
          password: form.password,
          captcha_id: captcha?.captcha_id,
          captcha_answer: form.captcha_answer,
        },
      });
      setFailedAttempts(0);
      setCooldownUntil(0);
      setAuthMessage({ type: "success", title: "验证通过", body: "正在进入工作台。" });
      await onAuthed();
    } catch (error) {
      const message = describeAuthError(error);
      setAuthMessage(message);
      if (!isRegister && error?.status === 401) {
        setFailedAttempts((current) => {
          const next = current + 1;
          if (next >= 3) {
            setCooldownUntil(Date.now() + Math.min(10, next * 2) * 1000);
            setClock(Date.now());
          }
          return next;
        });
      }
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
            <button className="auth-back" type="button" onClick={() => switchMode("login")} disabled={loading}>
              <ArrowLeft size={16} />
              返回登录
            </button>
          )}
          <div className="auth-title align-left">
            <h1>{isRegister ? "创建账户" : "欢迎回来"}</h1>
            <p>{isRegister ? "加入 Rabbit 安全探索平台" : "登录以继续安全探索工作流"}</p>
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
          <div className="auth-security-note">
            <ShieldCheck size={16} />
            <span>验证码校验、失败次数限制和安全 Session 已启用</span>
          </div>
          {authMessage && (
            <div className={cn("auth-inline-alert", authMessage.type)} role="alert" aria-live="polite">
              {authMessage.type === "success" ? <CheckCircle2 size={18} /> : <AlertCircle size={18} />}
              <div>
                <strong>{authMessage.title}</strong>
                <p>{authMessage.body}</p>
                {!isRegister && failedAttempts > 0 && authMessage.type === "danger" && (
                  <small>当前浏览器已记录 {failedAttempts} 次失败；服务端会按账号执行 15 分钟窗口限流。</small>
                )}
              </div>
            </div>
          )}
          <form className="stack-form auth-stack-form" onSubmit={submit}>
            <label>
              <span>用户名</span>
              <input
                autoComplete="username"
                required
                disabled={loading}
                value={form.username}
                onChange={(event) => updateField("username", event.target.value)}
                placeholder="请输入用户名"
              />
            </label>
            <label>
              <span>密码</span>
              <input
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                type="password"
                required
                disabled={loading}
                value={form.password}
                onChange={(event) => updateField("password", event.target.value)}
                placeholder={mode === "login" ? "请输入密码" : "请设置密码"}
              />
            </label>
            {mode === "register" && (
              <label>
                <span>确认密码</span>
                <input
                  autoComplete="new-password"
                  type="password"
                  required
                  disabled={loading}
                  aria-invalid={passwordsMismatch}
                  className={passwordsMismatch ? "input-invalid" : ""}
                  value={form.confirm_password}
                  onChange={(event) => updateField("confirm_password", event.target.value)}
                  placeholder="请再次输入密码"
                />
                {passwordsMismatch && <small className="field-hint danger">两次输入的密码不一致</small>}
              </label>
            )}
            <label>
              <span>验证码</span>
              <div className="captcha-row">
                <input
                  required
                  disabled={loading || captchaLoading}
                  inputMode="numeric"
                  value={form.captcha_answer}
                  onChange={(event) => updateField("captcha_answer", event.target.value)}
                  placeholder="请输入计算结果"
                />
                <button className="captcha-chip" type="button" onClick={loadCaptcha} disabled={loading || captchaLoading}>
                  {captchaLoading ? "获取中" : captcha?.question || "刷新"}
                  <RefreshCw className={captchaLoading ? "spin" : ""} size={15} />
                </button>
              </div>
            </label>
            <button className="primary-button auth-submit" type="submit" disabled={!canSubmit}>
              {loading ? <Loader2 className="spin" size={18} /> : <Lock size={18} />}
              {submitLabel}
            </button>
          </form>
          <p className="auth-switch">
            {mode === "login" ? "还没有账号？" : "已有账号？"}
            <button type="button" onClick={() => switchMode(mode === "login" ? "register" : "login")} disabled={loading}>
              {mode === "login" ? "立即注册" : "返回登录"}
            </button>
          </p>
        </main>
      </section>
    </div>
  );
}

function DashboardPage({ runAction, setToast }) {
  const [vulnerabilities, setVulnerabilities] = useState([]);
  const [projects, setProjects] = useState([]);
  const [workers, setWorkers] = useState([]);
  const [workerObservability, setWorkerObservability] = useState(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [newOpen, setNewOpen] = useState(false);

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    try {
      const [vulnList, projectList, workerList, workerTelemetry] = await Promise.all([
        apiRequest("/api/vulnerabilities"),
        apiRequest("/projects"),
        apiRequest("/api/workers").catch(() => []),
        apiRequest("/api/workers/observability").catch(() => null),
      ]);
      setVulnerabilities(Array.isArray(vulnList) ? vulnList : []);
      setProjects(Array.isArray(projectList) ? projectList : []);
      setWorkers(Array.isArray(workerList) ? workerList : []);
      setWorkerObservability(workerTelemetry);
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

  const statusDistribution = useMemo(() => buildStatusDistribution(vulnerabilities), [vulnerabilities]);
  const trendData = useMemo(() => buildVulnerabilityTrend(vulnerabilities), [vulnerabilities]);
  const severityTop = useMemo(() => buildSeverityTop(vulnerabilities), [vulnerabilities]);
  const recentActivity = useMemo(
    () =>
      [...vulnerabilities]
        .sort((a, b) => String(b.discovered_at || "").localeCompare(String(a.discovered_at || "")))
        .slice(0, 5),
    [vulnerabilities],
  );

  const projectCounts = useMemo(() => {
    const total = projects.length;
    const active = projects.filter((project) => project.status === "active").length;
    const completed = projects.filter((project) => project.status === "completed").length;
    const stopped = projects.filter((project) => project.status === "stopped").length;
    return { total, active, completed, stopped };
  }, [projects]);

  const recentProjects = useMemo(
    () =>
      [...projects]
        .sort((a, b) => String(b.updated_at || b.created_at || "").localeCompare(String(a.updated_at || a.created_at || "")))
        .slice(0, 5),
    [projects],
  );

  const workerCounts = useMemo(() => {
    if (workerObservability?.summary) {
      return {
        total: workerObservability.summary.total || 0,
        online: workerObservability.summary.online || 0,
        running: workerObservability.summary.busy || 0,
        idle: workerObservability.summary.idle || 0,
        offline: workerObservability.summary.offline || 0,
      };
    }
    return workers.reduce(
      (acc, worker) => {
        acc.total += 1;
        if (worker.status === "busy") acc.running += 1;
        if (worker.status === "idle") acc.idle += 1;
        if (worker.status === "idle" || worker.status === "busy") acc.online += 1;
        else acc.offline += 1;
        return acc;
      },
      { total: 0, online: 0, running: 0, idle: 0, offline: 0 },
    );
  }, [workerObservability, workers]);

  const workerHighlights = useMemo(() => {
    const rank = { busy: 0, idle: 1, offline: 2, disabled: 3 };
    return [...workers]
      .sort((left, right) => {
        const rankDelta = (rank[left.status] ?? 99) - (rank[right.status] ?? 99);
        if (rankDelta !== 0) return rankDelta;
        return String(left.name || "").localeCompare(String(right.name || ""));
      })
      .slice(0, 5);
  }, [workers]);

  const projectStatusMeta =
    {
      active: { label: "运行中", tone: "success" },
      completed: { label: "已完成", tone: "info" },
      stopped: { label: "已停止", tone: "warning" },
    };

  const quickActions = [
    { key: "new", label: "新建项目", desc: "定义起点、目标和提示", icon: Plus, onClick: () => setNewOpen(true) },
    { key: "vulns", label: "漏洞报告", desc: "查看与管理漏洞", icon: AlertTriangle, onClick: () => go("#/vulnerabilities") },
    { key: "workers", label: "工作节点", desc: "状态与模型配置", icon: Monitor, onClick: () => go("#/workers") },
    { key: "templates", label: "模板", desc: "复用项目模板", icon: FileText, onClick: () => go("#/templates") },
    { key: "audit", label: "审计日志", desc: "查看关键操作留痕", icon: History, onClick: () => go("#/audit") },
  ];

  return (
    <>
      <PageHeader
        icon={Home}
        title="仪表盘"
        subtitle="系统全局概览：项目、风险、Worker 与最近活动"
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
      <section className="content-wrap dashboard-shell">
        {loading ? (
          <EmptyState icon={Loader2} title="正在加载仪表盘" />
        ) : (
          <>
            <div className="metric-grid dashboard-metric-grid">
              <MetricCard
                label="全部项目"
                value={projectCounts.total}
                tone="info"
                icon={Folder}
                description="所有项目总数"
                onClick={() => go("#/projects")}
              />
              <MetricCard
                label="运行中项目"
                value={projectCounts.active}
                tone="success"
                icon={Play}
                description="当前正在执行"
                onClick={() => queueRoutePreset(PROJECT_PRESET_STORAGE_KEY, { status: "active" }, "#/projects")}
              />
              <MetricCard
                label="漏洞总数"
                value={statusDistribution.total || 0}
                tone="high"
                icon={ShieldAlert}
                description="当前纳入管理的漏洞"
                onClick={() => go("#/vulnerabilities")}
              />
              <MetricCard
                label="在线 Worker"
                value={workerCounts.online}
                tone="violet"
                icon={Monitor}
                description="当前可接收任务"
                onClick={() => queueRoutePreset(WORKER_PRESET_STORAGE_KEY, { status: "online" }, "#/workers")}
              />
            </div>
            <div className="dashboard-analytics-grid">
              <VulnerabilityTrend data={trendData} />
              <div className="dashboard-side-stack">
                <VulnerabilityStatusDistribution data={statusDistribution} />
                <SeverityTopList items={severityTop} onSelect={(item) => queueVulnerabilityFocus(item.id)} />
              </div>
            </div>
            <div className="dashboard-ops-grid">
              <section className="vuln-analysis-card dashboard-entity-card">
                <header>
                  <h3>项目概览</h3>
                  <span>{projectCounts.total} 个项目</span>
                </header>
                <div className="dashboard-inline-stats">
                  <MiniStat label="运行中" value={projectCounts.active} />
                  <MiniStat label="已完成" value={projectCounts.completed} />
                  <MiniStat label="已停止" value={projectCounts.stopped} />
                </div>
                {recentProjects.length === 0 ? (
                  <p className="analysis-empty">暂无项目数据</p>
                ) : (
                  <div className="dashboard-entity-list">
                    {recentProjects.map((project) => {
                      const meta = projectStatusMeta[project.status] || projectStatusMeta.active;
                      return (
                        <button
                          key={project.id}
                          className="dashboard-entity-item"
                          type="button"
                          onClick={() => go(`#/projects/${project.id}`)}
                        >
                          <div className="dashboard-entity-copy">
                            <strong title={project.title}>{project.title}</strong>
                            <small>
                              {project.id} · 更新于 {formatTime(project.updated_at || project.created_at)}
                            </small>
                          </div>
                          <Badge tone={meta.tone}>{meta.label}</Badge>
                        </button>
                      );
                    })}
                  </div>
                )}
              </section>
              <section className="vuln-analysis-card dashboard-entity-card">
                <header>
                  <h3>Worker 状态</h3>
                  <span>{workerCounts.total} 个节点</span>
                </header>
                <div className="dashboard-inline-stats">
                  <MiniStat label="在线" value={workerCounts.online} />
                  <MiniStat label="运行中" value={workerCounts.running} />
                  <MiniStat label="空闲" value={workerCounts.idle} />
                  <MiniStat label="离线" value={workerCounts.offline} />
                </div>
                {workerHighlights.length === 0 ? (
                  <p className="analysis-empty">暂无 Worker 数据</p>
                ) : (
                  <div className="dashboard-entity-list">
                    {workerHighlights.map((worker) => {
                      const meta = STATUS_META[worker.status] || STATUS_META.offline;
                      return (
                        <button
                          key={worker.name}
                          className="dashboard-entity-item"
                          type="button"
                          onClick={() => go("#/workers")}
                        >
                          <div className="dashboard-entity-copy">
                            <strong title={worker.name}>{worker.name}</strong>
                            <small title={worker.current_task || ""}>
                              {worker.current_task
                                ? clampText(worker.current_task, 56)
                                : `最近心跳 ${relativeHeartbeat(worker.last_heartbeat_seconds_ago)}`}
                            </small>
                          </div>
                          <Badge tone={meta.tone}>{meta.label}</Badge>
                        </button>
                      );
                    })}
                  </div>
                )}
              </section>
            </div>
            <div className="dashboard-bottom-grid">
              <section className="vuln-analysis-card dashboard-activity-card">
                <header>
                  <h3>最近活动</h3>
                  <span>最新发现</span>
                </header>
                {recentActivity.length === 0 ? (
                  <p className="analysis-empty">暂无漏洞活动</p>
                ) : (
                  <div className="dashboard-activity-list">
                    {recentActivity.map((item) => {
                      const meta = SEVERITY_META[item.severity] || SEVERITY_META.low;
                      return (
                        <button
                          key={`activity-${item.id}`}
                          className="dashboard-activity-item"
                          type="button"
                          onClick={() => queueVulnerabilityFocus(item.id)}
                        >
                          <div className="dashboard-activity-head">
                            <Badge tone={meta.tone}>{meta.label}</Badge>
                            <code>{item.fact_id}</code>
                          </div>
                          <strong title={item.title}>{item.title}</strong>
                          <p>
                            {item.project_name} · {formatTime(item.discovered_at)}
                          </p>
                        </button>
                      );
                    })}
                  </div>
                )}
              </section>
              <section className="vuln-analysis-card dashboard-quick-card">
                <header>
                  <h3>快捷入口</h3>
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
            </div>
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
  const [campaignByProjectId, setCampaignByProjectId] = useState({});
  const [campaignLoadingByProjectId, setCampaignLoadingByProjectId] = useState({});
  const [loading, setLoading] = useState(true);
  const [newOpen, setNewOpen] = useState(false);
  const [reopenTarget, setReopenTarget] = useState(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [searchTerm, setSearchTerm] = useState("");
  const [pageSize, setPageSize] = useState(10);
  const [page, setPage] = useState(1);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setCampaignByProjectId({});
      setCampaignLoadingByProjectId({});
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

  useEffect(() => {
    const preset = consumeRoutePreset(PROJECT_PRESET_STORAGE_KEY);
    if (preset?.status) setStatusFilter(String(preset.status));
  }, []);

  const counts = useMemo(() => {
    const total = projects.length;
    const active = projects.filter((project) => project.status === "active").length;
    const completed = projects.filter((project) => project.status === "completed").length;
    const stopped = projects.filter((project) => project.status === "stopped").length;
    return { total, active, completed, stopped };
  }, [projects]);
  const filteredProjects = useMemo(() => {
    const keyword = searchTerm.trim().toLowerCase();
    return projects.filter((project) => {
      if (statusFilter !== "all" && project.status !== statusFilter) return false;
      if (keyword) {
        const blob = [project.title, project.id, project.reason?.worker, project.reason?.trigger]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!blob.includes(keyword)) return false;
      }
      return true;
    });
  }, [projects, searchTerm, statusFilter]);
  const totalPages = Math.max(1, Math.ceil(filteredProjects.length / pageSize));
  const visibleProjects = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filteredProjects.slice(start, start + pageSize);
  }, [filteredProjects, page, pageSize]);

  useEffect(() => {
    const missingProjects = visibleProjects.filter(
      (project) => campaignByProjectId[project.id] === undefined && !campaignLoadingByProjectId[project.id],
    );
    if (!missingProjects.length) return undefined;

    let cancelled = false;
    const ids = missingProjects.map((project) => project.id);
    setCampaignLoadingByProjectId((current) => ({
      ...current,
      ...Object.fromEntries(ids.map((id) => [id, true])),
    }));

    Promise.all(
      missingProjects.map(async (project) => {
        try {
          const campaign = await apiRequest(`/api/projects/${project.id}/campaign`);
          return [project.id, campaign];
        } catch {
          return [project.id, null];
        }
      }),
    )
      .then((entries) => {
        if (cancelled) return;
        setCampaignByProjectId((current) => ({
          ...current,
          ...Object.fromEntries(entries),
        }));
      })
      .finally(() => {
        if (cancelled) return;
        setCampaignLoadingByProjectId((current) => {
          const next = { ...current };
          ids.forEach((id) => {
            delete next[id];
          });
          return next;
        });
      });

    return () => {
      cancelled = true;
    };
  }, [visibleProjects, campaignByProjectId, campaignLoadingByProjectId]);

  useEffect(() => {
    setPage((current) => Math.min(current, totalPages));
  }, [totalPages]);

  useEffect(() => {
    setPage(1);
  }, [pageSize, searchTerm, statusFilter]);

  const projectTabs = [
    { key: "all", label: "全部", count: counts.total, tone: "info" },
    { key: "active", label: "运行中", count: counts.active, tone: "success" },
    { key: "completed", label: "已完成", count: counts.completed, tone: "info" },
    { key: "stopped", label: "已停止", count: counts.stopped, tone: "warning" },
  ];

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
        icon={Folder}
        title="项目"
        subtitle="面向目标的事实图探索工作区"
        actions={
          <>
            <div className="status-pill project-running-pill">
              <span className="dot success" />
              <span>{counts.active} 个运行中</span>
            </div>
            <button className="primary-button compact" type="button" onClick={() => setNewOpen(true)}>
              <Plus size={18} />
              新建项目
            </button>
            <button className="primary-outline compact" type="button" onClick={load}>
              <RefreshCw size={18} />
              刷新
            </button>
          </>
        }
      />
      <section className="content-wrap project-dashboard-page">
        <div className="project-summary-grid">
          <ProjectSummaryCard
            label="全部项目"
            value={counts.total}
            description="所有项目总数"
            tone="blue"
            icon={Folder}
          />
          <ProjectSummaryCard
            label="运行中"
            value={counts.active}
            description="正在运行的项目"
            tone="green"
            icon={Play}
          />
          <ProjectSummaryCard
            label="已完成"
            value={counts.completed}
            description="已完成的项目"
            tone="slate"
            icon={CheckCircle2}
          />
          <ProjectSummaryCard
            label="已停止"
            value={counts.stopped}
            description="已停止的项目"
            tone="violet"
            icon={Pause}
          />
        </div>
        <div className="project-toolbar">
          <div className="project-filter-tabs" role="tablist" aria-label="项目状态筛选">
            {projectTabs.map((tab) => (
              <button
                key={tab.key}
                type="button"
                role="tab"
                aria-selected={statusFilter === tab.key}
                className={cn("project-filter-tab", statusFilter === tab.key && "active", tab.tone)}
                onClick={() => setStatusFilter(tab.key)}
              >
                <span>{tab.label}</span>
                <strong>{tab.count}</strong>
              </button>
            ))}
          </div>
          <label className="project-search">
            <Search size={15} />
            <input
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder="搜索项目名称、编号、负责人、标签..."
            />
          </label>
        </div>
        {loading ? (
          <EmptyState icon={Loader2} title="正在加载项目" />
        ) : filteredProjects.length === 0 ? (
          <EmptyState
            icon={Folder}
            title={projects.length === 0 ? "还没有项目" : "当前筛选下没有项目"}
            subtitle={projects.length === 0 ? "创建一个项目后，Rabbit 会围绕起点、目标和提示生成事实图。" : "调整筛选条件后再试。"}
            action={
              <button className="primary-button compact" type="button" onClick={() => setNewOpen(true)}>
                <Plus size={18} />
                新建项目
              </button>
            }
          />
        ) : (
          <>
            <div className="project-grid project-grid-redesigned">
            {visibleProjects.map((project) => (
              <ProjectCard
                key={project.id}
                project={project}
                campaign={campaignByProjectId[project.id]}
                campaignLoading={Boolean(campaignLoadingByProjectId[project.id])}
                onDelete={() => deleteProject(project)}
                onStop={() => updateStatus(project, "stopped")}
                onStart={() => updateStatus(project, "active")}
                onReopen={() => reopenProject(project)}
              />
            ))}
            </div>
            <div className="project-list-footer">
              <span>共 {filteredProjects.length} 个项目{filteredProjects.length !== projects.length ? ` / 全部 ${projects.length} 个` : ""}</span>
              <div className="project-list-footer-controls">
                <label className="pagination-size">
                  <select
                    value={pageSize}
                    onChange={(event) => {
                      setPageSize(Number(event.target.value) || 10);
                      setPage(1);
                    }}
                  >
                    <option value="10">10 条/页</option>
                    <option value="20">20 条/页</option>
                    <option value="50">50 条/页</option>
                  </select>
                </label>
                <div className="pagination project-pagination">
                  <button
                    type="button"
                    className="nav"
                    disabled={page <= 1}
                    onClick={() => setPage((current) => Math.max(1, current - 1))}
                  >
                    <ChevronLeft size={16} />
                    上一页
                  </button>
                  <button type="button" className="active">
                    {page} / {totalPages}
                  </button>
                  <button
                    type="button"
                    className="nav"
                    disabled={page >= totalPages}
                    onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                  >
                    下一页
                    <ChevronRight size={16} />
                  </button>
                </div>
              </div>
            </div>
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

function ProjectCard({ project, campaign, campaignLoading, onDelete, onStop, onStart, onReopen }) {
  const statusMeta =
    {
      active: { label: "运行中", tone: "success" },
      completed: { label: "已完成", tone: "info" },
      stopped: { label: "已停止", tone: "warning" },
    }[project.status] || STATUS_META.active;
  const updatedAt = formatTime(project.updated_at || project.created_at);
  return (
    <article className="project-card">
      <div className="project-card-head">
        <button className="project-open" type="button" onClick={() => go(`#/projects/${project.id}`)}>
          <span className="project-folder">
            <Folder size={24} />
          </span>
          <span className="project-main">
            <span className="project-title-row">
              <strong>{project.title}</strong>
              <Badge tone={statusMeta.tone}>{statusMeta.label}</Badge>
            </span>
            <span className="project-sub">
              {project.id} · 创建于 {formatTime(project.created_at)}
            </span>
          </span>
        </button>
        <details className="project-more-menu">
          <summary aria-label="更多操作">
            <MoreVertical size={18} />
          </summary>
          <div className="project-more-panel">
            <button type="button" onClick={() => go(`#/projects/${project.id}`)}>
              <Eye size={15} />
              打开项目
            </button>
            {project.status === "active" && (
              <button type="button" onClick={onStop}>
                <Square size={15} />
                停止项目
              </button>
            )}
            {project.status === "stopped" && (
              <button type="button" onClick={onStart}>
                <Play size={15} />
                继续运行
              </button>
            )}
            {project.status === "completed" && (
              <button type="button" onClick={onReopen}>
                <RefreshCw size={15} />
                重新打开
              </button>
            )}
            <button type="button" className="danger" onClick={onDelete}>
              <Trash2 size={15} />
              删除项目
            </button>
          </div>
        </details>
      </div>
      <div className="project-stats">
        <MiniStat label="事实" value={project.fact_count} />
        <MiniStat label="意图" value={project.intent_count} />
        <MiniStat label="工作中" value={project.working_intent_count} />
      </div>
      <div className="project-meta-row">
        <div>
          <span>
            <User size={14} />
            负责人
          </span>
          <strong>未设置</strong>
        </div>
        <div>
          <span>
            <CalendarDays size={14} />
            更新时间
          </span>
          <strong>{updatedAt}</strong>
        </div>
        <div>
          <span>
            <Tag size={14} />
            标签
          </span>
          <strong>未设置</strong>
        </div>
      </div>
      <ProjectCampaignPreview campaign={campaign} loading={campaignLoading} />
      {project.reason && (
        <div className="reason-strip project-reason-strip">
          <Activity size={16} />
          <span>{project.reason.worker}</span>
          <span>{project.reason.trigger}</span>
        </div>
      )}
      <div className="card-actions project-action-row">
        <button className="ghost-button compact project-action-button" type="button" onClick={() => go(`#/projects/${project.id}`)}>
          <Eye size={16} />
          打开
        </button>
        {project.status === "active" && (
          <button className="ghost-button compact warning project-action-button" type="button" onClick={onStop}>
            <Square size={16} />
            停止
          </button>
        )}
        {project.status === "stopped" && (
          <button className="ghost-button compact project-action-button" type="button" onClick={onStart}>
            <Play size={16} />
            继续
          </button>
        )}
        {project.status === "completed" && (
          <button className="ghost-button compact project-action-button" type="button" onClick={onReopen}>
            <RefreshCw size={16} />
            重新打开
          </button>
        )}
        <button className="ghost-button compact danger project-action-button" type="button" onClick={onDelete}>
          <Trash2 size={16} />
          删除
        </button>
      </div>
    </article>
  );
}

function ProjectCampaignPreview({ campaign, loading }) {
  if (loading) {
    return (
      <section className="project-briefing loading" aria-live="polite">
        <div className="project-briefing-head">
          <span className="project-briefing-kicker">主线态势</span>
          <span className="project-briefing-loading">
            <Loader2 size={13} className="spin" />
            正在汇总
          </span>
        </div>
        <p className="project-briefing-lead">正在生成当前项目的主线摘要。</p>
      </section>
    );
  }

  if (!campaign) {
    return (
      <section className="project-briefing empty">
        <div className="project-briefing-head">
          <span className="project-briefing-kicker">主线态势</span>
          <Badge tone="muted">暂不可用</Badge>
        </div>
        <p className="project-briefing-lead">当前项目还没有形成稳定的项目级主线摘要。</p>
      </section>
    );
  }

  const goalMeta = campaignGoalMeta(campaign.goal_status);

  return (
    <section className="project-briefing">
      <div className="project-briefing-head">
        <span className="project-briefing-kicker">主线态势</span>
        <div className="project-briefing-badges">
          <Badge tone={goalMeta.tone}>{goalMeta.label}</Badge>
          {campaign.counts.high_value_vulnerabilities > 0 && (
            <span className="project-briefing-chip">{campaign.counts.high_value_vulnerabilities} 个高价值</span>
          )}
        </div>
      </div>
      <p className="project-briefing-lead" title={campaign.lead}>
        {campaign.lead}
      </p>
      <div className="project-briefing-meta">
        <span>开放意图 {campaign.counts.open_intents}</span>
        <span>阻塞 {campaign.blockers.length}</span>
        <span>下一步 {campaign.next_steps.length}</span>
      </div>
    </section>
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
  const [campaign, setCampaign] = useState(null);
  const [timeline, setTimeline] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [tab, setTab] = useState("details");
  const [modal, setModal] = useState(null);
  const [layout, setLayout] = useState("dagre_tb");

  const load = useCallback(async () => {
    try {
      const [project, events, synthesis] = await Promise.all([
        apiRequest(`/projects/${projectId}`),
        apiRequest(`/api/projects/${projectId}/timeline`).catch(() => []),
        apiRequest(`/api/projects/${projectId}/campaign`).catch(() => null),
      ]);
      setDetail(project);
      setTimeline(events);
      setCampaign(synthesis);
    } catch (error) {
      setCampaign(null);
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
              <option value="dagre_tb">Dagre ↓</option>
              <option value="dagre_lr">Dagre →</option>
              <option value="klay_tb">Klay ↓</option>
              <option value="klay_lr">Klay →</option>
              <option value="elk_tb">ELK ↓</option>
              <option value="elk_lr">ELK →</option>
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
          campaign={campaign}
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
    const isBootstrapIntent = (intent) =>
      Boolean(
        intent &&
          intent.description === "bootstrap" &&
          intent.creator === "dispatcher.bootstrap" &&
          Array.isArray(intent.from) &&
          intent.from.length === 1 &&
          intent.from[0] === "origin" &&
          intent.to === null,
      );

    const estimateLabelCharWidth = (char, fontSize) => {
      if (/\s/.test(char)) return fontSize * 0.35;
      if (/[\u1100-\u115F\u2E80-\uA4CF\uAC00-\uD7A3\uF900-\uFAFF\uFE10-\uFE6F\uFF00-\uFF60\uFFE0-\uFFE6]/.test(char)) {
        return fontSize * 1.0;
      }
      return fontSize * 0.58;
    };

    const measureWrappedText = (text, maxWidth, fontSize) => {
      const content = (text || "").trim() || " ";
      const lines = [];
      let currentWidth = 0;
      let currentChars = 0;
      let maxLineWidth = 0;

      const pushLine = () => {
        if (currentChars === 0 && lines.length > 0) lines.push(0);
        else if (currentChars > 0) lines.push(currentWidth);
        maxLineWidth = Math.max(maxLineWidth, currentWidth);
        currentWidth = 0;
        currentChars = 0;
      };

      for (const char of Array.from(content)) {
        if (char === "\n") {
          pushLine();
          continue;
        }
        const charWidth = estimateLabelCharWidth(char, fontSize);
        if (currentChars > 0 && currentWidth + charWidth > maxWidth) pushLine();
        currentWidth += charWidth;
        currentChars += 1;
      }

      pushLine();

      const lineCount = Math.max(1, lines.length);
      const lineHeight = fontSize * 1.35;
      return {
        width: Math.min(maxWidth, Math.max(fontSize * 1.6, maxLineWidth)),
        height: lineCount * lineHeight,
      };
    };

    const factNodeSize = (label, nodeType) => {
      const preset =
        nodeType === "fact"
          ? { fontSize: 10, maxTextWidth: 116, minWidth: 52, minHeight: 34, paddingX: 10, paddingY: 10 }
          : { fontSize: 11, maxTextWidth: 92, minWidth: 58, minHeight: 38, paddingX: 10, paddingY: 10 };
      const measured = measureWrappedText(label, preset.maxTextWidth, preset.fontSize);
      return {
        width: Math.max(preset.minWidth, Math.ceil(measured.width + preset.paddingX * 2)),
        height: Math.max(preset.minHeight, Math.ceil(measured.height + preset.paddingY * 2)),
      };
    };

    const summarizeFactLabel = (fact) => {
      if (fact.id === "origin") return "Origin";
      if (fact.id === "goal") return "Goal";
      const normalized = String(fact.description || "").replace(/\s+/g, " ").trim();
      const chars = Array.from(normalized);
      if (chars.length <= 24) return normalized || fact.id;
      return `${chars.slice(0, 24).join("")}…`;
    };

    const openIntentNodeType = (intent) => {
      if (isBootstrapIntent(intent)) return intent.worker ? "bootstrap_running" : "bootstrap_pending";
      return intent.worker ? "in_progress" : "unclaimed";
    };

    const openIntentNodeLabel = (intent) => (isBootstrapIntent(intent) ? "Bootstrap" : "?");

    const openIntentNodeSize = (intent) => {
      if (isBootstrapIntent(intent)) return { width: 82, height: 30 };
      return { width: 22, height: 22 };
    };

    const nodes = detail.facts.map((fact) => {
      const nodeType = fact.id === "origin" ? "origin" : fact.id === "goal" ? "goal" : "fact";
      const label = summarizeFactLabel(fact);
      const size = factNodeSize(label, nodeType);
      return {
        data: {
          id: fact.id,
          label,
          description: fact.description,
          nodeType,
          width: size.width,
          height: size.height,
        },
      };
    });

    const edges = [];
    detail.intents.forEach((intent) => {
      const label = intent.description || "";
      (intent.from || []).forEach((source) => {
        if (intent.to) {
          edges.push({
            data: {
              id: `${intent.id}_${source}`,
              source,
              target: intent.to,
              intentId: intent.id,
              label,
              status: "concluded",
            },
          });
          return;
        }

        const phId = `_ph_${intent.id}`;
        if (!nodes.some((node) => node.data.id === phId)) {
          const nodeType = openIntentNodeType(intent);
          const nodeSize = openIntentNodeSize(intent);
          nodes.push({
            data: {
              id: phId,
              label: openIntentNodeLabel(intent),
              description: intent.description,
              nodeType,
              intentId: intent.id,
              width: nodeSize.width,
              height: nodeSize.height,
            },
          });
        }

        edges.push({
          data: {
            id: `${intent.id}_${source}`,
            source,
            target: phId,
            intentId: intent.id,
            label,
            status: openIntentNodeType(intent),
          },
        });
      }
      );

      if (!intent.to && isBootstrapIntent(intent)) {
        edges.push({
          data: {
            id: `${intent.id}_goal`,
            source: `_ph_${intent.id}`,
            target: "goal",
            intentId: intent.id,
            label: "",
            status: openIntentNodeType(intent),
            edgeType: "bootstrap_scope",
          },
        });
      }
    });

    return [...nodes, ...edges];
  }, [detail]);

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      wheelSensitivity: 0.16,
      minZoom: 0.18,
      maxZoom: 3.5,
      boxSelectionEnabled: false,
      style: [
        {
          selector: 'node[nodeType="origin"]',
          style: {
            label: "data(label)",
            shape: "round-rectangle",
            "background-color": "#14b8a6",
            color: "#fff",
            "font-family": "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
            "font-size": 11,
            "font-weight": "bold",
            "text-wrap": "wrap",
            "text-max-width": 92,
            "text-overflow-wrap": "anywhere",
            "text-valign": "center",
            "text-halign": "center",
            width: "data(width)",
            height: "data(height)",
            "border-width": 0,
          },
        },
        {
          selector: 'node[nodeType="goal"]',
          style: {
            label: "data(label)",
            shape: "round-rectangle",
            "background-color": "#f43f5e",
            color: "#fff",
            "font-family": "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
            "font-size": 11,
            "font-weight": "bold",
            "text-wrap": "wrap",
            "text-max-width": 92,
            "text-overflow-wrap": "anywhere",
            "text-valign": "center",
            "text-halign": "center",
            width: "data(width)",
            height: "data(height)",
            "border-width": 0,
          },
        },
        {
          selector: 'node[nodeType="fact"]',
          style: {
            label: "data(label)",
            shape: "round-rectangle",
            "background-color": "#6366f1",
            color: "#fff",
            "font-family": "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
            "font-size": 10,
            "font-weight": "bold",
            "text-wrap": "wrap",
            "text-max-width": 116,
            "text-overflow-wrap": "anywhere",
            "text-valign": "center",
            "text-halign": "center",
            width: "data(width)",
            height: "data(height)",
            "border-width": 0,
          },
        },
        {
          selector: 'node[nodeType="in_progress"]',
          style: {
            label: "?",
            shape: "ellipse",
            "background-color": "#f59e0b",
            "background-opacity": 0.8,
            color: "#fff",
            "font-family": "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
            "font-size": 11,
            "font-weight": "bold",
            width: 22,
            height: 22,
            "border-width": 2,
            "border-color": "#d97706",
          },
        },
        {
          selector: 'node[nodeType="unclaimed"]',
          style: {
            label: "?",
            shape: "ellipse",
            "background-color": "#cbd5e1",
            "background-opacity": 0.5,
            color: "#94a3b8",
            "font-family": "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
            "font-size": 11,
            "font-weight": "bold",
            width: 20,
            height: 20,
            "border-width": 1.5,
            "border-color": "#94a3b8",
            "border-style": "dashed",
          },
        },
        {
          selector: 'node[nodeType="bootstrap_pending"]',
          style: {
            "background-color": "#fff7ed",
            "background-opacity": 0.96,
            label: "data(label)",
            "border-color": "#fdba74",
            color: "#c2410c",
            shape: "round-rectangle",
            "font-family": "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
            "font-size": 10,
            "font-weight": "bold",
            width: "data(width)",
            height: "data(height)",
            "border-width": 1.5,
            "border-style": "dashed",
            "text-wrap": "wrap",
            "text-max-width": 70,
            "text-valign": "center",
            "text-halign": "center",
          },
        },
        {
          selector: 'node[nodeType="bootstrap_running"]',
          style: {
            "background-color": "#fb923c",
            "background-opacity": 0.96,
            label: "data(label)",
            "border-color": "#ea580c",
            color: "#fff7ed",
            shape: "round-rectangle",
            "font-family": "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
            "font-size": 10,
            "font-weight": "bold",
            width: "data(width)",
            height: "data(height)",
            "border-width": 2,
            "text-wrap": "wrap",
            "text-max-width": 70,
            "text-valign": "center",
            "text-halign": "center",
          },
        },
        {
          selector: 'edge[status="concluded"]',
          style: {
            width: 2,
            "line-color": "#6ee7b7",
            "target-arrow-color": "#6ee7b7",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            label: "data(label)",
            "font-size": 7,
            color: "#94a3b8",
            "text-rotation": "autorotate",
            "text-margin-y": -9,
            "text-max-width": 80,
            "text-wrap": "ellipsis",
            "text-background-color": "#f8fafc",
            "text-background-opacity": 0.85,
            "text-background-padding": 2,
            "text-events": "yes",
            "arrow-scale": 0.9,
          },
        },
        {
          selector: 'edge[status="in_progress"]',
          style: {
            width: 2,
            "line-color": "#fbbf24",
            "line-style": "dashed",
            "line-dash-pattern": [8, 4],
            "line-dash-offset": 0,
            "target-arrow-color": "#fbbf24",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "arrow-scale": 0.9,
            label: "data(label)",
            "font-size": 7,
            color: "#b45309",
            "text-rotation": "autorotate",
            "text-margin-y": -9,
            "text-max-width": 80,
            "text-wrap": "ellipsis",
            "text-background-color": "#fffbeb",
            "text-background-opacity": 0.85,
            "text-background-padding": 2,
            "text-events": "yes",
          },
        },
        {
          selector: 'edge[status="unclaimed"]',
          style: {
            width: 1.5,
            "line-color": "#cbd5e1",
            "line-style": "dashed",
            "line-dash-pattern": [5, 5],
            "target-arrow-color": "#cbd5e1",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            label: "data(label)",
            "font-size": 7,
            color: "#94a3b8",
            "text-rotation": "autorotate",
            "text-margin-y": -9,
            "text-max-width": 80,
            "text-wrap": "ellipsis",
            "text-background-color": "#f8fafc",
            "text-background-opacity": 0.85,
            "text-background-padding": 2,
            "text-events": "yes",
            "arrow-scale": 0.7,
          },
        },
        {
          selector: 'edge[status="bootstrap_pending"]',
          style: {
            width: 2,
            "line-color": "#fdba74",
            "line-style": "dashed",
            "line-dash-pattern": [8, 4],
            "line-dash-offset": 0,
            "target-arrow-color": "#fdba74",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            label: "data(label)",
            "font-size": 7,
            color: "#c2410c",
            "text-rotation": "autorotate",
            "text-margin-y": -9,
            "text-max-width": 88,
            "text-wrap": "ellipsis",
            "text-background-color": "#fff7ed",
            "text-background-opacity": 0.92,
            "text-background-padding": 2,
            "text-events": "yes",
            "arrow-scale": 0.85,
          },
        },
        {
          selector: 'edge[status="bootstrap_running"]',
          style: {
            width: 2.5,
            "line-color": "#fb923c",
            "line-style": "dashed",
            "line-dash-pattern": [10, 4],
            "line-dash-offset": 0,
            "target-arrow-color": "#fb923c",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            label: "data(label)",
            "font-size": 7,
            color: "#c2410c",
            "text-rotation": "autorotate",
            "text-margin-y": -9,
            "text-max-width": 88,
            "text-wrap": "ellipsis",
            "text-background-color": "#fff7ed",
            "text-background-opacity": 0.92,
            "text-background-padding": 2,
            "text-events": "yes",
            "arrow-scale": 0.95,
          },
        },
        {
          selector: 'edge[edgeType="bootstrap_scope"]',
          style: {
            label: "",
            width: 1.8,
            "curve-style": "bezier",
            "line-style": "dotted",
            "line-dash-pattern": [2, 5],
            "target-arrow-shape": "triangle-backcurve",
            "arrow-scale": 0.75,
            "target-distance-from-node": 2,
          },
        },
        { selector: ".highlight", style: { "z-index": 999 } },
        { selector: "edge.highlight", style: { "z-index": 999 } },
        {
          selector: "node.focus",
          style: {
            "border-width": 3,
            "border-color": "#312e81",
            "border-opacity": 0.95,
            "z-index": 1000,
          },
        },
        {
          selector: "edge.focus",
          style: {
            "z-index": 1000,
            "overlay-color": "#93c5fd",
            "overlay-opacity": 0.22,
            "overlay-padding": 5,
          },
        },
        {
          selector: "node.selected-fact",
          style: {
            "border-width": 0,
            "underlay-color": "#93c5fd",
            "underlay-padding": 8,
            "underlay-opacity": 0.28,
            "z-index": 1001,
          },
        },
        { selector: ".faded", style: { opacity: 0.5 } },
      ],
    });
    cyRef.current = cy;
    cy.on("tap", "node", (event) => {
      const node = event.target;
      const intentId = node.data("intentId");
      if (intentId) {
        onSelect({ type: "intent", id: intentId });
        return;
      }
      onSelect({ type: "fact", id: node.id() });
    });
    cy.on("tap", "edge", (event) => {
      const intentId = event.target.data("intentId");
      if (intentId) onSelect({ type: "intent", id: intentId });
    });
    cy.on("tap", (event) => {
      if (event.target === cy) onSelect(null);
    });
    const resizeObserver =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => {
            cy.resize();
            if (cy.elements().length > 0) {
              cy.fit(cy.elements(), 92);
            }
          });
    resizeObserver?.observe(containerRef.current);
    return () => {
      resizeObserver?.disconnect();
      cy.destroy();
      cyRef.current = null;
    };
  }, [elements, detail.facts, onSelect]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const direction = layout.endsWith("_lr") ? "LR" : "TB";
    const engine = layout.startsWith("elk") ? "elk" : layout.startsWith("klay") ? "klay" : "dagre";
    let options;

    if (engine === "elk") {
      options = {
        name: "elk",
        fit: true,
        padding: 50,
        animate: true,
        animationDuration: 350,
        animationEasing: "ease-in-out-cubic",
        elk: {
          algorithm: "layered",
          "elk.direction": direction === "TB" ? "DOWN" : "RIGHT",
          "elk.aspectRatio": "1.5",
          "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
          "elk.spacing.nodeNode": "50",
          "elk.layered.spacing.nodeNodeBetweenLayers": "80",
          "elk.spacing.edgeNode": "25",
          "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
          "elk.layered.nodePlacement.bk.fixedAlignment": "BALANCED",
        },
      };
    } else if (engine === "klay") {
      options = {
        name: "klay",
        fit: true,
        padding: 50,
        animate: true,
        animationDuration: 400,
        animationEasing: "ease-in-out-cubic",
        klay: {
          direction: direction === "TB" ? "DOWN" : "RIGHT",
          edgeRouting: "POLYLINE",
          crossingMinimization: "LAYER_SWEEP",
          nodeLayering: "NETWORK_SIMPLEX",
          nodePlacement: "BRANDES_KOEPF",
          separateConnectedComponents: true,
          spacing: direction === "LR" ? 52 : 40,
          inLayerSpacingFactor: direction === "LR" ? 1.15 : 1.0,
          thoroughness: 8,
        },
      };
    } else {
      options = {
        name: "dagre",
        rankDir: direction,
        nodeSep: 60,
        rankSep: 80,
        padding: 50,
        fit: true,
        animate: true,
        animationDuration: 400,
        animationEasing: "ease-in-out-cubic",
      };
    }
    cy.layout(options).run();
    window.setTimeout(() => {
      if (!cyRef.current) return;
      cyRef.current.fit(cyRef.current.elements(), 50);
    }, 80);
  }, [layout, elements]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const isBootstrapIntent = (intent) =>
      Boolean(
        intent &&
          intent.description === "bootstrap" &&
          intent.creator === "dispatcher.bootstrap" &&
          Array.isArray(intent.from) &&
          intent.from.length === 1 &&
          intent.from[0] === "origin" &&
          intent.to === null,
      );

    const collectIntentElements = (intent, nodeIds, edgeIds) => {
      if (intent.to) nodeIds.add(intent.to);
      else nodeIds.add(`_ph_${intent.id}`);
      for (const sourceId of intent.from || []) {
        nodeIds.add(sourceId);
        edgeIds.add(`${intent.id}_${sourceId}`);
      }
      if (isBootstrapIntent(intent)) {
        nodeIds.add("goal");
        edgeIds.add(`${intent.id}_goal`);
      }
    };

    const collectFactLineage = (factId) => {
      const upstreamFacts = new Set();
      const upstreamIntents = new Set();

      const walkFactUpstream = (id) => {
        if (upstreamFacts.has(id)) return;
        upstreamFacts.add(id);
        detail.intents.forEach((intent) => {
          if (intent.to === id) walkIntentUpstream(intent.id);
        });
      };

      const walkIntentUpstream = (intentId) => {
        if (upstreamIntents.has(intentId)) return;
        upstreamIntents.add(intentId);
        const intent = detail.intents.find((item) => item.id === intentId);
        if (!intent) return;
        (intent.from || []).forEach((sourceId) => walkFactUpstream(sourceId));
      };

      walkFactUpstream(factId);
      return { upstreamFacts, upstreamIntents };
    };

    cy.elements().removeClass("highlight focus faded selected-fact");

    if (!selected?.id) return;

    if (selected.type === "intent") {
      const intent = detail.intents.find((item) => item.id === selected.id);
      if (!intent) return;
      const nodeIds = new Set();
      const edgeIds = new Set();
      collectIntentElements(intent, nodeIds, edgeIds);
      const highlightNodes = cy.nodes().filter((node) => nodeIds.has(node.id()));
      const focusEdges = cy.edges().filter((edge) => edgeIds.has(edge.id()));
      highlightNodes.addClass("highlight");
      focusEdges.addClass("focus");
      const visible = highlightNodes.add(focusEdges);
      cy.elements().not(visible).addClass("faded");
      return;
    }

    if (selected.type === "fact") {
      const { upstreamFacts, upstreamIntents } = collectFactLineage(selected.id);
      const nodeIds = new Set(upstreamFacts);
      const edgeIds = new Set();
      upstreamIntents.forEach((intentId) => {
        const intent = detail.intents.find((item) => item.id === intentId);
        if (intent) collectIntentElements(intent, nodeIds, edgeIds);
      });

      const highlightNodes = cy.nodes().filter((node) => nodeIds.has(node.id()));
      const highlightEdges = cy.edges().filter((edge) => edgeIds.has(edge.id()));
      const focusNode = cy.getElementById(selected.id);

      highlightNodes.addClass("highlight");
      highlightEdges.addClass("highlight");
      focusNode.addClass("focus selected-fact");

      const visible = highlightNodes.add(highlightEdges).add(focusNode);
      cy.elements().not(visible).addClass("faded");
    }
  }, [detail, selected]);

  return <div className="graph-canvas" ref={containerRef} />;
}

function InspectorListItem({ title, description, meta, onClick }) {
  const Comp = onClick ? "button" : "article";
  const props = onClick
    ? {
        type: "button",
        onClick,
      }
    : {};
  return (
    <Comp className={cn("timeline-item", onClick && "clickable")} {...props}>
      <div className="timeline-item-head">
        <strong className="timeline-item-title" title={title}>
          {title}
        </strong>
      </div>
      <p title={description}>{description}</p>
      <small title={meta}>{meta}</small>
    </Comp>
  );
}

function Inspector({ detail, campaign, selected, setSelected, tab, setTab, timeline, onRefresh, runAction }) {
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
            <span className="inspector-tab-label">{label}</span>
            {count !== null && <span className="inspector-tab-badge">{count}</span>}
          </button>
        ))}
      </div>
      <div className="inspector-body">
        {tab === "details" && (
          <>
            {!selected && (
              <>
                <div className="detail-card">
                  <span>项目</span>
                  <h3>{detail.project.title}</h3>
                  <p>{detail.project.id}</p>
                  <div className="detail-grid">
                    <MiniStat label="事实" value={facts.length} />
                    <MiniStat label="意图" value={intents.length} />
                  </div>
                </div>
                <ProjectCampaignPanel campaign={campaign} />
              </>
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
                <InspectorListItem
                  key={hint.id}
                  title={hint.id}
                  description={hint.content}
                  meta={`${hint.creator} · ${formatTime(hint.created_at)}`}
                />
              ))
            )}
          </div>
        )}
        {tab === "logs" && (
          <div className="timeline-list">
            {intents.map((item) => (
              <InspectorListItem
                key={item.id}
                title={item.id}
                description={item.description}
                meta={item.worker || item.creator}
                onClick={() => {
                  setSelected({ type: "intent", id: item.id });
                  setTab("details");
                }}
              />
            ))}
          </div>
        )}
        {tab === "timeline" && (
          <div className="timeline-list">
            {timeline.length === 0 ? (
              <EmptyState title="暂无时间线" />
            ) : (
              timeline.map((event) => (
                <InspectorListItem
                  key={event.id}
                  title={event.event_type}
                  description={event.description}
                  meta={`${formatTime(event.timestamp)}${event.actor ? ` · ${event.actor}` : ""}`}
                  onClick={() => event.node_id && setSelected({ type: event.node_id.startsWith("i") ? "intent" : "fact", id: event.node_id })}
                />
              ))
            )}
          </div>
        )}
      </div>
    </aside>
  );
}

function ProjectCampaignPanel({ campaign }) {
  if (!campaign) {
    return (
      <div className="detail-card project-campaign-card project-campaign-empty">
        <div className="project-campaign-head">
          <div>
            <span>项目级研判</span>
            <h3>摘要暂不可用</h3>
          </div>
        </div>
        <p>当前项目还没有形成稳定的项目级主线摘要，或摘要接口暂时不可用。</p>
      </div>
    );
  }

  const goalMeta = campaignGoalMeta(campaign.goal_status);
  const projectMeta = projectStatusBadgeMeta(campaign.project_status);

  return (
    <div className="detail-card project-campaign-card">
      <div className="project-campaign-head">
        <div>
          <span>项目级研判</span>
          <h3>当前主线</h3>
        </div>
        <div className="project-campaign-badges">
          <Badge tone={goalMeta.tone}>{goalMeta.label}</Badge>
          <Badge tone={projectMeta.tone}>{projectMeta.label}</Badge>
        </div>
      </div>
      <p className="project-campaign-lead">{campaign.lead}</p>
      <p className="project-campaign-summary">{campaign.summary}</p>
      <div className="detail-grid project-campaign-stats">
        <MiniStat label="高价值漏洞" value={campaign.counts.high_value_vulnerabilities} />
        <MiniStat label="开放意图" value={campaign.counts.open_intents} />
        <MiniStat label="阻塞项" value={campaign.blockers.length} />
        <MiniStat label="下一步" value={campaign.next_steps.length} />
      </div>
      {!!campaign.top_findings?.length && (
        <CampaignListBlock title="强信号">
          {campaign.top_findings.map((finding) => (
            <article key={`${finding.source_type}-${finding.source_id}`} className="project-campaign-item">
              <div className="project-campaign-item-head">
                <strong title={finding.title}>{finding.title}</strong>
                <div className="project-campaign-inline">
                  {finding.severity && <Badge tone={findingBadgeTone(finding.severity)}>{severityLabel(finding.severity)}</Badge>}
                  <Badge tone={findingConfidenceTone(finding.confidence)}>{findingConfidenceLabel(finding.confidence)}</Badge>
                </div>
              </div>
              <p title={finding.summary}>{finding.summary}</p>
              <small>
                {finding.source_id} · {findingSourceLabel(finding.source_type)}
              </small>
            </article>
          ))}
        </CampaignListBlock>
      )}
      {!!campaign.open_intents?.length && (
        <CampaignListBlock title="待推进意图">
          {campaign.open_intents.map((item, index) => (
            <article key={`open-intent-${index}`} className="project-campaign-item">
              <strong>{item}</strong>
            </article>
          ))}
        </CampaignListBlock>
      )}
      {!!campaign.blockers?.length && (
        <CampaignListBlock title="当前阻塞">
          {campaign.blockers.map((item, index) => (
            <article key={`blocker-${index}`} className="project-campaign-item blocker">
              <strong>{item}</strong>
            </article>
          ))}
        </CampaignListBlock>
      )}
      {!!campaign.next_steps?.length && (
        <CampaignListBlock title="建议下一步">
          {campaign.next_steps.map((item, index) => (
            <article key={`next-step-${index}`} className="project-campaign-item next-step">
              <strong>{item}</strong>
            </article>
          ))}
        </CampaignListBlock>
      )}
    </div>
  );
}

function CampaignListBlock({ title, children }) {
  return (
    <section className="project-campaign-block">
      <header>
        <h4>{title}</h4>
      </header>
      <div className="project-campaign-list">{children}</div>
    </section>
  );
}

function campaignGoalMeta(status) {
  if (status === "achieved") return { label: "目标已达成", tone: "success" };
  if (status === "in_progress") return { label: "主线推进中", tone: "warning" };
  return { label: "当前受阻", tone: "danger" };
}

function projectStatusBadgeMeta(status) {
  if (status === "completed") return { label: "项目已完成", tone: "info" };
  if (status === "stopped") return { label: "项目已停止", tone: "warning" };
  return { label: "项目运行中", tone: "success" };
}

function findingBadgeTone(severity) {
  return SEVERITY_META[severity]?.tone || "muted";
}

function severityLabel(severity) {
  return SEVERITY_META[severity]?.label || severity || "-";
}

function findingConfidenceTone(confidence) {
  if (confidence === "confirmed") return "success";
  if (confidence === "supported") return "info";
  return "warning";
}

function findingConfidenceLabel(confidence) {
  if (confidence === "confirmed") return "已确认";
  if (confidence === "supported") return "有支撑";
  return "待核实";
}

function findingSourceLabel(sourceType) {
  if (sourceType === "vulnerability") return "漏洞";
  if (sourceType === "fact") return "事实";
  return "提示";
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
  const [filters, setFilters] = useState(() => {
    const fallback = { severity: "", project_id: "", status: "", search: route?.search || "", date_from: "", date_to: "" };
    if (typeof window === "undefined") return fallback;
    try {
      const raw = window.localStorage.getItem(VULN_FILTERS_STORAGE_KEY);
      const saved = raw ? JSON.parse(raw) : {};
      return { ...fallback, ...saved, search: route?.search || saved?.search || "" };
    } catch {
      return fallback;
    }
  });
  const [detailVulnId, setDetailVulnId] = useState(null);
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

  const updateSelectedStatus = async (status) => {
    if (!selectedIds.length) {
      setToast({ type: "warning", message: "请先选择要处理的漏洞" });
      return;
    }
    const label = status === "ignored" ? "已批量标记为忽略" : "已批量恢复为已确认";
    await runAction(label, () =>
      apiRequest("/api/vulnerabilities/batch/status", {
        method: "POST",
        body: { ids: selectedIds, status },
      }),
    );
    setSelectedIds([]);
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
  const pageIds = useMemo(() => pagedVulnerabilities.map((item) => item.id), [pagedVulnerabilities]);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.includes(id));
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.includes(id));
  const detailVuln = useMemo(
    () => vulnerabilities.find((item) => item.id === detailVulnId) || null,
    [detailVulnId, vulnerabilities],
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(VULN_FILTERS_STORAGE_KEY, JSON.stringify(filters));
  }, [filters]);

  useEffect(() => {
    setSelectedIds((prev) => prev.filter((id) => visibleIds.includes(id)));
  }, [visibleIds]);

  useEffect(() => {
    if (detailVulnId && !vulnerabilities.some((item) => item.id === detailVulnId)) {
      setDetailVulnId(null);
    }
  }, [detailVulnId, vulnerabilities]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const pendingId = window.sessionStorage.getItem(VULN_FOCUS_STORAGE_KEY);
    if (!pendingId || !visibleVulnerabilities.length) return;
    const focusIndex = visibleVulnerabilities.findIndex((item) => String(item.id) === pendingId);
    if (focusIndex === -1) return;
    setPage(Math.floor(focusIndex / pageSize) + 1);
    setDetailVulnId(pendingId);
    window.sessionStorage.removeItem(VULN_FOCUS_STORAGE_KEY);
  }, [pageSize, visibleVulnerabilities]);

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

  const togglePageSelected = () => {
    setSelectedIds((prev) =>
      allPageSelected ? prev.filter((id) => !pageIds.includes(id)) : Array.from(new Set([...prev, ...pageIds])),
    );
  };

  const toggleVisibleSelected = () => {
    setSelectedIds((prev) =>
      allVisibleSelected ? prev.filter((id) => !visibleIds.includes(id)) : Array.from(new Set([...prev, ...visibleIds])),
    );
  };

  const openDetail = (id) => setDetailVulnId(id);

  const closeDetail = () => setDetailVulnId(null);

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
            className="primary-outline compact report-export-button"
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
        <div className="report-summary-grid">
          <VulnerabilitySummaryCard icon={ShieldAlert} label="严重漏洞" value={visibleSummary.critical || 0} tone="critical" />
          <VulnerabilitySummaryCard icon={AlertTriangle} label="高危漏洞" value={visibleSummary.high || 0} tone="high" />
          <VulnerabilitySummaryCard icon={AlertCircle} label="中危漏洞" value={visibleSummary.medium || 0} tone="medium" />
          <VulnerabilitySummaryCard icon={ShieldCheck} label="低危漏洞" value={visibleSummary.low || 0} tone="low" />
          <VulnerabilitySummaryCard icon={CheckCheck} label="已确认" value={statusDistribution.confirmed || 0} tone="success" />
        </div>
        <div className="report-filter-bar">
          <label className="filter-search">
            <Search size={15} />
            <input
              value={filters.search}
              onChange={(event) => setFilters({ ...filters, search: event.target.value })}
              placeholder="搜索漏洞标题、编号、项目、标签..."
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
              <span className="date-range-separator">
                <ChevronRight size={14} />
              </span>
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
          <article className="vuln-table-card vuln-table-card-full">
            <header className="vuln-table-title">
              <div>
                <h2>漏洞列表</h2>
                <p>点击单条漏洞查看证据、过程和证明数据包</p>
              </div>
              <div className="button-row">
                <button className="ghost-button compact" type="button" disabled={!pageIds.length} onClick={togglePageSelected}>
                  {allPageSelected ? "取消本页" : "选择本页"}
                </button>
                <button className="ghost-button compact" type="button" disabled={!visibleIds.length} onClick={toggleVisibleSelected}>
                  {allVisibleSelected ? "取消结果集" : "全选结果集"}
                </button>
              </div>
            </header>
            {selectedIds.length > 0 && (
              <div className="vuln-bulk-bar">
                <div className="vuln-bulk-summary">
                  <strong>已选 {selectedIds.length} 条</strong>
                  <span>{allVisibleSelected ? "当前筛选结果已全选" : "可继续翻页追加选择"}</span>
                </div>
                <div className="button-row vuln-bulk-actions">
                  <button className="ghost-button compact" type="button" onClick={() => updateSelectedStatus("confirmed")}>
                    <CheckCircle2 size={15} />
                    批量确认
                  </button>
                  <button className="ghost-button compact warning" type="button" onClick={() => updateSelectedStatus("ignored")}>
                    <X size={15} />
                    批量忽略
                  </button>
                  <button
                    className="primary-outline compact"
                    type="button"
                    onClick={() => exportMd({ selected: selectedIds, title: "rabbit-vulnerabilities-batch" })}
                  >
                    <Download size={15} />
                    批量导出
                  </button>
                  <button className="ghost-button compact" type="button" onClick={() => setSelectedIds([])}>
                    清空选择
                  </button>
                </div>
              </div>
            )}
            <div className="vuln-table-scroll">
              <div className="vuln-table-head">
                <span />
                <span>漏洞名称</span>
                <span>所属项目</span>
                <span>严重程度</span>
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
                    active={detailVulnId === vuln.id}
                    onSelect={() => toggleSelected(vuln.id)}
                    onOpen={() => openDetail(vuln.id)}
                    onExport={() => exportMd({ selected: [vuln.id], title: `${vuln.project_id}-${vuln.fact_id}` })}
                    onStatusChange={(status) => updateVulnerabilityStatus(vuln, status)}
                  />
                ))}
              </div>
            </div>
            <footer className="vuln-table-footer">
              <span className="vuln-table-footer-meta">
                共 {filteredVulnCount} 条{filteredVulnCount > 0 ? ` · 第 ${pageStart}-${pageEnd} 条` : ""}
              </span>
              <div className="vuln-table-footer-controls">
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
              </div>
            </footer>
          </article>
        )}
      </section>
      {detailVuln && (
        <VulnerabilityDrawer
          vuln={detailVuln}
          onClose={closeDetail}
          onExport={() => exportMd({ selected: [detailVuln.id], title: `${detailVuln.project_id}-${detailVuln.fact_id}` })}
          onStatusChange={(status) => updateVulnerabilityStatus(detailVuln, status)}
        />
      )}
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

function SeverityTopList({ items, onSelect }) {
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
              <button
                key={`top-${item.id}`}
                className={cn("severity-top-item", onSelect && "linked")}
                type="button"
                onClick={() => onSelect?.(item)}
              >
                <span className="severity-top-rank">{index + 1}</span>
                <span className={cn("status-badge", meta.tone)}>{meta.label}</span>
                <strong title={item.title}>{clampText(item.title, 30)}</strong>
                <code>{item.fact_id}</code>
              </button>
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

function VulnerabilityItem({ vuln, selected, active, onSelect, onOpen, onExport, onStatusChange }) {
  const meta = SEVERITY_META[vuln.severity] || SEVERITY_META.low;
  const ignored = vuln.status === "ignored";
  return (
    <article className={cn("vuln-table-item", ignored && "ignored", active && "active")}>
      <div
        className="vuln-table-row"
        role="button"
        tabIndex={0}
        onClick={onOpen}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onOpen();
          }
        }}
      >
        <label className="vuln-select" onClick={(event) => event.stopPropagation()}>
          <input type="checkbox" checked={selected} onChange={onSelect} />
        </label>
        <div className="vuln-name-cell">
          <div className="vuln-meta">
            <span>{vuln.fact_id}</span>
          </div>
          <button
            className="vuln-title-button"
            type="button"
            title={vuln.title}
            onClick={(event) => {
              event.stopPropagation();
              onOpen();
            }}
          >
            <span className="vuln-title-text">{vuln.title}</span>
          </button>
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
        <div className="button-row vuln-table-actions" onClick={(event) => event.stopPropagation()}>
          <button className="table-inline-action detail" type="button" onClick={onOpen} title="查看详情" aria-label="查看详情">
            <Eye size={14} />
            详情
          </button>
          <details className="table-row-menu" onClick={(event) => event.stopPropagation()}>
            <summary className="table-inline-action" aria-label="更多操作">
              <MoreVertical size={14} />
              更多
            </summary>
            <div className="table-row-menu-panel">
              <button type="button" onClick={onExport}>
                <Download size={14} />
                导出
              </button>
              <button
                type="button"
                onClick={() => onStatusChange(ignored ? "confirmed" : "ignored")}
              >
                {ignored ? <CheckCircle2 size={14} /> : <X size={14} />}
                {ignored ? "恢复确认" : "设为忽略"}
              </button>
            </div>
          </details>
        </div>
      </div>
    </article>
  );
}

function VulnerabilityDrawer({ vuln, onClose, onExport, onStatusChange }) {
  const meta = SEVERITY_META[vuln.severity] || SEVERITY_META.low;
  const ignored = vuln.status === "ignored";
  const [reportDraft, setReportDraft] = useState(null);
  const [reportLoading, setReportLoading] = useState(true);
  const [reportRefreshing, setReportRefreshing] = useState(false);
  const [reportError, setReportError] = useState("");
  const [reportHint, setReportHint] = useState("");

  const regenerateReportDraft = useCallback(async () => {
    setReportRefreshing(true);
    setReportHint("正在使用 chat5.4 重生成交付版草稿…");
    try {
      const payload = await apiRequest(
        `/api/vulnerabilities/${encodeURIComponent(vuln.id)}/report?use_model=1`,
      );
      if (payload?.composer_source === "model") {
        setReportDraft(payload);
        setReportError("");
        setReportHint(`已切换到 ${payload.composer_model || "chat5.4"} 模型版`);
      } else {
        setReportHint("chat5.4 暂未返回，当前保留模板版草稿。");
      }
    } catch (error) {
      if (!reportDraft) {
        setReportError(error.message || "报告草稿生成失败");
      }
      setReportHint("chat5.4 暂时不可用，当前保留模板版草稿。");
    } finally {
      setReportRefreshing(false);
    }
  }, [reportDraft, vuln.id]);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    setReportLoading(true);
    setReportRefreshing(false);
    setReportError("");
    setReportHint("");

    const load = async () => {
      try {
        const payload = await apiRequest(`/api/vulnerabilities/${encodeURIComponent(vuln.id)}/report`);
        if (cancelled) return;
        setReportDraft(payload);
        setReportHint("模板草稿已就绪，正在请求 chat5.4 增强版…");
      } catch (error) {
        if (cancelled) return;
        setReportDraft(null);
        setReportError(error.message || "报告草稿生成失败");
        setReportLoading(false);
        return;
      }

      if (cancelled) return;
      setReportLoading(false);
      setReportRefreshing(true);
      try {
        const payload = await apiRequest(
          `/api/vulnerabilities/${encodeURIComponent(vuln.id)}/report?use_model=1`,
        );
        if (cancelled) return;
        if (payload?.composer_source === "model") {
          setReportDraft(payload);
          setReportHint(`已切换到 ${payload.composer_model || "chat5.4"} 模型版`);
        } else {
          setReportHint("chat5.4 暂未返回，当前展示模板版草稿。");
        }
      } catch {
        if (cancelled) return;
        setReportHint("chat5.4 暂时不可用，当前展示模板版草稿。");
      } finally {
        if (!cancelled) {
          setReportRefreshing(false);
        }
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, [vuln.id]);

  return (
    <div className="drawer-backdrop" role="presentation" onClick={onClose}>
      <aside
        className="drawer-panel vulnerability-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`漏洞详情 ${vuln.fact_id}`}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="drawer-header">
          <div className="drawer-heading">
            <div className="drawer-kicker">
              <span>{vuln.fact_id}</span>
              <Badge tone={meta.tone}>{meta.label}</Badge>
              <Badge tone={ignored ? "muted" : "success"}>{ignored ? "已忽略" : "已确认"}</Badge>
            </div>
            <h2>{vuln.title}</h2>
            <p>
              {vuln.project_name} · {vuln.project_id} · 发现于 {formatTime(vuln.discovered_at)}
            </p>
          </div>
          <button className="icon-button drawer-close" type="button" onClick={onClose} aria-label="关闭详情">
            <X size={18} />
          </button>
        </header>
        <div className="drawer-body">
          <VulnerabilityNarrativeCard
            report={reportDraft}
            loading={reportLoading}
            refreshing={reportRefreshing}
            error={reportError}
            hint={reportHint}
            onRefresh={regenerateReportDraft}
          />
          <div className="drawer-info-grid">
            <InfoBox label="所属项目" value={`${vuln.project_name} (${vuln.project_id})`} />
            <InfoBox label="确认事实" value={vuln.fact_id} />
            <InfoBox label="来源意图" value={vuln.source_intent_id || "-"} />
            <InfoBox label="工作节点" value={vuln.source_worker || "-"} />
          </div>
          <section className="drawer-section">
            <h4>证明说明</h4>
            <p className="soft-box">{vuln.description || "未记录"}</p>
          </section>
          <section className="drawer-section">
            <h4>关键证据</h4>
            <div className="evidence-list">
              {(vuln.evidence?.length ? vuln.evidence : ["未记录"]).map((item, index) => (
                <p key={`${item}-${index}`}>{item}</p>
              ))}
            </div>
          </section>
          <section className="drawer-section">
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
          <section className="drawer-section">
            <h4>漏洞浮现过程</h4>
            <div className="process-list">
              {(vuln.process || []).length === 0 ? (
                <p className="soft-box">未记录漏洞浮现过程。</p>
              ) : (
                (vuln.process || []).map((step, index) => (
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
                ))
              )}
            </div>
          </section>
        </div>
        <footer className="drawer-footer">
          <button className="ghost-button compact" type="button" onClick={onClose}>
            关闭
          </button>
          <div className="drawer-footer-actions">
            <button
              className="ghost-button compact"
              type="button"
              onClick={regenerateReportDraft}
              disabled={reportLoading || reportRefreshing}
            >
              {reportRefreshing ? <Loader2 className="spin" size={16} /> : <Sparkles size={16} />}
              模型重生成
            </button>
            <button className="primary-outline compact" type="button" onClick={onExport}>
              <Download size={16} />
              导出
            </button>
            <button
              className={cn("ghost-button compact", ignored ? "success" : "warning")}
              type="button"
              onClick={() => onStatusChange(ignored ? "confirmed" : "ignored")}
            >
              {ignored ? <CheckCircle2 size={16} /> : <X size={16} />}
              {ignored ? "恢复确认" : "设为忽略"}
            </button>
          </div>
        </footer>
      </aside>
    </div>
  );
}

function VulnerabilityNarrativeCard({ report, loading, refreshing, error, hint, onRefresh }) {
  return (
    <section className="drawer-section report-draft-section">
      <div className="report-draft-head">
        <div>
          <h4>交付版报告草稿</h4>
          <p>
            {report
              ? report.composer_source === "model"
                ? `模型整理 · ${report.composer_model || "chat5.4"}`
                : refreshing
                  ? "模板已就绪 · chat5.4 增强中"
                  : "模板草稿"
              : "基于现有证据生成，不改漏洞状态"}
          </p>
          {!!hint && <small className="report-draft-hint">{hint}</small>}
        </div>
        <button
          className="ghost-button compact"
          type="button"
          onClick={onRefresh}
          disabled={loading || refreshing}
        >
          {refreshing ? <Loader2 className="spin" size={15} /> : <RefreshCw size={15} />}
          模型重生成
        </button>
      </div>
      {loading ? (
        <div className="report-draft-card report-draft-loading">
          <Loader2 className="spin" size={16} />
          <span>正在整理报告草稿…</span>
        </div>
      ) : error ? (
        <div className="report-draft-card report-draft-error">
          <strong>报告草稿暂不可用</strong>
          <p>{error}</p>
        </div>
      ) : !report ? (
        <div className="report-draft-card report-draft-error">
          <strong>报告草稿暂不可用</strong>
          <p>当前漏洞还没有形成可展示的交付版草稿。</p>
        </div>
      ) : (
        <div className="report-draft-card">
          <div className="report-draft-summary">
            <span className="report-draft-type">{report.vulnerability_type}</span>
            <p>{report.executive_summary}</p>
          </div>
          {!!report.attack_surface?.length && (
            <div className="report-draft-block">
              <h5>攻击面</h5>
              <div className="report-draft-tags">
                {report.attack_surface.map((item) => (
                  <span key={item}>{item}</span>
                ))}
              </div>
            </div>
          )}
          <div className="report-draft-block">
            <h5>漏洞证明</h5>
            <p>{report.vulnerability_proof}</p>
          </div>
          {!!report.proof_points?.length && (
            <div className="report-draft-block">
              <h5>证明要点</h5>
              <div className="report-draft-points">
                {report.proof_points.map((point) => (
                  <article key={`${point.label}-${point.content}`}>
                    <strong>{point.label}</strong>
                    <p>{point.content}</p>
                  </article>
                ))}
              </div>
            </div>
          )}
          <div className="report-draft-two-col">
            <div className="report-draft-block">
              <h5>影响结论</h5>
              <p>{report.impact}</p>
            </div>
            <div className="report-draft-block">
              <h5>成因分析</h5>
              <p>{report.root_cause}</p>
            </div>
          </div>
          {!!report.evidence_highlights?.length && (
            <div className="report-draft-block">
              <h5>关键证据</h5>
              <ul className="report-draft-list">
                {report.evidence_highlights.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          )}
          {!!report.remediation?.length && (
            <div className="report-draft-block">
              <h5>修复建议</h5>
              <ul className="report-draft-list">
                {report.remediation.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          )}
          {!!report.operator_notes?.length && (
            <div className="report-draft-block muted">
              <h5>说明</h5>
              <ul className="report-draft-list">
                {report.operator_notes.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
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
  const [observability, setObservability] = useState(null);
  const [history, setHistory] = useState({});
  const [expanded, setExpanded] = useState({});
  const [editor, setEditor] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [batchTesting, setBatchTesting] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [statusFilter, setStatusFilter] = useState("all");
  const [searchTerm, setSearchTerm] = useState("");
  const [viewMode, setViewMode] = useState("grid");

  const load = useCallback(async ({ background = false } = {}) => {
    if (background) setRefreshing(true);
    try {
      const [statusList, configPayload, observabilityPayload] = await Promise.all([
        apiRequest("/api/workers").catch((error) => {
          setToast({ type: "warning", message: error.message || "工作节点状态暂不可用" });
          return [];
        }),
        apiRequest("/api/workers/config").catch((error) => {
          setToast({ type: "warning", message: error.message || "Worker 配置暂不可用" });
          return null;
        }),
        apiRequest("/api/workers/observability").catch(() => null),
      ]);
      setWorkers(statusList);
      setConfig(configPayload);
      setObservability(observabilityPayload);
      setLastUpdated(new Date());
    } finally {
      setLoading(false);
      if (background) setRefreshing(false);
    }
  }, [setToast]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const preset = consumeRoutePreset(WORKER_PRESET_STORAGE_KEY);
    if (preset?.status) setStatusFilter(String(preset.status));
  }, []);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const timer = window.setInterval(() => {
      load({ background: true }).catch(() => {});
    }, 15000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, load]);

  const statusByName = useMemo(() => new Map(workers.map((worker) => [worker.name, worker])), [workers]);
  const visibleWorkers = useMemo(() => {
    const configured = config?.workers || [];
    const names = new Set(configured.map((worker) => worker.name));
    const statusOnly = workers.filter((worker) => !names.has(worker.name)).map((worker) => ({ ...worker, env: {} }));
    return [...configured, ...statusOnly];
  }, [config, workers]);
  const configuredNames = useMemo(() => new Set((config?.workers || []).map((worker) => worker.name)), [config]);
  const workerCards = useMemo(() => {
    return visibleWorkers.map((worker) => {
      const live = statusByName.get(worker.name) || worker;
      const statusKey = live.status || (worker.enabled === false ? "disabled" : "offline");
      const statusMeta = STATUS_META[statusKey] || STATUS_META.offline;
      const taskTypes = worker.task_types?.length ? worker.task_types : [...TASK_TYPES];
      return {
        worker,
        live,
        statusKey,
        statusMeta,
        isManaged: configuredNames.has(worker.name),
        modelLabel: workerModelLabel(worker),
        runtimeLabel: workerRuntimeLabel(worker),
        heartbeatLabel: relativeHeartbeat(live.last_heartbeat_seconds_ago),
        taskTypes,
        searchBlob: [
          worker.name,
          worker.type,
          workerModelLabel(worker),
          workerRuntimeLabel(worker),
          statusMeta.label,
          taskTypes.join(" "),
          live.current_task || "",
        ]
          .join(" ")
          .toLowerCase(),
      };
    });
  }, [configuredNames, statusByName, visibleWorkers]);
  const workerCounts = useMemo(() => {
    const counts = { total: workerCards.length, online: 0, idle: 0, running: 0, offline: 0, disabled: 0, tasks: 0 };
    for (const card of workerCards) {
      if (card.statusKey === "busy") counts.running += 1;
      if (card.statusKey === "idle") counts.idle += 1;
      if (card.statusKey === "idle" || card.statusKey === "busy") counts.online += 1;
      if (card.statusKey === "offline") counts.offline += 1;
      if (card.statusKey === "disabled") counts.disabled += 1;
      counts.tasks += card.live.tasks_completed || 0;
    }
    return counts;
  }, [workerCards]);
  const statusTabs = useMemo(
    () => [
      { key: "all", label: "全部", count: workerCounts.total, tone: "info" },
      { key: "online", label: "在线", count: workerCounts.online, tone: "success" },
      { key: "idle", label: "空闲", count: workerCounts.idle, tone: "info" },
      { key: "offline", label: "离线", count: workerCounts.offline, tone: "danger" },
      { key: "disabled", label: "已关闭", count: workerCounts.disabled, tone: "muted" },
    ],
    [workerCounts],
  );
  const filteredWorkers = useMemo(() => {
    const keyword = searchTerm.trim().toLowerCase();
    const rank = { busy: 0, idle: 1, offline: 2, disabled: 3 };
    return workerCards
      .filter((card) => {
        if (statusFilter === "online" && !["idle", "busy"].includes(card.statusKey)) return false;
        if (statusFilter !== "all" && statusFilter !== "online" && card.statusKey !== statusFilter) return false;
        if (keyword && !card.searchBlob.includes(keyword)) return false;
        return true;
      })
      .sort((left, right) => {
        const rankDelta = (rank[left.statusKey] ?? 99) - (rank[right.statusKey] ?? 99);
        if (rankDelta !== 0) return rankDelta;
        return right.live.tasks_completed - left.live.tasks_completed || left.worker.name.localeCompare(right.worker.name);
      });
  }, [searchTerm, statusFilter, workerCards]);

  const saveWorkers = async (nextWorkers, label = "Worker 配置已保存") => {
    const updated = await runAction(label, () =>
      apiRequest("/api/workers/config", { method: "PUT", body: { workers: nextWorkers } }),
    );
    setConfig(updated);
    await load({ background: true });
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
  const openEditorForWorker = (worker) => {
    if (configuredNames.has(worker.name)) {
      setEditor(worker);
      return;
    }
    setEditor(workerDraftFromStatus(worker, config?.workers || []));
  };
  const testVisibleWorkers = async () => {
    if (!filteredWorkers.length || batchTesting) return;
    setBatchTesting(true);
    let ok = 0;
    const failed = [];
    try {
      for (const card of filteredWorkers) {
        try {
          const source = config?.workers?.find((item) => item.name === card.worker.name) || card.worker;
          const result = await apiRequest("/api/workers/config/test", {
            method: "POST",
            body: { worker: normalizeWorkerForSave(source) },
          });
          if (result.ok) ok += 1;
          else failed.push(result.worker_name || card.worker.name);
        } catch {
          failed.push(card.worker.name);
        }
      }
      setToast({
        type: failed.length ? "warning" : "success",
        message: failed.length
          ? `批量测试完成：${ok} 个正常，${failed.length} 个失败`
          : `批量测试完成：${ok} 个 Worker 连通性正常`,
      });
    } finally {
      setBatchTesting(false);
      await load({ background: true });
    }
  };

  return (
    <>
      <section className="content-wrap workers-console-page">
        <section className="workers-console-shell">
          <div className="workers-console-head">
            <div className="workers-console-title">
              <span className="workers-console-mark">
                <Monitor size={28} />
              </span>
              <div>
                <h1>工作节点</h1>
                <p>管理 Worker 实例、模型通道、任务执行状态与健康检查</p>
              </div>
            </div>
            <div className="workers-console-actions">
              <label className="workers-search">
                <Search size={16} />
                <input
                  value={searchTerm}
                  onChange={(event) => setSearchTerm(event.target.value)}
                  placeholder="搜索 Worker、模型、任务、标签..."
                />
              </label>
              <button
                className="primary-button compact"
                type="button"
                disabled={!config}
                onClick={() => setEditor(defaultWorkerDraft(config?.workers || []))}
              >
                <Plus size={18} />
                新增 Worker
              </button>
              <button className="ghost-button compact" type="button" disabled={!filteredWorkers.length || batchTesting} onClick={testVisibleWorkers}>
                <Activity size={16} />
                {batchTesting ? "测试中" : "批量测试"}
              </button>
              <button className="ghost-button compact" type="button" onClick={() => load({ background: true })} disabled={refreshing}>
                <RefreshCw className={refreshing ? "spin" : ""} size={16} />
                刷新
              </button>
            </div>
          </div>
        {loading ? (
          <EmptyState icon={Loader2} title="正在读取工作节点" />
        ) : visibleWorkers.length === 0 ? (
          <EmptyState
            icon={Bot}
            title="暂无 Worker"
            subtitle="新增 Worker 后，调度器会按优先级和并发配置分配任务。"
            action={
              <button
                className="primary-button compact"
                type="button"
                disabled={!config}
                onClick={() => setEditor(defaultWorkerDraft(config?.workers || []))}
              >
                <Plus size={18} />
                新增 Worker
              </button>
            }
          />
        ) : (
          <>
            <div className="workers-overview-grid">
              <WorkerOverviewCard tone="success" label="在线节点" value={workerCounts.online} subtitle="可接收任务" />
              <WorkerOverviewCard tone="muted" label="离线节点" value={workerCounts.offline} subtitle="等待恢复" />
              <WorkerOverviewCard tone="info" label="运行中任务" value={workerCounts.running} subtitle="当前执行中" />
              <WorkerOverviewCard tone="violet" label="累计任务数" value={workerCounts.tasks} subtitle="已记录完成" />
            </div>
            {observability && (
              <div className="workers-observability-grid">
                <section className="workers-observability-card">
                  <header>
                    <h3>调度容量</h3>
                    <span>实时限制</span>
                  </header>
                  <div className="workers-observability-stats">
                    <MiniStat label="最大并发" value={observability.summary?.max_workers || 0} />
                    <MiniStat label="运行任务" value={observability.summary?.running_tasks || 0} />
                    <MiniStat label="运行项目" value={observability.summary?.running_projects || 0} />
                    <MiniStat label="项目并发上限" value={observability.summary?.max_project_workers || 0} />
                  </div>
                </section>
                <section className="workers-observability-card">
                  <header>
                    <h3>最近结果</h3>
                    <span>任务历史</span>
                  </header>
                  <div className="workers-outcome-grid">
                    <MiniStat label="成功" value={observability.outcomes?.success || 0} />
                    <MiniStat label="失败" value={observability.outcomes?.failed || 0} />
                    <MiniStat label="拒绝" value={observability.outcomes?.rejected || 0} />
                    <MiniStat label="释放" value={observability.outcomes?.released || 0} />
                  </div>
                </section>
                <section className="workers-observability-card">
                  <header>
                    <h3>任务类型</h3>
                    <span>近 40 条</span>
                  </header>
                  {(observability.task_mix || []).length === 0 ? (
                    <p className="analysis-empty">暂无任务分布</p>
                  ) : (
                    <div className="workers-task-mix">
                      {observability.task_mix.map((item) => (
                        <div key={item.task_type} className="workers-task-mix-item">
                          <span>{item.task_type}</span>
                          <strong>{item.count}</strong>
                        </div>
                      ))}
                    </div>
                  )}
                </section>
                <section className="workers-observability-card">
                  <header>
                    <h3>调度观察</h3>
                    <span>{observability.rejections?.length ? `${observability.rejections.length} 条退避` : "运行中任务"}</span>
                  </header>
                  {(observability.rejections || []).length > 0 ? (
                    <div className="workers-observability-list">
                      {observability.rejections.map((item, index) => (
                        <article key={`${item.worker_name}-${item.project_id}-${index}`}>
                          <strong>{item.worker_name}</strong>
                          <span>{item.project_name} · {item.task_type}</span>
                          <small>{item.seconds_remaining ? `${Math.round(item.seconds_remaining)} 秒后重试` : "等待恢复"}</small>
                        </article>
                      ))}
                    </div>
                  ) : (observability.running_tasks || []).length > 0 ? (
                    <div className="workers-observability-list">
                      {observability.running_tasks.map((item, index) => (
                        <article key={`${item.worker_name}-${item.project_id}-${index}`}>
                          <strong>{item.worker_name}</strong>
                          <span title={item.current_task}>{clampText(item.current_task, 52)}</span>
                          <small>{item.project_name} · {item.task_type}</small>
                        </article>
                      ))}
                    </div>
                  ) : (observability.recent_history || []).length > 0 ? (
                    <div className="workers-observability-list">
                      {observability.recent_history.slice(0, 4).map((item, index) => (
                        <article key={`${item.worker_name}-${item.started_at}-${index}`}>
                          <strong>{item.worker_name}</strong>
                          <span>{item.project_name} · {item.task_type}</span>
                          <small>{item.outcome} · {formatTime(item.started_at)}</small>
                        </article>
                      ))}
                    </div>
                  ) : (
                    <p className="analysis-empty">当前没有运行中任务或退避记录</p>
                  )}
                </section>
              </div>
            )}
            <div className="workers-filter-bar">
              <div className="workers-filter-tabs" role="tablist" aria-label="工作节点状态筛选">
                {statusTabs.map((tab) => (
                  <button
                    key={tab.key}
                    type="button"
                    role="tab"
                    aria-selected={statusFilter === tab.key}
                    className={cn("workers-filter-tab", statusFilter === tab.key && "active", tab.tone)}
                    onClick={() => setStatusFilter(tab.key)}
                  >
                    <span>{tab.label}</span>
                    <strong>{tab.count}</strong>
                  </button>
                ))}
              </div>
              <div className="workers-filter-controls">
                <label className="workers-auto-refresh">
                  <span className={cn("workers-auto-indicator", autoRefresh && "on")} />
                  自动刷新
                  <button
                    className={cn("switch compact-switch", autoRefresh && "on")}
                    type="button"
                    onClick={() => setAutoRefresh((current) => !current)}
                    aria-label={autoRefresh ? "关闭自动刷新" : "开启自动刷新"}
                  >
                    <span />
                  </button>
                </label>
                <span className="workers-last-updated">
                  {lastUpdated ? `更新于 ${lastUpdated.toLocaleTimeString("zh-CN")}` : "待更新"}
                </span>
                <div className="workers-view-toggle" role="tablist" aria-label="工作节点布局">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={viewMode === "grid"}
                    className={cn(viewMode === "grid" && "active")}
                    onClick={() => setViewMode("grid")}
                  >
                    <LayoutGrid size={16} />
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={viewMode === "list"}
                    className={cn(viewMode === "list" && "active")}
                    onClick={() => setViewMode("list")}
                  >
                    <List size={16} />
                  </button>
                </div>
              </div>
            </div>
            {filteredWorkers.length === 0 ? (
              <EmptyState icon={Search} title="没有匹配的工作节点" subtitle="调整关键字或状态筛选后再试。" />
            ) : (
              <div className={cn("workers-node-grid", viewMode === "list" && "list")}>
                {filteredWorkers.map((card) => {
                  const worker = card.worker;
                  const status = card.live;
                  const isExpanded = Boolean(expanded[worker.name]);
                  const actionLabel = !card.isManaged ? "纳管" : worker.enabled === false ? "启用" : "关闭";
                  return (
                    <article className="worker-node-card" key={worker.name}>
                      <div className="worker-node-card-head">
                        <div className="worker-node-title">
                          <span className={cn("worker-node-dot", card.statusMeta.tone)} />
                          <div>
                            <div className="worker-node-title-row">
                              <h3>{worker.name}</h3>
                              {!card.isManaged && <span className="worker-node-discovered">未纳管</span>}
                            </div>
                            <p>{card.worker.type} · {card.modelLabel}</p>
                          </div>
                        </div>
                        <Badge tone={card.statusMeta.tone}>{card.statusMeta.label}</Badge>
                      </div>
                      {status.current_task && <div className="worker-node-task">{clampText(status.current_task, 132)}</div>}
                      <div className="worker-node-meta">
                        <div>
                          <span>Provider</span>
                          <strong>{worker.type}</strong>
                        </div>
                        <div>
                          <span>Model</span>
                          <strong>{card.modelLabel}</strong>
                        </div>
                        <div>
                          <span>Runtime</span>
                          <strong>{card.runtimeLabel}</strong>
                        </div>
                        <div>
                          <span>最近心跳</span>
                          <strong>{card.heartbeatLabel}</strong>
                        </div>
                      </div>
                      <div className="worker-node-stats">
                        <div>
                          <span>任务数</span>
                          <strong>{status.tasks_completed ?? 0}</strong>
                        </div>
                        <div>
                          <span>平均耗时</span>
                          <strong>{status.avg_duration_seconds ? `${status.avg_duration_seconds}s` : "-"}</strong>
                        </div>
                        <div>
                          <span>最大并发</span>
                          <strong>{worker.max_running ?? 1}</strong>
                        </div>
                        <div>
                          <span>优先级</span>
                          <strong>{worker.priority ?? 0}</strong>
                        </div>
                      </div>
                      <div className="worker-node-tags">
                        {card.taskTypes.map((taskType) => (
                          <span key={taskType}>{taskType}</span>
                        ))}
                      </div>
                      <div className="worker-node-actions">
                        <button
                          className={cn("worker-node-toggle", worker.enabled === false || !card.isManaged ? "start" : "stop")}
                          type="button"
                          disabled={!config}
                          onClick={() => (card.isManaged ? setEnabled(worker, worker.enabled === false) : openEditorForWorker(worker))}
                        >
                          {worker.enabled === false || !card.isManaged ? <Play size={16} /> : <Pause size={16} />}
                          {actionLabel}
                        </button>
                        <button className="ghost-button compact" type="button" disabled={!config} onClick={() => testWorker(worker)}>
                          <Activity size={16} />
                          测试
                        </button>
                        <button className="ghost-button compact" type="button" disabled={!config} onClick={() => openEditorForWorker(worker)}>
                          <Settings size={16} />
                          编辑
                        </button>
                        <button className="ghost-button compact" type="button" onClick={() => toggleHistory(worker.name)}>
                          <History size={16} />
                          {isExpanded ? "隐藏历史" : "历史"}
                        </button>
                        <button className="worker-node-menu" type="button" onClick={() => openEditorForWorker(worker)} aria-label="更多操作">
                          <MoreVertical size={16} />
                        </button>
                      </div>
                      {isExpanded && (
                        <div className="history-list worker-node-history">
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
          </>
        )}
        </section>
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

function WorkerOverviewCard({ tone, label, value, subtitle }) {
  return (
    <article className={cn("worker-overview-card", tone)}>
      <span className="worker-overview-ring" />
      <div>
        <strong>{label}</strong>
        <b>{value}</b>
        <small>{subtitle}</small>
      </div>
    </article>
  );
}

function workerModelLabel(worker) {
  const env = worker.env || {};
  return env.ANTHROPIC_MODEL || env.CODEX_MODEL || env.PI_MODEL || "未配置模型";
}

function workerRuntimeLabel(worker) {
  if (worker.type === "pi") return "local";
  if (worker.type === "mock") return "mock";
  return "cloud";
}

function workerDraftFromStatus(worker, existingWorkers) {
  return {
    ...defaultWorkerDraft(existingWorkers),
    ...worker,
    enabled: worker.enabled !== false,
    task_types: worker.task_types?.length ? worker.task_types : [...TASK_TYPES],
    max_running: worker.max_running ?? 1,
    priority: worker.priority ?? 0,
    env: { ...(worker.env || {}) },
    secret_env_keys: worker.secret_env_keys || [],
  };
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
  const [health, setHealth] = useState(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [cleaning, setCleaning] = useState(false);

  const settingsFallback = useMemo(
    () => ({
      intent_timeout: 15,
      reason_timeout: 15,
      worker_unhealthy_retry_after_seconds: 5,
      worker_rejected_retry_after_seconds: 5,
      max_failed_login_attempts: 5,
      rate_limit_window_minutes: 15,
      session_duration_hours: 24,
      log_retention_days: 30,
      export_retention_days: 30,
      notification_retention_days: 14,
      project_idle_alert_hours: 12,
    }),
    [],
  );

  useEffect(() => {
    apiRequest("/settings").then(setSettings).catch(() => setSettings(settingsFallback));
  }, [settingsFallback]);

  const loadHealth = useCallback(async () => {
    setHealthLoading(true);
    try {
      setHealth(await apiRequest("/settings/health"));
    } catch {
      setHealth(null);
    } finally {
      setHealthLoading(false);
    }
  }, []);

  useEffect(() => {
    loadHealth();
  }, [loadHealth]);

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      await runAction("设置已保存", () => apiRequest("/settings", { method: "PUT", body: settings }));
      await loadHealth();
    } finally {
      setSaving(false);
    }
  };

  const runCleanup = async () => {
    setCleaning(true);
    try {
      await runAction("系统清理已完成", () => apiRequest("/settings/cleanup", { method: "POST" }));
      await loadHealth();
    } finally {
      setCleaning(false);
    }
  };

  const setNumber = (key, value) => {
    setSettings((current) => ({ ...current, [key]: Number(value) }));
  };

  const healthTone = (status) => (status === "error" ? "danger" : status === "warning" ? "high" : "success");
  const healthLabel = (status) => (status === "error" ? "异常" : status === "warning" ? "告警" : "正常");

  return (
    <Modal
      title="系统设置"
      subtitle="统一管理调度、安全策略、历史保留周期和系统健康检查。"
      onClose={onClose}
      wide
    >
      {!settings ? (
        <EmptyState icon={Loader2} title="正在读取设置" />
      ) : (
        <form className="stack-form modal-body settings-shell" onSubmit={submit}>
          <section className="settings-section">
            <div className="settings-section-head">
              <div>
                <h3>运行控制</h3>
                <p>只影响调度节奏与冷却策略，不改项目和 Worker 配置结构。</p>
              </div>
              <Badge tone="info">调度</Badge>
            </div>
            <div className="two-col">
              <label>
                <span>意图超时（秒）</span>
                <input type="number" min="5" value={settings.intent_timeout} onChange={(event) => setNumber("intent_timeout", event.target.value)} />
                <small className="field-hint">意图在被回收前允许等待的最长时间。</small>
              </label>
              <label>
                <span>Reason 超时（秒）</span>
                <input type="number" min="5" value={settings.reason_timeout} onChange={(event) => setNumber("reason_timeout", event.target.value)} />
                <small className="field-hint">Reason 阶段在判定超时前的最长执行时间。</small>
              </label>
            </div>
            <div className="two-col">
              <label>
                <span>Worker 不健康冷却（秒）</span>
                <input
                  type="number"
                  min="1"
                  value={settings.worker_unhealthy_retry_after_seconds}
                  onChange={(event) => setNumber("worker_unhealthy_retry_after_seconds", event.target.value)}
                />
                <small className="field-hint">Worker 健康检查失败后，重新参与调度前的冷却时间。</small>
              </label>
              <label>
                <span>拒绝任务重试间隔（秒）</span>
                <input
                  type="number"
                  min="1"
                  value={settings.worker_rejected_retry_after_seconds}
                  onChange={(event) => setNumber("worker_rejected_retry_after_seconds", event.target.value)}
                />
                <small className="field-hint">同一 Worker 暂时拒绝任务后，再次尝试分配的等待时间。</small>
              </label>
            </div>
          </section>

          <section className="settings-section">
            <div className="settings-section-head">
              <div>
                <h3>认证与会话</h3>
                <p>控制登录失败锁定、会话寿命和浏览器认证窗口。</p>
              </div>
              <Badge tone="success">安全</Badge>
            </div>
            <div className="three-col">
              <label>
                <span>失败锁定阈值</span>
                <input
                  type="number"
                  min="1"
                  value={settings.max_failed_login_attempts}
                  onChange={(event) => setNumber("max_failed_login_attempts", event.target.value)}
                />
                <small className="field-hint">同一账号在窗口期内允许的最大失败次数。</small>
              </label>
              <label>
                <span>限流窗口（分钟）</span>
                <input
                  type="number"
                  min="1"
                  value={settings.rate_limit_window_minutes}
                  onChange={(event) => setNumber("rate_limit_window_minutes", event.target.value)}
                />
                <small className="field-hint">超过失败阈值后，窗口期内继续登录会被直接拦截。</small>
              </label>
              <label>
                <span>Session 时长（小时）</span>
                <input
                  type="number"
                  min="1"
                  value={settings.session_duration_hours}
                  onChange={(event) => setNumber("session_duration_hours", event.target.value)}
                />
                <small className="field-hint">有效会话的滑动过期时间。</small>
              </label>
            </div>
          </section>

          <section className="settings-section">
            <div className="settings-section-head">
              <div>
                <h3>保留与清理</h3>
                <p>只清理历史记录、导出记录和已读通知，不触碰项目事实、意图和漏洞数据。</p>
              </div>
              <Badge tone="medium">维护</Badge>
            </div>
            <div className="three-col">
              <label>
                <span>日志保留（天）</span>
                <input type="number" min="1" value={settings.log_retention_days} onChange={(event) => setNumber("log_retention_days", event.target.value)} />
                <small className="field-hint">用于审计日志、Worker 历史和登录尝试记录。</small>
              </label>
              <label>
                <span>导出记录保留（天）</span>
                <input
                  type="number"
                  min="1"
                  value={settings.export_retention_days}
                  onChange={(event) => setNumber("export_retention_days", event.target.value)}
                />
                <small className="field-hint">只清理导出历史记录，不影响实时导出功能。</small>
              </label>
              <label>
                <span>通知保留（天）</span>
                <input
                  type="number"
                  min="1"
                  value={settings.notification_retention_days}
                  onChange={(event) => setNumber("notification_retention_days", event.target.value)}
                />
                <small className="field-hint">仅清理已读通知，未读通知会保留。</small>
              </label>
            </div>
            <div className="two-col">
              <label>
                <span>项目无进展告警（小时）</span>
                <input
                  type="number"
                  min="1"
                  value={settings.project_idle_alert_hours}
                  onChange={(event) => setNumber("project_idle_alert_hours", event.target.value)}
                />
                <small className="field-hint">活动项目最近无新增事实、提示、意图或 Reason 心跳时触发告警。</small>
              </label>
              <div className="settings-action-card">
                <div>
                  <strong>立即清理历史数据</strong>
                  <p>按当前保留策略删除过期日志、已读通知、导出记录和失效会话。</p>
                </div>
                <button className="ghost-button compact" type="button" onClick={runCleanup} disabled={cleaning}>
                  {cleaning ? <Loader2 className="spin" size={16} /> : <Trash2 size={16} />}
                  立即清理
                </button>
              </div>
            </div>
          </section>

          <section className="settings-section">
            <div className="settings-section-head">
              <div>
                <h3>系统健康</h3>
                <p>查看当前 API、数据库、调度器和 Worker 的整体状态。</p>
              </div>
              <button className="ghost-button compact" type="button" onClick={loadHealth} disabled={healthLoading}>
                {healthLoading ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
                刷新状态
              </button>
            </div>

            {health ? (
              <>
                <div className="settings-health-grid">
                  <article className={cn("settings-health-card", health.summary.status)}>
                    <span>系统状态</span>
                    <strong>{healthLabel(health.summary.status)}</strong>
                    <small>更新时间 {formatTime(health.generated_at)}</small>
                  </article>
                  <article className="settings-health-card">
                    <span>活动项目</span>
                    <strong>{health.summary.active_projects}</strong>
                    <small>项目总数 {health.stats.projects}</small>
                  </article>
                  <article className="settings-health-card">
                    <span>在线 Worker</span>
                    <strong>{health.summary.online_workers}</strong>
                    <small>离线 {health.summary.offline_workers}</small>
                  </article>
                  <article className="settings-health-card">
                    <span>未读通知</span>
                    <strong>{health.stats.notifications_unread}</strong>
                    <small>审计日志 {health.stats.audit_entries}</small>
                  </article>
                </div>

                <div className="settings-check-list">
                  {health.checks.map((check) => (
                    <article key={check.key} className={cn("settings-check-item", check.status)}>
                      <div className="settings-check-head">
                        <strong>{check.label}</strong>
                        <Badge tone={healthTone(check.status)}>{healthLabel(check.status)}</Badge>
                      </div>
                      <p>{check.summary}</p>
                      {check.detail && <small>{check.detail}</small>}
                    </article>
                  ))}
                </div>

                <div className="settings-alert-block">
                  <div className="settings-alert-head">
                    <strong>告警与提醒</strong>
                    <span>{health.alerts.length} 条</span>
                  </div>
                  {health.alerts.length === 0 ? (
                    <div className="soft-box">当前没有需要处理的系统级告警。</div>
                  ) : (
                    <div className="settings-alert-list">
                      {health.alerts.map((alert, index) => (
                        <article key={`${alert.title}-${index}`} className={cn("settings-alert-item", alert.level)}>
                          <div className="settings-alert-title">
                            <AlertTriangle size={16} />
                            <strong>{alert.title}</strong>
                          </div>
                          {alert.detail && <p>{alert.detail}</p>}
                        </article>
                      ))}
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="soft-box">系统健康状态暂不可用，可稍后刷新重试。</div>
            )}
          </section>

          <div className="modal-footer">
            <button className="ghost-button" type="button" onClick={onClose}>
              关闭
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
