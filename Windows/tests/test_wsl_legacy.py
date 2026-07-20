from pathlib import Path
import unittest

from iphone5dualbooter.wsl_legacy import (
    build_legacy_full_restore_shell_command,
    build_legacy_wsl_command,
    _direct_windows_path_to_wsl,
    LegacyTranscriptFollower,
)


class LegacyFullRestoreTests(unittest.TestCase):
    def test_full_restore_has_jailbreak_and_no_no_device(self):
        shell = build_legacy_full_restore_shell_command(
            "/mnt/c/Tools/Legacy-iOS-Kit",
            "/mnt/c/Firmware/iPhone5,2_8.4.1_12H321_Restore.ipsw",
            "/mnt/c/Tools/full-restore.log",
        )
        self.assertIn("./restore.sh", shell)
        self.assertIn("stdbuf -oL -eL", shell)
        self.assertIn("--jailbreak", shell)
        self.assertIn("IPHONE5DUALBOOTER_HOST_HANDOFF_ACK_FILE", shell)
        self.assertIn("IPHONE5DUALBOOTER_IBEC_RETRY_SECONDS=45", shell)
        self.assertNotIn("--no-device", shell)
        self.assertNotIn("futurerestore", shell)
        self.assertIn("external pwnDFU", shell)
        self.assertIn("detect PWND and skip exploit", shell)
        self.assertIn("Do not re-enter DFU", shell)
        self.assertIn("Restore/Downgrade", shell)
        self.assertIn("tee -a", shell)

    def test_builds_wsl_process_command(self):
        command = build_legacy_wsl_command(
            Path("wsl.exe"),
            "Ubuntu",
            "echo hello",
        )
        self.assertEqual(command[:4], ["wsl.exe", "-d", "Ubuntu", "--"])
        self.assertEqual(command[-3:], ["bash", "-lc", "echo hello"])

    def test_converts_drive_path(self):
        converted = _direct_windows_path_to_wsl(
            r"C:\Users\ExampleUser\Downloads\Legacy-iOS-Kit",
            "Ubuntu",
        )
        self.assertEqual(
            converted,
            "/mnt/c/Users/ExampleUser/Downloads/Legacy-iOS-Kit",
        )

    def test_converts_wsl_unc_path(self):
        converted = _direct_windows_path_to_wsl(
            r"\\wsl.localhost\Ubuntu\home\ace\Legacy-iOS-Kit",
            "Ubuntu",
        )
        self.assertEqual(converted, "/home/ace/Legacy-iOS-Kit")


    def test_live_transcript_follower_strips_ansi_and_throttles_progress(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            transcript = Path(temp) / "legacy.log"
            transcript.write_text(
                "\x1b[92m[Log] Starting restore\x1b(B\x1b[m\n"
                "[=                                                 ]   1.0%\n"
                "[=====                                             ]  10.1%\n"
                "[======                                            ]  11.0%\n"
                "[iPhone5DualBooter] handoff complete\n",
                encoding="utf-8",
            )
            logs: list[str] = []
            follower = LegacyTranscriptFollower()
            emitted = follower.poll(transcript, logs.append)

        self.assertGreaterEqual(emitted, 3)
        self.assertTrue(
            any("Legacy: [Log] Starting restore" in line for line in logs)
        )
        self.assertTrue(any("Legacy progress: 1.0%" in line for line in logs))
        self.assertTrue(any("Legacy progress: 10.1%" in line for line in logs))
        self.assertFalse(any("11.0%" in line for line in logs))
        self.assertTrue(
            any("handoff complete" in line for line in logs)
        )
        self.assertFalse(any("\x1b" in line for line in logs))


    def test_waiting_for_device_popup_is_due_after_five_seconds_once(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            transcript = Path(temp) / "legacy.log"
            transcript.write_text("Waiting for device...\n", encoding="utf-8")
            follower = LegacyTranscriptFollower()
            follower.poll(transcript, lambda message: None)
            started = follower.waiting_for_device_since

        self.assertGreater(started, 0.0)
        self.assertFalse(follower.replug_popup_due(started + 4.99))
        self.assertTrue(follower.replug_popup_due(started + 5.0))
        self.assertFalse(follower.replug_popup_due(started + 20.0))

    def test_restore_mode_connection_cancels_pending_popup(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            transcript = Path(temp) / "legacy.log"
            transcript.write_text("Waiting for device...\n", encoding="utf-8")
            follower = LegacyTranscriptFollower()
            follower.poll(transcript, lambda message: None)
            started = follower.waiting_for_device_since

            with transcript.open("a", encoding="utf-8") as stream:
                stream.write("Device is now connected in restore mode\n")
            follower.poll(transcript, lambda message: None)

        self.assertFalse(follower.replug_popup_due(started + 10.0))

    def test_popup_text_contains_only_manual_cable_instruction(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "wsl_legacy.py"
        ).read_text(encoding="utf-8")

        self.assertIn("Unplug the iPhone USB cable", source)
        self.assertIn("Wait 2 seconds", source)
        self.assertIn("Plug the same cable back in", source)
        self.assertIn("After pressing OK", source)
        self.assertIn("Keep repeating that cycle", source)
        self.assertIn("detects the iPhone", source)
        self.assertNotIn("arm_manual_replug", source)
        self.assertNotIn("restart_pnp_device_elevated", source)
        self.assertNotIn("soft_replug_apple_device", source)


if __name__ == "__main__":
    unittest.main()
