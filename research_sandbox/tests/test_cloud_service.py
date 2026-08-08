import json
from pathlib import Path

import cloud_service


def test_disabled_empty_inbox_is_safe_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_BRAIN_ENABLED", "false")
    monkeypatch.setenv("RESEARCH_BRAIN_KILL_SWITCH", "false")
    result = cloud_service.process_once(tmp_path)
    assert result["status"] == "SAFE_NOOP_DISABLED"
    assert result["experiments_started"] == 0
    assert set(x.name for x in tmp_path.iterdir()) == {"inbox", "knowledge", "output", "audit"}


def test_kill_switch_wins_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_BRAIN_ENABLED", "true")
    monkeypatch.setenv("RESEARCH_BRAIN_KILL_SWITCH", "true")
    result = cloud_service.process_once(tmp_path)
    assert result["status"] == "SAFE_NOOP_KILLED"
    assert result["experiments_started"] == 0


def test_status_has_hard_permission_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv(cloud_service.ROOT_ENV, str(tmp_path))
    monkeypatch.setenv("RESEARCH_BRAIN_ENABLED", "false")
    result = cloud_service.status()
    assert result["brain_enabled"] is False
    assert all(value.startswith("DENIED") for value in result["permission_boundary"].values())
    assert Path(result["persistent_root"]) == tmp_path.resolve()


def test_only_quarantine_directories_are_materialized(tmp_path):
    paths = cloud_service.ensure_quarantine(tmp_path)
    assert set(paths) == {"inbox", "knowledge", "output", "audit"}
    assert all(Path(value).parent == tmp_path for value in paths.values())
