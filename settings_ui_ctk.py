"""Modern settings UI built on CustomTkinter (dark, card layout).

General: any number of MCP servers. Add / duplicate / delete.
Fallback: main.pyw keeps the old Tkinter window if this module
or customtkinter can't be imported.
"""

from __future__ import annotations

import copy
from pathlib import Path


STATUS_COLORS = {
    "connected": ("#1a7f37", "#e6f4ea"),
    "running": ("#1a7f37", "#e6f4ea"),
    "starting": ("#9a6700", "#fef7e0"),
    "stopped": ("#5f6368", "#f1f3f4"),
    "failed": ("#b3261e", "#fce8e6"),
    "needs-config": ("#9a6700", "#fef7e0"),
    "disabled": ("#5f6368", "#f1f3f4"),
}

STATUS_DOT = {
    "connected": "●",
    "running": "●",
    "starting": "◐",
    "stopped": "○",
    "failed": "✖",
    "needs-config": "⚠",
    "disabled": "○",
}


def _label(status: str) -> str:
    return {
        "running": "Running",
        "starting": "Starting",
        "failed": "Failed",
        "connected": "Connected",
        "disabled": "Disabled",
        "stopped": "Stopped",
        "needs-config": "Needs tunnel ID",
    }.get(status, str(status).title())


def show_doctor_window(results: list[dict], parent=None) -> None:
    """Closable, scrollable doctor results. Never traps focus.

    Why not messagebox: long output gets truncated, no scroll, no
    copy button, and with a topmost parent it can hide behind and
    look unclosable. This window always has X + Esc + Close.
    """
    import customtkinter as ctk

    bad = [r for r in results if not r.get("ok")]
    if parent is not None:
        try:
            dlg = ctk.CTkToplevel(parent)
        except Exception:
            dlg = ctk.CTk()
    else:
        dlg = ctk.CTk()
    try:
        dlg.title(
            f"Doctor — {len(results)-len(bad)}/{len(results)} ok"
        )
    except Exception:
        pass
    try:
        dlg.geometry("620x520")
        dlg.minsize(480, 360)
    except Exception:
        pass

    closed = {"v": False}

    def _close():
        if closed["v"]:
            return
        closed["v"] = True
        try:
            dlg.grab_release()
        except Exception:
            pass
        try:
            dlg.destroy()
        except Exception:
            pass

    try:
        dlg.protocol("WM_DELETE_WINDOW", _close)
    except Exception:
        pass
    try:
        dlg.bind("<Escape>", lambda _e: _close())
    except Exception:
        pass

    # Header you can read at a glance.
    try:
        head = ctk.CTkFrame(dlg, corner_radius=10)
        head.pack(fill="x", padx=12, pady=(12, 6))
        summary = (
            f"{len(results)-len(bad)}/{len(results)} checks passed"
            + (f" — {len(bad)} need attention" if bad else " — all good")
        )
        ctk.CTkLabel(
            head, text="Doctor results", font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=12, pady=(8, 0))
        ctk.CTkLabel(head, text=summary).pack(
            anchor="w", padx=12, pady=(0, 8)
        )
    except Exception:
        pass

    # Scrollable rows: green ok, red issue with detail.
    try:
        body = ctk.CTkScrollableFrame(dlg, corner_radius=10)
        body.pack(fill="both", expand=True, padx=12, pady=6)
        if not results:
            ctk.CTkLabel(body, text="No checks ran.").pack(
                anchor="w", padx=8, pady=4
            )
        for r in results:
            ok = bool(r.get("ok"))
            mark = "✓" if ok else "✖"
            color = "#1a7f37" if ok else "#b3261e"
            row = ctk.CTkFrame(body, corner_radius=8)
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(
                row, text=mark, text_color=color,
                font=("Segoe UI", 13, "bold"), width=28,
            ).pack(side="left", padx=(8, 2), pady=6)
            txt = ctk.CTkFrame(row, fg_color="transparent")
            txt.pack(side="left", fill="x", expand=True, padx=(0, 8))
            ctk.CTkLabel(
                txt, text=str(r.get("title", ""))[:140],
                anchor="w", justify="left",
            ).pack(anchor="w")
            detail = str(r.get("detail", "") or "")[:220]
            if detail and not ok:
                ctk.CTkLabel(
                    txt, text=detail, anchor="w", justify="left",
                    font=("Segoe UI", 10),
                ).pack(anchor="w")
    except Exception:
        pass

    # Footer: Copy + Close. Both always work.
    try:
        foot = ctk.CTkFrame(dlg, fg_color="transparent")
        foot.pack(fill="x", padx=12, pady=(6, 12))

        def _copy():
            try:
                lines = []
                for r in results:
                    mark = "PASS" if r.get("ok") else "FAIL"
                    lines.append(f"[{mark}] {r.get('title','')}")
                    if r.get("detail") and not r.get("ok"):
                        lines.append(f"  {r['detail'][:200]}")
                text = "\n".join(lines)
                dlg.clipboard_clear()
                dlg.clipboard_append(text)
                try:
                    _copy_btn.configure(text="Copied ✓")
                    dlg.after(
                        1200,
                        lambda: _copy_btn.configure(text="Copy results"),
                    )
                except Exception:
                    pass
            except Exception:
                pass

        _copy_btn = ctk.CTkButton(foot, text="Copy results",
                                  command=_copy)
        _copy_btn.pack(side="left")
        ctk.CTkButton(foot, text="Close", command=_close).pack(
            side="right"
        )
    except Exception:
        pass

    try:
        # Brief topmost so it never opens hidden behind tray,
        # then normal so it can't trap focus.
        dlg.lift()
        dlg.attributes("-topmost", True)
        dlg.after(800, lambda: dlg.attributes("-topmost", False))
        dlg.focus_force()
    except Exception:
        pass
    try:
        dlg.bind("<Return>", lambda _e: _close())
    except Exception:
        pass


