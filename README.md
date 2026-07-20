# iPhone5DualBooter

A cross-platform utility for restoring a supported iPhone 5 to a jailbroken iOS 8.4.1 host and installing a secondary iOS version using CoolBooterCLI.

If your device is already jailbroken and untethered but is not running iOS 8.4.1, you may still use this tool by selecting **Skip Restore**.

This repository contains separate native applications for each supported operating system. Each version is built specifically for its platform and does not depend on code from the other implementations.

---

## CoolBooter Untether Troubleshooting

If your device bootloops after restoring with the untether or after installing **CoolBooter Untetherer**, follow these steps:

1. Force restart the device.
2. Immediately hold **Volume Down** while it boots to enter the primary iOS installation.
3. Open **Cydia**.
4. Install all available package upgrades.
5. Add the following repository if it is not already present:

   ```text
   https://coolbooter.com
   ```

6. Uninstall **CoolBooter Untetherer**.
7. Downgrade **CoolBooter CLI** to **version 1.0-release**.
8. Reinstall **CoolBooter Untetherer**.
9. Reboot the device to test the untether.

> [!NOTE]
> If the first boot hangs after reinstalling the untether, force restart the device once more. In most cases, the untether should work normally afterward.

This appears to be a CoolBooter bug that only affects certain iOS versions.

### If the problem continues

Remove **CoolBooter Untetherer** and boot the secondary operating system manually through OpenSSH or a terminal application.

Run:

```bash
coolbootercli -b
```
---

## Supported Platforms

| Platform | Status |
|----------|--------|
|  Windows | ✅ Available |
|  Linux | ✅ Available |
|  macOS | ✅ Available |

---

## Repository Layout

```
iPhone5DualBooter/
├── Windows/
├── Linux/
└── macOS/
```

Each directory contains a completely independent application for its operating system.

---

## Features

- Restore iPhone 5 (Global, iPhone5,2, GSM, iPhone5,1) to iOS 8.4.1
- Native Legacy iOS Kit integration
- Automatic jailbreak
- CoolBooterCLI installation
- Secondary IPSW installation
- Wi-Fi SSH support
- Untether installation
- Guided setup workflow
- Simple graphical interface

---

## Supported Device

- iPhone 5 (Global)
  - Model: iPhone5,2
- iPhone 5 (GSM)
  - Model: iPhone5,1

Additional device support may be added in future releases.

---

## Platform Status

### Windows

Uses:

- Native Windows GUI
- WSL
- Legacy iOS Kit
- Patched idevicerestore
- Automatic USB handoff

### Linux

Native Linux implementation using Legacy iOS Kit without Windows compatibility layers or patched restore tools.

### macOS

Native macOS implementation using Legacy iOS Kit without Windows compatibility layers or patched restore tools.

---

## Downloads

Choose the version that matches your operating system.

- Windows
- Linux
- macOS

---

## License

This project includes original code along with third-party open-source components. See the documentation inside each platform directory for licensing information.

---

## Credits

Special thanks to the developers of:

- Legacy iOS Kit
- libimobiledevice
- idevicerestore
- CoolBooter
- tihmstar
- LukeZGD
- AppleDB

---

## Disclaimer

This software is provided as-is without warranty. Use at your own risk. Restoring or modifying iOS devices always carries some risk of data loss or device malfunction.
