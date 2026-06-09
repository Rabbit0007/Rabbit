import { AlertCircle, AlertTriangle, Sparkles, X } from "lucide-react";

import { cn } from "../utils";

export function ConfirmModal({
  title = "确认操作",
  message,
  tone = "default",
  confirmLabel = "确认",
  cancelLabel = "取消",
  onConfirm,
  onCancel,
}) {
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

export function PageHeader({ icon: Icon, title, subtitle, actions, compact = false }) {
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

export function Toast({ toast, onClose }) {
  return (
    <div className={cn("toast", toast.type || "info")}>
      <span>{toast.message}</span>
      <button type="button" onClick={onClose}>
        <X size={16} />
      </button>
    </div>
  );
}

export function Modal({ title, subtitle, children, onClose, wide = false }) {
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

export function EmptyState({ icon: Icon = Sparkles, title, subtitle, action }) {
  return (
    <div className="empty-state">
      <Icon size={42} />
      <h3>{title}</h3>
      {subtitle && <p>{subtitle}</p>}
      {action}
    </div>
  );
}

export function Badge({ tone = "muted", children }) {
  return <span className={cn("badge", tone)}>{children}</span>;
}

export function MiniStat({ label, value }) {
  return (
    <div className="mini-stat">
      <span>{label}</span>
      <strong>{value ?? "-"}</strong>
    </div>
  );
}

export function MetricCard({ label, value, tone, icon: Icon, description, onClick }) {
  const content = (
    <>
      <span className="metric-icon">
        {Icon ? <Icon size={20} /> : <span className="metric-dot" />}
      </span>
      <div className="metric-copy">
        <span>{label}</span>
        <strong>{value}</strong>
        {description && <small>{description}</small>}
      </div>
    </>
  );
  if (onClick) {
    return (
      <button className={cn("metric-card", tone, "interactive")} type="button" onClick={onClick}>
        {content}
      </button>
    );
  }
  return <div className={cn("metric-card", tone)}>{content}</div>;
}

export function VulnerabilitySummaryCard({ icon: Icon, label, value, tone }) {
  return (
    <article className={cn("report-summary-card", tone)}>
      <span className="report-summary-icon">
        <Icon size={20} />
      </span>
      <div className="report-summary-copy">
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
    </article>
  );
}

export function ProjectSummaryCard({ icon: Icon, label, value, description, tone }) {
  return (
    <article className={cn("project-summary-card", tone)}>
      <div className="project-summary-icon">
        <Icon size={22} />
      </div>
      <div className="project-summary-content">
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{description}</small>
      </div>
    </article>
  );
}
