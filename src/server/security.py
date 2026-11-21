"""Explicit bootstrap policies; credentials and tokens never appear in errors."""
from __future__ import annotations

import ipaddress
import os
import secrets
import stat
from pathlib import Path
from typing import Mapping


class CredentialsError(RuntimeError):
    """Invalid or absent explicit administrator bootstrap credentials."""


def is_trusted_loopback(peer_ip: str | None, trust_loopback: bool) -> bool:
    if not trust_loopback or not isinstance(peer_ip, str):
        return False
    try:
        address = ipaddress.ip_address(peer_ip)
    except ValueError:
        return False
    mapped = getattr(address, 'ipv4_mapped', None)
    return address.is_loopback or bool(mapped and mapped.is_loopback)


def resolve_bind_host(cli_host: str | None) -> str:
    return cli_host if cli_host else '127.0.0.1'


def load_admin_credentials(
    env: Mapping[str, str], path: Path | str | None
) -> tuple[str, str]:
    """Environment pair, otherwise an owned regular exact-0600 two-line file.

    A partial environment pair fails closed instead of falling back. A symlink
    is refused, and fstat validates the opened object rather than an earlier
    pathname observation. O_NONBLOCK prevents a non-regular FIFO from hanging
    before the regular-file check.
    """
    keys = ('DCDTUNI_ADMIN_USER', 'DCDTUNI_ADMIN_PASSWORD')
    if any(key in env for key in keys):
        user, password = (env.get(key) for key in keys)
        if (
            not isinstance(user, str) or not user.strip()
            or not isinstance(password, str) or not password.strip()
        ):
            raise CredentialsError('Complete nonempty administrator environment credentials are required')
        if any(character in user or character in password for character in ('\n', '\r')):
            raise CredentialsError('Administrator environment credentials must be single-line')
        return user.strip(), password

    if path is None:
        raise CredentialsError(
            'Set administrator environment credentials or --admin-credentials with an owned 0600 file'
        )
    try:
        descriptor = os.open(Path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise CredentialsError('Administrator credential file is missing, inaccessible or a symlink') from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid()
        ):
            raise CredentialsError('Administrator credential file must be an owned regular exact-0600 file')
        with os.fdopen(descriptor, 'r', encoding='utf-8') as stream:
            descriptor = -1
            lines = stream.read().splitlines()
    except (OSError, UnicodeError) as exc:
        raise CredentialsError('Administrator credential file could not be read') from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(lines) != 2 or not lines[0].strip() or not lines[1].strip():
        raise CredentialsError('Administrator credential file needs exactly two nonempty lines')
    return lines[0].strip(), lines[1]


def new_worker_token() -> str:
    return secrets.token_urlsafe(32)
