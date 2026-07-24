"""WebSocket tunnel client — username+password → JWT, auto-reconnect with backoff.
First run prompts for credentials. JWT expires 7 days — auto-refreshes.
"""

from __future__ import annotations

import base64
import getpass
import json
import logging
import random
import sys
import threading
import time
from http.client import HTTPConnection, HTTPResponse
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

import websocket

logger = logging.getLogger("tunnelvt")

DEFAULT_SERVER = "https://gotunnel.vinstechid.com"
VERSION = "1.0.0"
BUILD_HASH = "dev"

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade",
}
MAX_BODY = 10 * 1024 * 1024


def _identity_file() -> Path:
    return Path.home() / ".tunnelvt.json"


def _build_ws_url(server_url: str) -> str:
    p = urlparse(server_url)
    scheme = "wss" if p.scheme in ("https", "wss") else "ws"
    return urlunparse((scheme, p.netloc, "/_tunnel/connect", "", "", ""))


def _fetch_jwt(server_url: str, username: str, password: str) -> str:
    data = json.dumps({"username": username, "password": password}).encode()
    req = Request(server_url + "/_tunnel/auth", data=data,
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=10) as resp:
        if resp.status != 200:
            raise RuntimeError(resp.read().decode())
        return json.loads(resp.read())["jwt"]


class TunnelVT:
    """Connect to gotunnel and expose a local port."""

    def __init__(self, app: str = "", port: int = 0) -> None:
        self.app = app
        self.port = port
        self._username = ""
        self._password = ""
        self._jwt = ""
        self._ws: websocket.WebSocket | None = None

    def connect(self) -> None:
        self._ensure_jwt()
        backoff = 1.0
        while True:
            try:
                self._connect_ws()
                backoff = 1.0  # reset on clean disconnect
            except Exception as e:
                msg = str(e)
                if "invalid or expired" in msg:
                    self._jwt = ""
                    self._ensure_jwt()
                    backoff = 1.0
                    continue
                jitter = backoff * 0.25 * (2 * random.random() - 1)
                wait = backoff + jitter
                logger.info("reconnecting in %.1fs", wait)
                time.sleep(wait)
                backoff = min(backoff * 2, 60.0)

    def _connect_ws(self) -> None:
        ws_url = _build_ws_url(DEFAULT_SERVER)
        self._ws = websocket.create_connection(ws_url, timeout=30)
        try:
            self._register()
            self._read_loop()
        finally:
            self._ws.close()

    def _ensure_jwt(self) -> None:
        idf = _identity_file()
        if idf.exists():
            try:
                data = json.loads(idf.read_text())
                if data.get("username"):
                    self._username = data["username"]
                    self._password = data.get("password", "")
                    self._jwt = data.get("jwt", "")
            except Exception:
                pass

        if not self._username:
            self._username = input("Username: ").strip()
            if not self._username:
                raise RuntimeError("username required")
            self._password = getpass.getpass("Password: ").strip()
            if not self._password:
                raise RuntimeError("password required")

        if not self._jwt:
            try:
                self._jwt = _fetch_jwt(DEFAULT_SERVER, self._username, self._password)
            except Exception:
                idf.unlink(missing_ok=True)
                self._username = ""
                self._password = ""
                raise

        idf.write_text(json.dumps({
            "username": self._username,
            "password": self._password,
            "jwt": self._jwt,
        }))

    def _register(self) -> None:
        msg = {
            "type": "register", "jwt": self._jwt,
            "app": self.app, "port": self.port,
            "version": VERSION, "vhash": BUILD_HASH,
        }
        self._send(msg)
        ack = self._recv()
        if ack is None:
            raise RuntimeError("connection closed")
        if ack.get("type") == "error":
            raise RuntimeError(ack.get("error", "server rejected"))
        print(f"https://gotunnel.vinstechid.com/a/{self._username}/{self.app}/")
        logger.info("connected — %s/%s -> localhost:%d", self._username, self.app, self.port)

    def _read_loop(self) -> None:
        while True:
            msg = self._recv()
            if msg is None:
                break
            if msg.get("type") == "request":
                t = threading.Thread(target=self._handle_request, args=(msg,), daemon=True)
                t.start()

    def _handle_request(self, msg: dict[str, Any]) -> None:
        self._send(self._do_local(msg))

    def _do_local(self, msg: dict[str, Any]) -> dict[str, Any]:
        req_id = msg.get("id", "")
        def err(e: str) -> dict[str, Any]:
            return {"type": "error", "id": req_id, "error": e}
        try:
            body = base64.b64decode(msg.get("body", ""))
        except Exception:
            return err("invalid body encoding")
        conn = HTTPConnection("localhost", self.port, timeout=25)
        try:
            headers = {k: v for k, v in msg.get("headers", {}).items()
                       if k.lower() not in HOP_BY_HOP}
            conn.request(method=msg.get("method", "GET"), url=msg.get("path", "/"),
                         body=body if body else None, headers=headers)
            resp: HTTPResponse = conn.getresponse()
            resp_body = resp.read(MAX_BODY)
            return {
                "type": "response", "id": req_id, "status": resp.status,
                "headers": dict(resp.getheaders()),
                "body": base64.b64encode(resp_body).decode(),
            }
        except Exception as exc:
            return err(f"local request failed: {exc}")
        finally:
            conn.close()

    def _send(self, msg: dict[str, Any]) -> None:
        if self._ws:
            self._ws.send(json.dumps(msg))

    def _recv(self) -> dict[str, Any] | None:
        if self._ws is None:
            return None
        try:
            return json.loads(self._ws.recv())
        except websocket.WebSocketConnectionClosedException:
            return None
