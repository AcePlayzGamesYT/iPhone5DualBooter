from __future__ import annotations

import ipaddress


class SSHError(RuntimeError):
    pass


def validate_ipv4(value: str) -> str:
    text = value.strip()
    if not text:
        raise SSHError("Enter the iPhone's Wi-Fi IPv4 address.")

    try:
        address = ipaddress.ip_address(text)
    except ValueError as exc:
        raise SSHError(
            "Enter a valid IPv4 address, for example 192.168.1.123."
        ) from exc

    if address.version != 4:
        raise SSHError("Enter an IPv4 address rather than an IPv6 address.")
    if address.is_unspecified or address.is_multicast:
        raise SSHError("That IPv4 address cannot be used for SSH.")
    if address.is_loopback:
        raise SSHError("127.0.0.1 is this computer, not the iPhone.")

    return str(address)
