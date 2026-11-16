"""doctor: vooraf melden wat een run zal tegenkomen, zonder iets te wijzigen."""

from __future__ import annotations

import dataclasses
import io
import json
import os

import pytest

from photoimporter.config import Config
from photoimporter.doctor import ERROR, OK, WARNING, main, run_checks
from photoimporter.immich import ImmichError


def fake_exiftool(tmp_path, version="13.64") -> str:
    """Een exiftool dat alleen `-ver` beantwoordt: doctor leest niet meer dan dat."""
    tool = tmp_path / "exiftool"
    tool.write_text(f"#!/bin/sh\necho {version}\n", encoding="utf-8")
    tool.chmod(0o755)
    return str(tool)


def codes(report) -> dict[str, str]:
    return {check.name: check.level for check in report.checks}


def test_a_healthy_setup_reports_no_problems(env, tmp_path):
    cfg, store, _dirs = env
    report = run_checks(cfg, store=store, exiftool=fake_exiftool(tmp_path), network=False)
    assert report.ok and report.exit_code() == 0
    assert codes(report)["Doelmap"] == OK
    assert codes(report)["Planning"] == OK
    assert codes(report)["Immich"] == OK
    assert codes(report)["exiftool"] == OK
    assert report.as_dict()["errors"] == 0 and report.exit_code() == 0


def test_an_unmounted_target_is_an_error_a_run_would_fail_on(env, tmp_path):
    cfg, _store, _dirs = env
    cfg = dataclasses.replace(cfg, target=tmp_path / "niet-gemount")
    report = run_checks(cfg, exiftool="sh", network=False)
    assert codes(report)["Doelmap"] == ERROR and "NAS" in report.checks[1].message
    assert report.exit_code() == 2


def test_a_missing_exiftool_is_an_error_because_dates_would_all_be_file_dates(env, tmp_path):
    cfg, _store, _dirs = env
    report = run_checks(cfg, exiftool=str(tmp_path / "geen-exiftool"), network=False)
    level = codes(report)["exiftool"]
    assert level == ERROR
    message = next(c.message for c in report.checks if c.name == "exiftool")
    assert "bestandsdatum" in message and "libimage-exiftool-perl" in message
    assert report.exit_code() == 2


def test_a_missing_source_is_only_a_warning(env, tmp_path):
    cfg, _store, dirs = env
    dirs["inbox"].rmdir()
    report = run_checks(cfg, exiftool=fake_exiftool(tmp_path), network=False)
    assert codes(report)["Bron: inbox"] == WARNING
    assert report.exit_code() == 1
    assert report.ok is True  # een run sorteert gewoon door


def test_delete_in_icloud_is_reported_as_a_warning(env):
    cfg, _store, _dirs = env
    cfg = dataclasses.replace(cfg, icloud_keep_days=30)
    report = run_checks(cfg, exiftool="sh", network=False)
    assert codes(report)["Wissen in iCloud"] == WARNING
    message = next(c.message for c in report.checks if c.name == "Wissen in iCloud")
    assert "30 dagen" in message and "iPhone" in message


def test_an_impossible_schedule_and_nested_paths_are_errors(env):
    cfg, _store, dirs = env
    cfg = dataclasses.replace(cfg, schedule_cron="0 0 31 2 *")
    report = run_checks(cfg, exiftool="sh", network=False)
    assert codes(report)["Configuratie"] == ERROR
    assert "nooit" in next(c.message for c in report.checks if c.name == "Configuratie")

    nested = dataclasses.replace(cfg, source_inbox=dirs["target"] / "inbox")
    report = run_checks(nested, exiftool="sh", network=False)
    assert codes(report)["Configuratie"] == ERROR


