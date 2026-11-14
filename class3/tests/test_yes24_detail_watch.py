import importlib
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
watcher = importlib.import_module("yes24_detail_watch")


def blocked():
    return {
        "run_status": "blocked",
        "last_error": "HTTP 302: /product/goods/24417970; 자동 재시도 중단",
        "last_request": {
            "status": 302,
            "path": "/product/goods/24417970",
            "redirect_host": watcher.ERROR_HOST,
            "redirect_path": watcher.ERROR_PATH,
            "at": "1970-01-01T00:00:00+00:00",
        },
    }


def test_known_server_redirect_waits_and_then_restarts():
    state = {}
    assert watcher.decide(blocked(), 10, state, 100) == ("waiting", 800)
    assert watcher.decide(blocked(), 10, state, 900) == ("restart", 0)


@pytest.mark.parametrize("attempts,delay", [(0, 900), (1, 1800), (2, 3600)])
def test_retry_backoff(attempts, delay):
    assert watcher.decide(blocked(), 10, {"attempts": attempts}, 100) == (
        "waiting",
        delay - 100,
    )


def test_no_progress_exhausts_retries_but_progress_resets_budget():
    state = {"pending": 10, "attempts": 3}
    assert watcher.decide(blocked(), 10, state, 5000)[0] == "retry_exhausted"
    assert watcher.decide(blocked(), 9, state, 5000)[0] == "restart"
    assert state["attempts"] == 0


@pytest.mark.parametrize(
    "change",
    [
        {"run_status": "structure_error"},
        {"run_status": "interrupted"},
        {"run_status": "error"},
        {"last_error": "robots.txt 정책상 요청 불가"},
        {"last_request": {"status": 429}},
        {"last_request": {"status": 403}},
        {"last_request": {"status": 401}},
        {
            "last_request": {
                **blocked()["last_request"],
                "redirect_host": "other.example",
            }
        },
        {"last_request": {**blocked()["last_request"], "redirect_path": "/login"}},
    ],
)
def test_unknown_and_access_restrictions_never_restart(change):
    assert watcher.decide({**blocked(), **change}, 10, {}, 5000)[0] == "needs_attention"


def test_completed_collection_finishes_monitor():
    assert watcher.decide({"run_status": "finished_with_review"}, 0, {}, 5000) == (
        "finished",
        0,
    )


def test_read_state_uses_database_counts_without_changing_data(tmp_path):
    path = tmp_path / "books.sqlite3"
    store = importlib.import_module("yes24_detail_collection").Store(
        sqlite3.connect(path)
    )
    store.set_meta("run_status", "running")
    store.connection.close()
    before = path.read_bytes()
    assert watcher.read_state(path) == ({"run_status": "running"}, 0)
    assert path.read_bytes() == before


def test_monitor_observes_running_job_then_restarts_known_error_and_finishes(
    tmp_path, monkeypatch
):
    states = iter(
        [
            ({"run_status": "running"}, 10),
            (blocked(), 10),
            ({"run_status": "finished_with_review"}, 0),
        ]
    )
    locks = iter([True, False, False])
    calls = []
    monkeypatch.setattr(watcher, "read_state", lambda _: next(states))
    monkeypatch.setattr(watcher, "collector_running", lambda _: next(locks))
    monkeypatch.setattr(watcher.time, "sleep", lambda _: None)
    monkeypatch.setattr(watcher.time, "time", lambda: 5000)

    def launch(part):
        calls.append(part)
        return SimpleNamespace(pid=123, poll=lambda: 0)

    monkeypatch.setattr(watcher, "launch", launch)
    assert watcher.watch(2, tmp_path) == 0
    assert calls == [2]
    saved = json.loads(
        (tmp_path / "yes24_watch_part2.json").read_text(encoding="utf-8")
    )
    assert saved["status"] == "finished"


def test_stop_request_never_launches_collector(tmp_path, monkeypatch):
    (tmp_path / "yes24_watch_part2.stop").touch()
    monkeypatch.setattr(watcher, "launch", lambda _: pytest.fail("중단 요청 뒤 실행"))
    assert watcher.watch(2, tmp_path) == 0