def _short(text: str, limit: int = 90) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def open_settings_ctk(
    settings,
    registry,
    tunnel,
    helpers,
) -> None:
    import customtkinter as ctk
    from tkinter import filedialog, messagebox

    try:
        from mcp_registry import describe_server
    except Exception:
        describe_server = lambda *_a, **_k: {  # noqa: E731
            "command_str": "", "command_source": "",
            "cwd_str": "", "url_str": "", "url_source": "",
        }

    from mcp_manager_settings import (
        make_server_id,
        next_free_health_addr,
    )

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    eff_tid = helpers["effective_tunnel_id"]
    eff_health = helpers["effective_health_addr"]
    eff_roots = helpers["effective_filesystem_roots"]
    default_roots = helpers["default_filesystem_dirs"]

    # Working copy so Add/Delete only persist on Save.
    pending: dict = {
        sid: copy.deepcopy(cfg)
        for sid, cfg in settings.servers.items()
    }
    order: list[str] = list(registry.servers.keys())
    for sid in pending:
        if sid not in order:
            order.append(sid)

    win = ctk.CTk()
    win.title("MCP Manager — Settings")
    win.geometry("920x740")
    win.minsize(780, 580)

    try:
        win.lift()
        win.attributes("-topmost", True)
        win.after(600, lambda: win.attributes("-topmost", False))
        win.focus_force()
    except Exception:
        pass

    # Header --------------------------------------------------
    header = ctk.CTkFrame(win, corner_radius=12)
    header.pack(fill="x", padx=14, pady=(14, 8))
    ctk.CTkLabel(
        header, text="MCP Manager", font=("Segoe UI", 18, "bold")
    ).pack(side="left", padx=14, pady=10)
    overall_var = ctk.StringVar(
        value=f"Overall: {_label(tunnel.overall_status())}"
    )
    ctk.CTkLabel(header, textvariable=overall_var).pack(
        side="right", padx=14
    )
    rt_ver = helpers.get("runtime_version", "")
    if rt_ver:
        ctk.CTkLabel(
            header, text=f"runtime {rt_ver}"[:60], font=("Segoe UI", 9)
        ).pack(side="right", padx=(0, 4))

    scroll = ctk.CTkScrollableFrame(win, corner_radius=12)
    scroll.pack(fill="both", expand=True, padx=14, pady=8)

    # App / startup card --------------------------------------
    app_card = ctk.CTkFrame(scroll, corner_radius=12)
    app_card.pack(fill="x", pady=6)
    ctk.CTkLabel(
        app_card, text="Launch at login", font=("Segoe UI", 13, "bold")
    ).pack(anchor="w", padx=14, pady=(10, 2))
    startup_var = ctk.StringVar()
    cmd_var = ctk.StringVar()

    def _refresh_startup():
        try:
            on = helpers["startup_enabled"]()
            cmd = helpers["get_launch_command"]()
            startup_var.set(
                "Run at Windows login: ON"
                if on
                else "Run at Windows login: OFF"
            )
            cmd_var.set(
                f"Starts: {cmd[:100] + '…' if len(cmd) > 100 else cmd}"
            )
            btn.configure(text="Turn OFF" if on else "Turn ON")
        except Exception:
            startup_var.set("Run at Windows login: unknown")

    row = ctk.CTkFrame(app_card, fg_color="transparent")
    row.pack(fill="x", padx=14, pady=4)
    ctk.CTkLabel(row, textvariable=startup_var).pack(side="left")

    def _toggle_startup():
        try:
            ok, msg = helpers["set_startup_enabled"](
                not helpers["startup_enabled"]()
            )
            if not ok:
                messagebox.showerror("Startup", msg, parent=win)
            _refresh_startup()
            helpers["refresh"]()
        except Exception as exc:
            messagebox.showerror("Startup", str(exc), parent=win)

    btn = ctk.CTkButton(row, text="Turn ON", width=100,
                        command=_toggle_startup)
    btn.pack(side="right")
    ctk.CTkLabel(app_card, textvariable=cmd_var,
                 font=("Segoe UI", 10)).pack(
        anchor="w", padx=14, pady=(0, 10)
    )
    _refresh_startup()

    # Tunnels card --------------------------------------------
    tun_card = ctk.CTkFrame(scroll, corner_radius=12)
    tun_card.pack(fill="x", pady=6)
    ctk.CTkLabel(
        tun_card, text="Tunnels", font=("Segoe UI", 13, "bold")
    ).pack(anchor="w", padx=14, pady=(10, 2))
    ctk.CTkLabel(
        tun_card,
        text="One process per server. Each server needs its OWN tunnel ID.",
        font=("Segoe UI", 11),
    ).pack(anchor="w", padx=14)

    master_var = ctk.BooleanVar(value=settings.tunnel_enabled)
    ctk.CTkSwitch(
        tun_card, text="Enable all tunnels (master switch)",
        variable=master_var,
    ).pack(anchor="w", padx=14, pady=6)

    api_var = ctk.StringVar(value=settings.api_key_env)
    ctk.CTkLabel(tun_card, text="API key env var").pack(
        anchor="w", padx=14
    )
    ctk.CTkEntry(tun_card, textvariable=api_var).pack(
        fill="x", padx=14, pady=(0, 4)
    )

    pkg_var = ctk.StringVar(
        value=getattr(
            settings, "filesystem_package",
            "@modelcontextprotocol/server-filesystem@2026.8.31",
        )
    )
    ctk.CTkLabel(tun_card, text="Filesystem npm package (pinned)").pack(
        anchor="w", padx=14
    )
    ctk.CTkEntry(tun_card, textvariable=pkg_var).pack(
        fill="x", padx=14, pady=(0, 4)
    )

    heal_var = ctk.BooleanVar(
        value=bool(getattr(settings, "auto_heal", True))
    )
    ctk.CTkSwitch(
        tun_card, text="Auto-restart crashed tunnels (self-heal)",
        variable=heal_var,
    ).pack(anchor="w", padx=14, pady=4)

    # Health card: updates + uptime + doctor + recent calls ------
    try:
        import app_health as _ah
    except Exception:
        _ah = None

    health_card = ctk.CTkFrame(scroll, corner_radius=12)
    health_card.pack(fill="x", pady=6)
    ctk.CTkLabel(
        health_card, text="Health", font=("Segoe UI", 13, "bold")
    ).pack(anchor="w", padx=14, pady=(10, 2))

    upd_var = ctk.StringVar(value="Updates: click Check.")
    ctk.CTkLabel(health_card, textvariable=upd_var).pack(
        anchor="w", padx=14
    )

    def _check_updates_ui():
        if _ah is None:
            upd_var.set("Updates: checker unavailable.")
            return
        upd_var.set("Updates: checking…")
        try:
            res = _ah.check_updates(
                getattr(settings, "filesystem_package", ""),
                force=True,
            )
            if res.get("filesystem_update"):
                upd_var.set(
                    f"Update available: {res.get('filesystem_latest')}"
                )
            else:
                upd_var.set(
                    "All pinned packages current."
                    + (
                        f" ({res.get('filesystem_latest')})"
                        if res.get("filesystem_latest")
                        else ""
                    )
                )
        except Exception as exc:
            upd_var.set(f"Update check failed: {exc}"[:120])

    upt_var = ctk.StringVar(value="Uptime: …")
    ctk.CTkLabel(health_card, textvariable=upt_var).pack(
        anchor="w", padx=14
    )

    def _refresh_uptime_ui():
        if _ah is None:
            return
        parts = []
        for sid in list(registry.servers.keys())[:6]:
            try:
                s = _ah.uptime_summary(sid)
                parts.append(
                    f"{sid}: {s['uptime']}, {s['crashes']} crashes, "
                    f"{s['calls']} calls (~{s['in_tokens']//1000}k+"
                    f"{s['out_tokens']//1000}k tok)"
                )
            except Exception:
                pass
        upt_var.set("\n".join(parts)[:600] if parts else "Uptime: n/a")

    _refresh_uptime_ui()

    doc_var = ctk.StringVar(value="Doctor: not run yet.")
    ctk.CTkLabel(health_card, textvariable=doc_var).pack(
        anchor="w", padx=14
    )

    def _run_doctor_ui():
        if _ah is None:
            doc_var.set("Doctor unavailable.")
            return
        try:
            results = _ah.run_doctor(settings, registry, tunnel)
            bad = [r for r in results if not r.get("ok")]
            doc_var.set(
                f"Doctor: {len(results)-len(bad)}/{len(results)} ok"
                + (f", {len(bad)} issue(s)" if bad else " — all good")
            )
            show_doctor_window(results, parent=win)
        except Exception as exc:
            doc_var.set(f"Doctor failed: {exc}"[:120])

    calls_var = ctk.StringVar(value="Recent calls: …")
    ctk.CTkLabel(health_card, textvariable=calls_var).pack(
        anchor="w", padx=14
    )

    def _refresh_calls_ui():
        if _ah is None:
            return
        try:
            rows = _ah.read_toolcalls(limit=5)
            if not rows:
                calls_var.set("Recent calls: none yet.")
                return
            from datetime import datetime as _dt

            lines = []
            for r in rows[:5]:
                try:
                    ts = _dt.fromtimestamp(
                        int(r.get("t", 0))
                    ).strftime("%H:%M")
                except (TypeError, ValueError):
                    ts = "--:--"
                lines.append(
                    f"{ts} {r.get('s','?')} "
                    f"{str(r.get('q',''))[:12]}"
                )
            calls_var.set("Recent:\n" + "\n".join(lines))
        except Exception:
            pass

    _refresh_calls_ui()

    hrow = ctk.CTkFrame(health_card, fg_color="transparent")
    hrow.pack(fill="x", padx=14, pady=(4, 10))
    ctk.CTkButton(hrow, text="Check updates",
                  command=_check_updates_ui).pack(side="left")
    ctk.CTkButton(hrow, text="Run doctor",
                  command=_run_doctor_ui).pack(side="left", padx=(8, 0))
    ctk.CTkButton(
        hrow, text="Refresh stats",
        command=lambda: (_refresh_uptime_ui(), _refresh_calls_ui()),
    ).pack(side="left", padx=(8, 0))

    # Servers section -----------------------------------------
    sec_row = ctk.CTkFrame(scroll, fg_color="transparent")
    sec_row.pack(fill="x", pady=(8, 2))
    count_var = ctk.StringVar(value=f"Servers ({len(order)})")
    ctk.CTkLabel(
        sec_row, textvariable=count_var, font=("Segoe UI", 13, "bold")
    ).pack(side="left")

    cards_host = ctk.CTkFrame(scroll, fg_color="transparent")
    cards_host.pack(fill="x")

    # Per-card widget state, rebuilt on add/delete.
    ui_state: dict = {}

    def _add_server_dialog():
        dlg = ctk.CTkToplevel(win)
        dlg.title("Add MCP server")
        dlg.geometry("480x420")
        try:
            dlg.transient(win)
            dlg.grab_set()
        except Exception:
            pass
        ctk.CTkLabel(
            dlg, text="New MCP server", font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=16, pady=(14, 6))
        name_var = ctk.StringVar(value="My MCP")
        ctk.CTkLabel(dlg, text="Display name").pack(
            anchor="w", padx=16
        )
        ctk.CTkEntry(dlg, textvariable=name_var).pack(
            fill="x", padx=16
        )
        kind_var = ctk.StringVar(value="stdio")
        ctk.CTkLabel(dlg, text="Type").pack(anchor="w", padx=16, pady=(8, 0))
        ctk.CTkOptionMenu(
            dlg, values=["stdio", "external"], variable=kind_var
        ).pack(anchor="w", padx=16)
        cmd_var2 = ctk.StringVar(
            value="npx -y @modelcontextprotocol/server-filesystem"
        )
        url_var2 = ctk.StringVar(value="http://127.0.0.1:3000/mcp")
        ctk.CTkLabel(dlg, text="Command (stdio)").pack(
            anchor="w", padx=16, pady=(8, 0)
        )
        ctk.CTkEntry(dlg, textvariable=cmd_var2).pack(
            fill="x", padx=16
        )
        ctk.CTkLabel(dlg, text="Server URL (external)").pack(
            anchor="w", padx=16, pady=(8, 0)
        )
        ctk.CTkEntry(dlg, textvariable=url_var2).pack(
            fill="x", padx=16
        )

        def _create():
            name = name_var.get().strip() or "My MCP"
            sid = make_server_id(name, set(pending.keys()))
            kind = kind_var.get().strip().lower()
            if kind not in ("stdio", "external"):
                kind = "stdio"
            from mcp_manager_settings import ServerSettings

            # Distinct health port.
            used = set()
            for _sid in order:
                cfg = pending.get(_sid)
                if cfg is None:
                    continue
                try:
                    used.add(
                        int(cfg.health_addr.rsplit(":", 1)[1])
                    )
                except (ValueError, IndexError):
                    pass
            port = 8080
            while port in used:
                port += 1
            cfg = ServerSettings(
                enabled=True,
                autostart=False,
                channel=sid,
                color="#858b93",
                tunnel_id="",
                health_addr=f"127.0.0.1:{port}",
                display_name=name,
                transport=kind,
                command=cmd_var2.get().strip()
                if kind == "stdio"
                else "",
                working_dir="",
                server_url=url_var2.get().strip()
                if kind == "external"
                else "",
            )
            pending[sid] = cfg
            order.append(sid)
            try:
                dlg.destroy()
            except Exception:
                pass
            _rebuild_cards()

        ctk.CTkButton(dlg, text="Add server", command=_create).pack(
            padx=16, pady=16
        )

    ctk.CTkButton(sec_row, text="+ Add server",
                  command=_add_server_dialog).pack(side="right")

    def _rebuild_cards():
        for child in cards_host.winfo_children():
            child.destroy()
        ui_state.clear()
        count_var.set(f"Servers ({len(order)})")
        for server_id in list(order):
            cfg = pending.get(server_id)
            if cfg is None:
                continue
            try:
                server = registry.servers.get(server_id)
            except Exception:
                server = None
            disp_name = cfg.display_name or (
                server.display_name if server else server_id
            )
            card = ctk.CTkFrame(cards_host, corner_radius=12)
            card.pack(fill="x", pady=6)

            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=14, pady=(10, 2))
            name_var = ctk.StringVar(value=disp_name)
            ctk.CTkEntry(top, textvariable=name_var, width=220).pack(
                side="left"
            )
            try:
                st = tunnel.status_for(server_id)
            except Exception:
                st = "stopped"
            fg, _bg = STATUS_COLORS.get(st, ("#5f6368", "#f1f3f4"))
            pill = ctk.CTkLabel(
                top,
                text=f"{STATUS_DOT.get(st, '○')} {_label(st)}",
                fg_color=fg,
                text_color="white",
                corner_radius=10,
            )
            pill.pack(side="right", padx=4)
            st_dict = ui_state.setdefault(server_id, {})
            st_dict["pill"] = pill
            st_dict["name_var"] = name_var

            toggles = ctk.CTkFrame(card, fg_color="transparent")
            toggles.pack(fill="x", padx=14)
            ev = ctk.BooleanVar(value=cfg.enabled)
            av = ctk.BooleanVar(value=cfg.autostart)
            st_dict["enabled"] = ev
            st_dict["auto"] = av
            ctk.CTkSwitch(toggles, text="Enabled", variable=ev).pack(
                side="left"
            )
            ctk.CTkSwitch(toggles, text="Auto-start", variable=av).pack(
                side="left", padx=(16, 0)
            )
            ctk.CTkButton(
                toggles, text="Log", width=60,
                command=lambda sid=server_id: helpers["open_log"](sid),
            ).pack(side="right")
            ctk.CTkButton(
                toggles, text="Copy URL", width=80,
                command=lambda sid=server_id: helpers["copy_url"](sid),
            ).pack(side="right", padx=(0, 8))

            # Transport.
            ctk.CTkLabel(card, text="Type (stdio = local command, "
                                   "external = http URL)").pack(
                anchor="w", padx=14, pady=(6, 0)
            )
            kind_var = ctk.StringVar(
                value=cfg.transport
                if cfg.transport in ("stdio", "external")
                else "stdio"
            )
            st_dict["kind"] = kind_var
            ctk.CTkOptionMenu(
                card, values=["stdio", "external"], variable=kind_var,
                command=lambda _v, sid=server_id: _rebuild_cards(),
            ).pack(anchor="w", padx=14)

            cmd_var = ctk.StringVar(value=cfg.command)
            url_var = ctk.StringVar(value=cfg.server_url)
            cwd_var = ctk.StringVar(value=cfg.working_dir)
            st_dict["cmd"] = cmd_var
            st_dict["url"] = url_var
            st_dict["cwd"] = cwd_var
            kind = kind_var.get()
            try:
                info = describe_server(server_id, settings, registry)
            except Exception:
                info = {}
            if kind == "external":
                ctk.CTkLabel(card, text="Server URL override "
                                        "(blank = use env var)").pack(
                    anchor="w", padx=14, pady=(6, 0)
                )
                ctk.CTkEntry(
                    card, textvariable=url_var,
                    placeholder_text=_short(
                        info.get("url_str", "") or "http://127.0.0.1:3000/mcp"
                    ),
                ).pack(fill="x", padx=14)
                resolved_url = (info.get("url_str") or "").strip()
                if resolved_url:
                    src = info.get("url_source") or "resolved"
                    ctk.CTkLabel(
                        card,
                        text=f"Connecting to: {_short(resolved_url)} ({src})",
                        font=("Segoe UI", 10),
                    ).pack(anchor="w", padx=14)
                else:
                    ctk.CTkLabel(
                        card,
                        text="No URL set — add one above or set "
                             "MCP_SERVER_URL.",
                        font=("Segoe UI", 10),
                    ).pack(anchor="w", padx=14)
            else:
                ctk.CTkLabel(card, text="Command override "
                                        "(blank = use default)").pack(
                    anchor="w", padx=14, pady=(6, 0)
                )
                ctk.CTkEntry(
                    card, textvariable=cmd_var,
                    placeholder_text=_short(
                        info.get("command_str", "") or "command…"
                    ),
                ).pack(fill="x", padx=14)
                resolved_cmd = (info.get("command_str") or "").strip()
                if resolved_cmd:
                    src = info.get("command_source") or "resolved"
                    ctk.CTkLabel(
                        card,
                        text=f"Running: {_short(resolved_cmd, 100)} ({src})",
                        font=("Segoe UI", 10),
                    ).pack(anchor="w", padx=14)
                else:
                    ctk.CTkLabel(
                        card,
                        text="No command set — add one above.",
                        font=("Segoe UI", 10),
                    ).pack(anchor="w", padx=14)
                ctk.CTkLabel(card, text="Working folder override "
                                        "(blank = use default)").pack(
                    anchor="w", padx=14, pady=(6, 0)
                )
                cwd_row = ctk.CTkFrame(card, fg_color="transparent")
                cwd_row.pack(fill="x", padx=14)
                ctk.CTkEntry(
                    cwd_row, textvariable=cwd_var,
                    placeholder_text=_short(
                        info.get("cwd_str", "") or "default folder…"
                    ),
                ).pack(side="left", fill="x", expand=True)

                def _browse(sid=server_id):
                    picked = filedialog.askdirectory(parent=win)
                    if picked:
                        ui_state[sid]["cwd"].set(picked)

                ctk.CTkButton(cwd_row, text="Browse…", width=90,
                              command=_browse).pack(
                    side="right", padx=(8, 0)
                )
                resolved_cwd = (info.get("cwd_str") or "").strip()
                if resolved_cwd:
                    ctk.CTkLabel(
                        card,
                        text=f"Running in: {_short(resolved_cwd, 100)}",
                        font=("Segoe UI", 10),
                    ).pack(anchor="w", padx=14)

            ctk.CTkLabel(card, text="Tunnel ID (each server needs its OWN)").pack(
                anchor="w", padx=14, pady=(6, 0)
            )
            tv = ctk.StringVar(
                value=cfg.tunnel_id or eff_tid(settings, server_id)
            )
            st_dict["tid"] = tv
            ctk.CTkEntry(card, textvariable=tv).pack(fill="x", padx=14)

            ctk.CTkLabel(card, text="Health address").pack(
                anchor="w", padx=14, pady=(6, 0)
            )
            hv = ctk.StringVar(
                value=cfg.health_addr
                or eff_health(settings, server_id)
                or next_free_health_addr(settings)
            )
            st_dict["health"] = hv
            ctk.CTkEntry(card, textvariable=hv).pack(
                fill="x", padx=14, pady=(0, 4)
            )

            if server_id == "filesystem":
                ctk.CTkLabel(
                    card,
                    text="Folders the AI can access (fewer + narrower is safer)",
                ).pack(anchor="w", padx=14)
                flist = list(eff_roots(settings))
                if "filesystem_folders" not in st_dict:
                    st_dict["filesystem_folders"] = flist
                fframe = ctk.CTkFrame(card, fg_color="transparent")
                fframe.pack(fill="x", padx=14, pady=4)
                st_dict["fframe"] = fframe

                def _render_folders(sid=server_id):
                    fr = ui_state[sid]["fframe"]
                    for child in fr.winfo_children():
                        child.destroy()
                    for folder in ui_state[sid]["filesystem_folders"]:
                        r = ctk.CTkFrame(fr, fg_color="transparent")
                        r.pack(fill="x", pady=1)
                        ctk.CTkLabel(r, text=folder).pack(side="left")
                        ctk.CTkButton(
                            r, text="✕", width=36,
                            command=lambda f=folder, s=sid: (
                                ui_state[s]["filesystem_folders"].remove(f),
                                _render_folders(s),
                            ),
                        ).pack(side="right")

                def _add_folder(sid=server_id):
                    picked = filedialog.askdirectory(
                        parent=win,
                        title="Choose a folder for Filesystem MCP",
                    )
                    if picked:
                        cur = ui_state[sid]["filesystem_folders"]
                        if picked.lower() not in {
                            c.lower() for c in cur
                        }:
                            cur.append(picked)
                            _render_folders(sid)

                def _reset_folders(sid=server_id):
                    ui_state[sid]["filesystem_folders"] = list(
                        default_roots()
                    )
                    _render_folders(sid)

                _render_folders(server_id)
                brow = ctk.CTkFrame(card, fg_color="transparent")
                brow.pack(fill="x", padx=14, pady=(0, 6))
                ctk.CTkButton(
                    brow, text="Add folder…", command=_add_folder
                ).pack(side="left")
                ctk.CTkButton(
                    brow, text="Reset", command=_reset_folders
                ).pack(side="left", padx=(8, 0))

            # Danger row: duplicate / delete.
            danger = ctk.CTkFrame(card, fg_color="transparent")
            danger.pack(fill="x", padx=14, pady=(0, 10))

            def _duplicate(sid=server_id):
                src = pending.get(sid)
                if src is None:
                    return
                new_name = (src.display_name or sid) + " copy"
                new_id = make_server_id(new_name, set(pending.keys()))
                pending[new_id] = copy.deepcopy(src)
                pending[new_id].display_name = new_name
                pending[new_id].channel = new_id
                pending[new_id].tunnel_id = ""
                try:
                    pending[new_id].health_addr = (
                        next_free_health_addr(settings)
                    )
                except Exception:
                    pass
                order.append(new_id)
                _rebuild_cards()

            def _delete(sid=server_id):
                if not messagebox.askyesno(
                    "Delete server",
                    f"Delete '{sid}'?\nIts tunnel must be stopped first.",
                    parent=win,
                ):
                    return
                pending.pop(sid, None)
                if sid in order:
                    order.remove(sid)
                _rebuild_cards()

            ctk.CTkButton(
                danger, text="Duplicate", width=90, command=_duplicate
            ).pack(side="left")
            ctk.CTkButton(
                danger, text="Delete", width=80,
                fg_color="#b3261e", hover_color="#8f1d17",
                command=_delete,
            ).pack(side="left", padx=(8, 0))

    _rebuild_cards()

    # Footer --------------------------------------------------
    footer = ctk.CTkFrame(win, corner_radius=12)
    footer.pack(fill="x", padx=14, pady=(8, 14))
    status_var = ctk.StringVar(value="Ready. Ctrl+S saves • Esc closes.")

    def _do_save(restart_all: bool = False):
        new_tids = {
            sid: ui_state[sid]["tid"].get().strip()
            for sid in order if sid in ui_state
        }
        new_health = {
            sid: ui_state[sid]["health"].get().strip()
            for sid in order if sid in ui_state
        }
        problems: list[str] = []
        seen: dict = {}
        for sid, tid in new_tids.items():
            if not tid:
                continue
            if tid in seen:
                problems.append(
                    f"Duplicate tunnel ID for {sid} and {seen[tid]}."
                )
            else:
                seen[tid] = sid
        seen_hp: dict = {}
        for sid, hp in new_health.items():
            k = hp.lower()
            if k in seen_hp:
                problems.append(
                    f"Duplicate health {hp} for {sid} and {seen_hp[k]}."
                )
            else:
                seen_hp[k] = sid
        # Per-server transport validation.
        for sid in order:
            if sid not in ui_state:
                continue
            kind = ui_state[sid]["kind"].get().strip().lower()
            if kind == "external":
                if not ui_state[sid]["url"].get().strip():
                    problems.append(
                        f"{sid}: external needs a Server URL."
                    )
            else:
                # Filesystem builds its own command from folders.
                if sid != "filesystem" and not ui_state[sid]["cmd"].get().strip():
                    problems.append(
                        f"{sid}: stdio needs a Command."
                    )
        folders = None
        if "filesystem" in ui_state:
            folders = list(ui_state["filesystem"].get(
                "filesystem_folders", []))
            fs_enabled = pending.get("filesystem") and (
                pending["filesystem"].enabled
                or ui_state["filesystem"]["enabled"].get()
            )
            if fs_enabled:
                if not folders:
                    problems.append(
                        "Filesystem needs at least one folder."
                    )
                else:
                    missing = [f for f in folders if not Path(f).exists()]
                    if missing:
                        problems.append(
                            "Missing: " + ", ".join(missing[:3])
                        )
        if problems:
            messagebox.showerror(
                "Fix before saving", "\n\n".join(problems), parent=win
            )
            return
        # Apply UI state into pending copy.
        for sid in order:
            cfg = pending.get(sid)
            if cfg is None or sid not in ui_state:
                continue
            st = ui_state[sid]
            cfg.display_name = st["name_var"].get().strip() or sid
            cfg.enabled = st["enabled"].get()
            cfg.autostart = st["auto"].get()
            cfg.transport = st["kind"].get().strip().lower()
            cfg.command = st["cmd"].get().strip()
            cfg.server_url = st["url"].get().strip()
            cfg.working_dir = st["cwd"].get().strip()
            cfg.tunnel_id = new_tids.get(sid, "")
            cfg.health_addr = new_health.get(
                sid, cfg.health_addr
            )
            if not cfg.channel.strip():
                cfg.channel = sid
        if folders is not None and "filesystem" in pending:
            pending["filesystem"].allowed_dirs = list(folders)
        # Handle deletes: stop + drop.
        for sid in list(settings.servers.keys()):
            if sid not in pending:
                try:
                    helpers["stop_one"](sid)
                except Exception:
                    pass
        settings.servers.clear()
        for sid in order:
            if sid in pending:
                settings.servers[sid] = pending[sid]
        settings.tunnel_enabled = master_var.get()
        settings.api_key_env = api_var.get().strip() or (
            "CONTROL_PLANE_API_KEY"
        )
        settings.filesystem_package = pkg_var.get().strip() or (
            "@modelcontextprotocol/server-filesystem@2026.8.31"
        )
        settings.auto_heal = heal_var.get()
        obs = settings.servers.get("obsidian")
        if obs is not None:
            settings.tunnel_id = obs.tunnel_id.strip()
        helpers["update_dotenv"](
            {"CONTROL_PLANE_TUNNEL_ID": settings.tunnel_id}
        )
        helpers["load_dotenv"]()
        helpers["save_settings"](settings)
        try:
            registry.refresh_from_settings()
        except Exception:
            pass
        try:
            helpers["sync_tunnel"]()
        except Exception:
            pass
        status_var.set("Saved.")
        helpers["refresh"]()
        if restart_all:
            win.destroy()
            helpers["action"](helpers["tunnel_restart"])
            return
        win.destroy()

    ctk.CTkLabel(footer, textvariable=status_var).pack(
        side="left", padx=12
    )
    ctk.CTkButton(
        footer, text="Save", command=lambda: _do_save(False)
    ).pack(side="right", padx=8, pady=8)
    ctk.CTkButton(
        footer, text="Save & restart all",
        command=lambda: _do_save(True),
    ).pack(side="right", pady=8)
    ctk.CTkButton(
        footer, text="Logs", width=70,
        command=helpers["open_logs_folder"],
    ).pack(side="right", padx=8, pady=8)

    def _live():
        try:
            if not win.winfo_exists():
                return
            overall_var.set(
                f"Overall: {_label(tunnel.overall_status())}"
            )
            for sid, pill_w in [
                (k, v.get("pill")) for k, v in ui_state.items()
            ]:
                try:
                    if pill_w is None:
                        continue
                    st = tunnel.status_for(sid)
                    fg, _bg = STATUS_COLORS.get(
                        st, ("#5f6368", "#f1f3f4")
                    )
                    pill_w.configure(
                        text=f"{STATUS_DOT.get(st,'○')} {_label(st)}",
                        fg_color=fg,
                    )
                except Exception:
                    pass
            win.after(2000, _live)
        except Exception:
            pass

    win.bind("<Control-s>", lambda _e: _do_save(False))
    win.bind("<Control-S>", lambda _e: _do_save(False))
    win.bind("<Escape>", lambda _e: win.destroy())
    win.after(2000, _live)
    win.mainloop()
