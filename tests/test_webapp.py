from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from photoimporter.organizer import RunResult, RunStats
from photoimporter.scheduler import Scheduler
from photoimporter.webapp import WebApp


@pytest.fixture
def web(env):
    cfg, store, dirs = env
    gate = threading.Event()
    gate.set()
    calls: list[str] = []

    def runner(cfg, store, trigger="handmatig"):
        calls.append(trigger)
        run_id = store.start_run(trigger, False)
        store.add_event(run_id, "INFO", "test", "hallo van de run", None)
        gate.wait(5)
        store.finish_run(run_id, "ok", RunStats(moved=3).as_json())
        return RunResult(run_id, "ok", RunStats(moved=3))

    scheduler = Scheduler(cfg, store, runner=runner)
    scheduler.start()
    app = WebApp(cfg, store, scheduler, host="127.0.0.1", port=0)
    app.start()
    base = f"http://127.0.0.1:{app.port}"
    yield base, store, scheduler, gate, calls
    gate.set()
    app.stop()
    scheduler.stop()


def call(url, method="GET", headers=None):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def wait_for(cond, seconds=5):
    import time

    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_index_and_health(web):
    base, *_ = web
    status, body = call(base + "/")
    assert status == 200 and b"Photo Importer" in body
    assert call(base + "/healthz")[0] == 200
    assert call(base + "/bestaat-niet")[0] == 404


def test_status_before_any_run(web):
    base, *_ = web
    data = json.loads(call(base + "/api/status")[1])
    assert data["running"] is False and data["last_run"] is None
    assert data["next_run"] and data["icloud_status"] == "onbekend"


def test_run_button_starts_run_and_log_is_readable(web):
    base, store, scheduler, gate, calls = web
    status, body = call(base + "/api/run", "POST")
    assert status == 202
    assert wait_for(lambda: calls == ["handmatig"] and not scheduler.running)
    runs = json.loads(call(base + "/api/runs")[1])
    assert runs[0]["status"] == "ok"
    events = json.loads(call(f"{base}/api/runs/{runs[0]['id']}/events")[1])
    assert events[0]["message"] == "hallo van de run"
    later = json.loads(call(f"{base}/api/runs/{runs[0]['id']}/events?after={events[0]['id']}")[1])
    assert later == []
    st = json.loads(call(base + "/api/status")[1])
    assert st["last_run"]["stats"]["moved"] == 3


def test_second_press_while_running_gives_409(web):
    base, store, scheduler, gate, calls = web
    gate.clear()
    assert call(base + "/api/run", "POST")[0] == 202
    assert wait_for(lambda: scheduler.running)
    assert call(base + "/api/run", "POST")[0] == 409
    assert json.loads(call(base + "/api/status")[1])["running"] is True
    gate.set()


def test_cross_origin_post_is_refused(web):
    base, _store, _sched, _gate, calls = web
    status, _ = call(base + "/api/run", "POST", {"Origin": "https://evil.example"})
    assert status == 403 and calls == []
    host = base.removeprefix("http://")
    assert call(base + "/api/run", "POST", {"Origin": f"http://{host}"})[0] == 202


def test_review_list_and_resolve(web):
    base, store, *_ = web
    store.add_review(1, "date_fallback", "/t/a.jpg", "geen EXIF")
    items = json.loads(call(base + "/api/review")[1])
    assert len(items) == 1 and items[0]["kind"] == "date_fallback"
    assert call(f"{base}/api/review/{items[0]['id']}/resolve", "POST")[0] == 200
    assert json.loads(call(base + "/api/review")[1]) == []
    assert json.loads(call(base + "/api/status")[1])["open_review"] == 0


# --- instellingen --------------------------------------------------------------


def post_json(url, payload):
    req = urllib.request.Request(
        url, method="POST", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_settings_are_readable_with_schedule_explanation(web):
    base, *_ = web
    status, body = call(base + "/api/settings")
    data = json.loads(body)
    assert status == 200
    assert data["settings"]["schedule_cron"] == "0 12 * * *"
    assert data["schedule"]["text"] == "elke dag om 12:00" and len(data["schedule"]["upcoming"]) == 3
    assert data["libraries"]["busy"] is False


def test_saving_settings_updates_the_planner_immediately(web):
    base, store, scheduler, gate, calls = web
    status, data = post_json(
        base + "/api/settings",
        {"schedule_cron": "15 6 * * 1-5", "libraries": ["SharedSync-ABC"], "delete_enabled": True, "keep_days": 10},
    )
    assert status == 200
    assert data["schedule"]["text"] == "elke werkdag (ma-vr) om 06:15"
    assert wait_for(lambda: scheduler.next_run.strftime("%H:%M") == "06:15")
    assert calls == []  # opslaan start geen run
    assert scheduler.effective_config().icloud_libraries == ("SharedSync-ABC",)
    assert scheduler.effective_config().icloud_keep_days == 10


def test_invalid_settings_give_a_clear_error_and_are_not_saved(web):
    base, *_ = web
    status, data = post_json(base + "/api/settings", {"schedule_cron": "elke dag"})
    assert status == 422 and "planning" in data["error"]
    status, data = post_json(base + "/api/settings", {"keep_days": 0})
    assert status == 422
    assert json.loads(call(base + "/api/settings")[1])["settings"]["schedule_cron"] == "0 12 * * *"


def test_settings_post_rejects_garbage_and_foreign_origin(web):
    base, *_ = web
    req = urllib.request.Request(base + "/api/settings", method="POST", data=b"niet json")
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=5)
    assert err.value.code == 400
    status, _ = call(base + "/api/settings", "POST", {"Origin": "https://kwaadaardig.example"})
    assert status == 403


