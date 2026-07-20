# iPhone 5 DualBooter for Windows

A Windows GUI for restoring a supported **iPhone 5 (Global, iPhone5,2)** to a jailbroken iOS 8.4.1 host and installing a secondary iOS version with CoolBooterCLI.

The application coordinates Windows, WSL, `usbipd-win`, Legacy iOS Kit, a patched `idevicerestore`, Wi-Fi SSH, and CoolBooterCLI. Large downloaded tools, firmware files, build products, logs, device-specific files, and Python virtual environments are intentionally not included in this repository. They are created or downloaded locally when needed.

> [!WARNING]
> Restoring erases the device. Back up anything important first. This is an unofficial community project and is not affiliated with or supported by Apple, Legacy iOS Kit, CoolBooter, or the other upstream projects credited below.

## Current scope

- Host device: iPhone 5 Global/GSM (`iPhone5,2` / `n42ap`, `iPhone5,1` / `n42ap`)
- Host firmware: iOS 8.4.1 (`12H321`) (Other untethered jailbreak versions NOT tested)
- Secondary firmware: selected compatible 32-bit iOS IPSW
- Host platform: Windows 10 or Windows 11
- Restore backend: Legacy iOS Kit running through WSL
- Post-restore backend: Wi-Fi SSH and CoolBooterCLI

This repository contains device-specific restore patches. Do not assume that another device model is supported merely because an upstream tool supports it.

## Features

- Windows desktop interface built with PySide6
- Fresh setup of the local Python environment
- Runtime download/setup of Legacy iOS Kit instead of committing a user-modified copy
- Automatic WSL and Apple USB passthrough workflow
- Patched `idevicerestore` build and integrity manifests
- Live DFU-to-Recovery USB handoff without restarting the active restore
- WSL-safe restored/ASR transfer pacing
- CoolBooterCLI dependency installation on minimal jailbreak environments without APT
- Reuse of an IPSW already present in `/var/cbooter`
- Optional CoolBooter untether installation
- Standalone **Install Untether Only** action for an existing tethered installation

## Requirements

Before running the application, install:

1. **Python 3.10 or newer** for Windows
2. **WSL 2** with Ubuntu
3. **usbipd-win**
4. **Apple Mobile Device USB drivers** (normally installed with Apple Devices or iTunes)
5. A working network where the PC and iPhone can reach each other for Wi-Fi SSH

The phone must be placed into externally pwned DFU when the application requests it. Follow the instructions shown by the application and the relevant upstream documentation for your hardware.

## Quick start

1. Download or clone this repository.
2. Extract it to a normal writable folder. Avoid running directly from a ZIP archive.
3. Double-click `run.bat`.
4. On first launch, the script creates `.venv` and installs `requirements.txt` automatically.
5. Select the required IPSWs and follow the prompts in the GUI.

Manual setup is also possible:

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

## Building the Windows application

Run:

```powershell
build_windows.bat
```

Generated build and distribution folders are ignored by Git and should not be committed.

## Clean repository behavior

A source checkout intentionally does **not** contain:

- `.venv`
- Apple IPSWs
- SHSH blobs or APTickets
- a populated Legacy iOS Kit checkout
- downloaded CoolBooter packages
- cached patched binaries
- WSL source/build caches
- device identifiers, local usernames, or SSH data
- logs, temporary files, packaged releases, `build`, or `dist`

The application recreates the needed local state. See `GITHUB_SETUP.md` and `.gitignore` for the repository-cleanup rules.

## Integrity files

Two SHA-256 manifests are included:

- `PATCHED-FILES.sha256` covers the project files whose exact contents are important to the patched workflow.
- `PRESERVED-PATCH-ASSETS.sha256` covers the core patch/build assets that must remain unchanged when editing unrelated UI or post-restore code.

Verify them from the repository root in Git Bash or WSL:

```bash
sha256sum -c PATCHED-FILES.sha256
sha256sum -c PRESERVED-PATCH-ASSETS.sha256
```

PowerShell does not provide `sha256sum` by default, but the individual hashes can be checked with `Get-FileHash -Algorithm SHA256`.

## Troubleshooting

- **The app cannot see the iPhone:** Confirm Apple USB drivers and `usbipd-win` are installed, use a direct USB port, and close software that may claim the device.
- **WSL does not start:** Open Ubuntu once manually and finish its first-run setup.
- **SSH is refused after a reboot:** Wait for the iPhone to finish starting, reconnect it to the same Wi-Fi network, and confirm OpenSSH is running on the host OS.
- **CoolBooterCLI is installed but its command is missing:** The app attempts a package removal and clean reinstall automatically.
- **Untether dependencies are incomplete:** The direct installer resolves Substrate Safe Mode, MobileSubstrate, and the CoolBooter untether package in dependency order.

Keep the full application log when reporting a problem. Remove serial numbers, ECIDs, IP addresses, usernames, and other personal information before posting it publicly.

## credits

This project depends on and is made possible by work from the following developers and projects:

- **LukeZGD** — [Legacy iOS Kit](https://github.com/LukeZGD/Legacy-iOS-Kit) and the LukeZGD `idevicerestore` fork
- **libimobiledevice contributors** — `idevicerestore`, `libirecovery`, `libimobiledevice`, and relatedlibraries
- **CoolBooter team** — CoolBooterCLI and coolBooter Untetherer
- **Jay Freeman (saurik)** and Cydia ecosystem contributors for MobileSubstrate, Substrate Safe Mode, and supporting packages
- **Frans van Dorsselaer and contributors** — [`usbipd-win`](https://github.com/dorssel/usbipd-win)
- **dora2ios and contributors** — `ipwnder_lite`
- **Paramiko contributors** — Python SSH support
- **Qt Company and the PySide project** — PySide6 GUI framework
- The jailbreak and legacy-device communities whose research, testing, documentation, and tooling made this workflow possible

All third-party projects retain their own copyrights, trademarks, and licenses. No Apple firmware, SHSH blobs, jailbreak payloads, Legacy iOS Kit checkout, CoolBooter packages, Apple drivers, or `usbipd-win` binaries are bundled in this source repository. See `SOURCES.md` and `THIRD_PARTY.md` for additional references.

## Legal and safety notes

Use this project only with devices you own or have permission to service. You are responsible for complying with applicable laws, software licenses, and device-security policies. Firmware restoration and jailbreak workflows can erase data or leave a device temporarily unusable.

## Contributing

when submitting changes:

1. Keep generated and user-specific files out of the repository.
2. Do not silently modify the restore patches while working on unrelated features.
3. Run Python syntax/tests where available.
4. Regenerate both SHA-256 manifests whenever a listed file changes.
5. Explain any restore, USB, ASR, package, or boot-flow behavior change clearly in the pull request.
