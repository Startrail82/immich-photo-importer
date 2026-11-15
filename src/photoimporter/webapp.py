"""Kleine webinterface (alleen standaardbibliotheek): status, log, aandacht nodig, 'nu uitvoeren'.

Er is bewust geen eigen login: de container staat achter Traefik + Authentik.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import Config
from .icloud import STATE_KEY
from .scheduler import Scheduler
from .store import Store

INDEX = Path(__file__).parent / "web" / "index.html"


def _rows(rows) -> list[dict]:
    return [dict(r) for r in rows]


class WebApp:
    def __init__(self, cfg: Config, store: Store, scheduler: Scheduler, host: str = "0.0.0.0", port: int = 8000):
        self.cfg, self.store, self.scheduler = cfg, store, scheduler
        app = self

        class Handler(_Handler):
            application = app

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="web", daemon=True)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    # --- gegevens voor de API ------------------------------------------------
    def status(self) -> dict:
        runs = self.store.list_runs(1)
        last = dict(runs[0]) if runs else None
        if last and last.get("stats"):
            last["stats"] = json.loads(last["stats"])
        nxt = self.scheduler.next_run
        return {
            "version": __version__,
            "running": self.scheduler.running or bool(last and last["status"] == "bezig"),
            "next_run": nxt.strftime("%Y-%m-%d %H:%M") if nxt else None,
            "icloud_status": self.store.get_state(STATE_KEY, "onbekend"),
            "icloud_configured": self.cfg.icloud_enabled,
            "icloud_web_url": self.cfg.icloud_web_url,
            "immich_configured": self.cfg.immich_enabled,
            "pending_favorites": len(self.store.list_pending_favorites()),
            "open_review": len(self.store.list_review(True)),
            "last_run": last,
        }


class _Handler(BaseHTTPRequestHandler):
    application: WebApp
    server_version = "photoimporter"

    def log_message(self, *args) -> None:  # geen toegangslog in docker logs
        pass

    # --- helpers --------------------------------------------------------------
    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True  # geen browser-cross-site verzoek
        return urlparse(origin).netloc == self.headers.get("Host", "")

    # --- routes ---------------------------------------------------------------
    def do_GET(self) -> None:
        url = urlparse(self.path)
        app, query = self.application, parse_qs(urlparse(self.path).query)
        if url.path in ("/", "/index.html"):
            body = INDEX.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/healthz":
            self._json({"ok": True})
        elif url.path == "/api/status":
            self._json(app.status())
        elif url.path == "/api/runs":
            self._json(_rows(app.store.list_runs(int(query.get("limit", ["30"])[0]))))
        elif m := re.fullmatch(r"/api/runs/(\d+)/events", url.path):
            after = int(query.get("after", ["0"])[0])
            self._json(_rows(app.store.list_events(int(m.group(1)), after, 2000)))
        elif url.path == "/api/review":
            self._json(_rows(app.store.list_review(True)))
        else:
            self._json({"error": "niet gevonden"}, 404)

    def do_POST(self) -> None:
        app = self.application
        if not self._same_origin():
            return self._json({"error": "verzoek van een andere herkomst geweigerd"}, 403)
        if self.path == "/api/run":
            if app.scheduler.trigger_now():
                return self._json({"started": True}, 202)
            return self._json({"started": False, "error": "er loopt al een run"}, 409)
        if m := re.fullmatch(r"/api/review/(\d+)/resolve", self.path):
            app.store.resolve_review(int(m.group(1)))
            return self._json({"ok": True})
        self._json({"error": "niet gevonden"}, 404)
