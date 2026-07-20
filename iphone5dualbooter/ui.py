from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import traceback

from PySide6.QtCore import QSettings, QThread, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIntValidator
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QInputDialog,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .ipsw import (
    inspect_ipsw,
    validate_host_841_info,
    validate_host_secondary_models,
    validate_secondary_info,
    validate_secondary_version,
)
from .models import WorkflowSettings
from .usbipd import (
    USBIPDError,
    attach_to_wsl,
    detach_from_wsl,
    list_devices,
)
from .network import validate_ipv4
from .ssh import IOSSSH, wait_for_ssh
from .workflow import (
    UNTETHER_PACKAGE,
    install_package,
    run_workflow,
    validate_firmware_settings,
)


class DropLineEdit(QLineEdit):
    def __init__(self, suffixes: tuple[str, ...], parent=None):
        super().__init__(parent)
        self.suffixes = tuple(suffix.lower() for suffix in suffixes)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if urls and urls[0].toLocalFile().lower().endswith(self.suffixes):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        path = event.mimeData().urls()[0].toLocalFile()
        if path.lower().endswith(self.suffixes):
            self.setText(path)
            event.acceptProposedAction()


class LegacyAwareThread(QThread):
    legacyPromptRequested = Signal(int, str, int, str)
    wifiPromptRequested = Signal(str, int)
    externalPwnPromptRequested = Signal(int)
    firstBootPromptRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._legacy_event = threading.Event()
        self._legacy_action = "cancel"
        self._wifi_event = threading.Event()
        self._wifi_endpoint: tuple[str, int] | None = None
        self._external_pwn_event = threading.Event()
        self._external_pwn_confirmed = False
        self._first_boot_event = threading.Event()
        self._first_boot_confirmed = False

    def request_legacy_action(
        self,
        exit_code: int,
        transcript: Path,
        attempt: int,
        reason: str,
    ) -> str:
        self._legacy_action = "cancel"
        self._legacy_event.clear()
        self.legacyPromptRequested.emit(exit_code, str(transcript), attempt, reason)
        self._legacy_event.wait()
        return self._legacy_action

    def answer_legacy_prompt(self, action: str) -> None:
        self._legacy_action = action
        self._legacy_event.set()

    def request_wifi_endpoint(
        self,
        current_ip: str,
        current_port: int,
    ) -> tuple[str, int] | None:
        self._wifi_endpoint = None
        self._wifi_event.clear()
        self.wifiPromptRequested.emit(current_ip, current_port)
        self._wifi_event.wait()
        return self._wifi_endpoint

    def answer_wifi_prompt(
        self,
        endpoint: tuple[str, int] | None,
    ) -> None:
        self._wifi_endpoint = endpoint
        self._wifi_event.set()

    def request_external_pwndfu_confirmation(self, attempt: int) -> bool:
        self._external_pwn_confirmed = False
        self._external_pwn_event.clear()
        self.externalPwnPromptRequested.emit(attempt)
        self._external_pwn_event.wait()
        return self._external_pwn_confirmed

    def answer_external_pwndfu_prompt(self, confirmed: bool) -> None:
        self._external_pwn_confirmed = bool(confirmed)
        self._external_pwn_event.set()

    def request_first_boot_confirmation(self) -> bool:
        self._first_boot_confirmed = False
        self._first_boot_event.clear()
        self.firstBootPromptRequested.emit()
        self._first_boot_event.wait()
        return self._first_boot_confirmed

    def answer_first_boot_prompt(self, confirmed: bool) -> None:
        self._first_boot_confirmed = bool(confirmed)
        self._first_boot_event.set()


class WorkflowThread(LegacyAwareThread):
    logLine = Signal(str)
    stageChanged = Signal(int, str)
    transferProgress = Signal(int, int)
    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, settings: WorkflowSettings, app_root: Path):
        super().__init__()
        self.workflow_settings = settings
        self.app_root = app_root

    def run(self) -> None:
        try:
            run_workflow(
                self.workflow_settings,
                self.app_root,
                self.logLine.emit,
                self.stageChanged.emit,
                self.transferProgress.emit,
                self.request_legacy_action,
                self.request_wifi_endpoint,
                self.request_external_pwndfu_confirmation,
                self.request_first_boot_confirmation,
            )
        except Exception as exc:
            self.logLine.emit(
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            )
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit()


