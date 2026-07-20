#!/bin/bash
set -Eeuo pipefail
cd "$(dirname "$0")"

fail() {
    printf '\nError: %s\n' "$1" >&2
    printf '\nPress Return to close...'
    read -r _
    exit 1
}

have() {
    command -v "$1" >/dev/null 2>&1
}

install_homebrew() {
    printf 'Homebrew is required to install the native macOS dependencies.\n'
    printf 'Install Homebrew from https://brew.sh and run this launcher again.\n'
    fail "Homebrew was not found."
}

install_system_dependencies() {
    have brew || install_homebrew
    printf 'Checking macOS system dependencies...\n'
    brew update
    brew install python git curl libusb usbmuxd libimobiledevice
}

needs_system_dependencies() {
    if ! have brew || ! have python3 || ! have git || ! have curl; then
        return 0
    fi
    local formula
    for formula in libusb usbmuxd libimobiledevice; do
        if ! brew list --formula "$formula" >/dev/null 2>&1; then
            return 0
        fi
    done
    return 1
}

if [[ "$(uname -s)" != "Darwin" ]]; then
    fail "This application requires macOS."
fi

if [[ "${1:-}" == "--install-only" ]]; then
    install_system_dependencies
    printf '\nmacOS dependencies are installed.\n'
    exit 0
fi

if needs_system_dependencies; then
    install_system_dependencies
fi

have python3 || fail "Python 3 was not found after dependency installation."

if [[ ! -d .venv ]]; then
    python3 -m venv .venv || fail "Could not create the Python virtual environment."
fi

source .venv/bin/activate
python -m pip install --disable-pip-version-check --upgrade pip
python -m pip install --disable-pip-version-check -r requirements.txt
python app.py
