from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


POSTGRES_TOOLS = {"initdb", "pgbench", "pg_isready", "postgres"}


def find_postgres_binary(name: str) -> str | None:
    if name not in POSTGRES_TOOLS:
        raise ValueError(f"Unsupported PostgreSQL tool: {name}")
    direct = shutil.which(name)
    if direct:
        return direct

    pg_config = shutil.which("pg_config")
    if pg_config:
        try:
            result = subprocess.run(
                [pg_config, "--bindir"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            candidate = Path(result.stdout.strip()) / (f"{name}.exe" if os.name == "nt" else name)
            if candidate.is_file():
                return str(candidate)

    candidates: list[Path] = []
    if os.name == "nt":
        program_files = os.environ.get("ProgramFiles")
        if program_files:
            candidates.extend(Path(program_files).glob(f"PostgreSQL/*/bin/{name}.exe"))
    else:
        candidates.extend(Path("/usr/lib/postgresql").glob(f"*/bin/{name}"))
        candidates.extend(Path("/usr/lib").glob(f"postgresql*/bin/{name}"))
        candidates.extend(Path("/usr").glob(f"pgsql-*/bin/{name}"))
    existing = [candidate for candidate in candidates if candidate.is_file()]
    def version_key(candidate: Path) -> tuple[int, ...]:
        versions = re.findall(r"\d+", str(candidate.parent.parent))
        return tuple(int(value) for value in versions[-3:])

    return str(max(existing, key=version_key)) if existing else None


def tool_version(executable: str) -> str | None:
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (result.stdout or result.stderr).strip()
    return output.splitlines()[0] if result.returncode == 0 and output else None
