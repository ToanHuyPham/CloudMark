from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


POSTGRES_TOOLS = {"initdb", "pgbench", "pg_isready", "postgres"}
WEB_TOOLS = {"ab", "curl", "nginx", "openssl"}


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


def postgres_tool_supports(name: str, executable: str, feature: str) -> bool:
    if (name, feature) != ("pgbench", "transaction-log"):
        raise ValueError(f"Unsupported PostgreSQL capability check: {name}/{feature}")
    try:
        result = subprocess.run(
            [executable, "--help"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = f"{result.stdout}\n{result.stderr}"
    return result.returncode == 0 and "--log" in output and "--transactions" in output


def find_web_binary(name: str) -> str | None:
    if name not in WEB_TOOLS:
        raise ValueError(f"Unsupported web tool: {name}")
    direct = shutil.which(name)
    if direct:
        return direct
    candidates: list[Path] = []
    if os.name == "nt":
        system_drive = os.environ.get("SystemDrive", "C:")
        drive_root = Path(f"{system_drive}/")
        if name == "nginx":
            candidates.extend((drive_root / "nginx").glob("nginx-*/nginx.exe"))
            candidates.append(drive_root / "nginx" / "nginx.exe")
        elif name == "ab":
            candidates.append(drive_root / "Apache24" / "bin" / "ab.exe")
        elif name == "curl":
            candidates.append(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "curl.exe")
    else:
        known = {
            "nginx": [Path("/usr/sbin/nginx"), Path("/usr/local/sbin/nginx")],
            "ab": [Path("/usr/bin/ab"), Path("/usr/local/apache2/bin/ab")],
            "curl": [Path("/usr/bin/curl"), Path("/usr/local/bin/curl")],
            "openssl": [Path("/usr/bin/openssl"), Path("/usr/local/bin/openssl")],
        }
        candidates.extend(known[name])
    return str(next((candidate for candidate in candidates if candidate.is_file()), "")) or None


def _web_tool_output(name: str, executable: str) -> str | None:
    arguments = {"nginx": ["-V"], "ab": ["-V"], "curl": ["--version"], "openssl": ["version"]}
    if name not in arguments:
        raise ValueError(f"Unsupported web tool: {name}")
    try:
        result = subprocess.run(
            [executable, *arguments[name]],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = "\n".join(value.strip() for value in (result.stdout, result.stderr) if value.strip())
    return output if result.returncode == 0 and output else None


def web_tool_version(name: str, executable: str) -> str | None:
    output = _web_tool_output(name, executable)
    return output.splitlines()[0] if output else None


def web_tool_supports(name: str, executable: str, feature: str) -> bool:
    if (name, feature) not in {("curl", "http2"), ("nginx", "http2")}:
        raise ValueError(f"Unsupported Web capability check: {name}/{feature}")
    output = _web_tool_output(name, executable)
    if not output:
        return False
    if name == "curl":
        features_line = next(
            (line for line in output.splitlines() if line.casefold().startswith("features:")),
            "",
        )
        return "http2" in features_line.casefold().split()
    return "--with-http_v2_module" in output
