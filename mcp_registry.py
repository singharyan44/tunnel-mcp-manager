"""Generic MCP registry and runtime state model."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from mcp_manager_settings import ManagerSettings


@dataclass(frozen=True)
class MCPServer:
    id: str
    display_name: str
    command: tuple[str, ...]
    working_directory: Path
    channel: str
    transport: str = "stdio"


@dataclass
class MCPRuntime:
    status: str = "stopped"
    error: str = ""
    pid: int | None = None


def _command_from_env(
    name: str,
    fallback: tuple[str, ...],
) -> tuple[str, ...]:
    value = os.environ.get(name, "").strip()

    if value:
        try:
            import shlex as _shlex

            return tuple(
                _shlex.split(value, posix=False)
            )
        except ValueError:
            return tuple(value.split())

    return fallback


def parse_command_line(text: str) -> tuple[str, ...]:
    text = (text or "").strip()
    if not text:
        return ()
    try:
        import shlex as _shlex

        return tuple(_shlex.split(text, posix=False))
    except ValueError:
        return tuple(text.split())


def _windows_path_for_command(path: Path) -> str:
    """Return a Windows path using forward slashes.

    tunnel-client receives MCP commands as a single command string.
    Forward slashes avoid backslash escaping/parsing problems.
    """
    return path.as_posix()


def _quote_for_mcp_command(arg: str) -> str:
    # server-filesystem takes multiple folder args; folders often
    # contain spaces. Quote only when needed.
    if not arg:
        return '""'
    if any(c in arg for c in (' ', '"', '\t')):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


FILESYSTEM_PACKAGE_DEFAULT = (
    "@modelcontextprotocol/server-filesystem@2026.8.31"
)


def filesystem_command_for_roots(
    roots: list[str],
    package: str = FILESYSTEM_PACKAGE_DEFAULT,
) -> tuple[str, ...]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in roots:
        val = raw.strip().strip('"').strip("'")
        if not val or val.lower() in seen:
            continue
        seen.add(val.lower())
        try:
            posix = Path(val).as_posix()
        except Exception:
            posix = val.replace("\\", "/")
        cleaned.append(posix)
    if not cleaned:
        fallback = Path.home() / "Documents" / "GitHub"
        cleaned = [fallback.as_posix()]
    pkg = (package or FILESYSTEM_PACKAGE_DEFAULT).strip()
    if not pkg.startswith("@modelcontextprotocol/"):
        pkg = FILESYSTEM_PACKAGE_DEFAULT
    return (
        "npx",
        "-y",
        pkg,
        *cleaned,
    )


def build_registry(
    root: Path,
) -> dict[str, MCPServer]:

    # ------------------------------------------------------------------
    # Godot MCP (generic: env override wins, else blank = needs setup)
    # Set GODOT_MCP_COMMAND or fill Command in Settings. The old
    # personal default below is kept only as a hint, never required.
    # ------------------------------------------------------------------

    _godot_candidates = [
        Path.home()
        / "Documents"
        / "GitHub"
        / "UNSEEN"
        / ".mcp"
        / "venv"
        / "Scripts"
        / "godot-editor-mcp.exe",
        Path.home()
        / "Documents"
        / "GitHub"
        / "godot-mcp"
        / ".venv"
        / "Scripts"
        / "godot-editor-mcp.exe",
    ]

    godot_default = ()
    for _cand in _godot_candidates:
        if _cand.exists():
            godot_default = (_windows_path_for_command(_cand),)
            break

    # ------------------------------------------------------------------
    # OpenSCAD MCP
    # ------------------------------------------------------------------

    openscad_executable = (
        root.parent
        / "openscad-mcp"
        / ".venv"
        / "Scripts"
        / "openscad-mcp.exe"
    )

    openscad_default = (
        (_windows_path_for_command(openscad_executable),)
        if openscad_executable.exists()
        else ()
    )

    openscad_working_directory = (
        openscad_executable
        .parent
        .parent
        .parent
        if openscad_executable.exists()
        else root
    )

    # ------------------------------------------------------------------
    # Filesystem MCP
    # ------------------------------------------------------------------

    filesystem_root = (
        Path.home()
        / "Documents"
        / "GitHub"
    )

    filesystem_default = (
        "npx",
        "-y",
        "@modelcontextprotocol/server-filesystem",
        _windows_path_for_command(filesystem_root),
    )

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    return {
        "obsidian": MCPServer(
            id="obsidian",
            display_name="Obsidian MCP",
            command=(),
            working_directory=root,
            channel="main",
            transport="external",
        ),

        "openscad": MCPServer(
            id="openscad",
            display_name="OpenSCAD MCP",
            command=_command_from_env(
                "OPENSCAD_MCP_COMMAND",
                openscad_default,
            ),
            working_directory=openscad_working_directory,
            channel="openscad",
            transport="stdio",
        ),

        "godot": MCPServer(
            id="godot",
            display_name="Godot MCP",
            command=_command_from_env(
                "GODOT_MCP_COMMAND",
                godot_default,
            ),
            working_directory=root,
            channel="godot",
            transport="stdio",
        ),

        "filesystem": MCPServer(
            id="filesystem",
            display_name="Filesystem MCP",
            command=_command_from_env(
                "FILESYSTEM_MCP_COMMAND",
                filesystem_default,
            ),
            working_directory=root,
            channel="filesystem",
            transport="stdio",
        ),
    }


class MCPRegistry:

    def __init__(
        self,
        root: Path,
        settings: ManagerSettings,
    ):
        self.root = root
        self.servers = build_registry(root)

        self.runtime = {
            server_id: MCPRuntime()
            for server_id in self.servers
        }

        self.settings = settings
        self.refresh_from_settings()

    def refresh_from_settings(self) -> None:
        """General: overlay settings onto legacy base + add customs."""
        from mcp_manager_settings import effective_filesystem_roots

        # 1) Custom servers not in legacy base: build from settings.
        for server_id, cfg in self.settings.servers.items():
            if server_id in self.servers:
                continue
            transport = (cfg.transport or "stdio").strip().lower()
            if transport not in ("stdio", "external"):
                transport = "stdio"
            if transport == "external":
                self.servers[server_id] = MCPServer(
                    id=server_id,
                    display_name=cfg.display_name or server_id,
                    command=(),
                    working_directory=self._resolve_cwd(cfg),
                    channel=cfg.channel or server_id,
                    transport="external",
                )
            else:
                cmd = parse_command_line(cfg.command)
                # Per-server env override wins if set.
                if cfg.command_env.strip():
                    cmd = _command_from_env(cfg.command_env.strip(), cmd)
                self.servers[server_id] = MCPServer(
                    id=server_id,
                    display_name=cfg.display_name or server_id,
                    command=cmd,
                    working_directory=self._resolve_cwd(cfg),
                    channel=cfg.channel or server_id,
                    transport="stdio",
                )

        # 2) Known servers: apply settings overrides if present.
        for server_id, cfg in self.settings.servers.items():
            existing = self.servers.get(server_id)
            if existing is None:
                continue
            disp = cfg.display_name.strip() or existing.display_name
            transport = (cfg.transport or existing.transport).strip().lower()
            if transport not in ("stdio", "external"):
                transport = existing.transport
            cwd = self._resolve_cwd(cfg, fallback=existing.working_directory)
            channel = cfg.channel.strip() or existing.channel
            command = existing.command
            if transport == "external":
                command = ()
            elif cfg.command.strip():
                command = parse_command_line(cfg.command)
                if cfg.command_env.strip():
                    command = _command_from_env(
                        cfg.command_env.strip(), command
                    )
            elif cfg.command_env.strip():
                command = _command_from_env(
                    cfg.command_env.strip(), command
                )
            self.servers[server_id] = MCPServer(
                id=existing.id,
                display_name=disp,
                command=command,
                working_directory=cwd,
                channel=channel,
                transport=transport,
            )

        # 3) Filesystem: build from folders unless custom command/env set.
        try:
            fs_cfg = self.settings.servers.get("filesystem")
            fs = self.servers.get("filesystem")
            if fs is not None and fs_cfg is not None:
                has_custom = bool(fs_cfg.command.strip())
                has_env = bool(
                    os.environ.get("FILESYSTEM_MCP_COMMAND", "").strip()
                    or (fs_cfg.command_env.strip() and os.environ.get(
                        fs_cfg.command_env.strip(), ""
                    ).strip())
                )
                if not has_custom and not has_env:
                    roots = effective_filesystem_roots(self.settings)
                    pkg = getattr(
                        self.settings,
                        "filesystem_package",
                        FILESYSTEM_PACKAGE_DEFAULT,
                    )
                    self.servers["filesystem"] = MCPServer(
                        id=fs.id,
                        display_name=fs.display_name,
                        command=filesystem_command_for_roots(roots, pkg),
                        working_directory=fs.working_directory,
                        channel=fs.channel,
                        transport=fs.transport,
                    )
        except Exception:
            pass

        # 4) Drop servers deleted from settings (keep runtime safe).
        for sid in list(self.servers.keys()):
            if sid not in self.settings.servers:
                # Keep legacy base ids even if missing? No — general:
                # if user deleted, drop it.
                del self.servers[sid]

        # 5) Ensure runtime entries for all current servers.
        for sid in self.servers:
            if sid not in self.runtime:
                self.runtime[sid] = MCPRuntime()

    def _resolve_cwd(self, cfg, fallback=None):
        try:
            if cfg.working_dir.strip():
                p = Path(cfg.working_dir.strip()).expanduser()
                if p.exists():
                    return p
                return p
        except Exception:
            pass
        if fallback is not None:
            return fallback
        return self.root

    def enabled_autostart(
        self,
    ) -> list[MCPServer]:

        return [
            server
            for server_id, server in self.servers.items()
            if (
                self.settings.servers.get(server_id)
                is not None
                and self.settings.servers[server_id].enabled
                and self.settings.servers[server_id].autostart
            )
        ]

    def enabled(
        self,
    ) -> list[MCPServer]:

        return [
            server
            for server_id, server in self.servers.items()
            if (
                self.settings.servers.get(server_id) is not None
                and self.settings.servers[server_id].enabled
            )
        ]


def describe_server(
    server_id: str,
    settings,
    registry: MCPRegistry,
) -> dict:
    """Resolved values the UI should SHOW (not just the override box).

    Returns {command_str, command_source, cwd_str, url_str, url_source}.
    Sources: Settings override / ENV VAR / folders / built-in default.
    """
    out = {
        "command_str": "",
        "command_source": "",
        "cwd_str": "",
        "url_str": "",
        "url_source": "",
    }
    try:
        server = registry.servers.get(server_id)
        cfg = settings.servers.get(server_id)
        if server is None or cfg is None:
            return out
        if server.transport == "stdio":
            if server.command:
                out["command_str"] = " ".join(server.command)
            # Source detection.
            env_name = (cfg.command_env or "").strip()
            env_val = ""
            if env_name:
                try:
                    env_val = os.environ.get(env_name, "").strip()
                except Exception:
                    env_val = ""
            legacy_env = ""
            if server_id == "filesystem":
                legacy_env = os.environ.get(
                    "FILESYSTEM_MCP_COMMAND", ""
                ).strip()
            if env_val or legacy_env:
                out["command_source"] = (
                    f"from {env_name or 'FILESYSTEM_MCP_COMMAND'} env var"
                )
            elif cfg.command.strip():
                out["command_source"] = "from Settings override"
            elif server_id == "filesystem":
                out["command_source"] = "from folder list below"
            else:
                out["command_source"] = "built-in default"
            try:
                out["cwd_str"] = str(server.working_directory)
            except Exception:
                out["cwd_str"] = ""
        else:
            url = (cfg.server_url or "").strip()
            if url:
                out["url_str"] = url
                out["url_source"] = "from Settings"
            else:
                try:
                    env_url = os.environ.get(
                        "MCP_SERVER_URL", ""
                    ).strip()
                except Exception:
                    env_url = ""
                out["url_str"] = env_url
                out["url_source"] = (
                    "from MCP_SERVER_URL env var" if env_url else ""
                )
            try:
                out["cwd_str"] = str(server.working_directory)
            except Exception:
                out["cwd_str"] = ""
    except Exception:
        pass
    return out