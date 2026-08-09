from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .agent import join_and_work, join_session
from .benchmarks import run_storage, storage_preflight
from .bootstrap import create_plan, execute_plan
from .compute import run_system_benchmark, system_preflight
from .inventory import collect_inventory
from .profiles import COMPUTE_PROFILES, MEMORY_PROFILES, STORAGE_PROFILES
from .provider import detect_provider
from .runner import JobContext
from .server import serve


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="cloudmark", description="CloudMark infrastructure assessment controller and agent")
    root.add_argument("--version", action="version", version=__version__)
    sub = root.add_subparsers(dest="command", required=True)

    serve_parser = sub.add_parser("serve", help="Start the local controller API")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8787)
    serve_parser.add_argument("--data-dir", type=Path, default=Path(".cloudmark"))

    sub.add_parser("inventory", help="Collect local hardware and operating system inventory")

    doctor = sub.add_parser("doctor", help="Show the dependency installation plan")
    doctor.add_argument("--packs", default="compute,memory,storage,network,database,web")

    bootstrap = sub.add_parser("bootstrap", help="Install benchmark dependencies")
    bootstrap.add_argument("--packs", default="compute,memory,storage,network,database,web")
    bootstrap.add_argument("--yes", action="store_true", help="Execute the plan; otherwise preview only")

    run = sub.add_parser("run", help="Run a benchmark suite")
    run.add_argument("suite", choices=["compute", "memory", "storage"])
    run.add_argument("--profile")
    run.add_argument("--workspace", type=Path, default=Path(".cloudmark/benchmark-workspace"))
    run.add_argument("--timeout-seconds", type=int, help="Stop the run after this many seconds")
    run.add_argument("--yes", action="store_true", help="Confirm intentional benchmark load or temporary-file writes")

    join = sub.add_parser("join", help="Register once for diagnostics; use 'agent' for benchmark work")
    join.add_argument("--controller", required=True, help="Controller base URL, for example https://controller.example")
    join.add_argument("--session", required=True)
    join.add_argument("--token", required=True, help="Short-lived session join token")
    join.add_argument("--role", choices=["target", "generator", "replica", "peer"], default="peer")
    join.add_argument("--name")
    join.add_argument("--advertise-address", help="Peer-reachable IP address used only by paired provider agents")
    join.add_argument("--allow-http", action="store_true", help="Allow HTTP only on a trusted private network")

    agent = sub.add_parser("agent", help="Join a session and run the persistent distributed worker")
    agent.add_argument("--controller", required=True, help="Controller base URL, for example https://controller.example")
    agent.add_argument("--session", required=True)
    agent.add_argument("--token", required=True, help="Short-lived session join token")
    agent.add_argument("--role", choices=["target", "generator"], required=True)
    agent.add_argument("--name")
    agent.add_argument("--advertise-address", help="Peer-reachable IP address used by the other provider agent")
    agent.add_argument("--allow-http", action="store_true", help="Allow HTTP only on a trusted private network")

    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "serve":
        serve(args.host, args.port, args.data_dir)
    elif args.command == "inventory":
        _print({"inventory": collect_inventory(), "provider": detect_provider()})
    elif args.command in {"doctor", "bootstrap"}:
        packs = [item.strip() for item in args.packs.split(",") if item.strip()]
        plan = create_plan(packs)
        _print(plan.as_dict())
        if args.command == "bootstrap" and args.yes:
            _print({"results": execute_plan(plan)})
    elif args.command == "run":
        default_profiles = {"compute": "compute-quick", "memory": "memory-quick", "storage": "disk-quick"}
        profile = args.profile or default_profiles[args.suite]
        preflight = (
            storage_preflight(profile, args.workspace)
            if args.suite == "storage"
            else system_preflight(args.suite, profile, args.workspace)
        )
        _print({"preflight": preflight})
        if not args.yes:
            action = "temporary-file writes" if args.suite == "storage" else "intentional benchmark load"
            raise SystemExit(f"Add --yes to confirm {action}.")
        timeout_seconds = args.timeout_seconds or preflight["default_timeout_seconds"]
        if not 30 <= timeout_seconds <= 43_200:
            raise SystemExit("--timeout-seconds must be between 30 and 43200.")

        def show_progress(update: dict[str, object]) -> None:
            percent = round(float(update["progress"]) * 100)
            current = update.get("current_job") or update["phase"]
            print(f"[cloudmark] {percent:3d}% {current}", file=sys.stderr, flush=True)

        context = JobContext(
            "cli",
            total_steps=(
                len(STORAGE_PROFILES[profile]["jobs"]) + 2
                if args.suite == "storage"
                else len((COMPUTE_PROFILES if args.suite == "compute" else MEMORY_PROFILES)[profile]["jobs"])
            ),
            timeout_seconds=timeout_seconds,
            on_progress=show_progress,
        )
        if args.suite == "storage":
            _print(run_storage(profile, args.workspace, "cli", context=context))
        else:
            _print(run_system_benchmark(args.suite, profile, args.workspace, "cli", context=context))
    elif args.command == "join":
        _print(
            join_session(
                args.controller,
                args.session,
                args.token,
                args.role,
                args.name,
                advertise_address=args.advertise_address,
                allow_http=args.allow_http,
            )
        )
    elif args.command == "agent":
        join_and_work(
            args.controller,
            args.session,
            args.token,
            args.role,
            args.name,
            advertise_address=args.advertise_address,
            allow_http=args.allow_http,
        )


if __name__ == "__main__":
    main()
