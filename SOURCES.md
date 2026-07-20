# Implementation research notes

the preparation stage is based on these upstream facts:

1. Legacy iOS Kit describes its restore-time jailbreak as the **Custom IPSW
   Method** for 32-bit devices.
2. The OTA downgrade documentation defines OTA downgrade as restoration to an
   OTA-signed version.
3. Legacy restore logs show it downloading/extracting a full
   `*_8.4.1_*_Restore.ipsw`, creating or reusing a `*_Custom.ipsw`, and restoring
   that custom IPSW.
4. `restore.sh` has an `ipsw_openssh` option and a `--jailbreak` flag.
5. The upstream jailbreak resources include bootstrap, OpenSSH, fstab, untether,
   and device/version-specific assets.
6. `resources/jailbreak/daibutsu/move.sh` changes the launch-daemon layout,
   disables update daemons, and changes CrashHousekeeping behavior depending on
   the iOS version.

Official sources:

- https://github.com/LukeZGD/Legacy-iOS-Kit
- https://github.com/LukeZGD/Legacy-iOS-Kit/wiki/Jailbreaking
- https://github.com/LukeZGD/Legacy-iOS-Kit/wiki/OTA-Downgrade
- https://github.com/LukeZGD/Legacy-iOS-Kit/wiki/Troubleshooting
- https://raw.githubusercontent.com/LukeZGD/Legacy-iOS-Kit/main/restore.sh
- https://raw.githubusercontent.com/LukeZGD/Legacy-iOS-Kit/main/resources/jailbreak/daibutsu/move.sh


xternal pwnbdfu references

- Legacy iOS Kit wiki — Pwning Using Another iOS Device:
  https://github.com/LukeZGD/Legacy-iOS-Kit/wiki/Pwning-Using-Another-iOS-Device
- iPwnder Lite upstream:
  https://github.com/dora2ios/ipwnder_lite
- Legacy issue log showing an existing `PWND:[checkm8]` device is detected and
  the exploit is not executed:
  https://github.com/LukeZGD/Legacy-iOS-Kit/issues/86


sourcepatch references

- LukeZGD idevicerestore fork:
  https://github.com/LukeZGD/idevicerestore
- LukeZGD fork `src/dfu.c`:
  https://raw.githubusercontent.com/LukeZGD/idevicerestore/master/src/dfu.c
- libirecovery public API:
  https://github.com/libimobiledevice/libirecovery/blob/master/include/libirecovery.h

- LukeZGD dfu.c revision tested by v0.4.12:
  https://github.com/LukeZGD/idevicerestore/blob/22682048240929637124781353ab3b6ee30b8dad/src/dfu.c


build compatibility references

- LukeZGD compile script that clones LukeeGD/libirecovery:
  https://github.com/LukeZGD/idevicerestore/blob/master/compile.sh
- Maintained libirecovery source with the corrected call:
  https://github.com/libimobiledevice/libirecovery/blob/master/src/libirecovery.c


linker dependency reference

- LukeZGD `compile.sh` final Linux link flags include `-lzstd -llzma -lbz2`:
  https://raw.githubusercontent.com/LukeZGD/idevicerestore/master/compile.sh
- Ubuntu package supplying the LZMA development linker files:
  https://packages.ubuntu.com/search?keywords=liblzma-dev
