#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/python ]]; then
  python_bin="${PYTHON:-}"
  if [[ -z "$python_bin" ]]; then
    for candidate in python3.11 python3.12 python3.13 python3.14 /opt/homebrew/bin/python3 python3; do
      if command -v "$candidate" >/dev/null 2>&1 && \
        "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
        python_bin="$candidate"
        break
      fi
    done
  fi
  if [[ -z "$python_bin" ]] || ! "$python_bin" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "Python >=3.11 required. Set PYTHON to a compatible interpreter." >&2
    exit 1
  fi
  "$python_bin" -m venv .venv
fi
.venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11))'
.venv/bin/python -m pip install -e '.[dev]'
echo 'Ready. Run: source .venv/bin/activate'
