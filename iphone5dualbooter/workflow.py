from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .ipsw import (
    inspect_ipsw,
    validate_host_841_info,
    validate_host_secondary_models,
    validate_secondary_info,
    validate_secondary_version,
)
from .models import WorkflowSettings
from .repo import (
    download_repository_package,
    resolve_package_plan,
    RepoError,
)
from .restore import run_restore_backend
from .wsl_legacy import ExternalPwnPromptFn, LegacyPromptFn
from .network import validate_ipv4
from .ssh import IOSSSH, shell_quote, wait_for_ssh


LogFn = Callable[[str], None]
StageFn = Callable[[int, str], None]
ProgressFn = Callable[[int, int], None]
SSHEndpointPromptFn = Callable[[str, int], tuple[str, int] | None]
FirstBootPromptFn = Callable[[], bool]

CLI_PACKAGE = "com.coolbooter.coolbootercli"
UNTETHER_PACKAGE = "com.coolbooter.cbuntether"


def _command_exists(ssh: IOSSSH, command: str) -> bool:
    code, _ = ssh.run(
        f"export PATH=/usr/bin:/bin:/usr/sbin:/sbin; command -v {shell_quote(command)} >/dev/null 2>&1",
        check=False,
        timeout=30,
    )
    return code == 0


def _package_is_configured(ssh: IOSSSH, package_id: str) -> bool:
    code, output = ssh.run(
        "export PATH=/usr/bin:/bin:/usr/sbin:/sbin; "
        f"dpkg -s {shell_quote(package_id)} 2>/dev/null",
        check=False,
        timeout=30,
    )
    return code == 0 and "install ok installed" in output


def _install_with_apt(ssh: IOSSSH, package_id: str) -> None:
    command = (
        "export PATH=/usr/bin:/bin:/usr/sbin:/sbin; "
        "mkdir -p /etc/apt/sources.list.d; "
        "printf '%s\n' 'deb https://coolbooter.com/ ./' "
        "> /etc/apt/sources.list.d/coolbooter.list; "
        "apt-get update; "
        f"DEBIAN_FRONTEND=noninteractive apt-get -y --force-yes install {shell_quote(package_id)}"
    )
    ssh.run(command, check=True, timeout=900)


def _install_direct_deb_bundle(
    ssh: IOSSSH,
    package_id: str,
    log: LogFn,
    progress: ProgressFn,
) -> None:
    if not _command_exists(ssh, "dpkg"):
        raise RuntimeError("The iPhone does not have dpkg, so the packages cannot be installed.")

    log("APT is not on this jailbreak, so I am installing the .deb files directly instead.")

    # A damaged/partial install can leave dpkg saying CoolBooterCLI is installed
    # even though the actual coolbootercli executable is missing. Remove only
    # that package first so the normal direct-install pass puts all of its files
    # back. Dependencies are left alone.
    if (
        package_id == CLI_PACKAGE
        and _package_is_configured(ssh, CLI_PACKAGE)
        and not _command_exists(ssh, "coolbootercli")
    ):
        log("CoolBooterCLI says it is installed, but the command is missing. Reinstalling it now.")
        ssh.run(
            "export PATH=/usr/bin:/bin:/usr/sbin:/sbin; "
            f"dpkg --remove {shell_quote(CLI_PACKAGE)} || "
            f"dpkg -r {shell_quote(CLI_PACKAGE)} || true; "
            "rm -f /usr/bin/coolbootercli /usr/local/bin/coolbootercli",
            check=False,
            timeout=300,
        )

    plan = resolve_package_plan(package_id)
    package_names = [package.package_id for package in plan]
    log("Direct install order: " + " -> ".join(package_names))

    remote_dir = "/tmp/iphone5dualbooter-debs"
    ssh.run(f"rm -rf {shell_quote(remote_dir)}; mkdir -p {shell_quote(remote_dir)}", timeout=30)

    remote_files: list[str] = []
    for package in plan:
        if _package_is_configured(ssh, package.package_id):
            log(f"{package.package_id} is already installed, skipping it.")
            continue
        log(f"Downloading {package.package_id} {package.version}...")
        local_deb = download_repository_package(package)
        ssh.scp_put(local_deb, remote_dir, progress)
        remote_files.append(f"{remote_dir}/{local_deb.name}")

    if remote_files:
        quoted_files = " ".join(shell_quote(path) for path in remote_files)
        # First pass unpacks every package. The second pass and configure step deal
        # with packages that could not configure until their dependencies existed.
        ssh.run(
            "export PATH=/usr/bin:/bin:/usr/sbin:/sbin; "
            f"dpkg -i {quoted_files} || true; "
            f"dpkg -i {quoted_files} || true; "
            "dpkg --configure -a || true",
            check=False,
            timeout=1200,
        )
    else:
        ssh.run(
            "export PATH=/usr/bin:/bin:/usr/sbin:/sbin; dpkg --configure -a || true",
            check=False,
            timeout=300,
        )

    missing = [name for name in package_names if not _package_is_configured(ssh, name)]
    if missing:
        raise RuntimeError(
            "These packages are still not configured after the direct install: "
            + ", ".join(missing)
        )

    if package_id == CLI_PACKAGE and not _command_exists(ssh, "coolbootercli"):
        # Do one forced repair attempt in case dpkg reported success but failed
        # to restore the executable during the normal package-plan pass.
        log("CoolBooterCLI installed, but the command is still missing. Deleting and reinstalling it once more.")
        cli_package = next(
            package for package in resolve_package_plan(CLI_PACKAGE)
            if package.package_id == CLI_PACKAGE
        )
        local_deb = download_repository_package(cli_package)
        ssh.scp_put(local_deb, remote_dir, progress)
        remote_cli_deb = f"{remote_dir}/{local_deb.name}"
        ssh.run(
            "export PATH=/usr/bin:/bin:/usr/sbin:/sbin; "
            f"dpkg --remove {shell_quote(CLI_PACKAGE)} || "
            f"dpkg -r {shell_quote(CLI_PACKAGE)} || true; "
            "rm -f /usr/bin/coolbootercli /usr/local/bin/coolbootercli; "
            f"dpkg -i {shell_quote(remote_cli_deb)} || true; "
            "dpkg --configure -a || true",
            check=False,
            timeout=600,
        )

        if not _package_is_configured(ssh, CLI_PACKAGE):
            raise RuntimeError("CoolBooterCLI did not finish reinstalling.")
        if not _command_exists(ssh, "coolbootercli"):
            raise RuntimeError("CoolBooterCLI was reinstalled, but the coolbootercli command is still missing.")
        log("CoolBooterCLI was reinstalled and the command is back.")

    log(f"{package_id} installed successfully without APT.")


