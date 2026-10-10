from __future__ import annotations

from dataclasses import dataclass

import requests

DETAIL_LIMIT = 200


@dataclass(slots=True)
class HealthResult:
    ok: bool
    status: int | None
    detail: str


def http_ping(
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict,
    timeout: float,
    proxies: dict[str, str] | None = None,
) -> HealthResult:
    """POST a tiny request to an LLM endpoint and judge health by the HTTP status.

    Healthy = a 2xx response (endpoint reachable, key accepted, model callable). Any other
    status, or a connection/timeout error, is unhealthy. The dispatcher runs this in-process
    instead of shelling a curl into a container.
    """
    try:
        response = requests.post(url, headers=headers, json=json_body, timeout=timeout, proxies=proxies)
    except requests.RequestException as exc:
        return HealthResult(ok=False, status=None, detail=_clip(str(exc)))
    ok = 200 <= response.status_code < 300
    return HealthResult(ok=ok, status=response.status_code, detail="" if ok else _clip(response.text))


def proxies_from_env(env: dict[str, str]) -> dict[str, str] | None:
    """Build a requests proxies dict from the worker's env so the health check follows the
    same outbound proxy the worker would use (e.g. a common_env http(s)_proxy)."""
    all_proxy = env.get("all_proxy") or env.get("ALL_PROXY")
    http = env.get("http_proxy") or env.get("HTTP_PROXY") or all_proxy
    https = env.get("https_proxy") or env.get("HTTPS_PROXY") or all_proxy
    proxies: dict[str, str] = {}
    if http:
        proxies["http"] = http
    if https:
        proxies["https"] = https
    return proxies or None


def _clip(text: str) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= DETAIL_LIMIT else compact[:DETAIL_LIMIT] + "..."
