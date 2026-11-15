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
