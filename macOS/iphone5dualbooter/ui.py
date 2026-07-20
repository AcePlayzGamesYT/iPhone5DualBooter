from __future__ import annotations

from pathlib import Path
import os
import sys
import threading
import traceback

from PySide6.QtCore import QSettings, QThread, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIntValidator
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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


class WorkflowThread(QThread):
    logLine = Signal(str)
    stageChanged = Signal(int, str)
    transferProgress = Signal(int, int)
    legacyPromptRequested = Signal(int, int)
    wifiPromptRequested = Signal(str, int)
    firstBootPromptRequested = Signal()
    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, settings: WorkflowSettings, app_root: Path):
        super().__init__()
        self.workflow_settings = settings
        self.app_root = app_root
        self._legacy_event = threading.Event()
        self._legacy_action = "cancel"
        self._wifi_event = threading.Event()
        self._wifi_endpoint: tuple[str, int] | None = None
        self._first_boot_event = threading.Event()
        self._first_boot_confirmed = False

    def request_legacy_action(self, exit_code: int, attempt: int) -> str:
        self._legacy_action = "cancel"
        self._legacy_event.clear()
        self.legacyPromptRequested.emit(exit_code, attempt)
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

    def answer_wifi_prompt(self, endpoint: tuple[str, int] | None) -> None:
        self._wifi_endpoint = endpoint
        self._wifi_event.set()

    def request_first_boot_confirmation(self) -> bool:
        self._first_boot_confirmed = False
        self._first_boot_event.clear()
        self.firstBootPromptRequested.emit()
        self._first_boot_event.wait()
        return self._first_boot_confirmed

    def answer_first_boot_prompt(self, confirmed: bool) -> None:
        self._first_boot_confirmed = bool(confirmed)
        self._first_boot_event.set()

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
            self.logLine.emit(
                "CoolBooter Untetherer is installed. Reboot the iPhone to boot the secondary OS."
            )
            self.succeeded.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("iPhone 5 DualBooter for macOS v1.0.0")
        self.resize(980, 860)
        self.app_root = Path(__file__).resolve().parents[1]
        self.settings_store = QSettings("iPhone5DualBooter", "macOS")
        self.worker: QThread | None = None
        self._build_ui()
        self._load_settings()
        self._update_controls()

    def _path_row(
        self,
        field: QLineEdit,
        title: str,
        file_filter: str,
        directory: bool = False,
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(field)
        button = QPushButton("Browse…")
        button.clicked.connect(
            lambda: self._browse(field, title, file_filter, directory)
        )
        row.addWidget(button)
        return row

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        outer = QVBoxLayout(content)

        title = QLabel("iPhone 5 DualBooter for macOS")
        title.setStyleSheet("font-size: 21px; font-weight: 700;")
        outer.addWidget(title)

        notice = QLabel(
            "Native macOS edition. Legacy iOS Kit runs directly on macOS using the standard native restore process."
        )
        notice.setWordWrap(True)
        notice.setStyleSheet(
            "padding: 10px; background: #244b2f; color: white; border-radius: 5px;"
        )
        outer.addWidget(notice)

        firmware = QGroupBox("Firmware")
        firmware_form = QFormLayout(firmware)
        self.secondary_path = DropLineEdit((".ipsw",))
        firmware_form.addRow(
            "Secondary IPSW:",
            self._path_row(
                self.secondary_path,
                "Select the secondary IPSW",
                "IPSW files (*.ipsw)",
            ),
        )
        self.stock_host_ipsw = DropLineEdit((".ipsw",))
        firmware_form.addRow(
            "Stock iOS 8.4.1 IPSW:",
            self._path_row(
                self.stock_host_ipsw,
                "Select the stock iOS 8.4.1 IPSW",
                "IPSW files (*.ipsw)",
            ),
        )
        self.version = QLineEdit()
        self.version.setPlaceholderText("Examples: 8.0, 7.0.6, 7.0b1")
        firmware_form.addRow("Secondary iOS version:", self.version)
        self.datasize = QLineEdit()
        self.datasize.setValidator(QIntValidator(1, 128, self))
        self.datasize.setPlaceholderText("Blank uses the default")
        firmware_form.addRow("Secondary data size in GB:", self.datasize)
        outer.addWidget(firmware)

        restore = QGroupBox("Native Legacy iOS Kit restore")
        restore_form = QFormLayout(restore)
        self.skip_restore = QCheckBox(
            "Skip restore because the phone already has jailbroken iOS 8.4.1 and OpenSSH"
        )
        self.skip_restore.toggled.connect(self._update_controls)
        restore_form.addRow(self.skip_restore)
        self.legacy_dir = QLineEdit()
        restore_form.addRow(
            "Legacy iOS Kit folder:",
            self._path_row(
                self.legacy_dir,
                "Select Legacy iOS Kit folder",
                "All files (*)",
                True,
            ),
        )
        self.auto_download_legacy = QCheckBox(
            "Download Legacy iOS Kit automatically when missing"
        )
        self.auto_download_legacy.setChecked(True)
        restore_form.addRow(self.auto_download_legacy)
        instructions = QLabel(
            "The app launches Legacy iOS Kit natively with the stock 8.4.1 IPSW. Legacy iOS Kit handles pwnDFU directly on macOS. If Legacy installs dependencies, updates itself, or exits early, choose Run Legacy Again. After the restored phone boots, connect it to the same Wi-Fi network as this computer and enter its IP address when prompted."
        )
        instructions.setWordWrap(True)
        restore_form.addRow(instructions)
        outer.addWidget(restore)

        connection = QGroupBox("Post-restore connection")
        connection_form = QFormLayout(connection)
        self.phone_wifi_ip = QLineEdit()
        self.phone_wifi_ip.setPlaceholderText(
            "Example: 192.168.1.123 or leave blank for the later prompt"
        )
        connection_form.addRow("iPhone Wi-Fi IPv4:", self.phone_wifi_ip)
        self.ssh_port = QSpinBox()
        self.ssh_port.setRange(1, 65535)
        self.ssh_port.setValue(22)
        connection_form.addRow("SSH port:", self.ssh_port)
        self.root_password = QLineEdit("alpine")
        self.root_password.setEchoMode(QLineEdit.EchoMode.Password)
        connection_form.addRow("Root password:", self.root_password)
        self.install_untether = QCheckBox(
            "Install the CoolBooter untether after the secondary installation"
        )
        self.install_untether.setChecked(True)
        connection_form.addRow(self.install_untether)
        action_row = QHBoxLayout()
        self.test_ssh_button = QPushButton("Test SSH")
        self.test_ssh_button.clicked.connect(self._test_ssh)
        action_row.addWidget(self.test_ssh_button)
        self.install_untether_only_button = QPushButton("Install Untether Only")
        self.install_untether_only_button.clicked.connect(
            self._install_untether_only
        )
        action_row.addWidget(self.install_untether_only_button)
        connection_form.addRow(action_row)
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

        self.stage_label = QLabel("Ready")
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

    def _browse(
        self,
        field: QLineEdit,
        title: str,
        file_filter: str,
        directory: bool,
    ) -> None:
        if directory:
            path = QFileDialog.getExistingDirectory(self, title)
        else:
            path, _ = QFileDialog.getOpenFileName(self, title, "", file_filter)
        if path:
            field.setText(path)

    def _update_controls(self) -> None:
        enabled = not self.skip_restore.isChecked()
        self.stock_host_ipsw.setEnabled(enabled)
        self.legacy_dir.setEnabled(enabled)
        self.auto_download_legacy.setEnabled(enabled)

    def _inspect(self) -> None:
        try:
            version = validate_secondary_version(self.version.text())
            secondary = inspect_ipsw(Path(self.secondary_path.text().strip()))
            validate_secondary_info(secondary, version)
            lines = [
                "Secondary IPSW",
                f"Version: {secondary.product_version} ({secondary.build_version})",
                f"Models: {', '.join(sorted(secondary.product_types))}",
            ]
            stock_text = self.stock_host_ipsw.text().strip()
            if stock_text:
                stock = inspect_ipsw(Path(stock_text))
                validate_host_841_info(stock)
                validate_host_secondary_models(stock, secondary)
                lines.extend(
                    [
                        "",
                        "Stock IPSW",
                        f"Version: {stock.product_version} ({stock.build_version})",
                        f"Models: {', '.join(sorted(stock.product_types))}",
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
            raise ValueError("Select the stock iOS 8.4.1 IPSW.")
        datasize_text = self.datasize.text().strip()
        datasize = int(datasize_text) if datasize_text else None
        legacy_text = self.legacy_dir.text().strip()
        phone_ip = self.phone_wifi_ip.text().strip()
        if phone_ip:
            phone_ip = validate_ipv4(phone_ip)
        settings = WorkflowSettings(
            secondary_ipsw=secondary,
            stock_host_ipsw=stock,
            secondary_version=version,
            datasize_gb=datasize,
            skip_restore=skipped,
            legacy_kit_dir=Path(legacy_text) if legacy_text else None,
            auto_download_legacy_kit=self.auto_download_legacy.isChecked(),
            phone_wifi_ip=phone_ip,
            ssh_port=self.ssh_port.value(),
            root_password=self.root_password.text(),
            install_untether=self.install_untether.isChecked(),
        )
        validate_firmware_settings(settings)
        return settings

    def _start(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        if sys.platform != "darwin":
            QMessageBox.critical(
                self,
                "macOS required",
                "This application requires macOS.",
            )
            return
        try:
            settings = self._collect_settings()
        except Exception as exc:
            QMessageBox.critical(self, "Cannot start", str(exc))
            return
        self._save_settings()
        self.log.clear()
        self.progress.setValue(0)
        self.transfer.hide()
        self._set_busy(True)
        worker = WorkflowThread(settings, self.app_root)
        self.worker = worker
        worker.logLine.connect(self.log.appendPlainText)
        worker.stageChanged.connect(self._set_stage)
        worker.transferProgress.connect(self._set_transfer)
        worker.legacyPromptRequested.connect(self._show_legacy_prompt)
        worker.wifiPromptRequested.connect(self._show_wifi_prompt)
        worker.firstBootPromptRequested.connect(self._show_first_boot_prompt)
        worker.succeeded.connect(self._workflow_succeeded)
        worker.failed.connect(self._workflow_failed)
        worker.finished.connect(lambda: self._set_busy(False))
        worker.start()

    def _test_ssh(self) -> None:
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
            QMessageBox.critical(self, "SSH test failed", str(exc))
            return
        QMessageBox.information(
            self,
            "SSH works",
            f"Connected successfully to root@{phone_ip}:{port}.",
        )

    def _install_untether_only(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        try:
            phone_ip = validate_ipv4(self.phone_wifi_ip.text())
        except Exception as exc:
            QMessageBox.critical(self, "Cannot install untether", str(exc))
            return
        self._save_settings()
        self._set_busy(True)
        worker = UntetherInstallThread(
            phone_ip,
            self.ssh_port.value(),
            self.root_password.text(),
        )
        self.worker = worker
        worker.logLine.connect(self.log.appendPlainText)
        worker.stageChanged.connect(self._set_stage)
        worker.transferProgress.connect(self._set_transfer)
        worker.succeeded.connect(
            lambda: QMessageBox.information(
                self,
                "Untether installed",
                "The CoolBooter untether was installed successfully.",
            )
        )
        worker.failed.connect(self._workflow_failed)
        worker.finished.connect(lambda: self._set_busy(False))
        worker.start()

    def _show_legacy_prompt(self, exit_code: int, attempt: int) -> None:
        if not isinstance(self.worker, WorkflowThread):
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Did the restore finish?")
        box.setText(
            f"Legacy iOS Kit closed (launch {attempt}, exit code {exit_code})."
        )
        box.setInformativeText(
            "Pick Restore Finished only if Legacy said the restore completed and the phone booted to the jailbroken iOS 8.4.1 setup screen.\n\n"
            "Pick Run Legacy Again if it installed dependencies, updated itself, closed early, or showed an error. Legacy iOS Kit will handle pwnDFU again natively on macOS."
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

    def _show_wifi_prompt(self, current_ip: str, current_port: int) -> None:
        if not isinstance(self.worker, WorkflowThread):
            return
        default = current_ip or self.phone_wifi_ip.text().strip()
        value, accepted = QInputDialog.getText(
            self,
            "Connect to the restored iPhone",
            "Connect the iPhone to the same Wi-Fi as this computer, then enter its IPv4 address:",
            QLineEdit.EchoMode.Normal,
            default,
        )
        if not accepted:
            self.worker.answer_wifi_prompt(None)
            return
        try:
            endpoint = (validate_ipv4(value), current_port)
        except Exception as exc:
            QMessageBox.critical(self, "Invalid address", str(exc))
            self.worker.answer_wifi_prompt(None)
            return
        self.phone_wifi_ip.setText(endpoint[0])
        self.worker.answer_wifi_prompt(endpoint)

    def _show_first_boot_prompt(self) -> None:
        if not isinstance(self.worker, WorkflowThread):
            return
        answer = QMessageBox.question(
            self,
            "First CoolBooter boot",
            "Wait for the phone to return to the jailbroken host OS with OpenSSH available, then continue to send the second boot command.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        self.worker.answer_first_boot_prompt(
            answer == QMessageBox.StandardButton.Ok
        )

    def _set_stage(self, value: int, text: str) -> None:
        self.progress.setValue(value)
        self.stage_label.setText(text)

    def _set_transfer(self, sent: int, total: int) -> None:
        self.transfer.show()
        self.transfer.setMaximum(max(total, 1))
        self.transfer.setValue(min(sent, max(total, 1)))

    def _set_busy(self, busy: bool) -> None:
        self.start_button.setEnabled(not busy)
        self.inspect_button.setEnabled(not busy)
        self.test_ssh_button.setEnabled(not busy)
        self.install_untether_only_button.setEnabled(not busy)

    def _workflow_succeeded(self) -> None:
        QMessageBox.information(self, "Finished", "The workflow finished successfully.")

    def _workflow_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Workflow failed", message)

    def _save_settings(self) -> None:
        values = {
            "secondary_path": self.secondary_path.text(),
            "stock_host_ipsw": self.stock_host_ipsw.text(),
            "version": self.version.text(),
            "datasize": self.datasize.text(),
            "skip_restore": self.skip_restore.isChecked(),
            "legacy_dir": self.legacy_dir.text(),
            "auto_download_legacy": self.auto_download_legacy.isChecked(),
            "phone_wifi_ip": self.phone_wifi_ip.text(),
            "ssh_port": self.ssh_port.value(),
            "root_password": self.root_password.text(),
            "install_untether": self.install_untether.isChecked(),
        }
        for key, value in values.items():
            self.settings_store.setValue(key, value)

    def _load_settings(self) -> None:
        self.secondary_path.setText(
            str(self.settings_store.value("secondary_path", ""))
        )
        self.stock_host_ipsw.setText(
            str(self.settings_store.value("stock_host_ipsw", ""))
        )
        self.version.setText(str(self.settings_store.value("version", "")))
        self.datasize.setText(str(self.settings_store.value("datasize", "")))
        self.skip_restore.setChecked(
            self.settings_store.value("skip_restore", False, type=bool)
        )
        self.legacy_dir.setText(str(self.settings_store.value("legacy_dir", "")))
        self.auto_download_legacy.setChecked(
            self.settings_store.value("auto_download_legacy", True, type=bool)
        )
        self.phone_wifi_ip.setText(
            str(self.settings_store.value("phone_wifi_ip", ""))
        )
        self.ssh_port.setValue(
            self.settings_store.value("ssh_port", 22, type=int)
        )
        self.root_password.setText(
            str(self.settings_store.value("root_password", "alpine"))
        )
        self.install_untether.setChecked(
            self.settings_store.value("install_untether", True, type=bool)
        )


def main() -> int:
    app = QApplication([])
    window = MainWindow()
    window.show()
    return app.exec()
