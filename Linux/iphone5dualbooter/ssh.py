from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import socket
import time

import paramiko

from .network import SSHError, validate_ipv4


LogFn = Callable[[str], None]
ProgressFn = Callable[[int, int], None]


class IOSSSH:
    def __init__(self, host: str, port: int, password: str, log: LogFn):
        self.host = validate_ipv4(host)
        if not 1 <= int(port) <= 65535:
            raise SSHError("SSH port must be between 1 and 65535.")
        self.port = int(port)
        self.password = password
        self.log = log
        self.transport: paramiko.Transport | None = None

    def connect(self, timeout: float = 10.0) -> None:
        self.close()
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        transport = paramiko.Transport(sock)

                       
        security = transport.get_security_options()
        try:
            if "diffie-hellman-group1-sha1" not in security.kex:
                security.kex = tuple(security.kex) + (
                    "diffie-hellman-group1-sha1",
                )
        except Exception:
            pass
        try:
            if "ssh-rsa" not in security.key_types:
                security.key_types = tuple(security.key_types) + ("ssh-rsa",)
        except Exception:
            pass

        try:
            transport.start_client(timeout=timeout)
            transport.auth_password("root", self.password)
        except Exception:
            transport.close()
            raise

        if not transport.is_authenticated():
            transport.close()
            raise SSHError("SSH authentication failed for root.")

        self.transport = transport

    def close(self) -> None:
        if self.transport:
            self.transport.close()
        self.transport = None

    def _session(self) -> paramiko.Channel:
        if not self.transport or not self.transport.is_active():
            raise SSHError("SSH is not connected.")
        return self.transport.open_session(timeout=15)

    def run(
        self,
        command: str,
        check: bool = True,
        timeout: float | None = None,
    ) -> tuple[int, str]:
        self.log(f"iPhone# {command}")
        channel = self._session()
        channel.exec_command(command)
        output = bytearray()
        start = time.monotonic()

        while True:
            if channel.recv_ready():
                chunk = channel.recv(65536)
                output.extend(chunk)
                for line in chunk.decode(errors="replace").splitlines():
                    self.log(line)

            if channel.recv_stderr_ready():
                chunk = channel.recv_stderr(65536)
                output.extend(chunk)
                for line in chunk.decode(errors="replace").splitlines():
                    self.log(line)

            if channel.exit_status_ready():
                break

            if timeout is not None and time.monotonic() - start > timeout:
                channel.close()
                raise SSHError(
                    f"Remote command timed out after {timeout:.0f} seconds."
                )
            time.sleep(0.1)

        code = channel.recv_exit_status()
        text = output.decode(errors="replace")
        channel.close()

        if check and code != 0:
            raise SSHError(
                f"Remote command failed with exit code {code}: {command}"
            )
        return code, text

    def run_may_disconnect(
        self,
        command: str,
        success_markers: tuple[str, ...],
        timeout: float,
        accepted_exit_codes: tuple[int, ...] = (0,),
        accept_timeout: bool = False,
        heartbeat_seconds: float = 30.0,
    ) -> tuple[int | None, str]:
        self.log(f"iPhone# {command}")
        channel = self._session()
        channel.exec_command(command)
        output = bytearray()
        start = time.monotonic()
        last_heartbeat = start

        try:
            while True:
                if channel.recv_ready():
                    chunk = channel.recv(65536)
                    output.extend(chunk)
                    for line in chunk.decode(errors="replace").splitlines():
                        self.log(line)

                if channel.recv_stderr_ready():
                    chunk = channel.recv_stderr(65536)
                    output.extend(chunk)
                    for line in chunk.decode(errors="replace").splitlines():
                        self.log(line)

                if channel.exit_status_ready():
                    code = channel.recv_exit_status()
                    text = output.decode(errors="replace")
                    if code not in accepted_exit_codes:
                        raise SSHError(
                            f"Remote command failed with exit code {code}."
                        )
                    if code == -1:
                        self.log("CoolBooter returned -1, which means it kept running or rebooted the phone as expected.")
                    return code, text

                now = time.monotonic()
                if heartbeat_seconds > 0 and now - last_heartbeat >= heartbeat_seconds:
                    elapsed = int(now - start)
                    self.log(f"CoolBooter is still running... {elapsed // 60}m {elapsed % 60}s")
                    last_heartbeat = now

                if now - start > timeout:
                    text = output.decode(errors="replace")
                    if accept_timeout:
                        self.log("CoolBooter is still holding the SSH command open, so I am closing it and continuing.")
                        return None, text
                    raise SSHError("CoolBooterCLI timed out.")

                time.sleep(0.1)

        except (EOFError, OSError, paramiko.SSHException):
            text = output.decode(errors="replace")
            lowered = text.lower()
            if any(marker.lower() in lowered for marker in success_markers) or -1 in accepted_exit_codes:
                self.log("SSH disconnected because the phone rebooted, which is expected here.")
                return -1, text
            raise
        finally:
            channel.close()

    def scp_put(
        self,
        local_path: Path,
        remote_dir: str,
        progress: ProgressFn,
    ) -> None:
        local_path = Path(local_path)
        size = local_path.stat().st_size
        filename = local_path.name.replace("\n", "_")
        self.run(f"mkdir -p {shell_quote(remote_dir)}")

        channel = self._session()
        channel.exec_command(f"scp -t {shell_quote(remote_dir)}")
        _expect_scp_ack(channel)

        header = f"C0644 {size} {filename}\n".encode()
        channel.sendall(header)
        _expect_scp_ack(channel)

        sent = 0
        with local_path.open("rb") as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                channel.sendall(chunk)
                sent += len(chunk)
                progress(sent, size)

        channel.sendall(b"\x00")
        _expect_scp_ack(channel)
        channel.close()


def _expect_scp_ack(channel: paramiko.Channel) -> None:
    code = channel.recv(1)
    if code == b"\x00":
        return
    message = channel.recv(4096).decode(errors="replace")
    raise SSHError(f"SCP failed: {message or code!r}")


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def wait_for_ssh(
    host: str,
    port: int,
    password: str,
    log: LogFn,
    timeout_seconds: int,
) -> IOSSSH:
    address = validate_ipv4(host)
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    attempt = 0

    log(
        f"Waiting for Wi-Fi SSH at root@{address}:{port}. "
        "Keep the iPhone connected to the same Wi-Fi network as this computer."
    )

    while time.monotonic() < deadline:
        attempt += 1
        try:
            client = IOSSSH(address, port, password, log)
            client.connect(timeout=7)
            client.run("echo connected", check=True, timeout=10)
            log(f"Connected to the iPhone over Wi-Fi at {address}:{port}.")
            return client
        except Exception as exc:
            last_error = exc
            if attempt == 1 or attempt % 5 == 0:
                log(
                    f"SSH attempt {attempt} failed: {exc}. "
                    "Still waiting for the iPhone to join Wi-Fi..."
                )
            time.sleep(4)

    raise SSHError(
        "Timed out waiting for the iPhone's Wi-Fi SSH connection. "
        f"Last error: {last_error}"
    )
