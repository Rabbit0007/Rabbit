from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.client
import ipaddress
import logging
import os
from pathlib import Path
import selectors
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import SplitResult, urlsplit

import yaml

from cairn.project_scope import LOCAL_HOST_ALIASES, extract_target_tokens


LOG = logging.getLogger("rabbit-egress-proxy")
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
BLOCKED_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("ff00::/8"),
)


def proxy_credentials(project_id: str, token: str) -> tuple[str, str]:
    password = hmac.new(token.encode(), project_id.encode(), hashlib.sha256).hexdigest()
    return project_id, password


class ScopeAuthorizer:
    def __init__(self, context_root: Path, token: str, provider_endpoints: set[tuple[str, int]]):
        self.context_root = context_root
        self.token = token
        self.provider_endpoints = provider_endpoints

    def authenticate(self, header: str | None) -> str | None:
        if not header or not header.lower().startswith("basic "):
            return None
        try:
            decoded = base64.b64decode(header.split(None, 1)[1]).decode()
            project_id, password = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return None
        expected_user, expected_password = proxy_credentials(project_id, self.token)
        if not hmac.compare_digest(project_id, expected_user):
            return None
        if not hmac.compare_digest(password, expected_password):
            return None
        return project_id

    def allowed(self, project_id: str, host: str, port: int) -> bool:
        normalized = host.strip().strip("[]").strip(".").lower()
        if self._blocked_host(normalized):
            return False
        if (normalized, port) in self.provider_endpoints:
            return True
        if project_id == "__startup__":
            return False
        return (normalized, port) in self._project_endpoints(project_id)

    def _project_endpoints(self, project_id: str) -> set[tuple[str, int]]:
        path = self.context_root / "projects" / f"{project_id}.project-context.yaml"
        if not path.is_file():
            return set()
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            LOG.warning("failed to load scope context project=%s", project_id)
            return set()
        project = payload.get("project") if isinstance(payload, dict) else None
        summary = project.get("target_summary", "") if isinstance(project, dict) else ""
        endpoints: set[tuple[str, int]] = set()
        for token in str(summary).split():
            if "://" not in token:
                continue
            try:
                parsed = urlsplit(token)
                if parsed.hostname:
                    endpoints.add((parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)))
            except ValueError:
                continue
        # Bare hosts are treated as ordinary web targets, never as all-port authorization.
        for host in extract_target_tokens(str(summary)):
            normalized = host.lower()
            if not any(item[0] == normalized for item in endpoints):
                endpoints.add((normalized, 80))
                endpoints.add((normalized, 443))
        return endpoints

    @staticmethod
    def _blocked_host(host: str) -> bool:
        if host in LOCAL_HOST_ALIASES or host in {"0.0.0.0", "::"}:
            return True
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        return any(address in network for network in BLOCKED_NETWORKS)


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RabbitEgress/1.0"

    def do_CONNECT(self) -> None:  # noqa: N802
        target = self._connect_target()
        if target is None:
            return
        project_id = self._authorize(target[0], target[1])
        if project_id is None:
            return
        try:
            upstream = socket.create_connection(target, timeout=self.server.connect_timeout)
        except OSError as exc:
            LOG.warning("connect failed project=%s target=%s:%s error=%s", project_id, *target, exc)
            self.send_error(502, "upstream connection failed")
            return
        try:
            self.send_response(200, "Connection established")
            self.end_headers()
            self.connection.setblocking(False)
            upstream.setblocking(False)
            self._relay(self.connection, upstream)
        finally:
            upstream.close()

    def do_GET(self) -> None:  # noqa: N802
        self._forward_http()

    def do_HEAD(self) -> None:  # noqa: N802
        self._forward_http()

    def do_POST(self) -> None:  # noqa: N802
        self._forward_http()

    def do_PUT(self) -> None:  # noqa: N802
        self._forward_http()

    def do_PATCH(self) -> None:  # noqa: N802
        self._forward_http()

    def do_DELETE(self) -> None:  # noqa: N802
        self._forward_http()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._forward_http()

    def _authorize(self, host: str, port: int) -> str | None:
        project_id = self.server.authorizer.authenticate(self.headers.get("Proxy-Authorization"))
        if project_id is None:
            self.send_error(407, "proxy authentication required")
            return None
        if not self.server.authorizer.allowed(project_id, host, port):
            LOG.warning("denied project=%s target=%s:%s", project_id, host, port)
            self.send_error(403, "target is outside project scope")
            return None
        return project_id

    def _forward_http(self) -> None:
        target = self._resolve_target()
        if target is None or target.hostname is None:
            return
        port = target.port or (443 if target.scheme == "https" else 80)
        project_id = self._authorize(target.hostname, port)
        if project_id is None:
            return
        body = b""
        content_length = self.headers.get("Content-Length")
        if content_length:
            try:
                body = self.rfile.read(int(content_length))
            except ValueError:
                self.send_error(400, "invalid content-length")
                return
        connection_cls = http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
        try:
            upstream = connection_cls(target.hostname, port, timeout=self.server.read_timeout)
            upstream.request(
                self.command,
                self._target_path(target),
                body=body if body else None,
                headers=self._request_headers(target),
            )
            response = upstream.getresponse()
        except Exception as exc:
            LOG.warning("request failed project=%s target=%s error=%s", project_id, target.geturl(), exc)
            self.send_error(502, "upstream request failed")
            return
        try:
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in HOP_BY_HOP_HEADERS:
                    self.send_header(key, value)
            self.end_headers()
            self._stream_response(response)
        finally:
            upstream.close()

    def _stream_response(self, response: http.client.HTTPResponse) -> None:
        try:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            LOG.debug("client disconnected while receiving proxied response")

    def _resolve_target(self) -> SplitResult | None:
        parsed = urlsplit(self.path)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return parsed
        self.send_error(400, "absolute proxy URL required")
        return None

    def _connect_target(self) -> tuple[str, int] | None:
        if ":" not in self.path:
            self.send_error(400, "invalid CONNECT target")
            return None
        host, raw_port = self.path.rsplit(":", 1)
        try:
            return host.strip("[]"), int(raw_port)
        except ValueError:
            self.send_error(400, "invalid CONNECT port")
            return None

    def _request_headers(self, target: SplitResult) -> dict[str, str]:
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
        }
        port = target.port or (443 if target.scheme == "https" else 80)
        default_port = 443 if target.scheme == "https" else 80
        headers["Host"] = target.hostname if port == default_port else f"{target.hostname}:{port}"
        return headers

    @staticmethod
    def _target_path(target: SplitResult) -> str:
        path = target.path or "/"
        return f"{path}?{target.query}" if target.query else path

    def _relay(self, client: socket.socket, upstream: socket.socket) -> None:
        selector = selectors.DefaultSelector()
        selector.register(client, selectors.EVENT_READ, upstream)
        selector.register(upstream, selectors.EVENT_READ, client)
        try:
            while True:
                events = selector.select(timeout=self.server.read_timeout)
                if not events:
                    return
                for key, _ in events:
                    source = key.fileobj
                    target = key.data
                    data = source.recv(64 * 1024)
                    if not data:
                        return
                    target.sendall(data)
        finally:
            selector.close()

    def log_message(self, fmt: str, *args: object) -> None:
        LOG.debug("%s - %s", self.address_string(), fmt % args)


class ProxyServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], authorizer: ScopeAuthorizer, connect_timeout: float, read_timeout: float):
        super().__init__(address, ProxyHandler)
        self.authorizer = authorizer
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout


def _provider_endpoints(raw: str) -> set[tuple[str, int]]:
    endpoints: set[tuple[str, int]] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        host, separator, raw_port = item.rpartition(":")
        try:
            endpoints.add(((host if separator else item).lower(), int(raw_port) if separator else 443))
        except ValueError:
            raise SystemExit(f"invalid provider endpoint: {item}")
    return endpoints


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18888)
    parser.add_argument("--context-root", default=os.environ.get("CAIRN_PROJECT_CONTEXT_ROOT", "/scope"))
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--read-timeout", type=float, default=300.0)
    args = parser.parse_args()
    token = os.environ.get("CAIRN_WORKER_EGRESS_PROXY_TOKEN", "").strip()
    if not token:
        raise SystemExit("CAIRN_WORKER_EGRESS_PROXY_TOKEN is required")
    providers = _provider_endpoints(os.environ.get("CAIRN_EGRESS_PROVIDER_ENDPOINTS", ""))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = ProxyServer(
        (args.host, args.port),
        ScopeAuthorizer(Path(args.context_root), token, providers),
        args.connect_timeout,
        args.read_timeout,
    )
    LOG.info("egress proxy listening on %s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
