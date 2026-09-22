"""Individual-tunnel manager: one tunnel-client process per MCP server."""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from mcp_manager_settings import (
    ManagerSettings,
    effective_health_addr,
    effective_tunnel_id,
)
from mcp_registry import MCPRegistry, MCPServer


def _split_health_addr(health_addr: str) -> tuple[str, int]:
    host, port_text = health_addr.rsplit(":", 1)
    return host, int(port_text)


LOG_MAX_BYTES = 1_000_000
LOG_KEEP = 3


def rotate_log(path: Path, max_bytes: int = LOG_MAX_BYTES, keep: int = LOG_KEEP) -> None:
    """Keep logs from growing forever. tunnel-x.log -> .1, .2, .3."""
    try:
        if not path.exists():
            return
        if path.stat().st_size < max_bytes:
            return
        # Shift older: .2 -> .3, .1 -> .2, base -> .1
        for i in range(keep - 1, 0, -1):
            src = path.with_suffix(f".log.{i}") if i > 1 else None
            # Actually: base.log.1, base.log.2...
            older = Path(str(path) + f".{i}")
            newer = Path(str(path) + f".{i + 1}")
            try:
                if older.exists():
                    if i == keep - 1 and newer.exists():
                        try:
                            newer.unlink()
                        except OSError:
                            pass
                    # Move .i -> .i+1 for the top, else base handling below
                    if i < keep:
                        try:
                            older.replace(newer)
                        except OSError:
                            pass
            except OSError:
                pass
        try:
            first_backup = Path(str(path) + ".1")
            path.replace(first_backup)
        except OSError:
            pass
    except OSError:
        pass


def _backoff_seconds(fail_count: int) -> int:
    # 5s, 15s, 30s, 60s, 120s cap.
    table = [5, 15, 30, 60, 120]
    if fail_count <= 0:
        return table[0]
    idx = min(fail_count - 1, len(table) - 1)
    return table[idx]


