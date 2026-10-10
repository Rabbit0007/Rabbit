#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.client
import logging
import selectors
import socket
import socketserver
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import SplitResult, urlsplit


LOG = logging.getLogger("host-bridge-proxy")
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Host-side HTTP/HTTPS proxy bridge for Docker workers."
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18888)
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--read-timeout", type=float, default=60.0)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RabbitHostBridge/1.0"

    def do_CONNECT(self) -> None:  # noqa: N802
        host, port = self._parse_connect_target()
        if host is None or port is None:
            return
        try:
            upstream = socket.create_connection(
                (host, port), timeout=self.server.connect_timeout
            )
        except OSError as exc:
            LOG.warning("CONNECT %s failed: %s", self.path, exc)
            self.send_error(502, f"connect failed: {exc}")
            return

        try:
            self.send_response(200, "Connection established")
            self.end_headers()
            self.connection.setblocking(False)
            upstream.setblocking(False)
            self._relay_bidirectional(self.connection, upstream)
        finally:
            try:
                upstream.close()
            except OSError:
                pass

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

    def _forward_http(self) -> None:
        target = self._resolve_target()
        if target is None:
            return

        body = b""
        content_length = self.headers.get("Content-Length")
        if content_length:
            try:
                body = self.rfile.read(int(content_length))
            except ValueError:
                self.send_error(400, "invalid content-length")
                return

        connection_cls = (
            http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
        )
        try:
            upstream = connection_cls(
                target.hostname,
                target.port,
                timeout=self.server.read_timeout,
            )
            upstream.request(
                self.command,
                self._target_path(target),
                body=body if body else None,
                headers=self._request_headers(target),
            )
            response = upstream.getresponse()
        except Exception as exc:
            LOG.warning("%s %s failed: %s", self.command, self.path, exc)
            self.send_error(502, f"upstream failed: {exc}")
            return

        try:
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() in HOP_BY_HOP_HEADERS:
                    continue
                self.send_header(key, value)
            self.end_headers()

            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        finally:
            upstream.close()

    def _request_headers(self, target: SplitResult) -> dict[str, str]:
        headers: dict[str, str] = {}
        for key, value in self.headers.items():
            if key.lower() in HOP_BY_HOP_HEADERS:
                continue
            headers[key] = value
        host_header = target.netloc
        if target.port and (
            (target.scheme == "http" and target.port != 80)
            or (target.scheme == "https" and target.port != 443)
        ):
            host_header = f"{target.hostname}:{target.port}"
        elif target.hostname:
            host_header = target.hostname
        headers["Host"] = host_header
        return headers

    @staticmethod
    def _target_path(target: SplitResult) -> str:
        path = target.path or "/"
        if target.query:
            path += f"?{target.query}"
        return path

    def _resolve_target(self) -> SplitResult | None:
        parsed = urlsplit(self.path)
        if parsed.scheme and parsed.hostname:
            return self._normalize_target(parsed)

        host = self.headers.get("Host")
        if not host:
            self.send_error(400, "missing host header")
            return None
        parsed = urlsplit(f"http://{host}{self.path}")
        return self._normalize_target(parsed)

    def _normalize_target(self, parsed: SplitResult) -> SplitResult | None:
        scheme = parsed.scheme or "http"
        if scheme not in {"http", "https"}:
            self.send_error(400, f"unsupported scheme: {scheme}")
            return None
        port = parsed.port or (443 if scheme == "https" else 80)
        return SplitResult(scheme, f"{parsed.hostname}:{port}", parsed.path, parsed.query, "")

    def _parse_connect_target(self) -> tuple[str | None, int | None]:
        if ":" not in self.path:
            self.send_error(400, "invalid CONNECT target")
            return None, None
        host, raw_port = self.path.rsplit(":", 1)
        try:
            return host, int(raw_port)
        except ValueError:
            self.send_error(400, "invalid CONNECT port")
            return None, None

    def _relay_bidirectional(self, client: socket.socket, upstream: socket.socket) -> None:
        selector = selectors.DefaultSelector()
        selector.register(client, selectors.EVENT_READ, upstream)
        selector.register(upstream, selectors.EVENT_READ, client)
        try:
            while True:
                events = selector.select(timeout=self.server.read_timeout)
                if not events:
                    break
                for key, _ in events:
                    source: socket.socket = key.fileobj
                    target: socket.socket = key.data
                    try:
                        data = source.recv(64 * 1024)
                    except OSError:
                        return
                    if not data:
                        return
                    try:
                        target.sendall(data)
                    except OSError:
                        return
        finally:
            selector.close()

    def log_message(self, fmt: str, *args: object) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)


class ThreadedProxyServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_cls: type[ProxyHandler],
        *,
        connect_timeout: float,
        read_timeout: float,
    ) -> None:
        super().__init__(server_address, handler_cls)
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    server = ThreadedProxyServer(
        (args.host, args.port),
        ProxyHandler,
        connect_timeout=args.connect_timeout,
        read_timeout=args.read_timeout,
    )
    LOG.info("starting host bridge proxy on %s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("stopping host bridge proxy")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
