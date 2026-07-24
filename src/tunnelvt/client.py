"""WebSocket tunnel client — connect, register, forward requests to localhost.

Works through Cloudflare — WebSocket upgrade over HTTPS, server IP hidden.
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
from typing import Any
from urllib.parse import urlparse, urlunparse

import websocket

logger = logging.getLogger("tunnelvt")

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade",
}

MAX_BODY = 10 * 1024 * 1024


def _generate_device_id() -> str:
    return "".join(random.choices("0123456789abcdef", k=16))


def _build_ws_url(server_url: str) -> str:
    p = urlparse(server_url)
    scheme = "wss" if p.scheme in ("https", "wss") else "ws"
    return urlunparse((scheme, p.netloc, "/_tunnel/connect", "", "", ""))


class TunnelVT:
    """Connect to a tunnelvt server and expose a local port.

    Parameters
    ----------
    server_url : str
        Tunnel server URL, e.g. ``"https://tunnel.example.com"``.
    app : str
        App name for this tunnel.
    port : int
        Local port to expose.
    token : str
        Pre-shared auth token.
    device : str | None
        Device ID. Random 16-char hex if omitted.
    """

    def __init__(
        self,
        server_url: str,
        app: str,
        port: int,
        token: str = "",
        device: str | None = None,
    ) -> None:
        self.server_url = server_url
        self.app = app
        self.port = port
        self.token = token
        self.device = device or _generate_device_id()
        self._ws: websocket.WebSocket | None = None

    def connect(self) -> None:
        """Dial the server, register, and block forwarding requests."""
        ws_url = _build_ws_url(self.server_url)
        self._ws = websocket.create_connection(ws_url, timeout=30)
        try:
            self._register()
            self._read_loop()
        finally:
            self._ws.close()

    def _register(self) -> None:
        msg = {
            "type": "register",
            "token": self.token,
            "device": self.device,
            "app": self.app,
            "port": self.port,
        }
        self._send(msg)
        ack = self._recv()
        if ack is None:
            raise RuntimeError("server closed connection")
        if ack.get("type") == "error":
            raise RuntimeError(f"server rejected registration: {ack.get('error')}")
        self.device = ack.get("device", self.device)
        logger.info("connected — %s/%s -> localhost:%d", self.device, self.app, self.port)

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
                "type": "response",
                "id": req_id,
                "status": resp.status,
                "headers": resp_headers,
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
