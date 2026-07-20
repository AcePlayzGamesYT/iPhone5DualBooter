#!/bin/bash
set -Eeuo pipefail
cd "$(dirname "$0")"

fail() {
    printf '\nError: %s\n' "$1" >&2
    printf '\nPress Return to close...'
    read -r _
    exit 1
}

find_python() {
    local candidate

    for candidate in \
        /usr/local/bin/python3.13 \
        /opt/homebrew/bin/python3.13 \
        /usr/local/bin/python3.12 \
        /opt/homebrew/bin/python3.12 \
        /usr/bin/python3
    do
        if [[ -x "$candidate" ]]; then
            if "$candidate" -c 'import sys; raise SystemExit(not ((3, 8) <= sys.version_info[:2] < (3, 14)))' 2>/dev/null; then
                printf '%s\n' "$candidate"
                return 0
            fi
        fi
    done

    return 1
}

install_python() {
    command -v brew >/dev/null 2>&1 || fail "Install Homebrew from https://brew.sh, then run this launcher again."

    printf 'Installing Python 3.13...\n'
    brew install python@3.13

    find_python || fail "Python 3.13 was installed but could not be found."
}

if [[ "$(uname -s)" != "Darwin" ]]; then
    fail "This application requires macOS."
fi

PYTHON_BIN="$(find_python || true)"

if [[ -z "$PYTHON_BIN" ]]; then
    PYTHON_BIN="$(install_python)"
fi

if [[ -d .venv ]]; then
    VENV_VERSION="$(
        .venv/bin/python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true
    )"

    if [[ "$VENV_VERSION" == "3.14" || -z "$VENV_VERSION" ]]; then
        printf 'Removing incompatible Python virtual environment...\n'
        rm -rf .venv
    fi
fi

if [[ ! -d .venv ]]; then
    printf 'Creating Python virtual environment with %s...\n' "$PYTHON_BIN"
    "$PYTHON_BIN" -m venv .venv || fail "Could not create the Python virtual environment."
fi

source .venv/bin/activate

python -m pip install --disable-pip-version-check --upgrade pip
python -m pip install --disable-pip-version-check -r requirements.txt

python app.py
