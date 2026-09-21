"""Persistent, non-secret settings for the local MCP tray manager."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


SETTINGS_DIR = (
    Path(os.environ.get("LOCALAPPDATA", Path.home()))
    / "OpenSCAD-MCP-Manager"
)
SETTINGS_FILE = SETTINGS_DIR / "settings.json"


@dataclass
class ServerSettings:
    enabled: bool = True
    autostart: bool = False
    channel: str = ""
    color: str = "#858b93"
    # Individual-tunnel mode: one tunnel-client process per server.
    # Empty means "not configured yet" (except obsidian migrates
    # from the old global tunnel_id for backward compatibility).
    tunnel_id: str = ""
    health_addr: str = ""
    # Filesystem MCP only: folders the AI may access.
    # Empty = use built-in default (migrated on load).
    allowed_dirs: list[str] = field(default_factory=list)
    # General servers: display name, transport, launch command, cwd,
    # external URL. Empty = use built-in legacy defaults for the 4
    # known servers; custom servers must fill these in UI.
    display_name: str = ""
    transport: str = ""  # "stdio" | "external" | "" (=auto/legacy)
    command: str = ""  # full command line, e.g. "python server.py --port 1"
    working_dir: str = ""
    server_url: str = ""  # for transport == "external"
    # Optional per-server env override name, e.g. GODOT_MCP_COMMAND.
    # Empty = none.
    command_env: str = ""


@dataclass
class ManagerSettings:
    tray_autostart: bool = False

    # One tunnel configuration for the entire manager.
    tunnel_enabled: bool = True
    tunnel_id: str = ""

    # Name of the environment variable containing the runtime API key.
    # The key itself is NEVER stored here.
    api_key_env: str = "CONTROL_PLANE_API_KEY"

    health_addr: str = "127.0.0.1:8080"

    # Pinned npm package for filesystem MCP so first-run isn't a
    # surprise download. Power users can override via
    # FILESYSTEM_MCP_COMMAND env.
    filesystem_package: str = (
        "@modelcontextprotocol/server-filesystem@2026.8.31"
    )

    # Self-healing: auto-restart crashed tunnels with backoff.
    auto_heal: bool = True
    max_heal_retries: int = 5

    servers: dict[str, ServerSettings] = field(default_factory=dict)


DEFAULT_SERVERS: dict[str, ServerSettings] = {
    # Each server gets its own health port so individual
    # tunnel-client processes never fight over 8080.
    "obsidian": ServerSettings(
        enabled=True,
        autostart=True,
        channel="main",
        color="#8e44ad",
        tunnel_id="",
        health_addr="127.0.0.1:8080",
        display_name="Obsidian MCP",
        transport="external",
    ),
    "openscad": ServerSettings(
        enabled=True,
        autostart=True,
        channel="openscad",
        color="#2eae5b",
        tunnel_id="",
        health_addr="127.0.0.1:8081",
        display_name="OpenSCAD MCP",
        transport="stdio",
        command_env="OPENSCAD_MCP_COMMAND",
    ),
    "godot": ServerSettings(
        enabled=False,
        autostart=False,
        channel="godot",
        color="#36a2eb",
        tunnel_id="",
        health_addr="127.0.0.1:8082",
        display_name="Godot MCP",
        transport="stdio",
        command_env="GODOT_MCP_COMMAND",
    ),
    "filesystem": ServerSettings(
        enabled=False,
        autostart=False,
        channel="filesystem",
        color="#f39c12",
        tunnel_id="",
        health_addr="127.0.0.1:8083",
        allowed_dirs=[],
        display_name="Filesystem MCP",
        transport="stdio",
        command_env="FILESYSTEM_MCP_COMMAND",
    ),
}


DEFAULTS = ManagerSettings(
    servers=DEFAULT_SERVERS
)


def _clean_dir_list(raw: Any, default: list[str]) -> list[str]:
    if not isinstance(raw, list):
        return list(default)
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        val = item.strip().strip('"').strip("'")
        if not val or val.lower() in seen:
            continue
        seen.add(val.lower())
        cleaned.append(val)
    return cleaned


def _slug_id(name: str) -> str:
    import re as _re

    slug = _re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "server"


def _server_from_dict(
    raw: dict[str, Any],
    default: ServerSettings,
) -> ServerSettings:
    transport = str(
        raw.get("transport", default.transport)
    ).strip().lower()
    if transport not in ("stdio", "external", ""):
        transport = default.transport
    return ServerSettings(
        enabled=bool(raw.get("enabled", default.enabled)),
        autostart=bool(raw.get("autostart", default.autostart)),
        channel=str(raw.get("channel", default.channel)),
        color=str(raw.get("color", default.color)),
        tunnel_id=str(raw.get("tunnel_id", default.tunnel_id)),
        health_addr=str(
            raw.get("health_addr", default.health_addr)
        ),
        allowed_dirs=_clean_dir_list(
            raw.get("allowed_dirs", default.allowed_dirs),
            default.allowed_dirs,
        ),
        display_name=str(
            raw.get("display_name", default.display_name)
        ),
        transport=transport,
        command=str(raw.get("command", default.command)),
        working_dir=str(
            raw.get("working_dir", default.working_dir)
        ),
        server_url=str(raw.get("server_url", default.server_url)),
        command_env=str(
            raw.get("command_env", default.command_env)
        ),
    )


def _merge(raw: dict[str, Any]) -> ManagerSettings:
    try:
        _heal = int(
            raw.get("max_heal_retries", DEFAULTS.max_heal_retries)
        )
    except (TypeError, ValueError):
        _heal = DEFAULTS.max_heal_retries
    result = ManagerSettings(
        tray_autostart=bool(
            raw.get("tray_autostart", DEFAULTS.tray_autostart)
        ),
        tunnel_enabled=bool(
            raw.get("tunnel_enabled", DEFAULTS.tunnel_enabled)
        ),
        tunnel_id=str(
            raw.get("tunnel_id", DEFAULTS.tunnel_id)
        ),
        api_key_env=str(
            raw.get("api_key_env", DEFAULTS.api_key_env)
        ),
        health_addr=str(
            raw.get("health_addr", DEFAULTS.health_addr)
        ),
        filesystem_package=str(
            raw.get(
                "filesystem_package",
                DEFAULTS.filesystem_package,
            )
        ),
        auto_heal=bool(raw.get("auto_heal", DEFAULTS.auto_heal)),
        max_heal_retries=max(0, min(20, _heal)),
        servers={},
    )

    raw_servers = raw.get("servers", {})
    if not isinstance(raw_servers, dict):
        raw_servers = {}

    # Preserve known defaults.
    for server_id, default in DEFAULTS.servers.items():
        item = raw_servers.get(server_id, {})
        if not isinstance(item, dict):
            item = {}

        result.servers[server_id] = _server_from_dict(
            item,
            default,
        )

    # Preserve future/custom servers already present in settings.
    # General: any server id is allowed, with full command/transport.
    import random as _random

    for server_id, item in raw_servers.items():
        if server_id in result.servers:
            continue

        if not isinstance(item, dict):
            continue

        transport = str(item.get("transport", "stdio")).strip().lower()
        if transport not in ("stdio", "external"):
            transport = "stdio"
        result.servers[server_id] = ServerSettings(
            enabled=bool(item.get("enabled", False)),
            autostart=bool(item.get("autostart", False)),
            channel=str(item.get("channel", server_id)),
            color=str(item.get("color", "#858b93")),
            tunnel_id=str(item.get("tunnel_id", "")),
            health_addr=str(item.get("health_addr", "")),
            allowed_dirs=_clean_dir_list(
                item.get("allowed_dirs", []), []
            ),
            display_name=str(
                item.get("display_name", server_id)
            ),
            transport=transport,
            command=str(item.get("command", "")),
            working_dir=str(item.get("working_dir", "")),
            server_url=str(item.get("server_url", "")),
            command_env=str(item.get("command_env", "")),
        )

    # Auto-assign distinct health ports to any server missing one.
    used_ports: set[int] = set()
    for srv in result.servers.values():
        try:
            port = int(srv.health_addr.rsplit(":", 1)[1])
            used_ports.add(port)
        except (ValueError, IndexError):
            pass
    _next_port = 8080
    while _next_port in used_ports:
        _next_port += 1
    for sid, srv in result.servers.items():
        if not srv.health_addr.strip() or srv.health_addr.strip() == ":":
            # Known servers fall back to their distinct default port.
            default = DEFAULT_SERVERS.get(sid)
            if default is not None and default.health_addr:
                try:
                    dport = int(
                        default.health_addr.rsplit(":", 1)[1]
                    )
                    if dport not in used_ports:
                        srv.health_addr = default.health_addr
                        used_ports.add(dport)
                        _next_port = max(_next_port, dport + 1)
                    else:
                        raise ValueError
                except ValueError:
                    while _next_port in used_ports:
                        _next_port += 1
                    srv.health_addr = f"127.0.0.1:{_next_port}"
                    used_ports.add(_next_port)
                    _next_port += 1
            else:
                while _next_port in used_ports:
                    _next_port += 1
                srv.health_addr = f"127.0.0.1:{_next_port}"
                used_ports.add(_next_port)
                _next_port += 1
        if not srv.display_name.strip():
            default = DEFAULT_SERVERS.get(sid)
            if default is not None and default.display_name.strip():
                srv.display_name = default.display_name
            else:
                srv.display_name = srv.channel or sid or "Server"
        if not srv.transport.strip():
            default = DEFAULT_SERVERS.get(sid)
            if default is not None and default.transport.strip():
                srv.transport = default.transport
            else:
                srv.transport = (
                    "external" if srv.server_url.strip() else "stdio"
                )
        if not srv.channel.strip():
            default = DEFAULT_SERVERS.get(sid)
            if default is not None and default.channel.strip():
                srv.channel = default.channel
            else:
                srv.channel = _slug_id(srv.display_name)

    # Auto-assign a color for custom servers without one.
    _palette = [
        "#8e44ad", "#2eae5b", "#36a2eb", "#f39c12",
        "#e91e63", "#00bcd4", "#ff5722", "#607d8b",
    ]
    for i, (sid, srv) in enumerate(result.servers.items()):
        if sid not in DEFAULT_SERVERS and (
            not srv.color.strip() or srv.color.strip() == "#858b93"
        ):
            srv.color = _palette[i % len(_palette)]

    return result


def make_server_id(display_name: str, existing: set[str]) -> str:
    base = _slug_id(display_name)
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def next_free_health_addr(
    settings: ManagerSettings, start: int = 8080
) -> str:
    used: set[int] = set()
    for srv in settings.servers.values():
        try:
            used.add(int(srv.health_addr.rsplit(":", 1)[1]))
        except (ValueError, IndexError):
            pass
    port = start
    while port in used:
        port += 1
        if port > 8199:
            break
    return f"127.0.0.1:{port}"


def default_filesystem_dirs() -> list[str]:
    return [
        str(Path.home() / "Documents" / "GitHub"),
    ]


def effective_filesystem_roots(
    settings: ManagerSettings,
) -> list[str]:
    """Folders the filesystem MCP is allowed to expose."""
    server = settings.servers.get("filesystem")
    if server is not None and server.allowed_dirs:
        return list(server.allowed_dirs)
    return default_filesystem_dirs()


def effective_tunnel_id(
    settings: ManagerSettings,
    server_id: str,
) -> str:
    """Return the tunnel ID this server's own process should use.

    Individual-tunnel mode requires DISTINCT tunnel IDs per server.
    Sharing one ID across processes makes pollers steal each other's
    work, so we never auto-copy the global ID to non-obsidian servers.
    """
    server = settings.servers.get(server_id)
    if server is not None and server.tunnel_id.strip():
        return server.tunnel_id.strip()
    # Backward compat: obsidian keeps using the old global ID.
    if server_id == "obsidian":
        return settings.tunnel_id.strip()
    return ""


def effective_health_addr(
    settings: ManagerSettings,
    server_id: str,
) -> str:
    server = settings.servers.get(server_id)
    if server is not None and server.health_addr.strip():
        return server.health_addr.strip()
    default = DEFAULT_SERVERS.get(server_id)
    if default is not None and default.health_addr:
        return default.health_addr
    # Fallback to global only if nothing else is set.
    if settings.health_addr.strip():
        return settings.health_addr.strip()
    return "127.0.0.1:8080"


def load_settings() -> ManagerSettings:
    try:
        raw = json.loads(
            SETTINGS_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(raw, dict):
            raw = {}

        settings = _merge(raw)

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
        TypeError,
        ValueError,
    ):
        settings = _merge({})

    # Migrate early prototype colors.
    legacy_colours = {
        "openscad": "#e67e22",
        "filesystem": "#9b59b6",
    }

    for server_id, old_colour in legacy_colours.items():
        server = settings.servers.get(server_id)

        if (
            server is not None
            and server.color.lower() == old_colour
        ):
            default = DEFAULTS.servers.get(server_id)

            if default is not None:
                server.color = default.color

    # Keep the setting clean.
    if not settings.api_key_env.strip():
        settings.api_key_env = "CONTROL_PLANE_API_KEY"

    # Migration to individual-tunnel mode:
    # - obsidian inherits the old global tunnel_id / health_addr
    #   so the currently working tunnel keeps working.
    # - other servers keep empty tunnel_id so the UI can prompt
    #   for DISTINCT new tunnel IDs instead of silently sharing
    #   one ID (which would cause poll stealing).
    # - empty per-server health ports get distinct defaults.
    obsidian = settings.servers.get("obsidian")
    if obsidian is not None:
        if not obsidian.tunnel_id.strip():
            obsidian.tunnel_id = settings.tunnel_id.strip()
        if not obsidian.health_addr.strip():
            obsidian.health_addr = settings.health_addr.strip() or (
                "127.0.0.1:8080"
            )

    for server_id, server in settings.servers.items():
        if server_id == "obsidian":
            continue
        if not server.health_addr.strip():
            default = DEFAULT_SERVERS.get(server_id)
            if default is not None and default.health_addr:
                server.health_addr = default.health_addr
            else:
                server.health_addr = (
                    settings.health_addr.strip()
                    or "127.0.0.1:8080"
                )

    # Filesystem defaults to GitHub folder on first run so the
    # existing behavior keeps working until the user picks folders.
    fs = settings.servers.get("filesystem")
    if fs is not None and not fs.allowed_dirs:
        fs.allowed_dirs = default_filesystem_dirs()

    # Self-heal bad pin from earlier build (1.0.2 never existed).
    if "1.0.2" in (settings.filesystem_package or ""):
        settings.filesystem_package = (
            "@modelcontextprotocol/server-filesystem@2026.8.31"
        )

    return settings


def save_settings(settings: ManagerSettings) -> None:
    SETTINGS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = asdict(settings)

    # SECURITY:
    # The API key itself is intentionally absent.
    # Only its environment-variable name is persisted.
    # ATOMIC WRITE: temp file + rename so power loss mid-save
    # can't corrupt settings. Keep one .bak for recovery.
    import tempfile

    data = json.dumps(payload, indent=2) + "\n"
    try:
        if SETTINGS_FILE.exists():
            try:
                bak = SETTINGS_FILE.with_suffix(".bak.json")
                bak.write_text(
                    SETTINGS_FILE.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            except OSError:
                pass
        fd, tmp_name = tempfile.mkstemp(
            dir=str(SETTINGS_DIR),
            prefix="settings-",
            suffix=".tmp",
        )
        try:
            with open(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
            Path(tmp_name).replace(SETTINGS_FILE)
        finally:
            try:
                if Path(tmp_name).exists():
                    Path(tmp_name).unlink()
            except OSError:
                pass
    except OSError:
        # Last resort: direct write (old behavior).
        SETTINGS_FILE.write_text(data, encoding="utf-8")


def load_settings_with_backup() -> tuple[ManagerSettings, str]:
    """Load settings, falling back to .bak.json if main is corrupt.

    Returns (settings, source) where source is 'main', 'backup' or
    'defaults'.
    """
    try:
        raw = json.loads(
            SETTINGS_FILE.read_text(encoding="utf-8")
        )
        if not isinstance(raw, dict):
            raw = {}
        from copy import deepcopy as _dc  # local to avoid import cycle

        _s = _merge(raw)
        return _s, "main"
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    try:
        bak = SETTINGS_FILE.with_suffix(".bak.json")
        raw = json.loads(bak.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return _merge(raw), "backup"
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return _merge({}), "defaults"