class TunnelManager:
    """Supervise one tunnel-client-runtime process per server.

    Why individual and not unified:
    - one stdio crash must not kill obsidian / others
    - Start/Stop one server must not restart all others
    - each server gets its own log + health port + tunnel ID
    """

    def __init__(
        self,
        root: Path,
        settings: ManagerSettings,
        registry: MCPRegistry,
    ) -> None:
        self.root = root
        self.settings = settings
        self.registry = registry

        self.processes: dict[str, subprocess.Popen[str] | None] = {
            server_id: None for server_id in registry.servers
        }
        self.log_files: dict[str, object | None] = {
            server_id: None for server_id in registry.servers
        }
        self.statuses: dict[str, str] = {
            server_id: "stopped" for server_id in registry.servers
        }
        self.errors: dict[str, str] = {
            server_id: "" for server_id in registry.servers
        }

        # Backward-compat overall state for old tray code paths.
        self.status = "stopped"
        self.error = ""

        self.runtime = os.environ.get(
            "TUNNEL_CLIENT_RUNTIME",
            "tunnel-client-runtime.exe",
        )

        # Self-healing state per server.
        self.fail_counts: dict[str, int] = {
            server_id: 0 for server_id in registry.servers
        }
        self.next_retry: dict[str, float] = {
            server_id: 0.0 for server_id in registry.servers
        }
        # Notify hook set by tray app: (title, body) -> None
        self.notify: object = None
        self._runtime_version_cache: str | None = None

    # ---------------------------------------------------------
    # COMPAT HELPERS (new tray code should use status_for)
    # ---------------------------------------------------------

    def status_for(self, server_id: str) -> str:
        return self.statuses.get(server_id, "stopped")

    def error_for(self, server_id: str) -> str:
        return self.errors.get(server_id, "")

    def sync_with_registry(self) -> None:
        """General: pick up added/removed servers without restart."""
        for sid in list(self.registry.servers.keys()):
            if sid not in self.processes:
                self.processes[sid] = None
            if sid not in self.log_files:
                self.log_files[sid] = None
            if sid not in self.statuses:
                self.statuses[sid] = "stopped"
            if sid not in self.errors:
                self.errors[sid] = ""
            if sid not in self.fail_counts:
                self.fail_counts[sid] = 0
            if sid not in self.next_retry:
                self.next_retry[sid] = 0.0
        for sid in list(self.processes.keys()):
            if sid not in self.registry.servers:
                try:
                    self.stop_one(sid)
                except Exception:
                    pass
                for d in (
                    self.processes,
                    self.log_files,
                    self.statuses,
                    self.errors,
                    self.fail_counts,
                    self.next_retry,
                ):
                    d.pop(sid, None)
        self._sync_overall()

    def overall_status(self) -> str:
        vals = list(self.statuses.values())
        if not vals:
            return "stopped"
        if any(v == "failed" for v in vals):
            return "failed"
        if any(v == "connected" for v in vals):
            # If anything failed alongside, failed wins (handled above).
            # Mixed connected/starting -> starting until all healthy.
            if all(v in ("connected", "stopped", "disabled") for v in vals):
                # At least one connected.
                running = [
                    v for v in vals if v == "connected"
                ]
                if running and not any(
                    v == "starting" for v in vals
                ):
                    return "connected"
            return "starting" if any(
                v == "starting" for v in vals
            ) else "connected"
        if any(v == "starting" for v in vals):
            return "starting"
        if any(v == "needs-config" for v in vals):
            # Only report needs-config if nothing is running.
            if not any(
                v in ("connected", "starting") for v in vals
            ):
                return "needs-config"
            return "starting"
        return "stopped"

    def _sync_overall(self) -> None:
        self.status = self.overall_status()
        # Surface first per-server error as overall error.
        for server_id in self.registry.servers:
            if self.errors.get(server_id):
                self.error = (
                    f"{server_id}: "
                    f"{self.errors[server_id]}"
                )
                return
        self.error = ""

    # ---------------------------------------------------------
    # RUNTIME
    # ---------------------------------------------------------

    def _runtime_path(self) -> str:
        configured = Path(self.runtime)
        if configured.exists():
            return str(configured)
        return self.runtime

    def runtime_version(self) -> str:
        """Return tunnel-client-runtime version or '' if unknown."""
        if self._runtime_version_cache is not None:
            return self._runtime_version_cache
        exe = self._runtime_path()
        for flag in ("--version", "version"):
            try:
                out = subprocess.run(
                    [exe, flag],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                text = (out.stdout or "") + " " + (out.stderr or "")
                text = text.strip()
                if text:
                    # Keep first line, max 80 chars.
                    line = text.splitlines()[0][:80]
                    self._runtime_version_cache = line
                    return line
            except (OSError, subprocess.SubprocessError):
                continue
            except Exception:
                continue
        self._runtime_version_cache = ""
        return ""

    def heal_state_for(self, server_id: str) -> dict:
        return {
            "fail_count": self.fail_counts.get(server_id, 0),
            "next_retry_in": max(
                0,
                int(
                    self.next_retry.get(server_id, 0.0)
                    - time.monotonic()
                ),
            ),
        }

    def _notify(self, title: str, body: str) -> None:
        try:
            fn = self.notify
            if callable(fn):
                fn(title, body)
        except Exception:
            pass

    # ---------------------------------------------------------
    # MCP COMMAND FORMAT (single-server process => main)
    # ---------------------------------------------------------

    @staticmethod
    def _mcp_command_as_main(server: MCPServer) -> str:
        """Each individual process serves its one MCP as main.

        tunnel-client always requires a main binding. Keeping the
        original channel name would leave the process without main
        and it would refuse to start.
        """
        if not server.command:
            raise RuntimeError(
                f"MCP '{server.id}' has no command configured."
            )

        def _q(arg: str) -> str:
            if not arg:
                return '""'
            if any(c in arg for c in (' ', '"', '\t')):
                return '"' + arg.replace('"', '\\"') + '"'
            return arg

        command = " ".join(_q(a) for a in server.command)
        return f"channel=main,command={command}"

    # ---------------------------------------------------------
    # ARGUMENTS (one server)
    # ---------------------------------------------------------

    def _args_for_server(self, server_id: str) -> list[str]:
        server = self.registry.servers[server_id]
        tunnel_id = effective_tunnel_id(self.settings, server_id)
        health_addr = effective_health_addr(
            self.settings, server_id
        )
        api_env = (
            self.settings.api_key_env.strip()
            or "CONTROL_PLANE_API_KEY"
        )

        args = [
            self._runtime_path(),
            "run",
            "--control-plane.api-key",
            f"env:{api_env}",
            "--control-plane.tunnel-id",
            tunnel_id,
            "--health.listen-addr",
            health_addr,
            "--log.level",
            "info",
        ]

        if server.transport == "stdio":
            args.extend(
                [
                    "--mcp.command",
                    self._mcp_command_as_main(server),
                ]
            )
        else:
            # General: per-server URL wins, else legacy MCP_SERVER_URL
            # env (obsidian compat).
            cfg = self.settings.servers.get(server_id)
            main_url = ""
            if cfg is not None and cfg.server_url.strip():
                main_url = cfg.server_url.strip()
            if not main_url:
                main_url = os.environ.get(
                    "MCP_SERVER_URL",
                    "",
                ).strip()
            if not main_url:
                raise RuntimeError(
                    f"External server '{server_id}' has no URL.\n\n"
                    "Set its Server URL in Settings (or MCP_SERVER_URL)."
                )
            args.extend(
                [
                    "--mcp.server-url",
                    f"channel=main,url={main_url}",
                ]
            )

        extra_headers = os.environ.get(
            "MCP_EXTRA_HEADERS",
            "",
        ).strip()
        if extra_headers:
            args.extend(
                [
                    "--mcp.extra-headers",
                    extra_headers,
                ]
            )
        return args

    # ---------------------------------------------------------
    # VALIDATION (per server, isolated)
    # ---------------------------------------------------------

    def _validate_for_server(self, server_id: str) -> None:
        server = self.registry.servers[server_id]

        if not self.settings.tunnel_enabled:
            return

        server_cfg = self.settings.servers.get(server_id)
        if server_cfg is not None and not server_cfg.enabled:
            raise RuntimeError("Server is disabled.")

        tunnel_id = effective_tunnel_id(self.settings, server_id)
        if not tunnel_id:
            raise RuntimeError(
                f"No tunnel ID configured for '{server_id}'.\n\n"
                "Individual mode needs DISTINCT tunnel IDs.\n"
                "Create a new tunnel for this server and paste "
                "its ID in Settings."
            )

        api_env = (
            self.settings.api_key_env.strip()
            .strip('"')
            .strip("'")
            or "CONTROL_PLANE_API_KEY"
        )
        api_value = os.environ.get(api_env, "").strip()
        if not api_value:
            try:
                import secrets_store as _sec

                _sec.inject_secrets(self.settings)
                api_value = os.environ.get(api_env, "").strip()
            except Exception:
                pass
        if not api_value:
            dotenv_path = self.root / ".env"
            raise RuntimeError(
                "Runtime API key environment variable "
                f"'{api_env}' is empty or missing.\n\n"
                f"Checked Windows Vault + {dotenv_path}\n\n"
                f"Either save it to Vault in Settings → Secrets, "
                f"or add {api_env}=<key> to .env, "
                "then restart the tray app."
            )

        if server.transport != "stdio":
            cfg_url = ""
            if server_cfg is not None and server_cfg.server_url.strip():
                cfg_url = server_cfg.server_url.strip()
            main_url = cfg_url or os.environ.get(
                "MCP_SERVER_URL", ""
            ).strip()
            if not main_url:
                raise RuntimeError(
                    f"External server '{server_id}' has no URL.\n\n"
                    "Set its Server URL in Settings."
                )
        else:
            if not server.command:
                raise RuntimeError(
                    f"MCP '{server_id}' has no command.\n\n"
                    "Set its Command in Settings."
                )

        if server_id == "filesystem":
            from mcp_manager_settings import (
                effective_filesystem_roots,
            )

            roots = effective_filesystem_roots(self.settings)
            if not roots:
                raise RuntimeError(
                    "Filesystem MCP has no folders selected.\n\n"
                    "Open Settings → Filesystem MCP → Add folder."
                )
            missing = [
                r
                for r in roots
                if not Path(r).exists()
            ]
            if missing:
                raise RuntimeError(
                    "Filesystem folder(s) not found:\n"
                    + "\n".join(f"• {m}" for m in missing[:5])
                    + (
                        "\nRemove or fix them in Settings."
                        if len(missing) <= 5
                        else f"\n…and {len(missing)-5} more. Fix in Settings."
                    )
                )

        health_addr = effective_health_addr(
            self.settings, server_id
        )
        try:
            host, port = _split_health_addr(health_addr)
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid health address for '{server_id}': "
                f"{health_addr}"
            ) from exc

        try:
            with socket.create_connection(
                (host, port),
                timeout=0.25,
            ):
                raise RuntimeError(
                    f"The health address {health_addr} "
                    f"for '{server_id}' is already in use.\n\n"
                    "Another tunnel process (or another app) "
                    "holds that port."
                )
        except ConnectionRefusedError:
            pass
        except TimeoutError:
            pass
        except OSError as exc:
            # Port-in-use raises RuntimeError above. Any other
            # networking error: let the runtime report it.
            if "already in use" in str(exc):
                raise
            pass

    # ---------------------------------------------------------
    # LOG PATH
    # ---------------------------------------------------------

    def _log_path_for(self, server_id: str) -> Path:
        return self.root / f"tunnel-{server_id}.log"

    # ---------------------------------------------------------
    # START / STOP single server (isolated)
    # ---------------------------------------------------------

    def _is_running(self, server_id: str) -> bool:
        proc = self.processes.get(server_id)
        return proc is not None and proc.poll() is None

    def start_one(self, server_id: str) -> None:
        if server_id not in self.registry.servers:
            return
        if self._is_running(server_id):
            return

        if not self.settings.tunnel_enabled:
            self.statuses[server_id] = "stopped"
            self.errors[server_id] = ""
            self.registry.runtime[server_id].status = "stopped"
            self.registry.runtime[server_id].error = ""
            self._sync_overall()
            return

        self._validate_for_server(server_id)
        args = self._args_for_server(server_id)
        server = self.registry.servers[server_id]

        safe_env = os.environ.copy()
        if server.transport == "stdio":
            safe_env.pop("MCP_SERVER_URL", None)

        log_path = self._log_path_for(server_id)
        rotate_log(log_path)
        log_file = open(
            log_path,
            "a",
            encoding="utf-8",
            buffering=1,
        )
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log_file.write(
            "\n" + "=" * 70 + "\n"
            + f"Tunnel start [{server_id}]: {timestamp}\n"
            + "=" * 70 + "\n"
        )
        log_file.write("Launching tunnel-client-runtime:\n")
        log_file.write(
            " ".join(
                f'"{arg}"' if " " in arg else arg for arg in args
            )
            + "\n"
        )
        log_file.flush()

        self.log_files[server_id] = log_file
        self.processes[server_id] = subprocess.Popen(
            args,
            cwd=str(self.root),
            env=safe_env,
            creationflags=(subprocess.CREATE_NO_WINDOW),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.statuses[server_id] = "starting"
        self.errors[server_id] = ""
        # Manual start resets heal backoff.
        self.fail_counts[server_id] = 0
        self.next_retry[server_id] = 0.0
        runtime = self.registry.runtime[server_id]
        runtime.status = "starting"
        runtime.error = ""
        runtime.pid = None
        self._sync_overall()

    def stop_one(self, server_id: str) -> None:
        if server_id not in self.registry.servers:
            return
        proc = self.processes.get(server_id)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass

        log_file = self.log_files.get(server_id)
        if log_file is not None and not log_file.closed:
            try:
                log_file.close()
            except Exception:
                pass
        self.processes[server_id] = None
        self.log_files[server_id] = None
        self.statuses[server_id] = "stopped"
        self.errors[server_id] = ""
        self.fail_counts[server_id] = 0
        self.next_retry[server_id] = 0.0
        runtime = self.registry.runtime.get(server_id)
        if runtime is not None and runtime.status != "disabled":
            runtime.status = "stopped"
            runtime.error = ""
            runtime.pid = None

        # Wait for THIS server's health listener to disappear.
        try:
            host, port = _split_health_addr(
                effective_health_addr(self.settings, server_id)
            )
        except ValueError:
            self._sync_overall()
            return
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(
                    (host, port), timeout=0.15
                ):
                    time.sleep(0.1)
            except (ConnectionRefusedError, TimeoutError, OSError):
                break
        self._sync_overall()

    def restart_one(self, server_id: str) -> None:
        self.stop_one(server_id)
        self.start_one(server_id)

    # ---------------------------------------------------------
    # START / STOP / RESTART all (isolated per server)
    # ---------------------------------------------------------

    def start(self) -> None:
        if not self.settings.tunnel_enabled:
            for server_id in self.registry.servers:
                self.statuses[server_id] = "stopped"
                self.errors[server_id] = ""
                self.registry.runtime[server_id].status = (
                    "stopped"
                )
                self.registry.runtime[server_id].error = ""
            self._sync_overall()
            return

        servers = self.registry.enabled_autostart()
        if not servers:
            for server_id in self.registry.servers:
                # Don't wipe a useful failed state for servers
                # that were never asked to start; just ensure
                # non-autostart ones read as stopped.
                if server_id not in {
                    s.id for s in servers
                }:
                    if self.processes.get(server_id) is None:
                        self.statuses[server_id] = "stopped"
            self._sync_overall()
            return

        for server in servers:
            try:
                self.start_one(server.id)
            except Exception as exc:
                # Isolation: one bad server must not block others.
                self.statuses[server.id] = "failed"
                # Needs-config is friendlier than failed when the
                # only problem is a missing distinct tunnel ID.
                if "No tunnel ID configured" in str(exc):
                    self.statuses[server.id] = "needs-config"
                self.errors[server.id] = str(exc)
                runtime = self.registry.runtime[server.id]
                runtime.status = "failed"
                runtime.error = str(exc)
        self._sync_overall()

    def stop(self) -> None:
        for server_id in list(self.registry.servers):
            try:
                self.stop_one(server_id)
            except Exception:
                pass
        self._sync_overall()

    def restart(self) -> None:
        self.stop()
        self.start()

    # Public aliases matching old single-process API used by tray.
    def start_server(self, server_id: str) -> None:
        self.start_one(server_id)

    def stop_server(self, server_id: str) -> None:
        self.stop_one(server_id)

    def restart_server(self, server_id: str) -> None:
        self.restart_one(server_id)

    # ---------------------------------------------------------
    # REFRESH (per server, isolated)
    # ---------------------------------------------------------

    def refresh(self) -> None:
        for server_id in self.registry.servers:
            proc = self.processes.get(server_id)
            runtime = self.registry.runtime[server_id]

            if proc is not None and proc.poll() is not None:
                returncode = proc.returncode
                # Close dead handle so ports/logs release.
                try:
                    lf = self.log_files.get(server_id)
                    if lf is not None and not lf.closed:
                        lf.close()
                except Exception:
                    pass
                self.processes[server_id] = None
                self.log_files[server_id] = None
                self.statuses[server_id] = "failed"
                self.errors[server_id] = (
                    "tunnel-client-runtime exited "
                    f"with code {returncode}"
                )
                runtime.status = "failed"
                if not runtime.error:
                    runtime.error = self.errors[server_id]
                # Track consecutive crashes for backoff.
                try:
                    self.fail_counts[server_id] = (
                        self.fail_counts.get(server_id, 0) + 1
                    )
                except Exception:
                    pass

                # Self-healing: retry autostart servers with backoff.
                try:
                    auto_heal = bool(
                        getattr(self.settings, "auto_heal", True)
                    )
                    max_retries = int(
                        getattr(
                            self.settings, "max_heal_retries", 5
                        )
                    )
                    wants = {
                        s.id
                        for s in self.registry.enabled_autostart()
                    }
                    if (
                        auto_heal
                        and server_id in wants
                        and self.settings.tunnel_enabled
                        and self.fail_counts.get(server_id, 0)
                        <= max_retries
                    ):
                        wait = _backoff_seconds(
                            self.fail_counts.get(server_id, 1)
                        )
                        self.next_retry[server_id] = (
                            time.monotonic() + wait
                        )
                        self.errors[server_id] += (
                            f" (retry in {wait}s, attempt "
                            f"{self.fail_counts.get(server_id, 1)}/"
                            f"{max_retries})"
                        )
                        if self.fail_counts.get(server_id, 0) == 3:
                            self._notify(
                                f"{server_id} tunnel crashed",
                                f"Exit {returncode}. Retrying automatically.",
                            )
                    elif (
                        self.fail_counts.get(server_id, 0)
                        > int(
                            getattr(
                                self.settings,
                                "max_heal_retries",
                                5,
                            )
                        )
                    ):
                        self._notify(
                            f"{server_id} tunnel needs attention",
                            f"Gave up after {max_retries} retries. "
                            "Check log.",
                        )
                except Exception:
                    pass
                continue

            if proc is None:
                # Due retry? Attempt healing now.
                try:
                    due = self.next_retry.get(server_id, 0.0)
                    if (
                        due
                        and time.monotonic() >= due
                        and self.settings.tunnel_enabled
                    ):
                        wants = {
                            s.id
                            for s in self.registry.enabled_autostart()
                        }
                        if server_id in wants:
                            try:
                                self.start_one(server_id)
                                continue
                            except Exception as exc:
                                self.statuses[server_id] = "failed"
                                self.errors[server_id] = str(exc)
                                runtime.status = "failed"
                                runtime.error = str(exc)
                                continue
                except Exception:
                    pass
                # Preserve needs-config / failed until next start.
                if self.statuses.get(server_id) in (
                    "failed",
                    "needs-config",
                ) and self.errors.get(server_id):
                    runtime.status = "failed"
                    runtime.error = self.errors[server_id]
                else:
                    self.statuses[server_id] = "stopped"
                    runtime.status = "stopped"
                continue

            try:
                url = (
                    "http://"
                    + effective_health_addr(
                        self.settings, server_id
                    )
                    + "/readyz"
                )
                with urllib.request.urlopen(
                    url, timeout=1
                ) as response:
                    healthy = response.status == 200
            except Exception:
                healthy = False

            self.statuses[server_id] = (
                "connected" if healthy else "starting"
            )
            runtime.status = (
                "running" if healthy else "starting"
            )
            if healthy:
                # Healed — reset backoff.
                if self.fail_counts.get(server_id, 0):
                    self.fail_counts[server_id] = 0
                self.next_retry[server_id] = 0.0
                if not runtime.error:
                    self.errors[server_id] = ""
        self._sync_overall()
