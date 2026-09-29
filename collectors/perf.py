"""Fixed, read-only perf commands. Permission/PMU failures remain explicit."""

import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import time

from .parsers import parse_perf_stat


def perf_binary():
    return shutil.which("perf")


def start_stat(tids, seconds, binary=None):
    binary = binary or perf_binary()
    if not binary or not tids:
        return None
    command = [binary, "stat", "--per-thread", "--no-big-num", "--no-scale", "-x", ";",
               "-e", "{instructions,cycles}", "-t", ",".join(map(str, sorted(set(tids)))),
               "--", "sleep", str(seconds)]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env={**os.environ, "LC_ALL": "C"})
    except OSError as error:
        return {"error": str(error), "command": command}
    return {"process": process, "command": command, "monotonic_started": time.monotonic(),
            "started_at": datetime.now(timezone.utc).isoformat(timespec="microseconds")}


def finish_stat(handle, seconds):
    if handle is None:
        return {"status": "unavailable", "reason": "perf missing or no discovered target threads", "rows": [], "command": None}
    if handle.get("error"):
        return {"status": "unavailable", "reason": handle["error"], "rows": [], "command": handle["command"]}
    process = handle["process"]
    try:
        stdout, stderr = process.communicate(timeout=seconds + 5)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        return {"status": "unavailable", "reason": "perf timed out", "rows": [], "command": handle["command"], "stderr": stderr}
    rows = parse_perf_stat(stderr)
    status = "ok" if process.returncode == 0 and rows and all(row["status"] == "ok" for row in rows) else "partial" if rows else "unavailable"
    return {"status": status, "returncode": process.returncode, "command": handle["command"],
            "rows": rows, "stderr": stderr, "stdout": stdout,
            "elapsed_s": time.monotonic() - handle["monotonic_started"],
            "started_at": handle["started_at"], "ended_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "scope": "user+kernel; grouped instructions/cycles; no guest/system-wide attribution"}


def start_record(tids, seconds, path, binary=None):
    binary = binary or perf_binary()
    if not binary or not tids or seconds <= 0:
        return None
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [binary, "record", "-e", "cpu-clock", "-F", "99", "-g", "-t",
               ",".join(map(str, sorted(set(tids)))), "-o", str(path), "--", "sleep", str(seconds)]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   env={**os.environ, "LC_ALL": "C"})
    except OSError as error:
        return {"error": str(error), "command": command}
    return {"process": process, "command": command, "path": str(path), "seconds": seconds}


def finish_record(handle):
    if handle is None:
        return {"status": "unavailable", "reason": "perf record disabled/missing or no target threads", "path": None}
    if handle.get("error"):
        return {"status": "unavailable", "reason": handle["error"], "path": None, "command": handle["command"]}
    process = handle["process"]
    try:
        stdout, stderr = process.communicate(timeout=handle["seconds"] + 5)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
    valid = process.returncode == 0 and Path(handle["path"]).is_file() and Path(handle["path"]).stat().st_size > 0
    return {"status": "ok" if valid else "unavailable", "path": handle["path"] if valid else None,
            "returncode": process.returncode, "stdout": stdout, "stderr": stderr, "command": handle["command"],
            "note": "99 Hz cpu-clock stack capture of initial thread set only; later-born threads are not automatically added. Lost samples and symbol coverage require perf report inspection."}
