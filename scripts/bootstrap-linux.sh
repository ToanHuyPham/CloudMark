#!/usr/bin/env sh
set -eu

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip git
elif command -v zypper >/dev/null 2>&1; then
  sudo zypper --non-interactive refresh
  sudo zypper --non-interactive install python3 python3-pip git
elif command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y python3 python3-pip git
elif command -v yum >/dev/null 2>&1; then
  sudo yum install -y python3 python3-pip git
else
  echo "Unsupported package manager" >&2
  exit 2
fi

python3 -m venv .venv 2>/dev/null || true
if [ -x .venv/bin/python ]; then
  .venv/bin/python -m pip install -e .
  .venv/bin/python -m cloudmark doctor --packs storage,network,database,web
else
  python3 -m pip install --user -e .
  python3 -m cloudmark doctor --packs storage,network,database,web
fi

echo "Review the plan above, then run CloudMark bootstrap with sudo and --yes."
