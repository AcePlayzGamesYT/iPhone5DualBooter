from pathlib import Path
from unittest.mock import patch
import threading
import time
import unittest

from iphone5dualbooter.usbipd import (
    AppleUSBWatcher,
    USBIPDDevice,
    USBIPDError,
    build_usbipd_auto_attach_command,
    build_wsl_keepalive_commands,
    is_no_running_wsl_error,
    parse_usbipd_list,
    select_apple_candidate,
    validate_busid,
    validate_distro,
)


SAMPLE = """Connected:
BUSID  VID:PID    DEVICE                                                        STATE
2-4    05ac:12a8  Apple Mobile Device USB Driver                               Not shared
3-2    046d:c539  USB Input Device                                              Shared
4-1    05ac:1227  Apple Recovery (DFU) USB Driver                              Attached
"""


class USBIPDTests(unittest.TestCase):
    def test_parses_devices(self):
        devices = parse_usbipd_list(SAMPLE)
        self.assertEqual(len(devices), 3)
        self.assertEqual(devices[0].busid, "2-4")
        self.assertEqual(devices[0].hardware_id, "05ac:12a8")
        self.assertTrue(devices[0].is_apple)
        self.assertEqual(devices[0].mode_name, "Normal")
        self.assertFalse(devices[0].is_shared)
        self.assertTrue(devices[2].is_attached)

    def test_valid_busid(self):
        self.assertEqual(validate_busid("1-3.2"), "1-3.2")

    def test_rejects_bad_busid(self):
        with self.assertRaises(USBIPDError):
            validate_busid("2-4 & whoami")


class WSLKeepaliveTests(unittest.TestCase):
    def test_keepalive_uses_direct_wsl_exec_without_shell_program(self):
        commands = build_wsl_keepalive_commands(
            Path("wsl.exe"),
            "Ubuntu",
        )
        self.assertEqual(
            commands[0],
            [
                "wsl.exe",
                "-d",
                "Ubuntu",
                "--exec",
                "sleep",
                "infinity",
            ],
        )
        self.assertEqual(
            commands[1],
            [
                "wsl.exe",
                "-d",
                "Ubuntu",
                "--exec",
                "tail",
                "-f",
                "/dev/null",
            ],
        )
        joined = " ".join(commands[0])
        self.assertNotIn("pidfile", joined)
        self.assertNotIn("nohup", joined)
        self.assertNotIn("bash", joined)
        self.assertNotIn('"', joined)
        self.assertNotIn("'", joined)

    def test_detects_exact_usbipd_error(self):
        output = (
            "usbipd: error: There is no WSL 2 distribution running; "
            "keep a command prompt to a WSL 2 distribution open to leave it running."
        )
        self.assertTrue(is_no_running_wsl_error(output))

    def test_validates_distro(self):
        self.assertEqual(validate_distro(" Ubuntu "), "Ubuntu")
        with self.assertRaises(USBIPDError):
            validate_distro("Ubuntu\nwhoami")


class AppleSelectionTests(unittest.TestCase):
    def test_apple_vendor_id_is_detected_without_apple_name(self):
        device = USBIPDDevice(
            busid="1-7",
            hardware_id="05ac:1227",
            device="USB Device",
            state="Shared",
        )
        self.assertTrue(device.is_apple)
        self.assertEqual(device.mode_name, "DFU/pwnDFU")

    def test_new_pending_mode_beats_stale_attached_mode(self):
        devices = [
            USBIPDDevice(
                "1-7",
                "Apple Recovery (iBoot)",
                "Attached",
                "05ac:1281",
            ),
            USBIPDDevice(
                "1-8",
                "Apple Recovery (DFU) USB Driver",
                "Not shared",
                "05ac:1227",
            ),
        ]
        candidate, reason = select_apple_candidate(
            devices,
            preferred_busid="1-7",
            current_busid="1-7",
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.busid, "1-8")
        self.assertIn("pending", reason)

    def test_does_not_guess_between_multiple_new_apple_devices(self):
        devices = [
            USBIPDDevice("1-8", "Apple DFU", "Shared", "05ac:1227"),
            USBIPDDevice("2-2", "Apple DFU", "Shared", "05ac:1227"),
        ]
        candidate, reason = select_apple_candidate(devices)
        self.assertIsNone(candidate)
        self.assertIn("multiple", reason)