class UntetherInstallThread(QThread):
    logLine = Signal(str)
    stageChanged = Signal(int, str)
    transferProgress = Signal(int, int)
    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, phone_ip: str, ssh_port: int, root_password: str):
        super().__init__()
        self.phone_ip = phone_ip
        self.ssh_port = ssh_port
        self.root_password = root_password

    def run(self) -> None:
        try:
            self.stageChanged.emit(10, "Connecting to the iPhone")
            ssh = wait_for_ssh(
                self.phone_ip,
                self.ssh_port,
                self.root_password,
                self.logLine.emit,
                timeout_seconds=20 * 60,
            )
            try:
                self.stageChanged.emit(45, "Installing the CoolBooter untether")
                install_package(
                    ssh,
                    UNTETHER_PACKAGE,
                    self.logLine.emit,
                    self.transferProgress.emit,
                )
            finally:
                ssh.close()
        except Exception as exc:
            self.logLine.emit(
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            )
            self.failed.emit(str(exc))
        else:
            self.stageChanged.emit(100, "Untether installed")
            self.logLine.emit("CoolBooter Untetherer is installed. Reboot the iPhone to boot the secondary OS.")
            self.succeeded.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("iPhone 5 DualBooter v1.0.0")
        self.resize(980, 880)
        self.app_root = Path(__file__).resolve().parents[1]
        self.settings_store = QSettings("OpenAI", "iPhone5DualBooter")
        self.worker: WorkflowThread | None = None
        self._build_ui()
        self._load_settings()
        self._update_controls()
        if os.name == "nt":
            self._refresh_usb(silent=True)

    def _path_row(self, field: QLineEdit, title: str, file_filter: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(field)
        button = QPushButton("Browse…")
        button.clicked.connect(
            lambda: self._browse_file(field, title, file_filter)
        )
        row.addWidget(button)
        return row

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        outer = QVBoxLayout(content)

        title = QLabel("iPhone 5 DualBooter for Windows")
        title.setStyleSheet("font-size: 21px; font-weight: 700;")
        outer.addWidget(title)

        warning = QLabel(
            "This is the Windows version. Put the iPhone 5 in pwnDFU with whatever "
            "external method you already use, then reconnect it to this PC. After that, "
            "the app handles WSL, the iOS 8.4.1 restore, USB mode changes, and the "
            "CoolBooter setup."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            "padding: 10px; background: #5a3c00; color: white; border-radius: 5px;"
        )
        outer.addWidget(warning)

        firmware = QGroupBox("Firmware")
        form = QFormLayout(firmware)

        self.secondary_path = DropLineEdit((".ipsw",))
        form.addRow(
            "iOS version you want to dual boot:",
            self._path_row(
                self.secondary_path,
                "Select the secondary iOS IPSW",
                "IPSW files (*.ipsw)",
            ),
        )

        self.stock_host_ipsw = DropLineEdit((".ipsw",))
        form.addRow(
            "Stock iOS 8.4.1 IPSW:",
            self._path_row(
                self.stock_host_ipsw,
                "Select the stock iOS 8.4.1 Restore IPSW",
                "IPSW files (*.ipsw)",
            ),
        )

        self.version = QLineEdit()
        self.version.setPlaceholderText("Examples: 8.0, 7.0.6, 7.0b1")
        form.addRow("Secondary iOS version:", self.version)

        self.datasize = QLineEdit()
        self.datasize.setValidator(QIntValidator(1, 128, self))
        self.datasize.setPlaceholderText("Blank = default; whole number only, e.g. 5")
        form.addRow("Space for secondary iOS (GB):", self.datasize)
        outer.addWidget(firmware)

        restore = QGroupBox("iOS 8.4.1 setup")
        restore_form = QFormLayout(restore)

        self.skip_restore = QCheckBox(
            "Skip the restore because this phone is already jailbroken on iOS 8.4.1 with OpenSSH"
        )
        self.skip_restore.toggled.connect(self._update_controls)
        restore_form.addRow(self.skip_restore)

        self.restore_mode = QComboBox()
        self.restore_mode.addItem(
            "Full restore through WSL",
            "legacy_wsl_full",
        )
        self.restore_mode.currentIndexChanged.connect(self._update_controls)
        restore_form.addRow("Restore mode:", self.restore_mode)

        self.require_external_pwndfu = QCheckBox(
            "Ask me to confirm pwnDFU before Legacy starts"
        )
        self.require_external_pwndfu.setChecked(True)
        restore_form.addRow(self.require_external_pwndfu)

        self.use_windows_idevicerestore = QCheckBox(
            "Use the old unpatched Windows idevicerestore backend (leave this off)"
        )
        self.use_windows_idevicerestore.setChecked(False)
        self.use_windows_idevicerestore.toggled.connect(
            self._update_controls
        )
        restore_form.addRow(self.use_windows_idevicerestore)

        self.windows_idevicerestore_dir = QLineEdit()
        restore_form.addRow(
            "Windows idevicerestore suite folder (optional):",
            self._path_row(
                self.windows_idevicerestore_dir,
                "Select the folder containing idevicerestore.exe and DLLs",
                "All files (*)",
            ),
        )

        self.auto_download_windows_idevicerestore = QCheckBox(
            "Download the Windows idevicerestore files automatically if they are missing"
        )
        self.auto_download_windows_idevicerestore.setChecked(True)
        restore_form.addRow(
            self.auto_download_windows_idevicerestore
        )

        windows_host_note = QLabel(
            "Leave this on the patched WSL restore. The normal Windows build does not "
            "have the iBEC retry, live Recovery reconnect, or the other fixes that made "
            "this restore work."
        )
        windows_host_note.setWordWrap(True)
        restore_form.addRow(windows_host_note)

        self.patch_idevicerestore = QCheckBox(
            "Build and use the patched WSL idevicerestore"
        )
        self.patch_idevicerestore.setChecked(True)
        restore_form.addRow(self.patch_idevicerestore)

        self.ibec_retry_seconds = QSpinBox()
        self.ibec_retry_seconds.setRange(5, 180)
        self.ibec_retry_seconds.setValue(45)
        self.ibec_retry_seconds.setVisible(False)
        retry_mode = QLabel(
            "Keep retrying until it works or I cancel it"
        )
        retry_mode.setWordWrap(True)
        restore_form.addRow("DFU/recovery retry mode:", retry_mode)

        self.full_rebuild_fallback = QCheckBox(
            "Try the slower full build if the fast build fails"
        )
        self.full_rebuild_fallback.setChecked(True)
        restore_form.addRow(self.full_rebuild_fallback)

        rebuild_note = QLabel(
            "The patched build is saved after the first build. The app keeps the same "
            "restore running while the phone changes from DFU to Recovery and handles "
            "the USB reconnects automatically."
        )
        rebuild_note.setWordWrap(True)
        restore_form.addRow(rebuild_note)

        self.wsl_distro = QLineEdit("Ubuntu")
        restore_form.addRow("WSL distro:", self.wsl_distro)

        self.legacy_dir = QLineEdit()
        restore_form.addRow(
            "Legacy iOS Kit folder (optional):",
            self._path_row(
                self.legacy_dir,
                "Select Legacy iOS Kit folder",
                "All files (*)",
            ),
        )

        self.auto_download_legacy = QCheckBox(
            "Download Legacy iOS Kit automatically if it is missing"
        )
        self.auto_download_legacy.setChecked(True)
        restore_form.addRow(self.auto_download_legacy)

        usb_row = QHBoxLayout()
        self.usb_device = QComboBox()
        self.usb_device.setEditable(True)
        self.usb_device.setMinimumWidth(420)
        usb_row.addWidget(self.usb_device)
        self.refresh_usb_button = QPushButton("Refresh")
        self.refresh_usb_button.clicked.connect(self._refresh_usb)
        usb_row.addWidget(self.refresh_usb_button)
        restore_form.addRow("Apple USB device / BUSID:", usb_row)

        usb_actions = QHBoxLayout()
        self.attach_usb_button = QPushButton("Attach to WSL")
        self.attach_usb_button.clicked.connect(self._attach_usb)
        usb_actions.addWidget(self.attach_usb_button)
        self.detach_usb_button = QPushButton("Detach to Windows")
        self.detach_usb_button.clicked.connect(self._detach_usb)
        usb_actions.addWidget(self.detach_usb_button)
        restore_form.addRow(usb_actions)

        self.auto_attach_usb = QCheckBox(
            "Automatically keep the iPhone attached to WSL"
        )
        self.auto_attach_usb.setChecked(True)
        restore_form.addRow(self.auto_attach_usb)

        self.auto_detach_usb = QCheckBox(
            "Give the iPhone back to Windows after I confirm the restore finished"
        )
        self.auto_detach_usb.setChecked(True)
        restore_form.addRow(self.auto_detach_usb)

        instructions = QLabel(
            "<b>How to use it:</b><br>"
            "1. Put the iPhone 5 in pwnDFU before Legacy opens.<br>"
            "2. Plug it back into this Windows PC without restarting it.<br>"
            "3. Confirm the prompt and let the app attach it to WSL.<br>"
            "4. In Legacy, choose <b>Restore/Downgrade → iOS 8.4.1</b> and start it.<br>"
            "5. Do not touch the DFU buttons again after the restore starts.<br>"
            "6. When the phone boots, connect it to the same Wi-Fi as this PC and enter the IP.<br>"
            "7. The app will install CoolBooterCLI and the missing packages by itself."
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet(
            "padding: 9px; background: rgba(70, 110, 160, 0.18); border-radius: 5px;"
        )
        restore_form.addRow(instructions)
        outer.addWidget(restore)

        connection = QGroupBox("After the restore")
        connection_form = QFormLayout(connection)

        self.phone_wifi_ip = QLineEdit()
        self.phone_wifi_ip.setPlaceholderText(
            "Example: 192.168.1.123 — leave it blank to enter it later"
        )
        connection_form.addRow("iPhone Wi-Fi IPv4:", self.phone_wifi_ip)

        self.ssh_port = QSpinBox()
        self.ssh_port.setRange(1, 65535)
        self.ssh_port.setValue(22)
        connection_form.addRow("SSH port:", self.ssh_port)

        wifi_note = QLabel(
            "After the restore, connect the iPhone to the same Wi-Fi as this PC. "
            "The app will ask for its IP before it continues. You can get the IP "
            "from the Wi-Fi details on the phone or your router."
        )
        wifi_note.setWordWrap(True)
        connection_form.addRow(wifi_note)

        self.test_ssh_button = QPushButton("Test SSH")
        self.test_ssh_button.clicked.connect(self._test_wifi_ssh)
        connection_form.addRow(self.test_ssh_button)

        self.install_untether_only_button = QPushButton("Install Untether Only")
        self.install_untether_only_button.setToolTip(
            "Install the CoolBooter untether on an existing tethered CoolBooter setup without running the restore or installation again."
        )
        self.install_untether_only_button.clicked.connect(self._install_untether_only)
        connection_form.addRow(self.install_untether_only_button)

        self.root_password = QLineEdit("alpine")
        self.root_password.setEchoMode(QLineEdit.EchoMode.Password)
        connection_form.addRow("Root password:", self.root_password)

        self.install_untether = QCheckBox(
            "Install the CoolBooter untether after installation"
        )
        self.install_untether.setChecked(True)
        connection_form.addRow(self.install_untether)
        outer.addWidget(connection)

        buttons = QHBoxLayout()
        self.inspect_button = QPushButton("Check the IPSWs")
        self.inspect_button.clicked.connect(self._inspect)
        buttons.addWidget(self.inspect_button)
        self.start_button = QPushButton("Start")
        self.start_button.setStyleSheet("font-weight: 700; padding: 8px 18px;")
        self.start_button.clicked.connect(self._start)
        buttons.addWidget(self.start_button)
        outer.addLayout(buttons)

        self.stage_label = QLabel("Ready when you are")
        outer.addWidget(self.stage_label)

        self.progress = QProgressBar()
        outer.addWidget(self.progress)

        self.transfer = QProgressBar()
        self.transfer.setFormat("File transfer: %p%")
        self.transfer.hide()
        outer.addWidget(self.transfer)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        outer.addWidget(self.log, 1)

        scroll.setWidget(content)
        self.setCentralWidget(scroll)

    def _browse_file(self, field: QLineEdit, title: str, file_filter: str) -> None:
        if "Legacy iOS Kit folder" in title:
            path = QFileDialog.getExistingDirectory(self, title)
        else:
            path, _ = QFileDialog.getOpenFileName(self, title, "", file_filter)
        if path:
            field.setText(path)

    def _selected_busid(self) -> str:
        data = self.usb_device.currentData()
        if data:
            return str(data).strip()
        text = self.usb_device.currentText().strip()
        return text.split(" ", 1)[0].strip()

    def _refresh_usb(self, silent: bool = False) -> None:
        if os.name != "nt":
            return
        selected = self._selected_busid()
        try:
            devices = list_devices()
        except Exception as exc:
            if not silent:
                QMessageBox.warning(self, "USBIPD", str(exc))
            return

        devices.sort(key=lambda device: (not device.is_apple, device.busid))
        self.usb_device.clear()
        for device in devices:
            label = f"{device.busid} — {device.device} [{device.state}]"
            self.usb_device.addItem(label, device.busid)

        target = selected or getattr(self, "saved_busid", "")
        if target:
            index = self.usb_device.findData(target)
            if index >= 0:
                self.usb_device.setCurrentIndex(index)
        if not silent:
            self.log.appendPlainText(f"USBIPD: found {len(devices)} connected USB devices.")

    def _attach_usb(self) -> None:
        busid = self._selected_busid()
        if not busid:
            QMessageBox.warning(self, "USBIPD", "Select an Apple USB device first.")
            return
        try:
            attach_to_wsl(
                busid,
                self.wsl_distro.text().strip() or "Ubuntu",
                self.log.appendPlainText,
            )
        except Exception as exc:
            QMessageBox.critical(self, "USB attach failed", str(exc))
            return
        self._refresh_usb(silent=True)
        QMessageBox.information(
            self,
            "USB attached",
            "The selected device is attached to WSL. During the workflow, the "
            "background watcher will follow later USB mode changes automatically.",
        )

    def _detach_usb(self) -> None:
        busid = self._selected_busid()
        if not busid:
            QMessageBox.warning(self, "USBIPD", "Select a USB device first.")
            return
        try:
            detach_from_wsl(
                busid,
                self.log.appendPlainText,
                distro=self.wsl_distro.text().strip() or "Ubuntu",
            )
        except Exception as exc:
            QMessageBox.critical(self, "USB detach failed", str(exc))
            return
        self._refresh_usb(silent=True)

    def _test_wifi_ssh(self) -> None:
        try:
            phone_ip = validate_ipv4(self.phone_wifi_ip.text())
            port = self.ssh_port.value()
            client = IOSSSH(
                phone_ip,
                port,
                self.root_password.text(),
                self.log.appendPlainText,
            )
            client.connect(timeout=7)
            try:
                client.run("echo wifi-ssh-ok", check=True, timeout=10)
            finally:
                client.close()
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Wi-Fi SSH test failed",
                str(exc)
                + "\n\nMake sure the iPhone and PC are on the same Wi-Fi, "
                  "OpenSSH is installed, and the IP has not changed.",
            )
            return

        QMessageBox.information(
            self,
            "Wi-Fi SSH works",
            f"Connected successfully to root@{phone_ip}:{port}.",
        )

    def _update_controls(self) -> None:
        skipped = self.skip_restore.isChecked()
        mode = str(self.restore_mode.currentData())
        windows_mode = mode == "legacy_wsl_full"

        for widget in (
            self.restore_mode,
            self.legacy_dir,
            self.auto_download_legacy,
        ):
            widget.setEnabled(not skipped)

        self.wsl_distro.setEnabled(not skipped and windows_mode)
        self.usb_device.setEnabled(not skipped and windows_mode)
        self.refresh_usb_button.setEnabled(windows_mode)
        self.attach_usb_button.setEnabled(windows_mode)
        self.detach_usb_button.setEnabled(windows_mode)
        self.auto_attach_usb.setEnabled(not skipped and windows_mode)
        self.auto_detach_usb.setEnabled(not skipped and windows_mode)
        self.require_external_pwndfu.setEnabled(not skipped and windows_mode)
        self.use_windows_idevicerestore.setChecked(False)
        self.use_windows_idevicerestore.setEnabled(False)
        windows_host_enabled = (
            not skipped
            and windows_mode
            and self.use_windows_idevicerestore.isChecked()
        )
        self.windows_idevicerestore_dir.setEnabled(
            windows_host_enabled
        )
        self.auto_download_windows_idevicerestore.setEnabled(
            windows_host_enabled
        )
        self.patch_idevicerestore.setEnabled(
            not skipped
            and windows_mode
            and not self.use_windows_idevicerestore.isChecked()
        )
        self.ibec_retry_seconds.setEnabled(
            not skipped
            and windows_mode
            and not self.use_windows_idevicerestore.isChecked()
        )
        self.full_rebuild_fallback.setEnabled(
            not skipped
            and windows_mode
            and not self.use_windows_idevicerestore.isChecked()
        )

    def _inspect(self) -> None:
        try:
            version = validate_secondary_version(self.version.text())
            secondary = inspect_ipsw(Path(self.secondary_path.text().strip()))
            validate_secondary_info(secondary, version)

            lines = [
                "Secondary IPSW",
                f"  Entered: {version}",
                f"  Manifest: {secondary.product_version} ({secondary.build_version})",
                f"  Models: {', '.join(sorted(secondary.product_types))}",
            ]

            stock_text = self.stock_host_ipsw.text().strip()
            if stock_text:
                stock = inspect_ipsw(Path(stock_text))
                validate_host_841_info(stock)
                validate_host_secondary_models(stock, secondary)
                lines.extend(
                    [
                        "",
                        "Stock iOS 8.4.1 Restore IPSW",
                        f"  Version: {stock.product_version} ({stock.build_version})",
                        f"  Models: {', '.join(sorted(stock.product_types))}",
                    ]
                )
        except Exception as exc:
            QMessageBox.critical(self, "Firmware check failed", str(exc))
            return
        QMessageBox.information(self, "Firmware details", "\n".join(lines))

    def _collect_settings(self) -> WorkflowSettings:
        secondary = Path(self.secondary_path.text().strip())
        if not secondary.is_file():
            raise ValueError("Select the secondary IPSW.")

        version = validate_secondary_version(self.version.text())
        skipped = self.skip_restore.isChecked()
        stock_text = self.stock_host_ipsw.text().strip()
        stock = Path(stock_text) if stock_text else None
        if not skipped and (not stock or not stock.is_file()):
            raise ValueError("Select the stock iOS 8.4.1 Restore IPSW.")

        datasize_text = self.datasize.text().strip()
        datasize = int(datasize_text) if datasize_text else None

        mode = str(self.restore_mode.currentData())
        if not skipped and mode == "legacy_wsl_full" and os.name != "nt":
            raise ValueError("The WSL full restore mode must be run on Windows.")

        busid = self._selected_busid()
        if (
            not skipped
            and mode == "legacy_wsl_full"
            and self.auto_attach_usb.isChecked()
            and not busid
        ):
            raise ValueError("Refresh and select the Apple USB device/BUSID.")

        legacy_text = self.legacy_dir.text().strip()
        windows_idr_text = (
            self.windows_idevicerestore_dir.text().strip()
        )
        phone_ip = self.phone_wifi_ip.text().strip()
        if phone_ip:
            phone_ip = validate_ipv4(phone_ip)

        return WorkflowSettings(
            secondary_ipsw=secondary,
            stock_host_ipsw=stock,
            secondary_version=version,
            datasize_gb=datasize,
            skip_restore=skipped,
            restore_mode=mode,
            legacy_kit_dir=Path(legacy_text) if legacy_text else None,
            auto_download_legacy_kit=self.auto_download_legacy.isChecked(),
            legacy_wsl_distro=self.wsl_distro.text().strip() or "Ubuntu",
            usbipd_busid=busid,
            auto_attach_usb_to_wsl=self.auto_attach_usb.isChecked(),
            auto_detach_usb_after_restore=self.auto_detach_usb.isChecked(),
            require_external_pwndfu=(
                self.require_external_pwndfu.isChecked()
                and mode == "legacy_wsl_full"
            ),
            patch_idevicerestore=(
                self.patch_idevicerestore.isChecked()
                and mode == "legacy_wsl_full"
            ),
            ibec_retry_seconds=(
                self.ibec_retry_seconds.value()
                if mode == "legacy_wsl_full"
                else 45
            ),
            full_rebuild_fallback=(
                self.full_rebuild_fallback.isChecked()
                and mode == "legacy_wsl_full"
            ),
            phone_wifi_ip=phone_ip,
            ssh_port=self.ssh_port.value(),
            root_password=self.root_password.text(),
            install_untether=self.install_untether.isChecked(),
            use_windows_idevicerestore=(
                self.use_windows_idevicerestore.isChecked()
                and mode == "legacy_wsl_full"
            ),
            windows_idevicerestore_dir=(
                Path(windows_idr_text)
                if windows_idr_text
                else None
            ),
            auto_download_windows_idevicerestore=(
                self.auto_download_windows_idevicerestore.isChecked()
            ),
        )

    def _show_legacy_instructions(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Put the phone in pwnDFU first")
        box.setText("This app does not run the A6 pwnDFU exploit itself.")
        box.setInformativeText(
            "Before Legacy opens, put the iPhone 5 in pwnDFU using whatever method "
            "you already use, then plug it back into this Windows PC.\n\n"
            "After you confirm it, do not press the DFU buttons again and do not "
            "restart the phone. Legacy should detect that it is already pwned and continue."
        )
        continue_button = box.addButton(
            "Continue",
            QMessageBox.ButtonRole.AcceptRole,
        )
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(continue_button)
        box.exec()
        return box.clickedButton() is continue_button

    def _show_legacy_prompt(
        self,
        exit_code: int,
        transcript: str,
        attempt: int,
        reason: str,
    ) -> None:
        if not self.worker:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Did the restore finish?")
        box.setText(
            f"Legacy iOS Kit closed (launch {attempt}, exit code {exit_code})."
        )
        box.setInformativeText(
            "Pick Restore Finished only if Legacy said it completed and the phone booted "
            "to the jailbroken iOS 8.4.1 setup screen.\n\n"
            "Pick Run Legacy Again if it closed early, only installed something, updated "
            "itself, or showed an error. You will need to confirm pwnDFU again before it "
            "restarts.\n\n"
            f"Transcript: {transcript}"
        )
        complete_button = box.addButton(
            "Restore Finished — Continue",
            QMessageBox.ButtonRole.AcceptRole,
        )
        rerun_button = box.addButton(
            "Run Legacy Again",
            QMessageBox.ButtonRole.ActionRole,
        )
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(rerun_button)
        box.exec()

        clicked = box.clickedButton()
        if clicked is complete_button:
            action = "continue"
        elif clicked is rerun_button:
            action = "rerun"
        else:
            action = "cancel"
        self.worker.answer_legacy_prompt(action)

    def _show_external_pwndfu_prompt(self, attempt: int) -> None:
        if not self.worker:
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Confirm pwnDFU")
        box.setText(
            f"Legacy launch #{attempt} is paused until you confirm external pwnDFU."
        )
        box.setInformativeText(
            "1. Disconnect the iPhone 5 from this PC.\n"
            "2. Put it in pwnDFU using whatever external method you already use.\n"
            "3. Plug it back into Windows without restarting it.\n"
            "4. Wait for Windows to see the Apple DFU device.\n"
            "5. Confirm it below.\n\n"
            "The app cannot tell normal DFU apart from pwnDFU, so only continue when "
            "you know it is actually pwned. Do not repeat the DFU buttons inside Legacy."
        )
        confirm_button = box.addButton(
            "It is in pwnDFU — Continue",
            QMessageBox.ButtonRole.AcceptRole,
        )
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(confirm_button)
        box.exec()

        confirmed = box.clickedButton() is confirm_button
        self.worker.answer_external_pwndfu_prompt(confirmed)


    def _show_first_boot_prompt(self) -> None:
        if not self.worker:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("First CoolBooter setup")
        box.setText("Your device will restart while CoolBooter configures the secondary OS.")
        box.setInformativeText(
            "Wait until the iPhone has booted all the way back into the main iOS 8.4.1 system, "
            "then press OK. The app will run coolbootercli -b again to actually boot the secondary OS."
        )
        ok_button = box.addButton(QMessageBox.StandardButton.Ok)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(ok_button)
        box.exec()
        self.worker.answer_first_boot_prompt(box.clickedButton() is ok_button)

    def _show_wifi_prompt(self, current_ip: str, current_port: int) -> None:
        if not self.worker:
            return

        while True:
            phone_ip, accepted = QInputDialog.getText(
                self,
                "Enter the iPhone IP",
                "Connect the iPhone to the same Wi-Fi as this PC, then enter its IP "
                "address (example: 192.168.1.123):",
                QLineEdit.EchoMode.Normal,
                current_ip,
            )
            if not accepted:
                self.worker.answer_wifi_prompt(None)
                return

            try:
                phone_ip = validate_ipv4(phone_ip)
            except Exception as exc:
                QMessageBox.warning(self, "Invalid IP address", str(exc))
                current_ip = phone_ip.strip()
                continue
            break

        port, accepted = QInputDialog.getInt(
            self,
            "SSH port",
            "OpenSSH port:",
            int(current_port or 22),
            1,
            65535,
            1,
        )
        if not accepted:
            self.worker.answer_wifi_prompt(None)
            return

        self.phone_wifi_ip.setText(phone_ip)
        self.ssh_port.setValue(port)
        self._save_settings()
        self.worker.answer_wifi_prompt((phone_ip, port))

    def _install_untether_only(self) -> None:
        try:
            phone_ip = validate_ipv4(self.phone_wifi_ip.text())
            ssh_port = self.ssh_port.value()
            root_password = self.root_password.text()
            if not root_password:
                raise ValueError("Enter the iPhone root password.")
        except Exception as exc:
            QMessageBox.critical(self, "Cannot install untether", str(exc))
            return

        answer = QMessageBox.question(
            self,
            "Install Untether Only",
            "This only installs the CoolBooter untether on the current iOS 8.4.1 setup. "
            "It will not restore iOS or reinstall the secondary OS. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._save_settings()
        self.log.clear()
        self.progress.setValue(0)
        self.transfer.hide()
        self._set_busy(True)

        self.worker = UntetherInstallThread(phone_ip, ssh_port, root_password)
        self.worker.logLine.connect(self.log.appendPlainText)
        self.worker.stageChanged.connect(self._stage_changed)
        self.worker.transferProgress.connect(self._transfer_changed)
        self.worker.succeeded.connect(self._untether_only_success)
        self.worker.failed.connect(self._failure)
        self.worker.finished.connect(lambda: self._set_busy(False))
        self.worker.start()

    def _untether_only_success(self) -> None:
        QMessageBox.information(
            self,
            "Untether installed",
            "Done! The CoolBooter untether is installed. Reboot the iPhone whenever you want to boot the secondary OS.",
        )

    def _start(self) -> None:
        try:
            settings = self._collect_settings()
            validate_firmware_settings(settings)
        except Exception as exc:
            QMessageBox.critical(self, "Cannot start", str(exc))
            return

        if not settings.skip_restore and not self._show_legacy_instructions():
            return

        answer = QMessageBox.warning(
            self,
            "Erase and repartition warning",
            "This erases the iPhone and later repartitions it for CoolBooter. "
            "Use a spare iPhone 5, a backup, and a reliable cable.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._save_settings()
        self.log.clear()
        self.progress.setValue(0)
        self.transfer.hide()
        self._set_busy(True)

        self.worker = WorkflowThread(settings, self.app_root)
        self.worker.legacyPromptRequested.connect(self._show_legacy_prompt)
        self.worker.wifiPromptRequested.connect(self._show_wifi_prompt)
        self.worker.externalPwnPromptRequested.connect(
            self._show_external_pwndfu_prompt
        )
        self.worker.firstBootPromptRequested.connect(self._show_first_boot_prompt)
        self.worker.logLine.connect(self.log.appendPlainText)
        self.worker.stageChanged.connect(self._stage_changed)
        self.worker.transferProgress.connect(self._transfer_changed)
        self.worker.succeeded.connect(self._success)
        self.worker.failed.connect(self._failure)
        self.worker.finished.connect(lambda: self._set_busy(False))
        self.worker.start()

    def _set_busy(self, busy: bool) -> None:
        self.start_button.setEnabled(not busy)
        self.inspect_button.setEnabled(not busy)
        self.test_ssh_button.setEnabled(not busy)
        self.install_untether_only_button.setEnabled(not busy)
        # Keep USB controls usable while Legacy is open because mode changes can detach it.
        self.refresh_usb_button.setEnabled(os.name == "nt")
        self.attach_usb_button.setEnabled(os.name == "nt")
        self.detach_usb_button.setEnabled(os.name == "nt")
        if not busy:
            self._update_controls()

    def _stage_changed(self, percent: int, text: str) -> None:
        self.progress.setValue(percent)
        self.stage_label.setText(text)
        self.transfer.hide()

    def _transfer_changed(self, sent: int, total: int) -> None:
        self.transfer.show()
        self.transfer.setValue(int((sent / total) * 100) if total else 0)

    def _success(self) -> None:
        if self.install_untether.isChecked():
            message = (
                "Done!\n\nThe untether is installed. To boot the secondary OS again, "
                "just reboot the iPhone."
            )
        else:
            message = (
                "Done!\n\nTo boot the secondary OS again, SSH into the iPhone and run "
                "coolbootercli -b, or open a terminal app on the phone and run "
                "coolbootercli -b."
            )
        QMessageBox.information(self, "Done!", message)

    def _failure(self, message: str) -> None:
        QMessageBox.critical(
            self,
            "Workflow stopped",
            message + "\n\nThe full traceback is in the log.",
        )

    def _save_settings(self) -> None:
        values = {
            "secondary_path": self.secondary_path.text(),
            "stock_host_ipsw": self.stock_host_ipsw.text(),
            "version": self.version.text(),
            "datasize": self.datasize.text(),
            "skip_restore": self.skip_restore.isChecked(),
            "restore_mode_v040": self.restore_mode.currentData(),
            "wsl_distro": self.wsl_distro.text(),
            "legacy_dir": self.legacy_dir.text(),
            "auto_download_legacy": self.auto_download_legacy.isChecked(),
            "usb_busid": self._selected_busid(),
            "auto_attach_usb": self.auto_attach_usb.isChecked(),
            "auto_detach_usb": self.auto_detach_usb.isChecked(),
            "require_external_pwndfu": self.require_external_pwndfu.isChecked(),
            "use_windows_idevicerestore": (
                self.use_windows_idevicerestore.isChecked()
            ),
            "windows_idevicerestore_dir": (
                self.windows_idevicerestore_dir.text()
            ),
            "auto_download_windows_idevicerestore": (
                self.auto_download_windows_idevicerestore.isChecked()
            ),
            "patch_idevicerestore": self.patch_idevicerestore.isChecked(),
            "ibec_retry_seconds": self.ibec_retry_seconds.value(),
            "full_rebuild_fallback": self.full_rebuild_fallback.isChecked(),
            "phone_wifi_ip": self.phone_wifi_ip.text(),
            "ssh_port": self.ssh_port.value(),
            "install_untether": self.install_untether.isChecked(),
        }
        for key, value in values.items():
            self.settings_store.setValue(key, value)

    def _load_settings(self) -> None:
        self.secondary_path.setText(self.settings_store.value("secondary_path", ""))
        self.stock_host_ipsw.setText(
            self.settings_store.value("stock_host_ipsw", "")
        )
        self.version.setText(self.settings_store.value("version", ""))
        self.datasize.setText(self.settings_store.value("datasize", ""))
        self.skip_restore.setChecked(
            self.settings_store.value("skip_restore", False, type=bool)
        )

        default_mode = "legacy_wsl_full"
        mode = self.settings_store.value("restore_mode_v040", default_mode)
        index = self.restore_mode.findData(mode)
        if index >= 0:
            self.restore_mode.setCurrentIndex(index)

        self.wsl_distro.setText(self.settings_store.value("wsl_distro", "Ubuntu"))
        self.legacy_dir.setText(self.settings_store.value("legacy_dir", ""))
        self.auto_download_legacy.setChecked(
            self.settings_store.value("auto_download_legacy", True, type=bool)
        )
        self.saved_busid = self.settings_store.value("usb_busid", "")
        self.auto_attach_usb.setChecked(
            self.settings_store.value("auto_attach_usb", True, type=bool)
        )
        self.auto_detach_usb.setChecked(
            self.settings_store.value("auto_detach_usb", True, type=bool)
        )
        self.require_external_pwndfu.setChecked(
            self.settings_store.value(
                "require_external_pwndfu",
                True,
                type=bool,
            )
        )
        self.use_windows_idevicerestore.setChecked(
            self.settings_store.value(
                "use_windows_idevicerestore",
                False,
                type=bool,
            )
        )
        # Transition hotfix: do not revive an old saved Windows-host setting.
        self.use_windows_idevicerestore.setChecked(False)
        self.windows_idevicerestore_dir.setText(
            self.settings_store.value(
                "windows_idevicerestore_dir",
                "",
            )
        )
        self.auto_download_windows_idevicerestore.setChecked(
            self.settings_store.value(
                "auto_download_windows_idevicerestore",
                True,
                type=bool,
            )
        )
        self.patch_idevicerestore.setChecked(
            self.settings_store.value(
                "patch_idevicerestore",
                True,
                type=bool,
            )
        )
        self.ibec_retry_seconds.setValue(
            int(self.settings_store.value("ibec_retry_seconds", 45))
        )
        self.full_rebuild_fallback.setChecked(
            self.settings_store.value(
                "full_rebuild_fallback",
                True,
                type=bool,
            )
        )
        self.phone_wifi_ip.setText(
            self.settings_store.value("phone_wifi_ip", "")
        )
        self.ssh_port.setValue(int(self.settings_store.value("ssh_port", 22)))
        self.install_untether.setChecked(
            self.settings_store.value("install_untether", True, type=bool)
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("iPhone5DualBooter")
    window = MainWindow()
    window.show()
    return app.exec()
