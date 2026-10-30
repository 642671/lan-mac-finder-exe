"""Local HTTP server used by the Windows EXE application."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from . import APP_ID, __version__
from .windows_network import (
    ScanManager,
    collect_devices,
    discover_networks,
    normalize_mac_suffix,
)


def resource_root() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return str(sys._MEIPASS)
    return str(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


FRONTEND_DIR = os.path.join(resource_root(), "frontend")


def route_parts(path: str) -> list[str]:
    parts = [
        unquote(part)
        for part in urlsplit(path).path.split("/")
        if part
    ]
    while APP_ID in parts:
        index = parts.index(APP_ID)
        del parts[: index + 1]
    if parts and parts[0] == "api":
        parts = parts[1:]
    return parts


class LocalAppHandler(BaseHTTPRequestHandler):
    server_version = f"LanMacFinder/{__version__}"
    timeout = 30
    frontend_dir = FRONTEND_DIR

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(
        self,
        status: int,
        payload: Mapping[str, Any],
        *,
        head_only: bool = False,
    ) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(raw)

    def _send_bytes(
        self,
        status: int,
        content_type: str,
        data: bytes,
        *,
        cache: str = "no-store",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        full_path = os.path.normpath(os.path.join(self.frontend_dir, relative))
        root = os.path.normpath(self.frontend_dir)
        if not full_path.startswith(root + os.sep) and full_path != root:
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        if not os.path.isfile(full_path):
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        extension = os.path.splitext(full_path)[1].lower()
        content_types = {
            ".css": "text/css; charset=utf-8",
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }
        with open(full_path, "rb") as handle:
            data = handle.read()
        if extension == ".html":
            data = data.replace(b"__APP_VERSION__", __version__.encode("ascii"))
        self._send_bytes(
            200,
            content_types.get(extension, "application/octet-stream"),
            data,
        )

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > 16 * 1024:
            raise ValueError("request body is too large")
        if length == 0:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _manager(self) -> ScanManager:
        return self.server.scan_manager

    def _targets(self):
        return self.server.discovery_cache.get()

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        parts = route_parts(self.path)
        try:
            if parts == ["health"]:
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "status": "ok",
                        "app": APP_ID,
                        "version": __version__,
                        "python": sys.version.split()[0],
                        "max_hosts": 1022,
                    },
                )
                return
            if parts == ["networks"]:
                targets, warnings = self._targets()
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "app": APP_ID,
                        "version": __version__,
                        "max_hosts": 1022,
                        "networks": [target.as_dict() for target in targets],
                        "warnings": warnings,
                    },
                )
                return
            if parts == ["devices"]:
                targets, warnings = self._targets()
                devices, device_warnings = collect_devices(targets)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "app": APP_ID,
                        "version": __version__,
                        "devices": devices,
                        "warnings": warnings + device_warnings,
                    },
                )
                return
            if parts == ["scan", "status"]:
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "app": APP_ID,
                        "version": __version__,
                        **self._manager().status(),
                    },
                )
                return
            self._serve_static(path)
        except Exception as exc:
            self._send_json(500, {"ok": False, "app": APP_ID, "error": str(exc)})

    def do_POST(self) -> None:
        parts = route_parts(self.path)
        try:
            if parts == ["scan", "start"]:
                body = self._read_json_body()
                suffix = normalize_mac_suffix(str(body.get("suffix") or ""))
                if suffix is None:
                    self._send_json(
                        400,
                        {
                            "ok": False,
                            "app": APP_ID,
                            "error": "MAC suffix must use AA-BB, AA:BB, or AABB",
                        },
                    )
                    return
                started = self._manager().start(suffix)
                self._send_json(
                    202 if started else 200,
                    {
                        "ok": True,
                        "app": APP_ID,
                        "started": started,
                        "running": True,
                        "suffix": suffix,
                    },
                )
                return
            if parts == ["scan", "stop"]:
                self._read_json_body()
                stopping = self._manager().stop()
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "app": APP_ID,
                        "stopping": stopping,
                    },
                )
                return
            self._send_json(404, {"ok": False, "app": APP_ID, "error": "not found"})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"ok": False, "app": APP_ID, "error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"ok": False, "app": APP_ID, "error": str(exc)})


class LocalAppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int]) -> None:
        super().__init__(server_address, LocalAppHandler)
        self.scan_manager = ScanManager()
        self.discovery_cache = DiscoveryCache()


class DiscoveryCache:
    def __init__(self, ttl: float = 8.0) -> None:
        self.ttl = ttl
        self.lock = threading.Lock()
        self.created_at = 0.0
        self.targets = []
        self.warnings = []

    def get(self):
        with self.lock:
            if time.monotonic() - self.created_at > self.ttl:
                self.targets, self.warnings = discover_networks()
                self.created_at = time.monotonic()
            return list(self.targets), list(self.warnings)


def start_server(port: int = 0) -> tuple[LocalAppServer, threading.Thread, str]:
    server = LocalAppServer(("127.0.0.1", int(port)))
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.25},
        name="lan-mac-http",
        daemon=True,
    )
    thread.start()
    host, actual_port = server.server_address
    return server, thread, f"http://{host}:{actual_port}/"
