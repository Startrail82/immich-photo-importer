"""Nagebootste Immich-server voor tests (alleen de endpoints die wij gebruiken)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeImmichState:
    def __init__(self, api_key: str = "geheim", import_paths: list[str] | None = None):
        self.api_key = api_key
        self.libraries = [{"id": "lib-1", "name": "Ongesorteerd", "importPaths": import_paths or []}]
        self.assets: dict[str, str] = {}  # originalPath -> asset id
        self.asset_info: dict[str, dict] = {}  # asset id -> {"originalPath", "isFavorite", "rating"}
        self.page_size_cap = 1000
        self.hide_first_searches = 0  # zoekopdrachten die nog niets vinden (simuleert trage import)
        self.forced_status: dict[tuple[str, str], int] = {}  # (methode, pad) -> http-status
        self.search_calls = 0
        self.scan_calls: list[str] = []
        self.favorite_calls: list[list[str]] = []
        self.keys_seen: list[str | None] = []


class _Handler(BaseHTTPRequestHandler):
    state: FakeImmichState

    def log_message(self, *args):  # stil
        pass

    def _send(self, status: int, payload=None):
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length)) if length else None

    def _handle(self, method: str):
        st = self.state
        path = self.path
        st.keys_seen.append(self.headers.get("x-api-key"))
        if st.forced_status.get((method, path)):
            return self._send(st.forced_status[(method, path)], {"message": "geforceerd"})
        if path != "/api/server/ping" and self.headers.get("x-api-key") != st.api_key:
            return self._send(401, {"message": "ongeldige sleutel"})
        body = self._body()

        if method == "GET" and path == "/api/server/ping":
            return self._send(200, {"res": "pong"})
        if method == "GET" and path == "/api/libraries":
            return self._send(200, st.libraries)
        if method == "POST" and path.startswith("/api/libraries/") and path.endswith("/scan"):
            st.scan_calls.append(path.split("/")[3])
            return self._send(204)
        if method == "POST" and path == "/api/search/metadata":
            wanted = (body or {}).get("originalPath")
            if body and ("isFavorite" in body or "rating" in body):
                hits = [
                    {"id": i, "originalPath": a["originalPath"], "isFavorite": a.get("isFavorite", False)}
                    for i, a in sorted(st.asset_info.items())
                    if all(a.get(k) == v for k, v in body.items() if k in ("isFavorite", "rating"))
                ]
                cap = min(body.get("size", 1000), st.page_size_cap)
                page = int(body.get("page", 1))
                chunk = hits[(page - 1) * cap : page * cap]
                more = str(page + 1) if page * cap < len(hits) else None
                return self._send(
                    200, {"assets": {"items": chunk, "total": len(hits), "count": len(chunk), "nextPage": more}}
                )
            st.search_calls += 1
            items = []
            if st.search_calls > st.hide_first_searches and wanted in st.assets:
                items = [{"id": st.assets[wanted], "originalPath": wanted}]
            return self._send(
                200, {"assets": {"items": items, "total": len(items), "count": len(items), "nextPage": None}}
            )
        if method == "PUT" and path == "/api/assets":
            assert body.get("isFavorite") is True
            st.favorite_calls.append(list(body["ids"]))
            for i in body["ids"]:
                if i in st.asset_info:
                    st.asset_info[i]["isFavorite"] = True
            return self._send(204)
        return self._send(404, {"message": f"onbekend: {method} {path}"})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")


class FakeImmich:
    def __init__(self, state: FakeImmichState):
        handler = type("Handler", (_Handler,), {"state": state})
        self.state = state
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