def install_package(
    ssh: IOSSSH,
    package_id: str,
    log: LogFn,
    progress: ProgressFn,
) -> None:
    try:
        if _command_exists(ssh, "apt-get"):
            log("APT is available, installing from the CoolBooter repo normally...")
            try:
                _install_with_apt(ssh, package_id)
            except Exception as apt_error:
                log(f"The normal APT install failed: {apt_error}")
                log("Trying the direct .deb installer instead...")
                _install_direct_deb_bundle(ssh, package_id, log, progress)
        else:
            _install_direct_deb_bundle(ssh, package_id, log, progress)
    except RepoError as exc:
        raise RuntimeError(
            f"Could not download the packages needed for {package_id}: {exc}"
        ) from exc

    if not _package_is_configured(ssh, package_id):
        raise RuntimeError(f"{package_id} did not finish configuring.")


def validate_firmware_settings(
    settings: WorkflowSettings,
    log: LogFn | None = None,
) -> None:
    version = validate_secondary_version(settings.secondary_version)
    secondary = inspect_ipsw(settings.secondary_ipsw)
    validate_secondary_info(secondary, version)

    if not settings.skip_restore:
        if not settings.stock_host_ipsw:
            raise ValueError("Select the stock iOS 8.4.1 Restore IPSW.")
        stock = inspect_ipsw(settings.stock_host_ipsw)
        validate_host_841_info(stock)
        validate_host_secondary_models(stock, secondary)
        if log:
            log(
                f"Stock host: iOS {stock.product_version} ({stock.build_version}), "
                f"{', '.join(sorted(stock.product_types))}"
            )

    if log:
        log(
            f"Secondary: iOS {secondary.product_version} ({secondary.build_version}), "
            f"{', '.join(sorted(secondary.product_types))}"
        )


