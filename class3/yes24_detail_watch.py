"""실행 중 수집기를 감시하고 확인된 서버 오류에서만 제한적으로 재개한다."""

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime

from yes24_detail_collection import ROOT, now_iso, single_process_lock

DELAYS = (900, 1800, 3600)
ERROR_HOST = "image.yes24.com"
ERROR_PATH = "/sysimage/contents/Error/error_500.html"


def read_state(database):
    with closing(
        sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        db.execute("BEGIN")
        meta = {
            key: json.loads(value) for key, value in db.execute("SELECT * FROM meta")
        }
        pending = db.execute(
            "SELECT count(*) FROM jobs WHERE status='pending'"
        ).fetchone()[0]
    return meta, pending


def collector_running(lock):
    try:
        with single_process_lock(lock):
            return False
    except OSError:
        return True


def decide(meta, pending, state, timestamp):
    """반환값은 동작과 재개까지의 대기 초다. 접근 제한 상태를 덮어쓰지 않는다."""
    if state.get("pending") != pending:
        if state.get("pending", pending) > pending:
            state["attempts"] = 0
            state.pop("retry_at", None)
        state["pending"] = pending
    status = meta.get("run_status", "")
    if pending == 0 and status in ("finished", "finished_with_review"):
        return "finished", 0
    request = meta.get("last_request") or {}
    if not (
        status == "blocked"
        and request.get("status") == 302
        and request.get("redirect_host") == ERROR_HOST
        and request.get("redirect_path") == ERROR_PATH
        and str(request.get("path", "")).startswith("/product/goods/")
        and meta.get("last_error") == f"HTTP 302: {request['path']}; 자동 재시도 중단"
    ):
        return "needs_attention", 0
    attempts = state.get("attempts", 0)
    if attempts >= len(DELAYS):
        return "retry_exhausted", 0
    if "retry_at" not in state:
        try:
            failed_at = datetime.fromisoformat(request["at"]).timestamp()
        except (KeyError, ValueError, TypeError):
            failed_at = timestamp
        state["retry_at"] = min(failed_at, timestamp) + DELAYS[attempts]
    remaining = max(0, state["retry_at"] - timestamp)
    return ("waiting" if remaining else "restart"), remaining


def save_state(path, state):
    state["updated_at"] = now_iso()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def launch(part):
    return subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "yes24_detail_collection.py"),
            "--part",
            str(part),
            "--through-year",
            "2026",
            "--resume-after-stop",
        ],
        cwd=ROOT.parent,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def watch(part, directory):
    directory.mkdir(parents=True, exist_ok=True)
    prefix = directory / f"yes24_watch_part{part}"
    state_path = prefix.with_suffix(".json")
    stop_path = prefix.with_suffix(".stop")
    database = ROOT / f"data/yes24_details_2010_2026.part{part}.sqlite3"
    lock = ROOT / "data/yes24_details_2010_2026.lock"
    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists()
        else {}
    )
    state.update(pid=os.getpid(), part=part, started_at=now_iso())
    child = None
    previous = None
    heartbeat = 0
    with single_process_lock(directory / "yes24_watch.lock"):
        while True:
            timestamp = time.time()
            if stop_path.exists():
                state["status"] = "stopped_by_user"
                save_state(state_path, state)
                logging.info("감시 중단 요청 확인. 실행 중 수집기는 계속 실행됩니다.")
                return 0
            meta, pending = read_state(database)
            active = (child is not None and child.poll() is None) or collector_running(
                lock
            )
            if active:
                # 수집이 진행된 경우에만 재개 한도를 초기화한다.
                if pending < state.get("pending", pending):
                    state["attempts"] = 0
                    state.pop("retry_at", None)
                state["pending"] = pending
                action, delay = "monitoring", 0
            else:
                action, delay = decide(meta, pending, state, timestamp)
            state.update(status=action, last_error=meta.get("last_error"))
            if action == "restart":
                state["attempts"] = state.get("attempts", 0) + 1
                state.pop("retry_at", None)
                save_state(state_path, state)
                child = launch(part)
                state["collector_pid"] = child.pid
                logging.warning(
                    "서버 오류 후 재개 %s/3, 수집기 PID %s",
                    state["attempts"],
                    child.pid,
                )
            save_state(state_path, state)
            if action != previous or timestamp - heartbeat >= 600:
                logging.info(
                    "상태=%s, 미처리=%s, 대기=%.0f초, 오류=%s",
                    action,
                    pending,
                    delay,
                    state["last_error"],
                )
                previous, heartbeat = action, timestamp
            if action in ("finished", "needs_attention", "retry_exhausted"):
                return 0 if action == "finished" else 1
            time.sleep(30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--part", type=int, choices=(1, 2), required=True)
    args = parser.parse_args()
    directory = ROOT.parent / ".tmp"
    directory.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(
                directory / f"yes24_watch_part{args.part}.log", encoding="utf-8"
            )
        ],
    )
    try:
        return watch(args.part, directory)
    except Exception:
        logging.exception("감시 오류: 자동 재개를 중단합니다.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
