"""WebSocket tunnel client — auto-fetch JWT on first run, save to ~/.tunnelvt.json.

No login. Identity is a random device ID + JWT persisted locally. Survives
network changes, reboots, different WiFi. Not tied to MAC or IP.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import random
import sys
import threading
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


def _generate_device_id() -> str:
    return "".join(random.choices("0123456789abcdef", k=16))


def _build_ws_url(server_url: str) -> str:
    p = urlparse(server_url)
    scheme = "wss" if p.scheme in ("https", "wss") else "ws"
    return urlunparse((scheme, p.netloc, "/_tunnel/connect", "", "", ""))


def _fetch_jwt(server_url: str, device: str) -> str:
    data = json.dumps({"device": device}).encode()
    req = Request(server_url + "/_tunnel/hello", data=data,
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read())
        return body["jwt"]


class TunnelVT:
    """Connect to gotunnel and expose a local port.

    Parameters
    ----------
    app : str
        App name for this tunnel.
    port : int
        Local port to expose.
    """

    def __init__(self, app: str = "", port: int = 0) -> None:
        self.app = app
        self.port = port
        self._device = ""
        self._jwt = ""
        self._ws: websocket.WebSocket | None = None

    def connect(self) -> None:
        self._load_or_fetch_identity()
        ws_url = _build_ws_url(DEFAULT_SERVER)
        self._ws = websocket.create_connection(ws_url, timeout=30)
        try:
            self._register()
            self._read_loop()
        finally:
            self._ws.close()

    def _load_or_fetch_identity(self) -> None:
        idf = _identity_file()
        if idf.exists():
            try:
                data = json.loads(idf.read_text())
                if data.get("jwt"):
                    self._device = data["device"]
                    self._jwt = data["jwt"]
                    return
            except Exception:
                pass

        self._device = _generate_device_id()
        self._jwt = _fetch_jwt(DEFAULT_SERVER, self._device)
        idf.write_text(json.dumps({"device": self._device, "jwt": self._jwt}))

    def _register(self) -> None:
        msg = {
            "type": "register",
            "jwt": self._jwt,
            "device": self._device,
            "app": self.app,
            "port": self.port,
            "version": VERSION,
            "vhash": BUILD_HASH,
        }
        self._send(msg)
        ack = self._recv()
        if ack is None:
            raise RuntimeError("server closed connection")
        if ack.get("type") == "error":
            raise RuntimeError(f"server rejected registration: {ack.get('error')}")
        self._device = ack.get("device", self._device)
        print(f"https://gotunnel.vinstechid.com/{self._device}/{self.app}/")
        logger.info("connected — %s/%s -> localhost:%d", self._device, self.app, self.port)

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
            conn.request(
                method=msg.get("method", "GET"),
                url=msg.get("path", "/"),
                body=body if body else None,
                headers=headers,
            )
            resp: HTTPResponse = conn.getresponse()
            resp_body = resp.read(MAX_BODY)
            resp_headers = dict(resp.getheaders())
            return {
                "type": "response", "id": req_id, "status": resp.status,
                "headers": resp_headers, "body": base64.b64encode(resp_body).decode(),
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