def test_schedule_preview_endpoint(web):
    base, *_ = web
    ok = json.loads(call(base + "/api/schedule/preview?cron=*/30+*+*+*+*")[1])
    assert ok["valid"] and ok["text"] == "elke 30 minuten"
    bad = json.loads(call(base + "/api/schedule/preview?cron=foutief")[1])
    assert not bad["valid"] and bad["error"]


def test_refresh_libraries_runs_in_background_and_is_remembered(web, monkeypatch):
    base, *_ = web
    release = threading.Event()

    def fake_list(cfg, store, log):
        release.wait(5)
        return ["PrimarySync", "SharedSync-ABC"], "2 bibliotheek/bibliotheken gevonden"

    monkeypatch.setattr("photoimporter.webapp.list_libraries", fake_list)

    status, body = call(base + "/api/libraries/refresh", "POST")
    assert status == 202
    assert json.loads(call(base + "/api/settings")[1])["libraries"]["busy"] is True
    assert call(base + "/api/libraries/refresh", "POST")[0] == 409  # al bezig

    release.set()
    assert wait_for(lambda: json.loads(call(base + "/api/settings")[1])["libraries"]["busy"] is False)
    libs = json.loads(call(base + "/api/settings")[1])["libraries"]
    assert libs["names"] == ["PrimarySync", "SharedSync-ABC"]


def test_refresh_libraries_failure_is_shown_not_raised(web, monkeypatch):
    base, *_ = web
    monkeypatch.setattr(
        "photoimporter.webapp.list_libraries",
        lambda cfg, store, log: (None, "bibliotheken ophalen mislukt: not finished indexing yet"),
    )
    assert call(base + "/api/libraries/refresh", "POST")[0] == 202
    assert wait_for(lambda: "indexing" in json.loads(call(base + "/api/settings")[1])["libraries"]["message"])


def test_status_reports_icloud_busy_while_libraries_are_fetched(web, monkeypatch):
    base, *_ = web
    release = threading.Event()
    monkeypatch.setattr("photoimporter.webapp.list_libraries", lambda c, s, l: (release.wait(5), ["PrimarySync"], "ok")[1:])
    assert json.loads(call(base + "/api/status")[1])["icloud_busy"] is False
    call(base + "/api/libraries/refresh", "POST")
    assert json.loads(call(base + "/api/status")[1])["icloud_busy"] is True
    release.set()
    assert wait_for(lambda: json.loads(call(base + "/api/status")[1])["icloud_busy"] is False)


def test_settings_api_never_returns_secrets(web):
    base, *_ = web
    status, data = post_json(base + "/api/settings", {"icloud_password": "topgeheim", "immich_api_key": "sleutel123"})
    assert status == 200
    raw = json.dumps(data)
    assert "topgeheim" not in raw and "sleutel123" not in raw
    assert data["settings"]["icloud_password_set"] is True
    _, body = call(base + "/api/settings")
    assert b"topgeheim" not in body and b"sleutel123" not in body


def test_immich_test_endpoint_reports_missing_fields(web):
    base, *_ = web
    status, data = post_json(base + "/api/test/immich", {"immich_url": "", "immich_api_key": ""})
    assert status == 200 and data["ok"] is False and data["steps"][0]["name"] == "Gegevens"


def test_icloud_test_runs_in_background_and_result_is_polled(web, monkeypatch):
    base, *_ = web
    monkeypatch.setattr(
        "photoimporter.webapp.probe_icloud",
        lambda cfg, store, log: {"ok": True, "steps": [{"name": "Inloggen", "ok": True, "message": "gelukt"}]},
    )
    status, data = post_json(base + "/api/test/icloud", {"icloud_username": "a@b.c", "icloud_password": "x"})
    assert status == 202 and data["started"] is True

    def done():
        _, body = call(base + "/api/settings")
        t = json.loads(body)["icloud_test"]
        return not t["busy"] and t["result"] is not None

    assert wait_for(done)


def test_follow_file_setting_defaults_on_and_can_be_switched_off(web):
    base, store, scheduler, gate, calls = web
    assert json.loads(call(base + "/api/settings")[1])["settings"]["favorites_follow_file"] is True
    status, data = post_json(base + "/api/settings", {"favorites_follow_file": False})
    assert status == 200 and data["settings"]["favorites_follow_file"] is False
    assert scheduler.effective_config().favorites_follow_file is False


def test_favorites_schedule_settings_are_saved_and_validated(web):
    base, store, scheduler, gate, calls = web
    s = json.loads(call(base + "/api/settings")[1])["settings"]
    assert s["favorites_cron"] == "0 7-23 * * *" and s["favorites_schedule_enabled"] is True
    status, data = post_json(base + "/api/settings", {"favorites_cron": "0 8-20 * * *", "favorites_schedule_enabled": False})
    assert status == 200 and data["settings"]["favorites_cron"] == "0 8-20 * * *"
    eff = scheduler.effective_config()
    assert eff.favorites_cron == "0 8-20 * * *" and eff.favorites_schedule_enabled is False
    status, data = post_json(base + "/api/settings", {"favorites_cron": "elk uur"})
    assert status == 422 and "favorieten" in data["error"]
