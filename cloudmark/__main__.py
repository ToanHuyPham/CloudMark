from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__
from .agent import join_session
from .benchmarks import run_storage, storage_preflight
from .bootstrap import create_plan, execute_plan
from .inventory import collect_inventory
from .provider import detect_provider
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
    doctor.add_argument("--packs", default="storage,network,database,web")

    bootstrap = sub.add_parser("bootstrap", help="Install benchmark dependencies")
    bootstrap.add_argument("--packs", default="storage,network,database,web")
    bootstrap.add_argument("--yes", action="store_true", help="Execute the plan; otherwise preview only")

    run = sub.add_parser("run", help="Run a benchmark suite")
    run.add_argument("suite", choices=["storage"])
    run.add_argument("--profile", default="disk-quick")
    run.add_argument("--workspace", type=Path, default=Path(".cloudmark/benchmark-workspace"))
    run.add_argument("--yes", action="store_true", help="Confirm writing a temporary benchmark file")

    join = sub.add_parser("join", help="Join a distributed assessment session")
    join.add_argument("--controller", required=True, help="Controller base URL, for example https://controller.example")
    join.add_argument("--session", required=True)
    join.add_argument("--token", required=True, help="One-time join token")
    join.add_argument("--role", choices=["target", "generator", "replica", "peer"], default="peer")
    join.add_argument("--name")
    join.add_argument("--allow-http", action="store_true", help="Allow HTTP only on a trusted private network")

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
        preflight = storage_preflight(args.profile, args.workspace)
        _print({"preflight": preflight})
        if not args.yes:
            raise SystemExit("Add --yes to confirm safe temporary-file writes.")
        _print(run_storage(args.profile, args.workspace, "cli"))
    elif args.command == "join":
        _print(
            join_session(
                args.controller,
                args.session,
                args.token,
                args.role,
                args.name,
                allow_http=args.allow_http,
            )
        )


if __name__ == "__main__":
    main()
