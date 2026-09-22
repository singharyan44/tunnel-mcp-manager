"""Health extras: update checks, uptime/crash history, token sense,
doctor checks, and extremely compressed tool-call log.

Tool-call log is gzip JSON-lines per day, only decompressed on read.
All state lives under LOCALAPPDATA/OpenSCAD-MCP-Manager/.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path


def _data_dir() -> Path:
    base = Path(
        os.environ.get("LOCALAPPDATA", Path.home())
    ) / "OpenSCAD-MCP-Manager"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return base


# ----------------------------------------------------------
# UPDATE CHECKER (cached 24h, silent on failure)
# ----------------------------------------------------------

UPDATE_CACHE = "update-check.json"
UPDATE_TTL = 24 * 3600


def _load_update_cache() -> dict:
    try:
        return json.loads(
            (_data_dir() / UPDATE_CACHE).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_update_cache(payload: dict) -> None:
    try:
        (_data_dir() / UPDATE_CACHE).write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def check_updates(
    filesystem_package: str, force: bool = False
) -> dict:
    """Return {filesystem_latest, filesystem_update, checked_at}.

    Never raises; network failures yield update=False.
    """
    now = time.time()
    cache = _load_update_cache()
    if (
        not force
        and cache.get("checked_at")
        and now - float(cache["checked_at"]) < UPDATE_TTL
    ):
        return cache
    result: dict = {
        "checked_at": now,
        "filesystem_current": filesystem_package,
        "filesystem_latest": "",
        "filesystem_update": False,
    }
    try:
        out = subprocess.run(
            [
                "npm", "view",
                "@modelcontextprotocol/server-filesystem",
                "dist-tags.latest",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            ),
        )
        latest = (out.stdout or "").strip().strip("'\"")
        if latest and re.match(r"^[0-9]{4}\.", latest):
            result["filesystem_latest"] = (
                f"@modelcontextprotocol/server-filesystem@{latest}"
            )
            cur = (filesystem_package or "").strip()
            result["filesystem_update"] = bool(cur) and (
                cur != result["filesystem_latest"]
            )
    except (OSError, subprocess.SubprocessError):
        pass
    except Exception:
        pass
    _save_update_cache(result)
    return result


# ----------------------------------------------------------
# CRASH HISTORY + UPTIME (tiny JSON, capped)
# ----------------------------------------------------------

STATS_FILE = "stats.json"
MAX_EVENTS = 200


def _load_stats() -> dict:
    try:
        raw = json.loads(
            (_data_dir() / STATS_FILE).read_text(encoding="utf-8")
        )
        if isinstance(raw, dict):
            return raw
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"servers": {}}


def _save_stats(data: dict) -> None:
    try:
        (_data_dir() / STATS_FILE).write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def record_event(
    server_id: str, kind: str, detail: str = ""
) -> None:
    """kind: start|stop|crash|healed|connected."""
    data = _load_stats()
    srv = data.setdefault("servers", {}).setdefault(
        server_id, {"events": [], "uptime_sec": 0,
                    "calls": 0, "est_in_tokens": 0,
                    "est_out_tokens": 0}
    )
    srv.setdefault("events", []).append(
        {"t": time.time(), "kind": kind, "detail": detail[:200]}
    )
    # Cap: keep newest MAX_EVENTS.
    if len(srv["events"]) > MAX_EVENTS:
        srv["events"] = srv["events"][-MAX_EVENTS:]
    _save_stats(data)


def record_uptime(server_id: str, seconds: float) -> None:
    if seconds <= 0:
        return
    data = _load_stats()
    srv = data.setdefault("servers", {}).setdefault(
        server_id, {"events": [], "uptime_sec": 0,
                    "calls": 0, "est_in_tokens": 0,
                    "est_out_tokens": 0}
    )
    srv["uptime_sec"] = float(srv.get("uptime_sec", 0)) + seconds
    _save_stats(data)


def uptime_summary(server_id: str) -> dict:
    data = _load_stats()
    srv = data.get("servers", {}).get(server_id, {})
    events = srv.get("events", [])
    crashes = sum(1 for e in events if e.get("kind") == "crash")
    starts = sum(1 for e in events if e.get("kind") == "start")
    uptime = float(srv.get("uptime_sec", 0))
    last_crash = ""
    for e in reversed(events):
        if e.get("kind") == "crash":
            try:
                last_crash = datetime.fromtimestamp(
                    float(e.get("t", 0))
                ).strftime("%m-%d %H:%M")
            except (TypeError, ValueError):
                pass
            break
    h = int(uptime // 3600)
    m = int((uptime % 3600) // 60)
    return {
        "crashes": crashes,
        "starts": starts,
        "uptime": f"{h}h {m}m" if h else f"{m}m",
        "last_crash": last_crash,
        "calls": int(srv.get("calls", 0)),
        "in_tokens": int(srv.get("est_in_tokens", 0)),
        "out_tokens": int(srv.get("est_out_tokens", 0)),
    }


def record_calls(
    server_id: str, count: int = 1,
    in_tokens: int = 0, out_tokens: int = 0,
) -> None:
    data = _load_stats()
    srv = data.setdefault("servers", {}).setdefault(
        server_id, {"events": [], "uptime_sec": 0,
                    "calls": 0, "est_in_tokens": 0,
                    "est_out_tokens": 0}
    )
    srv["calls"] = int(srv.get("calls", 0)) + count
    srv["est_in_tokens"] = int(srv.get("est_in_tokens", 0)) + in_tokens
    srv["est_out_tokens"] = int(srv.get("est_out_tokens", 0)) + out_tokens
    _save_stats(data)


# ----------------------------------------------------------
# TOOL-CALL LOG: gzip JSONL per day, expand only on read
# ----------------------------------------------------------

def _toolcall_path(day: str | None = None) -> Path:
    day = day or datetime.now().strftime("%Y%m%d")
    return _data_dir() / f"toolcalls-{day}.jsonl.gz"


def append_toolcall(
    server_id: str, request_id: str, rpc_id: str = "",
    in_tokens: int = 350, out_tokens: int = 600,
) -> None:
    """Append one extremely small row. Estimate tokens heuristically."""
    row = {
        "t": int(time.time()),
        "s": server_id,
        "r": request_id[:40],
        "q": rpc_id[:24],
        "i": in_tokens,
        "o": out_tokens,
    }
    try:
        with gzip.open(_toolcall_path(), "at", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError:
        pass
    record_calls(server_id, 1, in_tokens, out_tokens)


def read_toolcalls(
    server_id: str | None = None, days: int = 7, limit: int = 200
) -> list[dict]:
    """Decompress on read only. Newest first, capped."""
    out: list[dict] = []
    today = datetime.now()
    from datetime import timedelta

    for back in range(days):
        day = (today - timedelta(days=back)).strftime("%Y%m%d")
        path = _toolcall_path(day)
        if not path.exists():
            continue
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if server_id and row.get("s") != server_id:
                        continue
                    out.append(row)
                    if len(out) >= limit:
                        return sorted(
                            out, key=lambda r: r.get("t", 0),
                            reverse=True,
                        )
        except OSError:
            continue
    return sorted(out, key=lambda r: r.get("t", 0), reverse=True)[:limit]


def tail_tunnel_logs_for_calls(root: Path) -> int:
    """Scan tunnel-*.log tails for new forwarded commands.

    Tracks file offsets in memory file (no log growth). Returns new rows.
    """
    state_file = _data_dir() / "toolcall-offsets.json"
    try:
        offsets = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(offsets, dict):
            offsets = {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        offsets = {}
    added = 0
    pattern = re.compile(
        r"dispatcher forwarded command to MCP server.*?request_id=(cmd_\S+)"
    )
    rpc_pattern = re.compile(r"rpc_request_id=(\S+)")
    try:
        logs = sorted(root.glob("tunnel-*.log"))
    except OSError:
        logs = []
    for log in logs:
        server_id = log.stem.replace("tunnel-", "")
        try:
            size = log.stat().st_size
        except OSError:
            continue
        # Only read last 64KB to stay cheap.
        start = offsets.get(str(log), max(0, size - 65536))
        if size < start:
            start = 0
        if size == start:
            continue
        try:
            with open(log, "rb") as fh:
                fh.seek(start)
                chunk = fh.read().decode("utf-8", errors="replace")
            offsets[str(log)] = size
            for line in chunk.splitlines():
                m = pattern.search(line)
                if not m:
                    continue
                rm = rpc_pattern.search(line)
                append_toolcall(
                    server_id,
                    m.group(1),
                    rm.group(1) if rm else "",
                )
                added += 1
                if added > 500:
                    break
        except OSError:
            continue
    try:
        state_file.write_text(json.dumps(offsets), encoding="utf-8")
    except OSError:
        pass
    return added


# ----------------------------------------------------------
# DOCTOR
# ----------------------------------------------------------

def run_doctor(settings, registry, tunnel) -> list[dict]:
    """Return [{ok, title, detail}] — never raises."""
    results: list[dict] = []

    def _add(ok: bool, title: str, detail: str = ""):
        results.append({"ok": ok, "title": title, "detail": detail})

    # 1. Runtime exe.
    try:
        exe = tunnel._runtime_path()
        exists = Path(exe).exists() if Path(exe).is_absolute() or (
            "/" in exe or "\\" in exe
        ) else True
        ver = ""
        try:
            ver = tunnel.runtime_version()
        except Exception:
            pass
        _add(
            True,
            f"Runtime: {ver or exe}"[:90],
            "" if ver else f"Path: {exe}",
        )
    except Exception as exc:
        _add(False, "Runtime check failed", str(exc)[:200])

    # 2. API key (vault or env — value never shown).
    try:
        api_env = settings.api_key_env.strip() or "CONTROL_PLANE_API_KEY"
        try:
            import secrets_store as _sec

            src = _sec.secret_source(api_env)
        except Exception:
            src = (
                "env"
                if os.environ.get(api_env, "").strip()
                else "missing"
            )
        has = src in ("vault", "env", "vault+env")
        _add(
            has,
            f"API key {api_env}: {src if has else 'MISSING'}",
            "" if has else (
                "Save it to Vault in Settings → Secrets, "
                "or add it to .env, then restart tray."
            ),
        )
    except Exception as exc:
        _add(False, "API key check failed", str(exc)[:200])

    # 3. Per-server checks.
    for sid, srv in registry.servers.items():
        cfg = settings.servers.get(sid)
        # Tunnel ID format.
        try:
            from mcp_manager_settings import effective_tunnel_id

            tid = effective_tunnel_id(settings, sid)
            ok = bool(tid) and tid.startswith("tunnel_")
            _add(
                ok,
                f"{sid}: tunnel ID {'ok' if ok else 'MISSING/BAD'}",
                "" if ok else "Paste a DISTINCT tunnel_… ID in Settings.",
            )
        except Exception as exc:
            _add(False, f"{sid}: tunnel check failed", str(exc)[:150])
        # Health port free or ours.
        try:
            from mcp_manager_settings import effective_health_addr

            haddr = effective_health_addr(settings, sid)
            host, port_text = haddr.rsplit(":", 1)
            port = int(port_text)
            try:
                with socket.create_connection((host, port), timeout=0.25):
                    # In use — ok if our own process holds it.
                    ours = (
                        tunnel.processes.get(sid) is not None
                        and tunnel.processes.get(sid).poll() is None
                    )
                    _add(
                        ours,
                        f"{sid}: port {port} "
                        f"{'held by us' if ours else 'BUSY'}",
                        "" if ours else "Another app holds it.",
                    )
            except (ConnectionRefusedError, TimeoutError, OSError):
                _add(True, f"{sid}: port {port} free-or-starting")
        except ValueError:
            _add(False, f"{sid}: bad health address",
                 f"{cfg.health_addr if cfg else ''}")
        except Exception as exc:
            _add(False, f"{sid}: port check failed", str(exc)[:150])
        # Command / URL.
        try:
            if srv.transport == "stdio":
                if cfg is not None and sid == "filesystem":
                    from mcp_manager_settings import (
                        effective_filesystem_roots,
                    )

                    roots = effective_filesystem_roots(settings)
                    missing = [
                        r for r in roots if not Path(r).exists()
                    ]
                    _add(
                        not missing,
                        f"{sid}: {len(roots)} folder(s) "
                        f"{'ok' if not missing else 'MISSING'}",
                        "" if not missing else "; ".join(missing[:3]),
                    )
                else:
                    has_cmd = bool(srv.command)
                    _add(
                        has_cmd,
                        f"{sid}: command {'ok' if has_cmd else 'MISSING'}",
                        "" if has_cmd else "Set Command in Settings.",
                    )
                    # First arg exists? (exe / npx / python)
                    if has_cmd:
                        first = srv.command[0].strip('"')
                        if first.lower() not in (
                            "npx", "python", "pythonw", "uvx",
                        ) and (
                            "/" in first or "\\" in first or
                            first.lower().endswith(".exe")
                        ):
                            exists = Path(first).exists()
                            if not exists:
                                _add(
                                    False,
                                    f"{sid}: exe not found",
                                    first[:120],
                                )
            else:
                url = ""
                if cfg is not None and cfg.server_url.strip():
                    url = cfg.server_url.strip()
                if not url:
                    url = os.environ.get("MCP_SERVER_URL", "").strip()
                _add(
                    bool(url),
                    f"{sid}: URL {'ok' if url else 'MISSING'}",
                    "" if url else "Set Server URL in Settings.",
                )
        except Exception as exc:
            _add(False, f"{sid}: command check failed", str(exc)[:150])

    # 4. Node for filesystem/npx servers.
    try:
        needs_node = any(
            s.command and s.command[0].lower() == "npx"
            for s in registry.servers.values()
        )
        if needs_node:
            out = subprocess.run(
                ["npx", "--version"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(
                    subprocess, "CREATE_NO_WINDOW", 0
                ),
            )
            ok = out.returncode == 0
            _add(
                ok,
                f"Node/npx: {(out.stdout or '').strip() or 'missing'}",
                "" if ok else "Install Node LTS.",
            )
    except (OSError, subprocess.SubprocessError):
        _add(False, "Node/npx: missing", "Install Node LTS.")
    except Exception as exc:
        _add(False, "Node check failed", str(exc)[:150])

    return results
