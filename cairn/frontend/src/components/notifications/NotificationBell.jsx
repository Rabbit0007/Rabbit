import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bell, CheckCheck, Inbox, Loader2, Trash2 } from "lucide-react";

import { apiRequest } from "../../api";
import { clampText, cn, formatTime, go } from "../../utils";

export default function NotificationBell({ setToast }) {
  const [count, setCount] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState("all");
  const wrapRef = useRef(null);

  const loadCount = useCallback(async () => {
    try {
      const res = await apiRequest("/api/notifications/unread-count");
      setCount(Number(res?.count) || 0);
    } catch {
      // Keep the last badge value when the polling request fails.
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
      setItems(Array.isArray(list) ? list : []);
      await loadCount();
    } catch (error) {
      if (setToast) setToast({ type: "danger", message: error.message || "通知加载失败" });
    } finally {
      setLoading(false);
    }
  }, [loadCount, setToast]);

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

  const markRead = async (ids) => {
    if (!ids?.length) return;
    try {
      const res = await apiRequest("/api/notifications/read", { method: "POST", body: { ids } });
      setCount(Number(res?.count) || 0);
      setItems((prev) => prev.map((item) => (ids.includes(item.id) ? { ...item, read: true } : item)));
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

  const openNotification = async (item) => {
    if (!item.read) {
      await markRead([item.id]);
    }
    if (item.link) {
      setOpen(false);
      go(item.link);
    }
  };

  const filteredItems = useMemo(() => {
    if (filter === "unread") return items.filter((item) => !item.read);
    if (filter === "warning") return items.filter((item) => ["warning", "danger"].includes(item.level));
    return items;
  }, [filter, items]);

  const groupedItems = useMemo(() => {
    const groups = new Map();
    filteredItems.forEach((item) => {
      const date = String(item.created_at || "").slice(0, 10) || "未标记日期";
      if (!groups.has(date)) groups.set(date, []);
      groups.get(date).push(item);
    });
    return [...groups.entries()];
  }, [filteredItems]);

  const unreadCount = useMemo(() => items.filter((item) => !item.read).length, [items]);
  const warningCount = useMemo(() => items.filter((item) => ["warning", "danger"].includes(item.level)).length, [items]);
  const filterTabs = [
    { key: "all", label: "全部", count: items.length },
    { key: "unread", label: "未读", count: unreadCount },
    { key: "warning", label: "预警", count: warningCount },
  ];

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
            <div className="notification-panel-title">
              <strong>通知</strong>
              <small>{unreadCount ? `${unreadCount} 条未读` : "全部已读"}</small>
            </div>
            <div className="notification-actions">
              <button type="button" onClick={markAllRead} disabled={!unreadCount}>
                <CheckCheck size={14} />
                全部已读
              </button>
              <button type="button" className="danger" onClick={clearAll} disabled={!items.length}>
                <Trash2 size={14} />
                清空
              </button>
            </div>
          </header>
          <div className="notification-filter-tabs" role="tablist" aria-label="通知筛选">
            {filterTabs.map((tab) => (
              <button
                key={tab.key}
                type="button"
                role="tab"
                aria-selected={filter === tab.key}
                className={cn("notification-filter-tab", filter === tab.key && "active")}
                onClick={() => setFilter(tab.key)}
              >
                <span>{tab.label}</span>
                <strong>{tab.count}</strong>
              </button>
            ))}
          </div>
          <div className="notification-list">
            {loading ? (
              <div className="search-empty">
                <Loader2 className="spin" size={16} />
                <span>加载中...</span>
              </div>
            ) : filteredItems.length === 0 ? (
              <div className="search-empty">
                <Inbox size={18} />
                <span>{filter === "all" ? "暂无通知" : "当前筛选下暂无通知"}</span>
              </div>
            ) : (
              groupedItems.map(([date, entries]) => (
                <section className="notification-group" key={date}>
                  <div className="notification-group-label">{date}</div>
                  {entries.map((item) => (
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
                  ))}
                </section>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
