from pathlib import Path
import unittest

from iphone5dualbooter.models import WorkflowSettings
from iphone5dualbooter.wsl_legacy import (
    build_legacy_full_restore_shell_command,
)


class ExternalPwnDFUTests(unittest.TestCase):
    def test_setting_is_present_and_true(self):
        settings = WorkflowSettings(
            secondary_ipsw=Path("secondary.ipsw"),
            stock_host_ipsw=Path("host.ipsw"),
            secondary_version="7.0b1",
            datasize_gb=None,
            skip_restore=False,
            restore_mode="legacy_wsl_full",
            legacy_kit_dir=None,
            auto_download_legacy_kit=True,
            legacy_wsl_distro="Ubuntu",
            usbipd_busid="1-7",
            auto_attach_usb_to_wsl=True,
            auto_detach_usb_after_restore=True,
            require_external_pwndfu=True,
            patch_idevicerestore=True,
            ibec_retry_seconds=45,
            full_rebuild_fallback=True,
            phone_wifi_ip="",
            ssh_port=22,
            root_password="alpine",
            install_untether=True,
        )
        self.assertTrue(settings.require_external_pwndfu)

    def test_shell_never_tells_user_to_run_the_exploit_again(self):
        shell = build_legacy_full_restore_shell_command(
            "/mnt/c/Legacy-iOS-Kit",
            "/mnt/c/iPhone5,2_8.4.1_12H321_Restore.ipsw",
            "/mnt/c/legacy.log",
        )
        self.assertIn("--jailbreak", shell)
        self.assertIn("IPHONE5DUALBOOTER_HOST_HANDOFF_ACK_FILE", shell)
        self.assertIn("detect PWND", shell)
        self.assertIn("skip exploit", shell)
        self.assertIn("Do not re-enter DFU", shell)
        self.assertNotIn("Follow every DFU and pwnDFU prompt exactly", shell)


if __name__ == "__main__":
    unittest.main()
