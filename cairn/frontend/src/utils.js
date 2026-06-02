export const SEVERITY_META = {
  critical: { label: "严重", tone: "critical" },
  high: { label: "高危", tone: "high" },
  medium: { label: "中危", tone: "medium" },
  low: { label: "低危", tone: "low" },
};

export const STATUS_META = {
  active: { label: "运行中", tone: "success" },
  stopped: { label: "已停止", tone: "warning" },
  completed: { label: "已完成", tone: "muted" },
  idle: { label: "空闲", tone: "info" },
  busy: { label: "忙碌", tone: "success" },
  offline: { label: "离线", tone: "danger" },
  disabled: { label: "已关闭", tone: "muted" },
};

export const TASK_TYPES = ["bootstrap", "reason", "explore"];

export function cn(...parts) {
  return parts.filter(Boolean).join(" ");
}

export function clampText(value, length = 120) {
  const text = String(value || "");
  return text.length > length ? `${text.slice(0, length)}...` : text;
}

export function formatTime(value) {
  if (!value) return "-";
  const normalized = String(value).endsWith("Z") ? value : `${value}`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function relativeHeartbeat(seconds) {
  if (seconds === null || seconds === undefined) return "-";
  if (seconds < 60) return `${Math.round(seconds)} 秒前`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`;
  return `${Math.round(seconds / 3600)} 小时前`;
}

export function parseHash() {
  const raw = window.location.hash.replace(/^#\/?/, "");
  if (!raw) return { page: "dashboard" };
  const [pathPart, queryPart = ""] = raw.split("?");
  const params = new URLSearchParams(queryPart);
  const search = params.get("q") || "";
  const parts = pathPart.split("/").filter(Boolean);
  if (parts[0] === "dashboard") return { page: "dashboard" };
  if (parts[0] === "projects" && parts[1]) {
    return { page: "project", projectId: decodeURIComponent(parts[1]) };
  }
  if (parts[0] === "projects") return { page: "projects" };
  if (parts[0] === "vulnerabilities")
    return { page: "vulnerabilities", view: parts[1] ? decodeURIComponent(parts[1]) : "overview", search };
  if (parts[0] === "workers") return { page: "workers" };
  if (parts[0] === "templates") return { page: "templates" };
  if (parts[0] === "audit") return { page: "audit" };
  return { page: "dashboard" };
}

export function go(hash) {
  window.location.hash = hash;
}

export function groupBy(items, keyFn) {
  const groups = new Map();
  for (const item of items) {
    const key = keyFn(item);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return groups;
}

export function parseHintLines(text, creator = "Human") {
  return String(text || "")
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((content) => ({ content, creator }));
}