def run_workflow(
    settings: WorkflowSettings,
    app_root: Path,
    log: LogFn,
    stage: StageFn,
    progress: ProgressFn,
    legacy_prompt: LegacyPromptFn | None = None,
    ssh_endpoint_prompt: SSHEndpointPromptFn | None = None,
    external_pwn_prompt: ExternalPwnPromptFn | None = None,
    first_boot_prompt: FirstBootPromptFn | None = None,
) -> None:
    stage(3, "Validating firmware")
    version = validate_secondary_version(settings.secondary_version)
    validate_firmware_settings(settings, log)

    stage(10, "External pwnDFU checkpoint + Legacy iOS Kit restore")
    run_restore_backend(
        settings,
        app_root,
        log,
        legacy_prompt,
        external_pwn_prompt,
    )

    if ssh_endpoint_prompt is not None:
        endpoint = ssh_endpoint_prompt(settings.phone_wifi_ip, settings.ssh_port)
        if endpoint is None:
            raise RuntimeError("Wi-Fi SSH setup was cancelled.")
        phone_ip, ssh_port = endpoint
    else:
        phone_ip, ssh_port = settings.phone_wifi_ip, settings.ssh_port

    phone_ip = validate_ipv4(phone_ip)

    stage(
        35,
        f"Waiting for Wi-Fi OpenSSH at {phone_ip}:{ssh_port}",
    )
    ssh = wait_for_ssh(
        phone_ip,
        ssh_port,
        settings.root_password,
        log,
        timeout_seconds=20 * 60,
    )

    try:
        stage(45, "Installing CoolBooterCLI")
        install_package(ssh, CLI_PACKAGE, log, progress)

        stage(55, "Copying secondary IPSW")
        remote_ipsw = "/var/cbooter/" + settings.secondary_ipsw.name

        # The small jailbreak bootstrap does not always include wc or stat.
        # Reuse the selected IPSW when a file with the same name already exists.
        exists_code, _ = ssh.run(
            f"test -f {shell_quote(remote_ipsw)}",
            check=False,
            timeout=30,
        )

        if exists_code == 0:
            log("That IPSW is already in /var/cbooter, so I am using it instead of copying it again.")
        else:
            log("The IPSW is not already on the iPhone, copying it now.")
            log(
                f"Copying {settings.secondary_ipsw.name} to /var/cbooter. "
                "Large IPSWs can take several minutes."
            )
            ssh.scp_put(settings.secondary_ipsw, "/var/cbooter", progress)

            verify_code, _ = ssh.run(
                f"test -f {shell_quote(remote_ipsw)}",
                check=False,
                timeout=30,
            )
            if verify_code != 0:
                raise RuntimeError("The IPSW copy did not finish because the file is not on the iPhone.")
            log("IPSW copy finished and the file is on the iPhone.")

        stage(68, "Installing secondary iOS")
        # validate_secondary_version() only allows digits, dots, and an optional
        # beta suffix, so the version can be passed directly without quotes.
        command = f"coolbootercli {version}"
        if settings.datasize_gb is not None:
            command += f" --datasize {settings.datasize_gb}GB"

        success_markers = (
            "installation succeeded",
            "installation complete",
            "installation finished",
            "finishing up installation",
            "rebooting",
        )
        log(f"Running: {command}")
        install_code, output = ssh.run_may_disconnect(
            command,
            success_markers=success_markers,
            timeout=3 * 60 * 60,
            accepted_exit_codes=(0, -1),
            heartbeat_seconds=30,
        )

        lowered_output = output.lower()
        marker_found = any(marker in lowered_output for marker in success_markers)
        if install_code == -1:
            log("CoolBooter returned -1, so the secondary iOS installation was started successfully.")
        elif marker_found:
            log("CoolBooterCLI confirmed that the secondary iOS installation finished.")
        elif install_code == 0:
            log("CoolBooterCLI exited with code 0. Continuing to the boot step.")

    finally:
        ssh.close()

    if settings.install_untether:
        stage(80, "Waiting for the phone to install the untether")
        ssh = wait_for_ssh(
            phone_ip,
            ssh_port,
            settings.root_password,
            log,
            timeout_seconds=20 * 60,
        )
        try:
            stage(86, "Installing CoolBooter Untetherer")
            install_package(ssh, UNTETHER_PACKAGE, log, progress)
            log("CoolBooter Untetherer is installed.")

            stage(92, "Booting the secondary OS")
            log("Running: coolbootercli -b")
            ssh.run_may_disconnect(
                "coolbootercli -b",
                success_markers=("boot", "reboot"),
                timeout=10 * 60,
                accepted_exit_codes=(0, -1),
                accept_timeout=True,
                heartbeat_seconds=30,
            )
        finally:
            ssh.close()

        log("The boot command was sent.")
        log("To boot the secondary OS again later, just reboot the iPhone because the untether is installed.")
    else:
        stage(80, "Starting the first CoolBooter boot")
        ssh = wait_for_ssh(
            phone_ip,
            ssh_port,
            settings.root_password,
            log,
            timeout_seconds=20 * 60,
        )
        try:
            log("Running the first boot command: coolbootercli -b")
            ssh.run_may_disconnect(
                "coolbootercli -b",
                success_markers=("boot", "reboot"),
                timeout=10 * 60,
                accepted_exit_codes=(0, -1),
                accept_timeout=True,
                heartbeat_seconds=30,
            )
        finally:
            ssh.close()

        if first_boot_prompt is not None:
            confirmed = first_boot_prompt()
            if not confirmed:
                raise RuntimeError("The second CoolBooter boot was cancelled.")

        stage(92, "Booting the secondary OS")
        ssh = wait_for_ssh(
            phone_ip,
            ssh_port,
            settings.root_password,
            log,
            timeout_seconds=20 * 60,
        )
        try:
            log("Running the second boot command: coolbootercli -b")
            ssh.run_may_disconnect(
                "coolbootercli -b",
                success_markers=("boot", "reboot"),
                timeout=10 * 60,
                accepted_exit_codes=(0, -1),
                accept_timeout=True,
                heartbeat_seconds=30,
            )
        finally:
            ssh.close()

        log("The second boot command was sent.")
        log("To boot the secondary OS again later, SSH into the iPhone and run coolbootercli -b, or run coolbootercli -b from a terminal app on the phone.")

    stage(100, "Done")
    log("Done.")
