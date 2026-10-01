"""Server-owned configuration. No RCON target is accepted from HTTP callers."""

from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
import os
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from dotenv import dotenv_values


_PRIVATE_V4 = tuple(
    ip_network(block) for block in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_PRIVATE_V6 = ip_network("fc00::/7")


def _valid_origin(value: str, *, allow_http: bool) -> tuple[str, str]:
    if not value or value != value.strip() or any(char.isspace() for char in value):
        raise ValueError("origin must be a single URL origin")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("origin must be a single URL origin") from exc
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or "@" in parsed.netloc
        or port == 0
    ):
        raise ValueError("origin must be a single URL origin")
    if parsed.scheme == "http" and not allow_http:
        raise ValueError("HTTP target requires explicit private HTTP permission")
    return parsed.scheme, parsed.hostname


def _is_private_http_host(host: str) -> bool:
    if host in ("localhost",) or host.endswith(".localhost"):
        return True
    try:
        address = ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    if address.version == 4:
        return any(address in block for block in _PRIVATE_V4)
    return address in _PRIVATE_V6


@dataclass(frozen=True)
class RconTarget:
    origin: str
    bearer_secret: str = field(repr=False)
    tls_ca_path: Path | None = None
    allow_private_http: bool = False
    allow_public_http: bool = False
    connect_timeout: float = 5.0
    read_timeout: float = 8.0
    max_response_bytes: int = 1_048_576
    read_retries: int = 1

    def __post_init__(self) -> None:
        scheme, host = _valid_origin(
            self.origin, allow_http=self.allow_private_http or self.allow_public_http
        )
        if scheme == "http":
            if _is_private_http_host(host) and not self.allow_private_http:
                raise ValueError("private HTTP target requires permission")
            if not _is_private_http_host(host) and not self.allow_public_http:
                raise ValueError("public HTTP target requires permission")
        if (
            not self.bearer_secret
            or self.bearer_secret != self.bearer_secret.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in self.bearer_secret)
        ):
            raise ValueError("RCON Bearer secret is missing or malformed")
        if self.tls_ca_path is not None and not self.tls_ca_path.is_file():
            raise ValueError("RCON CA file does not exist")
        if self.connect_timeout <= 0 or self.read_timeout <= 0:
            raise ValueError("RCON timeouts must be positive")
        if not 0 < self.max_response_bytes <= 16 * 1_048_576 or not 0 <= self.read_retries <= 2:
            raise ValueError("RCON limits must be bounded")


@dataclass(frozen=True)
class PanelSettings:
    db_path: Path
    public_origin: str
    session_secure: bool
    rcon_target: RconTarget | None = None
    config_key: str | None = field(default=None, repr=False)
    allow_public_http_rcon: bool = False
    history_enabled: bool = False
    bot_read_token: str | None = field(default=None, repr=False)
    bot_admin_token: str | None = field(default=None, repr=False)
    feed_token: str | None = field(default=None, repr=False)
    feed_origin: str | None = None
    bot_public_origin: str | None = None

    def __post_init__(self) -> None:
        for token in (self.bot_read_token, self.bot_admin_token, self.feed_token):
            if token is not None and (len(token) < 32 or not token.isascii() or any(c.isspace() or ord(c) < 33 for c in token)):
                raise ValueError("bot tokens must contain at least 32 printable ASCII characters")
        if self.bot_read_token and self.bot_read_token == self.bot_admin_token:
            raise ValueError("bot read and management tokens must be different")
        if self.feed_token and self.feed_token in (self.bot_read_token, self.bot_admin_token):
            raise ValueError("feed and bot credentials must be different")
        if self.feed_token and not self.feed_origin:
            raise ValueError("PANEL_FEED_ORIGIN is required with PANEL_FEED_TOKEN")
        scheme, _host = _valid_origin(self.public_origin, allow_http=True)
        if self.bot_public_origin:
            _, bot_host = _valid_origin(self.bot_public_origin, allow_http=True)
            if bot_host.lower() != _host.lower():
                raise ValueError("bot gateway must use the panel hostname")
        if scheme == "https" and not self.session_secure:
            raise ValueError("HTTPS panel sessions require Secure cookies")
        if scheme == "http" and self.session_secure:
            raise ValueError("HTTP panel sessions cannot use Secure cookies; use HTTPS or set PANEL_SESSION_SECURE=false")


def _bool_env(value: str, name: str) -> bool:
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    raise ValueError(f"{name} must be true or false")


def load_settings(environ: Mapping[str, str] | None = None) -> PanelSettings:
    """Load deployer-controlled settings; invalid RCON details fail closed."""
    if environ is None:
        values = {key: value for key, value in dotenv_values(Path(__file__).parent.parent / ".env").items() if value is not None}
        values.update(os.environ)
    else:
        values = environ
    origin = values.get("WARDOGS_RCON_ORIGIN", "")
    bearer = values.get("WARDOGS_RCON_BEARER", "")
    allow_public_http = _bool_env(
        values.get("PANEL_ALLOW_PUBLIC_HTTP_RCON", "false"),
        "PANEL_ALLOW_PUBLIC_HTTP_RCON",
    )
    rcon_target = None
    if origin and bearer:
        try:
            ca_value = values.get("WARDOGS_RCON_CA_PATH", "")
            rcon_target = RconTarget(
                origin=origin,
                bearer_secret=bearer,
                tls_ca_path=Path(ca_value) if ca_value else None,
                allow_private_http=_bool_env(
                    values.get("WARDOGS_ALLOW_PRIVATE_HTTP", "false"),
                    "WARDOGS_ALLOW_PRIVATE_HTTP",
                ),
                allow_public_http=allow_public_http and _bool_env(
                    values.get("WARDOGS_ALLOW_PUBLIC_HTTP", "false"),
                    "WARDOGS_ALLOW_PUBLIC_HTTP",
                ),
                connect_timeout=float(values.get("WARDOGS_CONNECT_TIMEOUT", "5")),
                read_timeout=float(values.get("WARDOGS_READ_TIMEOUT", "8")),
                max_response_bytes=int(values.get("WARDOGS_MAX_RESPONSE_BYTES", "1048576")),
                read_retries=int(values.get("WARDOGS_READ_RETRIES", "1")),
            )
        except (TypeError, ValueError):
            # An incomplete or unsafe target must never trigger a network call.
            rcon_target = None
    public_origin = values.get("PANEL_PUBLIC_ORIGIN", "http://127.0.0.1:8000")
    return PanelSettings(
        db_path=Path(values.get("PANEL_DB_PATH", "./data/panel.sqlite3")),
        public_origin=public_origin,
        session_secure=_bool_env(values.get("PANEL_SESSION_SECURE", "true" if public_origin.startswith("https://") else "false"), "PANEL_SESSION_SECURE"),
        rcon_target=rcon_target,
        config_key=values.get("PANEL_CONFIG_KEY") or None,
        allow_public_http_rcon=allow_public_http,
        history_enabled=_bool_env(values.get("PANEL_HISTORY_ENABLED", "true"), "PANEL_HISTORY_ENABLED"),
        bot_read_token=values.get("PANEL_BOT_READ_TOKEN") or None,
        bot_admin_token=values.get("PANEL_BOT_ADMIN_TOKEN") or None,
        feed_token=values.get("PANEL_FEED_TOKEN") or None,
        feed_origin=values.get("PANEL_FEED_ORIGIN") or None,
        bot_public_origin=values.get("PANEL_BOT_PUBLIC_ORIGIN") or None,
    )
