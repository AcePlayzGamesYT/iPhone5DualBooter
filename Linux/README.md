# iPhone 5 DualBooter for Linux

A native Linux GUI for restoring a supported iPhone 5 to jailbroken iOS 8.4.1 with Legacy iOS Kit and installing a secondary iOS version with CoolBooterCLI. If your device is jailbroken untethered but NOT on iOS 8.4.1 you can still use this tool. Select the Skip Restore option.

This edition is Linux-only. It does not include WSL, usbipd-win, Windows idevicerestore binaries, patched restore files, or Windows build scripts.

## Features

- Native Legacy iOS Kit restore
- Automatic Legacy iOS Kit download
- iOS 8.4.1 host and secondary IPSW validation
- Wi-Fi SSH connection after restore
- CoolBooterCLI and dependency installation
- Secondary IPSW transfer and reuse
- Optional CoolBooter untether installation
- Separate Install Untether Only action

## Requirements

- A Linux distribution with a desktop environment
- Python 3.10 or newer
- USB access to the iPhone
- A supported iPhone 5
- A stock iOS 8.4.1 IPSW
- A compatible secondary IPSW
- A way to place the iPhone in pwnDFU
- OpenSSH installed on the restored iOS 8.4.1 host

## Setup

Run:

```bash
chmod +x run_linux.sh
./run_linux.sh
```

`run_linux.sh` detects Ubuntu/Debian, Fedora, Arch, or openSUSE, installs missing system and Qt dependencies with the distribution package manager, creates `.venv`, installs the Python packages, and launches the app. It may request the administrator password when system packages are missing.

To install only the system dependencies:

```bash
./install_linux_dependencies.sh
```

## Workflow

1. Put the iPhone in pwnDFU. This can be done via Legacy iOS Kit ran by the workflow.
2. Select the stock iOS 8.4.1 IPSW.
3. Select the secondary IPSW and enter its version.
4. Start the workflow.
5. Complete the native Legacy iOS Kit restore.
6. Connect the restored iPhone to the same Wi-Fi network as the computer and enter its IP address when prompted.
7. Enter the iPhone IPv4 address when prompted.
8. The app installs CoolBooterCLI, copies the IPSW, installs the secondary OS, and optionally installs the untether.

## Credits

- LukeZGD and contributors — Legacy iOS Kit
- CoolBooter developers — CoolBooter and CoolBooterCLI
- Jay Freeman and contributors — Cydia Substrate components
- Paramiko contributors — SSH and SCP support
- Qt and PySide contributors — desktop interface
- libimobiledevice contributors — Linux iOS device tooling

Apple firmware and third-party tools are not bundled. Each project retains its own licenses and copyrights.