def test_immich_failure_is_a_warning_with_the_reason(env, monkeypatch, tmp_path):
    cfg, store, _dirs = env
    cfg = dataclasses.replace(cfg, immich_url="https://immich.example", immich_api_key="k", immich_target_path="/photos")

    def boom(*args, **kwargs):
        raise ImmichError("Immich niet bereikbaar op https://immich.example/api: timed out")

    monkeypatch.setattr("photoimporter.doctor.ImmichClient", boom)
    report = run_checks(cfg, store=store, exiftool=fake_exiftool(tmp_path))
    assert codes(report)["Immich"] == WARNING
    message = next(c.message for c in report.checks if c.name == "Immich")
    assert "timed out" in message
    assert report.exit_code() == 1  # kopiëren gaat door, alleen de favorieten wachten


def test_immich_is_not_contacted_offline_or_when_unconfigured(env, monkeypatch):
    cfg, store, _dirs = env
    cfg = dataclasses.replace(cfg, immich_url="https://immich.example", immich_api_key="k", immich_target_path="/photos")

    def never(*args, **kwargs):
        raise AssertionError("doctor mag offline geen verbinding maken")

    monkeypatch.setattr("photoimporter.doctor.ImmichClient", never)
    assert codes(run_checks(cfg, store=store, exiftool="sh", network=False))["Immich"] == WARNING

    unset = dataclasses.replace(cfg, immich_url="")
    report = run_checks(unset, store=store, exiftool="sh")
    assert codes(report)["Immich"] == OK


def test_report_text_and_json_agree_on_the_outcome(env):
    cfg, _store, _dirs = env
    out = io.StringIO()
    assert main(cfg, as_json=False, network=False, out=out) in (0, 1, 2)
    text = out.getvalue()
    assert "Doelmap" in text and ("gevonden" in text or "geen problemen gevonden" in text)

    out = io.StringIO()
    code = main(cfg, as_json=True, network=False, out=out)
    data = json.loads(out.getvalue())
    assert data["ok"] in (True, False) and isinstance(data["checks"], list)
    assert code == (2 if data["errors"] else 1 if data["warnings"] else 0)
    assert {c["name"] for c in data["checks"]} >= {"Doelmap", "exiftool", "Planning"}


def test_doctor_changes_nothing_in_the_tree(env):
    cfg, store, dirs = env
    (dirs["inbox"] / "foto.jpg").write_bytes(b"\xff\xd8\xff\xe0 nep")
    before = sorted(str(p.relative_to(dirs["target"])) for p in dirs["target"].rglob("*"))
    run_checks(cfg, store=store, exiftool="sh", network=False)
    after = sorted(str(p.relative_to(dirs["target"])) for p in dirs["target"].rglob("*"))
    assert before == after == []
    assert (dirs["inbox"] / "foto.jpg").exists()
    assert store.list_runs(10) == []


def test_cli_doctor_exits_2_on_a_missing_target(env, monkeypatch, tmp_path, capsys):
    from photoimporter.cli import main as cli_main

    monkeypatch.setenv("PI_TARGET", str(tmp_path / "nergens"))
    monkeypatch.setenv("PI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PI_SOURCE_INBOX", str(tmp_path / "inbox"))
    (tmp_path / "inbox").mkdir()
    assert cli_main(["doctor", "--offline"]) == 2
    assert "Doelmap" in capsys.readouterr().out


def test_cli_doctor_json_output_is_parseable(env, monkeypatch, tmp_path, capsys):
    from photoimporter.cli import main as cli_main

    for key in ("PI_IMMICH_URL", "PI_IMMICH_API_KEY", "PI_ICLOUD_USERNAME", "PI_ICLOUD_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PI_TARGET", str(tmp_path / "target"))
    monkeypatch.setenv("PI_DATA_DIR", str(tmp_path / "data"))
    for name in ("inbox", "fav", "other", "target"):
        (tmp_path / name).mkdir(exist_ok=True)
    monkeypatch.setenv("PI_SOURCE_INBOX", str(tmp_path / "inbox"))
    monkeypatch.setenv("PI_SOURCE_FAVORITES", str(tmp_path / "fav"))
    monkeypatch.setenv("PI_SOURCE_OTHER", str(tmp_path / "other"))
    code = cli_main(["doctor", "--json", "--offline"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["checks"] and code == (2 if payload["errors"] else 1 if payload["warnings"] else 0)
