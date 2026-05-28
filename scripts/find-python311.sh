#!/usr/bin/env bash
# find-python311.sh — Print the path to a Python 3.11+ interpreter, or exit 1.
# Usage: PYTHON_BIN="$(bash scripts/find-python311.sh)"
# Searches pyenv (PATH-independent) then versioned binaries and Homebrew paths.
# Safe to call from launchd, which inherits a minimal PATH.
set -euo pipefail

_require_py311() {
    "$1" -c "import sys; assert sys.version_info >= (3, 11)" 2>/dev/null
}

PYTHON_BIN=""

# 1. Try pyenv (use absolute binary, not PATH-dependent shim)
# Guard against missing HOME (launchd or env -i can leave HOME unset; set -u would abort)
if [[ -n "${HOME:-}" ]] && [[ -x "${HOME}/.pyenv/bin/pyenv" ]]; then
    _pyenv_py="$("${HOME}/.pyenv/bin/pyenv" which python3 2>/dev/null || true)"
    if [[ -n "$_pyenv_py" ]] && _require_py311 "$_pyenv_py"; then
        PYTHON_BIN="$_pyenv_py"
    fi
fi

# 2. Try versioned binaries and Homebrew paths
if [[ -z "$PYTHON_BIN" ]]; then
    for _py in python3.13 python3.12 python3.11 \
               /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
               /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11; do
        if command -v "$_py" &>/dev/null && _require_py311 "$_py"; then
            PYTHON_BIN="$(command -v "$_py")"
            break
        fi
    done
fi

if [[ -z "$PYTHON_BIN" ]]; then
    echo "ERROR: Python 3.11+ not found. Install via pyenv (pyenv install 3.13) or Homebrew (brew install python@3.13)." >&2
    exit 1
fi

echo "$PYTHON_BIN"
