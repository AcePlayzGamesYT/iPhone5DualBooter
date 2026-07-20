#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"

fail() {
    printf '\nError: %s\n' "$1" >&2
    exit 1
}

have() {
    command -v "$1" >/dev/null 2>&1
}

run_root() {
    if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
        "$@"
    elif have sudo; then
        sudo "$@"
    elif have pkexec; then
        pkexec "$@"
    else
        fail "Administrator access is required. Install sudo or run this script as root."
    fi
}

install_apt() {
    run_root apt-get update
    run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
        python3 python3-venv python3-pip git curl ca-certificates \
        libusb-1.0-0 usbmuxd libimobiledevice-utils \
        libxcb-cursor0 libxcb-xinerama0 libxkbcommon-x11-0 \
        libxcb-keysyms1 libxcb-randr0 libxcb-shape0 libxcb-xfixes0 \
        libxcb-icccm4 libxcb-image0 libxcb-render-util0 libgl1
}

install_dnf() {
    run_root dnf install -y \
        python3 python3-pip git curl ca-certificates \
        libusb1 usbmuxd libimobiledevice-utils \
        xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms \
        libxkbcommon-x11 mesa-libGL
}

install_pacman() {
    run_root pacman -Sy --needed --noconfirm \
        python python-pip git curl ca-certificates \
        libusb usbmuxd libimobiledevice \
        xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms \
        libxkbcommon-x11 libglvnd
}

install_zypper() {
    run_root zypper --non-interactive refresh
    run_root zypper --non-interactive install \
        python3 python3-pip python3-virtualenv git curl ca-certificates \
        libusb-1_0-0 usbmuxd libimobiledevice-utils \
        libxcb-cursor0 libxcb-xinerama0 libxkbcommon-x11-0 \
        libxcb-keysyms1 libxcb-randr0 libxcb-shape0 libxcb-xfixes0 \
        libxcb-icccm4 libxcb-image0 libxcb-render-util0 Mesa-libGL1
}

install_system_dependencies() {
    printf 'Checking Linux system dependencies...\n'
    if have apt-get; then
        install_apt
    elif have dnf; then
        install_dnf
    elif have pacman; then
        install_pacman
    elif have zypper; then
        install_zypper
    else
        fail "Supported package manager not found. Use Ubuntu, Debian, Fedora, Arch, or openSUSE, or install the requirements manually."
    fi
}

needs_system_dependencies() {
    if ! have python3 || ! have git || ! have curl; then
        return 0
    fi
    if have dpkg-query; then
        local package
        for package in python3-venv libusb-1.0-0 usbmuxd libimobiledevice-utils libxcb-cursor0 libxkbcommon-x11-0; do
            if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
                return 0
            fi
        done
    fi
    return 1
}

if [[ "${1:-}" == "--install-only" ]]; then
    install_system_dependencies
    printf '\nLinux dependencies are installed.\n'
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

if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
    fail "No graphical desktop session was detected. Start the app from your Linux desktop, not a text-only terminal or SSH session."
fi

python app.py
