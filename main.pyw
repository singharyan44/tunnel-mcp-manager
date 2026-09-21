"""Local MCP registry and OpenAI tunnel-client tray manager."""

from __future__ import annotations

import math
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import winreg
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw
from pystray import Icon, Menu, MenuItem

from mcp_manager_settings import (
    ManagerSettings,
    default_filesystem_dirs,
    effective_filesystem_roots,
    effective_health_addr,
    effective_tunnel_id,
    load_settings,
    save_settings,
)
from mcp_registry import MCPRegistry
from tunnel_manager import TunnelManager


ROOT = Path(__file__).resolve().parent

STARTUP_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Run"
)

STARTUP_NAME = "OpenSCAD_MCP_Manager"

PID_FILE = ROOT / "mcp-manager.pid"


# ------------------------------------------------------------
# ENVIRONMENT
# ------------------------------------------------------------

def load_dotenv() -> None:
    path = ROOT / ".env"

    if not path.exists():
        return

    for line in path.read_text(
        encoding="utf-8"
    ).splitlines():

        line = line.strip()

        if line.startswith("export "):
            line = line[7:].lstrip()

        if (
            line
            and not line.startswith("#")
            and "=" in line
        ):
            key, value = line.split("=", 1)

            key = key.strip()
            value = value.strip()

            if key:
                os.environ[key] = (
                    value.strip('"').strip("'")
                )


def update_dotenv(
    values: dict[str, str],
) -> None:
    """
    Update non-secret launcher settings.

    Secrets themselves should remain referenced through
    environment-variable names rather than being written here.
    """

    path = ROOT / ".env"

    lines = (
        path.read_text(
            encoding="utf-8"
        ).splitlines()
        if path.exists()
        else []
    )

    seen: set[str] = set()
    output: list[str] = []

    for line in lines:
        stripped = line.strip()

        if (
            stripped
            and not stripped.startswith("#")
            and "=" in stripped
        ):
            key = stripped.split(
                "=", 1
            )[0].strip()

            if key in values:
                output.append(
                    f"{key}={values[key]}"
                )
                seen.add(key)
                continue

        output.append(line)

    for key, value in values.items():
        if key not in seen:
            output.append(
                f"{key}={value}"
            )

    path.write_text(
        "\n".join(output).rstrip() + "\n",
        encoding="utf-8",
    )


# ------------------------------------------------------------
# SINGLE INSTANCE
# ------------------------------------------------------------

def _process_exists(pid: int) -> bool:
    """Return whether a Windows process with this PID exists."""

    if pid <= 0:
        return False

    if pid == os.getpid():
        return True

    try:
        os.kill(pid, 0)
        return True

    except (
        ProcessLookupError,
        PermissionError,
        OSError,
        SystemError,
    ):
        # Windows can raise SystemError from os.kill(pid, 0)
        # when the PID is stale/invalid.
        return False


