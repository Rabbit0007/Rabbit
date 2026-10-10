export async function apiRequest(path, options = {}) {
  const { method = "GET", body, headers = {} } = options;
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (response.status === 204) {
    return null;
  }

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : await response.text().catch(() => "");

  if (!response.ok) {
    const detail = payload?.detail ?? payload?.message ?? payload;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message || JSON.stringify(detail || {});
    const error = new Error(message || `HTTP ${response.status}`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }

  return payload;
}

export async function downloadFromApi(path, fallbackName = "rabbit-export.md") {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(text || `下载失败：HTTP ${response.status}`);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  const filename = match ? decodeURIComponent(match[1]) : fallbackName;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
