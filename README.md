# iPhone5DualBooter

A crossplatform utility for restoring an iPhone 5 to a jailbroken iOS 8.4.1 host and installing a secondary iOS version using CoolBooterCLI. If your device is jailbroken untethered, but not on iOS 8.4.1, you can still use this tool. Select the Skip Restore option.

This repository contains separate native applications for each supported operating system. Each version is designed specifically for its platform and does not rely on code from the other versions.

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