class AppleUSBWatcherTests(unittest.TestCase):
    def test_watcher_follows_busid_and_mode_changes(self):
        snapshots = [
            [
                USBIPDDevice(
                    "1-7",
                    "Apple Mobile Device",
                    "Attached",
                    "05ac:12a8",
                )
            ],
            [],
            [
                USBIPDDevice(
                    "1-8",
                    "Apple Recovery (iBoot)",
                    "Shared",
                    "05ac:1281",
                )
            ],
            [],
            [
                USBIPDDevice(
                    "1-9",
                    "Apple Recovery (DFU) USB Driver",
                    "Not shared",
                    "05ac:1227",
                )
            ],
        ]
        index = 0
        lock = threading.Lock()
        attach_calls: list[tuple[str, str]] = []
        logs: list[str] = []

        def supplier():
            nonlocal index
            with lock:
                value = snapshots[min(index, len(snapshots) - 1)]
                index += 1
                return value

        def fake_ensure(distro, log, wsl):
            return Path("wsl.exe")

        def fake_attach(
            busid,
            distro,
            log,
            wsl,
            stop_keepalive_on_error=True,
        ):
            attach_calls.append((busid, distro))

        watcher = AppleUSBWatcher(
            distro="Ubuntu",
            preferred_busid="1-7",
            log=logs.append,
            wsl=Path("wsl.exe"),
            poll_interval=0.01,
            device_supplier=supplier,
            attach_function=fake_attach,
            ensure_wsl_function=fake_ensure,
        )
        watcher._native_auto_attach_supported = False
        watcher.start()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and len(attach_calls) < 2:
            time.sleep(0.01)
        watcher.stop()

        self.assertIn(("1-8", "Ubuntu"), attach_calls)
        self.assertIn(("1-9", "Ubuntu"), attach_calls)
        self.assertEqual(watcher.current_busid, "1-9")
        self.assertTrue(
            any("followed the iPhone" in message for message in logs)
        )


    def test_same_busid_new_pid_restarts_native_supervisor_and_waits_for_attached(self):
        snapshots = [
            [
                USBIPDDevice(
                    "1-7",
                    "Apple Recovery (iBoot)",
                    "Attached",
                    "05ac:1281",
                )
            ],
            [
                USBIPDDevice(
                    "1-7",
                    "Apple Mobile Device",
                    "Not shared",
                    "05ac:12a8",
                )
            ],
            [
                USBIPDDevice(
                    "1-7",
                    "Apple Mobile Device",
                    "Attached",
                    "05ac:12a8",
                )
            ],
        ]
        index = 0
        logs: list[str] = []
        starts: list[tuple[str, str]] = []
        stops: list[str] = []

        def supplier():
            nonlocal index
            value = snapshots[min(index, len(snapshots) - 1)]
            index += 1
            return value

        def fake_ensure(distro, log, wsl):
            return Path("wsl.exe")

        def fake_start(busid, distro, log, wsl):
            current = snapshots[min(index - 1, len(snapshots) - 1)][0]
            starts.append((busid, current.hardware_id))
            return object()

        def fake_stop(busid, log):
            stops.append(busid)

        watcher = AppleUSBWatcher(
            distro="Ubuntu",
            preferred_busid="1-7",
            log=logs.append,
            wsl=Path("wsl.exe"),
            poll_interval=0.01,
            device_supplier=supplier,
            ensure_wsl_function=fake_ensure,
        )
        watcher._native_auto_attach_supported = True
        watcher._native_auto_attach_busid = "1-7"
        watcher._native_auto_attach_hardware_id = "05ac:1281"

        with patch(
            "iphone5dualbooter.usbipd.start_usbipd_auto_attach",
            side_effect=fake_start,
        ), patch(
            "iphone5dualbooter.usbipd.stop_usbipd_auto_attach",
            side_effect=fake_stop,
        ):
            watcher.start()
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if starts and index >= 3:
                    break
                time.sleep(0.01)
            observed_attached = watcher.wait_until_attached(0.25)
            watcher.stop()

        self.assertTrue(observed_attached)
        self.assertIn("1-7", stops)
        self.assertIn(("1-7", "05ac:12a8"), starts)
        self.assertTrue(
            any("new Apple USB enumeration" in message for message in logs)
        )
        # The third snapshot must be the one that produced a genuine
        # Attached state. Starting an auto-attach supervisor alone is not
        # allowed to satisfy the event.
        self.assertGreaterEqual(index, 3)


class USBIPDAutoAttachTests(unittest.TestCase):
    def test_builds_native_auto_attach_command(self):
        command = build_usbipd_auto_attach_command(
            Path("usbipd.exe"),
            "1-7",
        )
        self.assertEqual(
            command,
            [
                "usbipd.exe",
                "attach",
                "--wsl",
                "--auto-attach",
                "--busid",
                "1-7",
            ],
        )

    def test_auto_attach_command_has_no_polling_shell(self):
        command = build_usbipd_auto_attach_command(
            Path("usbipd.exe"),
            "1-7",
        )
        joined = " ".join(command)
        self.assertNotIn("powershell", joined.casefold())
        self.assertNotIn("while", joined.casefold())
        self.assertNotIn("sleep", joined.casefold())


if __name__ == "__main__":
    unittest.main()
