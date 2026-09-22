"""Secret injector: Windows Credential Manager first, .env fallback.

- Values are NEVER printed, logged, or stored in settings.json.
- settings.json keeps only VARIABLE NAMES (e.g. CONTROL_PLANE_API_KEY).
- At startup inject_secrets() copies vault values into os.environ for
  names that are missing, so the rest of the app (tunnel args use
  env:NAME) works unchanged.
- UI shows only SOURCE (Vault / .env / missing), never values.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SERVICE = "tunnel-mcp-manager"

_ENV_REF = re.compile(r"env:([A-Za-z_][A-Za-z0-9_]*)")


def backend_name() -> str:
    try:
        import keyring

        kr = keyring.get_keyring()
        return f"{type(kr).__module__}.{type(kr).__name__}"
    except Exception as exc:
        return f"unavailable ({exc})"


def vault_available() -> bool:
    try:
        import keyring

        keyring.get_keyring()
        return True
    except Exception:
        return False


def vault_get(name: str) -> str:
    try:
        import keyring

        val = keyring.get_password(SERVICE, name)
        return val or ""
    except Exception:
        return ""


def vault_set(name: str, value: str) -> None:
    import keyring

    keyring.set_password(SERVICE, name, value)


def vault_delete(name: str) -> None:
    try:
        import keyring

        keyring.delete_password(SERVICE, name)
    except Exception:
        pass


def env_has(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def secret_source(name: str) -> str:
    """Where would this secret come from? vault | env | missing."""
    if vault_get(name).strip():
        return "vault"
    if env_has(name):
        return "env"
    return "missing"


def env_refs_in_text(text: str) -> list[str]:
    seen: list[str] = []
    for m in _ENV_REF.finditer(text or ""):
        name = m.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def tracked_names(settings=None, extra_headers: str = "") -> list[str]:
    """All secret names the app cares about (no values)."""
    names: list[str] = []
    try:
        api_env = ""
        if settings is not None:
            api_env = (settings.api_key_env or "").strip()
        api_env = api_env or "CONTROL_PLANE_API_KEY"
        names.append(api_env)
    except Exception:
        names.append("CONTROL_PLANE_API_KEY")
    try:
        headers = extra_headers or os.environ.get("MCP_EXTRA_HEADERS", "")
        for ref in env_refs_in_text(headers):
            if ref not in names:
                names.append(ref)
    except Exception:
        pass
    # Common token referenced inside extra headers (see tunnel logs).
    if "OBSIDIAN_TOKEN" not in names:
        names.append("OBSIDIAN_TOKEN")
    return names


def inject_secrets(settings=None) -> dict[str, str]:
    """Copy vault values into os.environ for missing names.

    Returns {name: source} for tracked names. Never returns values.
    """
    try:
        headers = os.environ.get("MCP_EXTRA_HEADERS", "")
    except Exception:
        headers = ""
    sources: dict[str, str] = {}
    for name in tracked_names(settings, headers):
        if env_has(name):
            sources[name] = (
                "vault+env" if vault_get(name).strip() else "env"
            )
            continue
        try:
            val = vault_get(name)
        except Exception:
            val = ""
        if val.strip():
            os.environ[name] = val
            sources[name] = "vault"
        else:
            sources[name] = "missing"
    return sources


def move_env_to_vault(name: str, scrub_env_file: bool = True) -> str:
    """Store current .env/process value into vault.

    Returns 'moved' | 'vault-only' | 'nothing-to-move'.
    With scrub_env_file, blanks NAME= in the local .env so the
    plaintext copy is gone (vault becomes the only source).
    """
    value = os.environ.get(name, "")
    if not value.strip():
        return "vault-only" if vault_get(name).strip() else "nothing-to-move"
    vault_set(name, value)
    if scrub_env_file:
        try:
            from pathlib import Path as _P

            root = _P(__file__).resolve().parent
            env_path = root / ".env"
            if env_path.exists():
                lines = env_path.read_text(encoding="utf-8").splitlines()
                out = []
                for line in lines:
                    s = line.strip()
                    if s and not s.startswith("#") and "=" in s:
                        key = s.split("=", 1)[0].strip()
                        if key == name:
                            out.append(f"{name}=")
                            continue
                    out.append(line)
                env_path.write_text(
                    "\n".join(out).rstrip() + "\n", encoding="utf-8"
                )
        except OSError:
            pass
    return "moved"


def blank_env_name(env_path: Path, name: str) -> bool:
    try:
        if not env_path.exists():
            return False
        lines = env_path.read_text(encoding="utf-8").splitlines()
        changed = False
        out = []
        for line in lines:
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                if s.split("=", 1)[0].strip() == name:
                    out.append(f"{name}=")
                    changed = True
                    continue
            out.append(line)
        if changed:
            env_path.write_text(
                "\n".join(out).rstrip() + "\n", encoding="utf-8"
            )
        return changed
    except OSError:
        return False