def acquire_single_instance(auto_replace: bool = False) -> bool:
    existing_pid: int | None = None

    try:
        existing_pid = int(
            PID_FILE.read_text(
                encoding="ascii"
            ).strip()
        )

    except (
        FileNotFoundError,
        ValueError,
        OSError,
    ):
        pass

    if (
        existing_pid
        and _process_exists(existing_pid)
        and existing_pid != os.getpid()
    ):
        if auto_replace:
            replace = True
        else:
            prompt = tk.Tk()
            prompt.withdraw()
            try:
                replace = messagebox.askyesno(
                    "MCP Manager Already Open",
                    (
                        "The MCP Manager is already running.\n\n"
                        "Close the existing instance and open this one?"
                    ),
                    parent=prompt,
                )
            finally:
                try:
                    prompt.destroy()
                except Exception:
                    pass
            if not replace:
                return False

        subprocess.run(
            [
                "taskkill",
                "/PID",
                str(existing_pid),
                "/T",
                "/F",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        deadline = time.time() + 5

        while (
            _process_exists(existing_pid)
            and time.time() < deadline
        ):
            time.sleep(0.1)

    PID_FILE.write_text(
        str(os.getpid()),
        encoding="ascii",
    )

    return True


def release_single_instance() -> None:
    try:
        if (
            PID_FILE.read_text(
                encoding="ascii"
            ).strip()
            == str(os.getpid())
        ):
            PID_FILE.unlink()

    except (
        FileNotFoundError,
        OSError,
    ):
        pass


# ------------------------------------------------------------
# INITIALIZATION
# ------------------------------------------------------------

load_dotenv()

settings: ManagerSettings = load_settings()

settings.tunnel_id = os.environ.get(
    "CONTROL_PLANE_TUNNEL_ID",
    settings.tunnel_id,
).strip()

# Backward compat: obsidian keeps using the shared env ID until the
# user pastes DISTINCT per-server IDs in Settings.
if "obsidian" in settings.servers:
    env_tunnel = os.environ.get(
        "CONTROL_PLANE_TUNNEL_ID", ""
    ).strip()
    if env_tunnel and not settings.servers[
        "obsidian"
    ].tunnel_id.strip():
        settings.servers["obsidian"].tunnel_id = env_tunnel
    # Keep global in sync with obsidian for old tooling.
    if settings.servers["obsidian"].tunnel_id.strip():
        settings.tunnel_id = settings.servers[
            "obsidian"
        ].tunnel_id.strip()


# The setting contains an environment variable NAME,
# never the API key itself.
if not re.fullmatch(
    r"[A-Za-z_][A-Za-z0-9_]*",
    settings.api_key_env.strip(),
):
    settings.api_key_env = (
        "CONTROL_PLANE_API_KEY"
    )

save_settings(settings)

registry = MCPRegistry(
    ROOT,
    settings,
)

tunnel = TunnelManager(
    ROOT,
    settings,
    registry,
)

RUNTIME_VERSION = ""
try:
    RUNTIME_VERSION = tunnel.runtime_version()
except Exception:
    RUNTIME_VERSION = ""


def tray_notify(title: str, body: str) -> None:
    try:
        # Throttle: max 1 toast per 30s per title.
        now = time.monotonic()
        last = getattr(tray_notify, "_last", {})
        if now - last.get(title, 0) < 30:
            return
        last[title] = now
        tray_notify._last = last  # type: ignore[attr-defined]
        icon.notify(body[:240], title[:60])
    except Exception:
        pass


try:
    tunnel.notify = tray_notify
except Exception:
    pass


icon: Icon

settings_window_lock = threading.Lock()
settings_window_open = False
_last_menu_fingerprint: str = ""


# ------------------------------------------------------------
# TRAY ICON
# ------------------------------------------------------------

def create_icon() -> Image.Image:
    running = sorted(
        server_id
        for server_id, state
        in registry.runtime.items()
        if state.status
        in (
            "starting",
            "running",
        )
    )

    image = Image.new(
        "RGBA",
        (64, 64),
        (0, 0, 0, 0),
    )

    draw = ImageDraw.Draw(image)

    # --------------------------------------------------------
    # NOTHING RUNNING
    # --------------------------------------------------------

    if not running:
        draw.ellipse(
            (12, 12, 52, 52),
            fill="#d32f2f",
        )

        return image

    # --------------------------------------------------------
    # ONE MCP
    # --------------------------------------------------------

    bounds = (
        6,
        6,
        58,
        58,
    )

    if len(running) == 1:
        server_id = running[0]

        color = settings.servers[
            server_id
        ].color

        draw.ellipse(
            bounds,
            fill=color,
            outline="#202020",
            width=2,
        )

        return image

    # --------------------------------------------------------
    # MULTIPLE MCPS
    # --------------------------------------------------------

    angle = 360 / len(running)

    for index, server_id in enumerate(
        running
    ):
        color = settings.servers[
            server_id
        ].color

        start = (
            -90
            + index * angle
        )

        draw.pieslice(
            bounds,
            start,
            start + angle,
            fill=color,
        )

    draw.ellipse(
        bounds,
        outline="#202020",
        width=2,
    )

    center = (
        32,
        32,
    )

    for index in range(
        len(running)
    ):
        radians = math.radians(
            -90
            + index * angle
        )

        edge = (
            32
            + int(
                26
                * math.cos(radians)
            ),
            32
            + int(
                26
                * math.sin(radians)
            ),
        )

        draw.line(
            (
                center,
                edge,
            ),
            fill="#202020",
            width=2,
        )

    return image


# ------------------------------------------------------------
# STATUS
# ------------------------------------------------------------

def label(status: str) -> str:
    return {
        "running": "Running",
        "starting": "Starting",
        "failed": "Failed",
        "connected": "Connected",
        "tunnel-unavailable": "Tunnel unavailable",
        "disabled": "Disabled",
        "stopped": "Stopped",
        "needs-config": "Needs tunnel ID",
    }.get(
        status,
        status.title(),
    )


def tooltip() -> str:
    # Individual mode: one short line per server showing its own
    # tunnel state. Keep it tiny for the 128-char Windows limit.
    lines = []
    for server_id, server in registry.servers.items():
        tstatus = tunnel.status_for(server_id)
        short = server.display_name.replace(" MCP", "")
        active = tstatus in ("starting", "connected")
        # Prefer tunnel state; runtime mirrors it in refresh().
        state = (
            "Running"
            if tstatus == "connected"
            else label(tstatus)
        )
        lines.append(
            f"{'●' if active else '○'} {short} {state}"
        )

    text = "\n".join(lines) if lines else "MCP Manager"

    if len(text) > 120:
        text = text[:117] + "..."

    return text


def refresh() -> None:
    global _last_menu_fingerprint
    try:
        tunnel.refresh()

        for server_id in registry.servers:
            err = tunnel.error_for(server_id)
            if err:
                print(
                    f"[TUNNEL ERROR] {server_id}: {err}",
                    flush=True,
                )

        for server_id, runtime in registry.runtime.items():
            if runtime.error:
                print(
                    f"[MCP ERROR] {server_id}: "
                    f"{runtime.error}",
                    flush=True,
                )

    except Exception as exc:
        print(
            f"[REFRESH ERROR] {exc}",
            flush=True,
        )

    try:
        icon.icon = create_icon()
    except Exception:
        pass
    try:
        icon.title = tooltip()
    except Exception:
        pass
    # Quieter tray: only rebuild menu when something visible changed.
    # Rebuilding every second makes the open menu flicker.
    try:
        fp_parts = [tunnel.overall_status()]
        for sid in registry.servers:
            fp_parts.append(f"{sid}={tunnel.status_for(sid)}")
        fp_parts.append(f"startup={startup_enabled()}")
        fp = "|".join(fp_parts)
        if fp != _last_menu_fingerprint:
            _last_menu_fingerprint = fp
            icon.update_menu()
    except Exception:
        try:
            icon.update_menu()
        except Exception:
            pass


# ------------------------------------------------------------
# ACTIONS
# ------------------------------------------------------------

def action(fn) -> None:
    try:
        fn()

    except Exception as exc:
        try:
            messagebox.showerror(
                "MCP Manager",
                str(exc),
            )
        except Exception:
            pass

    finally:
        refresh()


def start_all(
    _icon=None,
    _item=None,
):
    action(tunnel.start)


def stop_all(
    _icon=None,
    _item=None,
):
    action(tunnel.stop)


def restart_all(
    _icon=None,
    _item=None,
):
    action(tunnel.restart)


def start_server(
    server_id: str,
):
    """Start ONE server's own tunnel process (session only).

    Does NOT change Enabled/Autostart prefs and does NOT restart
    other servers. Use Settings to change saved prefs.
    """

    def _run():
        tunnel.start_server(server_id)

    action(_run)


def stop_server(
    server_id: str,
):
    """Stop ONE server's own tunnel process (session only).

    Does NOT change saved prefs and does NOT touch others.
    """

    def _run():
        tunnel.stop_server(server_id)

    action(_run)


def restart_server(
    server_id: str,
):
    """Restart ONE server's own tunnel process only."""

    def _run():
        tunnel.restart_server(server_id)

    action(_run)


# ------------------------------------------------------------
# WINDOWS STARTUP
# ------------------------------------------------------------

def startup_enabled() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            STARTUP_KEY,
            0,
            winreg.KEY_READ,
        ) as key:
            winreg.QueryValueEx(
                key,
                STARTUP_NAME,
            )

        return True

    except (
        FileNotFoundError,
        OSError,
    ):
        return False


def _app_script_path() -> Path:
    # Frozen exe (PyInstaller) vs plain .pyw.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(__file__).resolve()


def _python_for_startup() -> str:
    # Prefer pythonw for .pyw so boot doesn't flash a console.
    # Fall back to current interpreter, then plain python.
    try:
        exe = Path(sys.executable)
        if exe.name.lower() in ("python.exe", "pythonw.exe"):
            cand = exe.parent / "pythonw.exe"
            if cand.exists():
                return str(cand)
            if exe.exists():
                return str(exe)
    except Exception:
        pass
    return sys.executable


def get_launch_command() -> str:
    script = _app_script_path()
    if getattr(sys, "frozen", False):
        return f'"{script}" --minimized'
    py = _python_for_startup()
    return f'"{py}" "{script}" --minimized'


def set_startup_enabled(enabled: bool) -> tuple[bool, str]:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            STARTUP_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if not enabled:
                try:
                    winreg.DeleteValue(key, STARTUP_NAME)
                except FileNotFoundError:
                    pass
                settings.tray_autostart = False
            else:
                winreg.SetValueEx(
                    key,
                    STARTUP_NAME,
                    0,
                    winreg.REG_SZ,
                    get_launch_command(),
                )
                settings.tray_autostart = True
        save_settings(settings)
        refresh()
        return True, "OK"
    except OSError as exc:
        return False, str(exc)


def toggle_startup(_icon=None, _item=None):
    ok, msg = set_startup_enabled(not startup_enabled())
    if not ok:
        try:
            messagebox.showerror("Startup Setting Error", msg)
        except Exception:
            pass


def restart_tray_app(dev: bool = False):
    """Relaunch this tray app to pick up code changes.

    Stops tunnels, spawns a fresh process with --restart (no prompt),
    then exits this one. New process re-acquires the PID file.
    """
    try:
        try:
            tunnel.stop()
        except Exception:
            pass
        script = _app_script_path()
        if getattr(sys, "frozen", False):
            args = [str(script), "--restart"]
            if dev or "--dev" in sys.argv:
                args.append("--dev")
        else:
            args = [sys.executable, str(script), "--restart"]
            if dev or "--dev" in sys.argv:
                args.append("--dev")
        subprocess.Popen(
            args,
            cwd=str(ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    finally:
        try:
            release_single_instance()
        except Exception:
            pass
        try:
            icon.stop()
        except Exception:
            pass
        os._exit(0)


def open_project_folder(_icon=None, _item=None):
    try:
        os.startfile(str(ROOT))
    except Exception as exc:
        try:
            messagebox.showerror("Open folder failed", str(exc))
        except Exception:
            pass


def _dev_watch_loop():
    # Dev mode: watch our own .py/.pyw files, auto-restart on save.
    # Debounced + ignores logs/pycache.
    seen: dict[str, float] = {}
    ROOT_RES = ROOT.resolve()

    def _snapshot() -> dict[str, float]:
        out: dict[str, float] = {}
        try:
            for path in ROOT_RES.glob("*.py*"):
                if path.name.startswith("__pycache__"):
                    continue
                try:
                    out[str(path)] = path.stat().st_mtime
                except OSError:
                    continue
        except Exception:
            pass
        return out

    seen = _snapshot()
    while True:
        time.sleep(1.0)
        try:
            cur = _snapshot()
            if cur != seen:
                # Debounce: wait for editor to finish writing.
                time.sleep(1.0)
                cur2 = _snapshot()
                seen = cur2
                restart_tray_app(dev=True)
                return
        except Exception:
            pass


# ------------------------------------------------------------
# QUICK ACTIONS (DX: logs, health URLs)
# ------------------------------------------------------------

STATUS_DOT = {
    "connected": "●",
    "running": "●",
    "starting": "◐",
    "stopped": "○",
    "failed": "✖",
    "needs-config": "⚠",
    "disabled": "○",
}

FRIENDLY_HINT = {
    "connected": "healthy",
    "running": "healthy",
    "starting": "starting…",
    "stopped": "stopped",
    "failed": "check log",
    "needs-config": "needs tunnel ID",
    "disabled": "disabled",
}


def _server_short_label(server_id: str) -> str:
    try:
        server = registry.servers[server_id]
        tstatus = tunnel.status_for(server_id)
    except Exception:
        return server_id
    dot = STATUS_DOT.get(tstatus, "○")
    hint = FRIENDLY_HINT.get(tstatus, tstatus)
    name = server.display_name.replace(" MCP", "")
    return f"{dot} {name} — {hint}"


def open_log_file(server_id: str | None = None):
    try:
        if server_id is None:
            path = ROOT
        else:
            path = ROOT / f"tunnel-{server_id}.log"
            if not path.exists():
                # Fall back to legacy shared log.
                legacy = ROOT / "tunnel-client.log"
                path = legacy if legacy.exists() else ROOT
        if path.is_dir():
            os.startfile(str(path))
        elif path.exists():
            os.startfile(str(path))
        else:
            messagebox.showinfo(
                "No log yet",
                f"No log file for {server_id or 'manager'} yet.\n"
                "Start that tunnel first.",
            )
    except Exception as exc:
        try:
            messagebox.showerror("Open log failed", str(exc))
        except Exception:
            pass


def open_logs_folder(_icon=None, _item=None):
    open_log_file(None)


def copy_text_to_clipboard(text: str, label: str = "Copied"):
    try:
        # `clip` is built into Windows, no extra deps.
        subprocess.run(
            ["clip"],
            input=text.encode("utf-16-le"),
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as exc:
        try:
            messagebox.showerror("Copy failed", str(exc))
        except Exception:
            pass


def copy_health_url(server_id: str):
    url = (
        "http://"
        + effective_health_addr(settings, server_id)
        + "/readyz"
    )
    copy_text_to_clipboard(url)


# ------------------------------------------------------------
# SETTINGS WINDOW
# ------------------------------------------------------------

def _apply_modern_theme(root: tk.Tk) -> None:
    try:
        style = ttk.Style(root)
        for theme in ("vista", "xpnative", "clam"):
            try:
                if theme in style.theme_names():
                    style.theme_use(theme)
                    break
            except Exception:
                continue
        style.configure("TLabel", font=("Segoe UI", 9))
        style.configure("TButton", font=("Segoe UI", 9), padding=(10, 4))
        style.configure(
            "Accent.TButton", font=("Segoe UI", 9, "bold"), padding=(12, 5)
        )
        style.configure("TCheckbutton", font=("Segoe UI", 9))
        style.configure("TLabelframe", font=("Segoe UI", 9, "bold"))
        style.configure("TEntry", padding=3)
    except Exception:
        pass


def _open_settings_window() -> None:
    # Prefer modern CustomTkinter UI, fallback to Tk on any error.
    global settings_window_open
    try:
        from settings_ui_ctk import open_settings_ctk

        helpers = {
            "effective_tunnel_id": effective_tunnel_id,
            "effective_health_addr": effective_health_addr,
            "effective_filesystem_roots": effective_filesystem_roots,
            "default_filesystem_dirs": default_filesystem_dirs,
            "save_settings": save_settings,
            "update_dotenv": update_dotenv,
            "load_dotenv": load_dotenv,
            "refresh": refresh,
            "action": action,
            "tunnel_restart": tunnel.restart,
            "open_log": open_log_file,
            "copy_url": copy_health_url,
            "open_logs_folder": open_logs_folder,
            "startup_enabled": startup_enabled,
            "set_startup_enabled": set_startup_enabled,
            "get_launch_command": get_launch_command,
            "runtime_version": RUNTIME_VERSION,
            "stop_one": tunnel.stop_server,
            "sync_tunnel": tunnel.sync_with_registry,
        }
        try:
            try:
                open_settings_ctk(settings, registry, tunnel, helpers)
            finally:
                with settings_window_lock:
                    settings_window_open = False
            return
        except Exception as exc:
            print(f"[SETTINGS CTK FALLBACK] {exc}", flush=True)
            with settings_window_lock:
                # Keep True so TK fallback can run and reset itself.
                settings_window_open = True
    except Exception as exc:
        print(f"[SETTINGS CTK IMPORT FALLBACK] {exc}", flush=True)

    _open_settings_window_tk()


def _open_settings_window_tk() -> None:
    global settings_window_open

    window = tk.Tk()
    window.title("MCP Manager — Settings")
    window.geometry("860x700")
    window.minsize(720, 540)
    window.resizable(True, True)
    _apply_modern_theme(window)
    # Bring to front so it never hides behind other apps.
    try:
        window.lift()
        window.attributes("-topmost", True)
        window.after(600, lambda: window.attributes("-topmost", False))
        window.focus_force()
    except Exception:
        pass

    # Scrollable content so 4 server cards + folder editor fit.
    canvas = tk.Canvas(window, highlightthickness=0)
    scrollbar = ttk.Scrollbar(
        window, orient="vertical", command=canvas.yview
    )
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    frame = ttk.Frame(canvas, padding=14)
    frame_id = canvas.create_window((0, 0), window=frame, anchor="nw")

    def _on_frame_config(_e=None):
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _on_canvas_config(event):
        canvas.itemconfig(frame_id, width=event.width)

    frame.bind("<Configure>", _on_frame_config)
    canvas.bind("<Configure>", _on_canvas_config)

    def _on_mousewheel(event):
        try:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass

    canvas.bind_all("<MouseWheel>", _on_mousewheel)

    # --------------------------------------------------------
    # APPLICATION
    # --------------------------------------------------------

    ttk.Label(frame, text="Application", font=("Segoe UI", 11, "bold")).pack(
        anchor="w"
    )
    app_row = ttk.Frame(frame)
    app_row.pack(fill="x", pady=(2, 0))
    startup_state = tk.StringVar()
    startup_cmd_preview = tk.StringVar()

    def _refresh_startup_row():
        try:
            on = startup_enabled()
            cmd = get_launch_command()
            startup_state.set(
                "Run at Windows login: ON" if on else "Run at Windows login: OFF"
            )
            # Keep preview short for the UI.
            preview = cmd if len(cmd) <= 90 else cmd[:87] + "…"
            startup_cmd_preview.set(f"Starts: {preview}")
        except Exception:
            startup_state.set("Run at Windows login: unknown")
            startup_cmd_preview.set("")

    _refresh_startup_row()
    ttk.Label(app_row, textvariable=startup_state).pack(
        side="left", anchor="w"
    )
    startup_btn = ttk.Button(
        app_row,
        text="Turn OFF" if startup_enabled() else "Turn ON",
        width=10,
    )
    startup_btn.pack(side="right")

    def _on_startup_btn():
        try:
            ok, msg = set_startup_enabled(not startup_enabled())
            if not ok:
                messagebox.showerror(
                    "Startup setting", msg, parent=window
                )
            _refresh_startup_row()
            try:
                startup_btn.configure(
                    text="Turn OFF" if startup_enabled() else "Turn ON"
                )
            except Exception:
                pass
            refresh()
        except Exception as exc:
            messagebox.showerror(
                "Startup setting", str(exc), parent=window
            )

    startup_btn.configure(command=_on_startup_btn)
    ttk.Label(
        frame, textvariable=startup_cmd_preview, foreground="#666666",
        wraplength=760, justify="left", font=("Segoe UI", 8),
    ).pack(anchor="w")

    # --------------------------------------------------------
    # TUNNEL
    # --------------------------------------------------------

    ttk.Label(
        frame,
        text="Tunnels — one process per server",
        font=("Segoe UI", 11, "bold"),
    ).pack(anchor="w", pady=(12, 2))

    overall = tunnel.overall_status()
    ttk.Label(
        frame,
        text=(
            f"Overall: {label(overall)}  •  "
            "Each server needs its OWN tunnel ID. "
            "Tip: after Save, use tray → Restart for just that server."
        ),
        wraplength=740,
        justify="left",
        foreground="#555555",
    ).pack(anchor="w", pady=(0, 4))

    tunnel_enabled = tk.BooleanVar(
        value=settings.tunnel_enabled
    )

    ttk.Checkbutton(
        frame,
        text="Enable all tunnels (master switch)",
        variable=tunnel_enabled,
    ).pack(anchor="w")

    fields = ttk.Frame(frame)

    fields.pack(
        fill="x",
        pady=4,
    )

    fields.columnconfigure(
        1,
        weight=1,
    )

    ttk.Label(
        fields,
        text="Runtime API key environment variable",
    ).grid(
        row=0,
        column=0,
        sticky="w",
        padx=(0, 8),
        pady=2,
    )

    api_env = tk.StringVar(
        value=settings.api_key_env
    )

    ttk.Entry(
        fields,
        textvariable=api_env,
    ).grid(
        row=0,
        column=1,
        sticky="ew",
        pady=2,
    )

    ttk.Label(
        fields,
        text="Overall status",
    ).grid(
        row=1,
        column=0,
        sticky="w",
        padx=(0, 8),
        pady=2,
    )

    overall_var = tk.StringVar(value=label(tunnel.overall_status()))
    ttk.Label(fields, textvariable=overall_var).grid(
        row=1, column=1, sticky="w", pady=2
    )

    # --------------------------------------------------------
    # MCP SERVERS
    # --------------------------------------------------------

    ttk.Label(
        frame,
        text="MCP Servers",
        font=(
            "Segoe UI",
            10,
            "bold",
        ),
    ).pack(
        anchor="w",
        pady=(
            14,
            4,
        ),
    )

    server_container = ttk.Frame(
        frame,
    )

    server_container.pack(
        fill="both",
        expand=True,
    )

    STATUS_BG = {
        "connected": "#e6f4ea",
        "running": "#e6f4ea",
        "starting": "#fef7e0",
        "stopped": "#f1f3f4",
        "failed": "#fce8e6",
        "needs-config": "#fef7e0",
        "disabled": "#f1f3f4",
    }

    enabled_vars: dict[str, tk.BooleanVar] = {}
    auto_vars: dict[str, tk.BooleanVar] = {}
    tunnel_id_vars: dict[str, tk.StringVar] = {}
    health_vars: dict[str, tk.StringVar] = {}
    folder_boxes: dict[str, tk.Listbox] = {}
    pill_labels: dict[str, tk.Label] = {}
    card_frames: dict[str, ttk.LabelFrame] = {}

    for server_id, server in registry.servers.items():
        server_settings = settings.servers[server_id]
        tstatus = tunnel.status_for(server_id)
        bg = STATUS_BG.get(tstatus, "#f1f3f4")

        box = ttk.LabelFrame(
            server_container,
            text=f"  {server.display_name}  •  {label(tstatus)}  ",
            padding=10,
        )
        box.pack(fill="x", pady=6)
        box.columnconfigure(1, weight=1)

        # Top row: toggles + status pill + quick actions.
        top = ttk.Frame(box)
        top.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        enabled = tk.BooleanVar(value=server_settings.enabled)
        auto = tk.BooleanVar(value=server_settings.autostart)
        enabled_vars[server_id] = enabled
        auto_vars[server_id] = auto
        ttk.Checkbutton(top, text="Enabled", variable=enabled).pack(
            side="left"
        )
        ttk.Checkbutton(
            top, text="Auto-start", variable=auto
        ).pack(side="left", padx=(10, 0))
        pill = tk.Label(
            top,
            text=f" {STATUS_DOT.get(tstatus,'○')} {label(tstatus)} ",
            bg=bg,
            relief="groove",
            borderwidth=1,
            font=("Segoe UI", 8, "bold"),
        )
        pill.pack(side="right", padx=(6, 0))
        pill_labels[server_id] = pill
        card_frames[server_id] = box

        # Tunnel ID + health.
        tid = tk.StringVar(
            value=effective_tunnel_id(settings, server_id)
        )
        haddr = tk.StringVar(
            value=effective_health_addr(settings, server_id)
        )
        tunnel_id_vars[server_id] = tid
        health_vars[server_id] = haddr

        ttk.Label(box, text="Tunnel ID").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=2
        )
        tid_entry = ttk.Entry(box, textvariable=tid, width=50)
        tid_entry.grid(row=1, column=1, columnspan=2, sticky="ew", pady=2)

        ttk.Label(box, text="Health").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=2
        )
        health_row = ttk.Frame(box)
        health_row.grid(row=2, column=1, columnspan=2, sticky="ew", pady=2)
        health_row.columnconfigure(0, weight=1)
        ttk.Entry(health_row, textvariable=haddr).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(
            health_row,
            text="Copy URL",
            width=10,
            command=lambda sid=server_id: copy_health_url(sid),
        ).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(
            health_row,
            text="Log",
            width=6,
            command=lambda sid=server_id: open_log_file(sid),
        ).grid(row=0, column=2, padx=(6, 0))

        # Filesystem folder picker.
        if server_id == "filesystem":
            ttk.Label(
                box,
                text=(
                    "Folders the AI can read/write. "
                    "Fewer + narrower is safer."
                ),
                foreground="#555555",
                wraplength=700,
                justify="left",
            ).grid(
                row=3, column=0, columnspan=3, sticky="w", pady=(6, 2)
            )
            list_frame = ttk.Frame(box)
            list_frame.grid(
                row=4, column=0, columnspan=3, sticky="ew", pady=2
            )
            list_frame.columnconfigure(0, weight=1)
            lb = tk.Listbox(
                list_frame, height=4, selectmode="extended"
            )
            lb.grid(row=0, column=0, sticky="ew")
            lb_scroll = ttk.Scrollbar(
                list_frame, orient="vertical", command=lb.yview
            )
            lb.configure(yscrollcommand=lb_scroll.set)
            lb_scroll.grid(row=0, column=1, sticky="ns")
            for folder in effective_filesystem_roots(settings):
                lb.insert("end", folder)
            folder_boxes[server_id] = lb

            btn_row = ttk.Frame(box)
            btn_row.grid(row=5, column=0, columnspan=3, sticky="w", pady=4)

            def _fs_add(sid=server_id):
                picked = filedialog.askdirectory(
                    parent=window,
                    title="Choose a folder for Filesystem MCP",
                )
                if picked:
                    box_lb = folder_boxes[sid]
                    existing = {
                        box_lb.get(i).lower()
                        for i in range(box_lb.size())
                    }
                    if picked.lower() not in existing:
                        box_lb.insert("end", picked)

            def _fs_remove(sid=server_id):
                box_lb = folder_boxes[sid]
                for idx in reversed(box_lb.curselection()):
                    box_lb.delete(idx)

            def _fs_reset(sid=server_id):
                box_lb = folder_boxes[sid]
                box_lb.delete(0, "end")
                for folder in default_filesystem_dirs():
                    box_lb.insert("end", folder)

            ttk.Button(btn_row, text="Add folder…", command=_fs_add).pack(
                side="left"
            )
            ttk.Button(
                btn_row, text="Remove selected", command=_fs_remove
            ).pack(side="left", padx=(6, 0))
            ttk.Button(
                btn_row, text="Reset", command=_fs_reset
            ).pack(side="left", padx=(6, 0))

            if os.environ.get("FILESYSTEM_MCP_COMMAND", "").strip():
                ttk.Label(
                    box,
                    text=(
                        "Note: FILESYSTEM_MCP_COMMAND env override is "
                        "active — folder list is ignored until you unset it."
                    ),
                    foreground="#b06000",
                    wraplength=700,
                    justify="left",
                ).grid(
                    row=6, column=0, columnspan=3, sticky="w", pady=2
                )

    # --------------------------------------------------------
    # SAVE + VALIDATION
    # --------------------------------------------------------

    def _collect_folders() -> list[str]:
        lb = folder_boxes.get("filesystem")
        if lb is None:
            return effective_filesystem_roots(settings)
        return [lb.get(i) for i in range(lb.size())]

    def save_and_close(restart_all: bool = False):
        settings.tunnel_enabled = tunnel_enabled.get()
        settings.api_key_env = (
            api_env.get().strip() or "CONTROL_PLANE_API_KEY"
        )

        new_tids: dict[str, str] = {}
        new_health: dict[str, str] = {}
        for server_id in registry.servers:
            new_tids[server_id] = tunnel_id_vars[server_id].get().strip()
            new_health[server_id] = (
                health_vars[server_id].get().strip()
                or effective_health_addr(settings, server_id)
            )

        # UX validation before touching disk.
        problems: list[str] = []
        # Distinct tunnel IDs (ignore empty — those just won't start).
        seen_tid: dict[str, str] = {}
        for sid, tid in new_tids.items():
            if not tid:
                continue
            if tid in seen_tid:
                problems.append(
                    f"Duplicate tunnel ID for {sid} and "
                    f"{seen_tid[tid]}. Each server needs its OWN ID."
                )
            else:
                seen_tid[tid] = sid
        # Distinct health ports.
        seen_hp: dict[str, str] = {}
        for sid, hp in new_health.items():
            key = hp.lower()
            if key in seen_hp:
                problems.append(
                    f"Duplicate health address {hp} for {sid} and "
                    f"{seen_hp[key]}. Use 8080/8081/8082/8083."
                )
            else:
                seen_hp[key] = sid
        # Filesystem folders.
        folders = _collect_folders()
        if "filesystem" in registry.servers and (
            settings.servers["filesystem"].enabled
            or enabled_vars["filesystem"].get()
        ):
            if not folders:
                problems.append(
                    "Filesystem MCP has no folders. Add at least one."
                )
            else:
                missing = [f for f in folders if not Path(f).exists()]
                if missing:
                    problems.append(
                        "Folder(s) not found:\n"
                        + "\n".join(f"• {m}" for m in missing[:4])
                    )
        if problems:
            messagebox.showerror(
                "Fix before saving",
                "\n\n".join(problems),
                parent=window,
            )
            return

        for server_id in registry.servers:
            settings.servers[server_id].enabled = enabled_vars[
                server_id
            ].get()
            settings.servers[server_id].autostart = auto_vars[
                server_id
            ].get()
            settings.servers[server_id].tunnel_id = new_tids[server_id]
            settings.servers[server_id].health_addr = new_health[
                server_id
            ]
        if "filesystem" in folder_boxes:
            settings.servers["filesystem"].allowed_dirs = folders

        obsidian_tid = settings.servers.get("obsidian")
        if obsidian_tid is not None:
            settings.tunnel_id = obsidian_tid.tunnel_id.strip()
        update_dotenv({"CONTROL_PLANE_TUNNEL_ID": settings.tunnel_id})
        load_dotenv()
        save_settings(settings)
        try:
            registry.refresh_from_settings()
        except Exception:
            pass

        if restart_all:
            window.destroy()
            action(tunnel.restart)
            return
        window.destroy()
        refresh()

    btn_bar = ttk.Frame(frame)
    btn_bar.pack(fill="x", pady=14)
    ttk.Button(
        btn_bar, text="Open logs folder", command=open_logs_folder
    ).pack(side="left")
    hint = ttk.Label(
        btn_bar,
        text="Ctrl+S save • Esc close • status refreshes live",
        foreground="#666666",
        font=("Segoe UI", 8),
    )
    hint.pack(side="left", padx=(12, 0))
    ttk.Button(
        btn_bar,
        text="Save",
        style="Accent.TButton",
        command=lambda: save_and_close(False),
    ).pack(side="right")
    ttk.Button(
        btn_bar,
        text="Save & restart all",
        command=lambda: save_and_close(True),
    ).pack(side="right", padx=(0, 8))
    ttk.Button(btn_bar, text="Cancel", command=lambda: _on_close()).pack(
        side="right", padx=(0, 8)
    )

    def _live_refresh():
        try:
            if not window.winfo_exists():
                return
            overall_var.set(label(tunnel.overall_status()))
            for sid, pill_w in pill_labels.items():
                try:
                    st = tunnel.status_for(sid)
                    pill_w.configure(
                        text=f" {STATUS_DOT.get(st,'○')} {label(st)} ",
                        bg=STATUS_BG.get(st, "#f1f3f4"),
                    )
                    card = card_frames.get(sid)
                    if card is not None:
                        try:
                            srv = registry.servers[sid]
                            card.configure(
                                text=f"  {srv.display_name}  •  {label(st)}  "
                            )
                        except Exception:
                            pass
                except Exception:
                    pass
            window.after(2000, _live_refresh)
        except Exception:
            pass

    def _on_close():
        try:
            canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        try:
            window.unbind_all("<Control-s>")
            window.unbind_all("<Escape>")
        except Exception:
            pass
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", _on_close)
    try:
        window.bind_all("<Control-s>", lambda _e: save_and_close(False))
        window.bind_all("<Control-S>", lambda _e: save_and_close(False))
        window.bind_all("<Escape>", lambda _e: _on_close())
    except Exception:
        pass

    try:
        window.after(2000, _live_refresh)
        window.mainloop()
    finally:
        try:
            canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        with settings_window_lock:
            settings_window_open = False


def open_settings(
    _icon=None,
    _item=None,
):
    global settings_window_open

    with settings_window_lock:
        if settings_window_open:
            return

        settings_window_open = True

    threading.Thread(
        target=_open_settings_window,
        name="MCP-Manager-Settings",
        daemon=True,
    ).start()


# ------------------------------------------------------------
# QUIT
# ------------------------------------------------------------

def quit_app(
    _icon=None,
    _item=None,
):
    try:
        tunnel.stop()
    finally:
        release_single_instance()
        icon.stop()


# ------------------------------------------------------------
# MONITOR
# ------------------------------------------------------------

_prev_statuses: dict[str, str] = {}
_last_uptime_tick: float = 0.0


def monitor():
    global _last_uptime_tick
    _last_uptime_tick = time.monotonic()
    while True:
        try:
            refresh()
        except Exception:
            pass

        # Crash history + uptime + compressed call log.
        try:
            import app_health as _ah

            now = time.monotonic()
            elapsed = now - _last_uptime_tick
            _last_uptime_tick = now
            for sid in list(registry.servers.keys()):
                try:
                    cur = tunnel.status_for(sid)
                except Exception:
                    cur = "stopped"
                prev = _prev_statuses.get(sid, cur)
                if cur != prev:
                    if cur == "failed":
                        _ah.record_event(sid, "crash",
                                         tunnel.error_for(sid))
                    elif cur == "connected" and prev in (
                        "starting", "failed", "stopped",
                    ):
                        _ah.record_event(sid, "connected")
                        # Reset heal counter display via stats only.
                    elif cur == "starting" and prev == "stopped":
                        _ah.record_event(sid, "start")
                    elif cur == "stopped" and prev in (
                        "connected", "starting", "failed",
                    ):
                        _ah.record_event(sid, "stop")
                    _prev_statuses[sid] = cur
                elif cur == "connected":
                    _prev_statuses[sid] = cur
                else:
                    _prev_statuses.setdefault(sid, cur)
                if cur == "connected":
                    _ah.record_uptime(sid, elapsed)
            try:
                _ah.tail_tunnel_logs_for_calls(ROOT)
            except Exception:
                pass
        except Exception:
            pass

        threading.Event().wait(2.5)


# ------------------------------------------------------------
# TRAY MENU
# ------------------------------------------------------------

def _overall_header(_item=None) -> str:
    try:
        return f"MCP Manager — {label(tunnel.overall_status())}"
    except Exception:
        return "MCP Manager"


def _copy_all_health_urls(_icon=None, _item=None):
    lines = []
    for sid in registry.servers:
        lines.append(
            f"{sid}: http://"
            + effective_health_addr(settings, sid)
            + "/readyz"
        )
    copy_text_to_clipboard("\n".join(lines))


def _build_server_submenu(
    server_id: str,
) -> Menu:
    def _status_text(_item=None, sid=server_id) -> str:
        try:
            tstatus = tunnel.status_for(sid)
            err = tunnel.error_for(sid)
            base = f"{label(tstatus)}"
            if err and tstatus in ("failed", "needs-config"):
                short = err.split("\n")[0][:60]
                base += f" — {short}"
            return base
        except Exception:
            return "Status unavailable"

    def _on_start(_i=None, sid=server_id):
        start_server(sid)

    def _on_stop(_i=None, sid=server_id):
        stop_server(sid)

    def _on_restart(_i=None, sid=server_id):
        restart_server(sid)

    def _on_log(_i=None, sid=server_id):
        open_log_file(sid)

    def _on_copy(_i=None, sid=server_id):
        copy_health_url(sid)

    items: list[MenuItem] = [
        MenuItem(_status_text, None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("▶  Start", _on_start),
        MenuItem("■  Stop", _on_stop),
        MenuItem("↻  Restart", _on_restart),
        Menu.SEPARATOR,
        MenuItem("Copy health URL", _on_copy),
        MenuItem("Open log", _on_log),
    ]
    # DX: filesystem shows folder count in submenu.
    if server_id == "filesystem":
        def _folders_text(_item=None) -> str:
            try:
                roots = effective_filesystem_roots(settings)
                if not roots:
                    return "Folders: none selected"
                shown = "; ".join(roots[:2])
                extra = (
                    f" (+{len(roots)-2} more)"
                    if len(roots) > 2
                    else ""
                )
                return f"Folders ({len(roots)}): {shown}{extra}"[:90]
            except Exception:
                return "Folders: unavailable"

        items.insert(1, MenuItem(_folders_text, None, enabled=False))
    return Menu(*items)


def _server_label(server_id: str):
    def _get(_item=None, sid=server_id) -> str:
        return _server_short_label(sid)

    return _get


server_menu = Menu(
    *(
        MenuItem(
            _server_label(server_id),
            _build_server_submenu(server_id),
        )
        for server_id, server in registry.servers.items()
    )
)

all_menu = Menu(
    MenuItem("▶  Start autostart tunnels", start_all),
    MenuItem("↻  Restart all tunnels", restart_all),
    MenuItem("■  Stop all tunnels", stop_all),
)

def _restart_tray_plain(_icon=None, _item=None):
    restart_tray_app(dev=False)


def _restart_tray_dev(_icon=None, _item=None):
    restart_tray_app(dev=True)


def _open_doctor(_icon=None, _item=None):
    try:
        import app_health as _ah

        results = _ah.run_doctor(settings, registry, tunnel)
        bad = [r for r in results if not r.get("ok")]
        try:
            from settings_ui_ctk import show_doctor_window

            show_doctor_window(results, parent=None)
        except Exception:
            # Fallback if CustomTkinter is missing: short popup.
            lines = []
            for r in results[:14]:
                mark = "✓" if r.get("ok") else "✖"
                lines.append(f"{mark} {r.get('title','')}")
            try:
                messagebox.showinfo(
                    f"Doctor — {len(bad)} issue(s)",
                    "\n".join(lines)[:1500] or "All checks passed.",
                )
            except Exception:
                pass
        if bad:
            try:
                tray_notify(
                    "Doctor found issues",
                    "; ".join(
                        r.get("title", "") for r in bad[:3]
                    )[:200],
                )
            except Exception:
                pass
    except Exception as exc:
        try:
            messagebox.showerror("Doctor failed", str(exc))
        except Exception:
            pass


def _check_updates_now(_icon=None, _item=None):
    try:
        import app_health as _ah

        res = _ah.check_updates(
            getattr(settings, "filesystem_package", ""), force=True
        )
        if res.get("filesystem_update"):
            msg = (
                f"Filesystem update available:\n{res.get('filesystem_latest')}"
                f"\nCurrent: {res.get('filesystem_current')}"
                "\n\nChange it in Settings → Tunnels."
            )
            try:
                messagebox.showinfo("Updates", msg)
            except Exception:
                pass
            try:
                tray_notify("Filesystem update", str(
                    res.get("filesystem_latest", ""))[:150])
            except Exception:
                pass
        else:
            try:
                messagebox.showinfo(
                    "Updates",
                    "All pinned packages look current.\n"
                    f"Checked: {res.get('filesystem_latest') or 'n/a'}",
                )
            except Exception:
                pass
    except Exception as exc:
        try:
            messagebox.showerror("Update check failed", str(exc))
        except Exception:
            pass


tools_menu = Menu(
    MenuItem("⚙  Settings…", open_settings),
    MenuItem("🩺  Doctor — check setup", _open_doctor),
    MenuItem("⬆  Check for updates", _check_updates_now),
    MenuItem("🗂  Open logs folder", open_logs_folder),
    MenuItem("📁  Open project folder", open_project_folder),
    MenuItem("📋  Copy all health URLs", _copy_all_health_urls),
    Menu.SEPARATOR,
    MenuItem(
        "↻  Restart tray (pick up code changes)",
        _restart_tray_plain,
    ),
    MenuItem(
        lambda _item=None: (
            "✓ Dev auto-reload: ON"
            if "--dev" in sys.argv
            else "Dev auto-reload: OFF"
        ),
        _restart_tray_dev,
    ),
)


icon = Icon(
    "MCP Manager",
    create_icon(),
    menu=Menu(
        MenuItem(_overall_header, None, enabled=False),
        MenuItem(
            lambda _item=None: tooltip().replace("\n", "  •  ")[:120],
            None,
            enabled=False,
        ),
        Menu.SEPARATOR,
        MenuItem("MCP Servers", server_menu),
        MenuItem("All tunnels", all_menu),
        MenuItem("Tools", tools_menu),
        MenuItem(
            lambda _item=None: (
                "☑ Start with Windows"
                if startup_enabled()
                else "☐ Start with Windows"
            ),
            toggle_startup,
        ),
        Menu.SEPARATOR,
        MenuItem("Exit", quit_app),
    ),
)


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

if __name__ == "__main__":
    want_restart = "--restart" in sys.argv
    want_dev = "--dev" in sys.argv

    if not acquire_single_instance(auto_replace=want_restart):
        sys.exit(0)

    try:
        # TunnelManager decides which enabled/autostart
        # MCPs are actually launched.
        start_all()

        threading.Thread(
            target=monitor,
            daemon=True,
            name="MCP-Manager-Monitor",
        ).start()

        if want_dev:
            threading.Thread(
                target=_dev_watch_loop,
                daemon=True,
                name="MCP-Manager-DevWatch",
            ).start()

        icon.run()

    except Exception:
        try:
            from tunnel_manager import rotate_log as _rotate

            _rotate(ROOT / "launcher_error.txt")
        except Exception:
            pass
        with open(
            ROOT / "launcher_error.txt",
            "a",
            encoding="utf-8",
        ) as file:
            import traceback

            traceback.print_exc(
                file=file
            )

    finally:
        release_single_instance()