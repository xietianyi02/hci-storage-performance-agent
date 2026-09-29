"""Run with python -m collectors collect|merge. Standard-library dependencies only."""

import argparse
from pathlib import Path
import sys

from .capture import collect, write_json
from .merge import build_batch, load_json


def parser():
    command = argparse.ArgumentParser(description="Read-only local Linux fullfio evidence collection and Batch normalization. Does not execute fio, SSH, affinity changes, or tuning.")
    sub = command.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("collect", help="Observe local /proc and optional perf on Linux")
    capture.add_argument("--run-id", required=True, help="Same fullfio run identity on guest/local/remote")
    capture.add_argument("--host-role", required=True, choices=("guest", "local", "remote"))
    capture.add_argument("--out", required=True, help="New JSON bundle output path; existing files are not overwritten")
    capture.add_argument("--duration", type=float, default=60, help="Measurement seconds excluding warmup; does not launch fio")
    capture.add_argument("--warmup", type=float, default=30, help="Warmup observation seconds before measurement (default 30)")
    capture.add_argument("--interval", type=float, default=3, help="Per-window /proc and perf sampling seconds (default 3)")
    capture.add_argument("--fio-json", help="Existing fio JSON summary (usually guest); not a window IO denominator")
    capture.add_argument("--scenario-json", help="Shared scenario metadata with protocol and canonical fio model")
    capture.add_argument("--roles-json", help="Process discovery rules [{pattern,role,hosts}] overriding defaults")
    capture.add_argument("--start-at", help="Optional future ISO timestamp with timezone for externally synchronized start")
    capture.add_argument("--record-seconds", type=float, default=5, help="Short measurement-phase perf record window (0 disables); perf stat reattaches each interval")
    capture.add_argument("--no-perf", action="store_true", help="Only proc/sys; counters remain null")
    merge = sub.add_parser("merge", help="Validate windows and emit measured Batch for API import; works on Windows too")
    merge.add_argument("--bundle", action="append", required=True, help="Repeat for guest/local/remote JSON bundles")
    merge.add_argument("--batch-json", required=True, help="Batch metadata {id,title,build,environment,changes}")
    merge.add_argument("--scenario-json", required=True, help="Scenario {id,title,protocol,fio}")
    merge.add_argument("--fio-json", help="Existing fio JSON summary; otherwise use guest bundle summary")
    merge.add_argument("--fio-window", help="External orchestrator {started_at,ended_at,completed_ios}; omitted means per-IO costs unknown")
    merge.add_argument("--fio-samples-json", help="Per-window guest [{start_offset_s,end_offset_s,completed_ios,latency_ms}]; offsets include warmup, no averaged IOPS fabrication")
    merge.add_argument("--tolerance", type=float, default=0.1, help="Max timestamp alignment tolerance in seconds (default 0.1)")
    merge.add_argument("--out", required=True, help="New importable Batch JSON path")
    return command


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "collect":
            bundle = collect(args.run_id, args.host_role, args.out, duration=args.duration, warmup=args.warmup, interval=args.interval,
                             fio_json=args.fio_json, roles=load_json(args.roles_json) if args.roles_json else None,
                             use_perf=not args.no_perf, record_seconds=args.record_seconds,
                             scenario=load_json(args.scenario_json) if args.scenario_json else None, start_at=args.start_at)
            print(f"Recorded {args.host_role} observation bundle: {args.out}; {len(bundle['threads'])} pools; quality notes={len(bundle['quality'])}")
        else:
            if Path(args.out).exists():
                raise FileExistsError("Output exists; use a new Batch filename")
            if not 0 <= args.tolerance <= 5:
                raise ValueError("Alignment tolerance must be 0..5 seconds; record external synchronization uncertainty")
            paths = [Path(path) for path in args.bundle]
            batch = build_batch([load_json(path) for path in paths], load_json(args.batch_json), load_json(args.scenario_json),
                                load_json(args.fio_json) if args.fio_json else None,
                                fio_window=load_json(args.fio_window) if args.fio_window else None,
                                fio_samples=load_json(args.fio_samples_json) if args.fio_samples_json else None,
                                tolerance_s=args.tolerance)
            batch["scenarios"][0]["artifacts"].extend({"kind": "collector_bundle", "path": str(path.resolve()), "note": "Original local host observation JSON"} for path in paths)
            write_json(args.out, batch)
            print(f"Normalized Batch: {args.out}; source=measured; completed_ios={batch['scenarios'][0]['window']['completed_ios']}")
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"Collection/import error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
