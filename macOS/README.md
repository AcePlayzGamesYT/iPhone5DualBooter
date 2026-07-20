# iPhone 5 DualBooter for macOS

A native macOS GUI for restoring a supported iPhone 5 to jailbroken iOS 8.4.1 with Legacy iOS Kit and installing a secondary iOS version with CoolBooterCLI.

This edition is macOS-only. It does not include WSL, usbipd-win, Windows idevicerestore binaries, Linux package-manager scripts, or patched restore files.

## Features

- Native Legacy iOS Kit restore
- Automatic Legacy iOS Kit download
- iOS 8.4.1 host and secondary IPSW validation
- Wi-Fi SSH connection after restore
- CoolBooterCLI and dependency installation
- Secondary IPSW transfer and reuse
- Optional CoolBooter untether installation
- Separate Install Untether Only action
- Clickable macOS `.command` launchers

## Requirements

- A supported macOS release with Legacy iOS Kit compatibility
- Intel or Apple silicon Mac
- Homebrew
- Python 3.10 or newer
- USB access to the iPhone
- A supported iPhone 5
- A stock iOS 8.4.1 IPSW
- A compatible secondary IPSW
- A way to place the iPhone in pwnDFU
- OpenSSH installed on the restored iOS 8.4.1 host

## Setup

In Terminal, run:

```bash
chmod +x run_macos.command install_macos_dependencies.command
./run_macos.command
```

You can also double-click `run_macos.command` in Finder after making it executable once.

The launcher checks for Homebrew and native device dependencies, creates `.venv`, installs the Python packages, and opens the app.

To install only the native dependencies:

```bash
./install_macos_dependencies.command
```

If macOS blocks the launcher, Control-click it in Finder, select **Open**, and confirm.

## Workflow

1. Put the iPhone in pwnDFU.
2. Select the stock iOS 8.4.1 IPSW.
3. Select the secondary IPSW and enter its version.
4. Start the workflow.
5. Complete the native Legacy iOS Kit restore.
6. Connect the restored iPhone to the same Wi-Fi network as the Mac.
7. Enter the iPhone IPv4 address when prompted.
8. The app installs CoolBooterCLI, copies the IPSW, installs the secondary OS, and optionally installs the untether.

## Credits

- LukeZGD and contributors — Legacy iOS Kit
- CoolBooter developers — CoolBooter and CoolBooterCLI
- Jay Freeman and contributors — Cydia Substrate components
- Paramiko contributors — SSH and SCP support
- Qt and PySide contributors — desktop interface
- libimobiledevice contributors — native iOS device tooling

Apple firmware and third-party tools are not bundled. Each project retains its own licenses and copyrights.
