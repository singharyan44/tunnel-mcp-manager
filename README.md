# MCP Manager — Windows tray for OpenAI tunnel-client + any MCP server

One tray icon, one process per MCP server. Each server gets its own
tunnel ID, health port, log file, and Start/Stop/Restart — so one crash
never takes the rest down.

![platform](https://img.shields.io/badge/platform-Windows-blue)
![license](https://img.shields.io/badge/license-MIT-green)

## Features

- **General servers** — add any stdio command or external HTTP MCP, not just the 4 presets
- **Individual tunnels** — distinct tunnel ID + health port per server (8080, 8081, …)
- **Self-healing** — crashed tunnels retry with backoff (5s → 2m), toast on repeat failures
- **Doctor** — checks runtime, API key, tunnel IDs, ports, commands, folders, Node
- **Health** — uptime, crash counts, call counts with rough token estimates
- **Compressed call log** — gzip JSONL per day, decompressed only on read
- **Modern settings UI** — CustomTkinter dark cards (Tk fallback included)
- **Filesystem picker** — choose exactly which folders the AI may touch
- **Update checker** — warns when the pinned filesystem package is stale
- **One-click build** — PyInstaller `build.ps1` → single folder + exe

## Quick start

1. Install Python 3.11+, Node LTS, and download `tunnel-client-runtime.exe`.
2. `pip install -r requirements.txt`
3. `copy .env.example .env` — fill in your key + tunnel IDs (never commit `.env`).
4. Create one tunnel per server in the OpenAI dashboard, paste each ID in
   Settings (tray → Tools → Settings).
5. `python main.pyw` — or `python main.pyw --dev` for auto-reload on code edits.

## Security

- Secrets live **only** in `.env` (gitignored). `settings.json` stores variable
  **names**, never values.
- Logs contain tunnel IDs and local paths — also gitignored. Don't paste them
  publicly without scrubbing.
- Filesystem MCP is powerful: keep its folder list as narrow as possible.
- If a key ever leaks (chat, screenshot, log), **rotate it immediately** in the
  OpenAI dashboard, then update `.env`.

## Repo layout

| File | What |
|---|---|
| `main.pyw` | Tray icon, menus, monitor loop, single-instance, autostart |
| `settings_ui_ctk.py` | Modern settings window + doctor dialog |
| `mcp_manager_settings.py` | Atomic settings load/save, per-server config |
| `mcp_registry.py` | Builds any server from settings (stdio/external) |
| `tunnel_manager.py` | One supervised process per server, healing, rotation |
| `app_health.py` | Updates, uptime, tokens, doctor, compressed call log |
| `build.ps1` | One-click PyInstaller build |
| `.env.example` | Template — copy to `.env` |

## Contributing

PRs welcome. Please don't include `.env`, `*.log`, `*.pid`, or screenshots
with tunnel IDs / keys.
