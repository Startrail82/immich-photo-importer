"""Kleine client voor de Immich-API (alleen wat we nodig hebben, zonder extra dependencies)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import PurePosixPath


class ImmichError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _norm(path: str) -> PurePosixPath:
    return PurePosixPath(path.rstrip("/") or "/")


class ImmichClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        base = base_url.strip().rstrip("/")
        if not base.endswith("/api"):
            base += "/api"
        self._base = base
        self._api_key = api_key
        self._timeout = timeout

    # --- laag niveau -----------------------------------------------------
    def _request(self, method: str, path: str, body: object | None = None) -> object | None:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"x-api-key": self._api_key, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self._base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            hint = ""
            if exc.code in (401, 403):
                hint = " (API-sleutel ongeldig of mist rechten)"
            raise ImmichError(f"{method} {path} gaf HTTP {exc.code}{hint}: {detail}", exc.code) from exc
        except OSError as exc:  # o.a. URLError, timeouts
            reason = getattr(exc, "reason", exc)
            raise ImmichError(f"Immich niet bereikbaar op {self._base}: {reason}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ImmichError(f"{method} {path}: antwoord is geen geldige JSON") from exc

    # --- bewerkingen -----------------------------------------------------
    def ping(self) -> bool:
        result = self._request("GET", "/server/ping")
        return isinstance(result, dict) and result.get("res") == "pong"

    def list_libraries(self) -> list[dict]:
        result = self._request("GET", "/libraries")
        return result if isinstance(result, list) else []

    def find_library_id(self, immich_path: str) -> str | None:
        """Zoekt de bibliotheek waarvan een importpad gelijk is aan, of een bovenliggende map is van, het pad."""
        target = _norm(immich_path)
        best: tuple[int, str] | None = None
        for library in self.list_libraries():
            for raw in library.get("importPaths") or []:
                import_path = _norm(raw)
                if import_path == target:
                    return library["id"]
                if import_path in target.parents:
                    depth = len(import_path.parts)
                    if best is None or depth > best[0]:
                        best = (depth, library["id"])
        return best[1] if best else None

    def scan_library(self, library_id: str) -> None:
        self._request("POST", f"/libraries/{library_id}/scan", {})

    def find_asset_id(self, original_path: str) -> str | None:
        """Zoekt een asset op exact bestandspad zoals Immich het kent."""
        result = self._request("POST", "/search/metadata", {"originalPath": original_path, "size": 10})
        assets = result.get("assets") if isinstance(result, dict) else None
        for item in (assets or {}).get("items", []):
            if item.get("originalPath") == original_path:
                return item["id"]
        return None

    def iter_assets(self, filters: dict, size: int = 1000):
        """Loopt over alle assets die aan de filters voldoen (bv. {"isFavorite": True})."""
        page: int | None = 1
        while page:
            result = self._request("POST", "/search/metadata", {**filters, "page": page, "size": size})
            assets = result.get("assets") if isinstance(result, dict) else None
            assets = assets or {}
            yield from assets.get("items") or []
            nxt = assets.get("nextPage")
            page = int(nxt) if nxt else None

    def set_favorite(self, asset_ids: list[str], chunk: int = 500) -> None:
        for start in range(0, len(asset_ids), chunk):
            self._request("PUT", "/assets", {"ids": asset_ids[start : start + chunk], "isFavorite": True})
