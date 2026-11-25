# -*- coding: utf-8 -*-
"""First-run authentication, configuration management, and MCP registry helpers for Voice Studio & Colab MCP."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from voice_studio import PROJECT_ROOT
from voice_studio.colab.colab_cli_bridge import check_colab_cli_auth, resolve_colab_cli_binary
from voice_studio.colab.drive_bridge import authenticate_google_drive
from voice_studio.colab.gdrive_cli import resolve_gdrive_cli_binary
from voice_studio.config.loader import load_dotenv_file, load_pipeline_config, resolve_repo_path

DEFAULT_GLOBAL_MCP_CONFIG = Path.home() / ".gemini" / "config" / "mcp_config.json"


def resolve_colab_mcp_command(base_dir: Path = PROJECT_ROOT) -> tuple[str, List[str]]:
    """Resolves official googlecolab/colab-mcp command and args without requiring global uv."""
    for root_cand in (base_dir, base_dir.parent):
        local_bin = root_cand / ".venv-colab-mcp" / "bin" / "colab-mcp"
        if local_bin.is_file():
            return str(local_bin), []
    path_bin = shutil.which("colab-mcp")
    if path_bin:
        return path_bin, []
    uvx_bin = shutil.which("uvx")
    if uvx_bin:
        return uvx_bin, ["git+https://github.com/googlecolab/colab-mcp"]
    return "", []


def build_mcp_servers_dict(base_dir: Path = PROJECT_ROOT) -> Dict[str, Any]:
    """Builds standard mcpServers mapping for voice-studio and official colab-mcp."""
    py_exe = sys.executable
    for root_cand in (base_dir, base_dir.parent):
        venv_py = root_cand / ".venv" / "bin" / "python"
        if venv_py.is_file():
            py_exe = str(venv_py)
            break
    servers: Dict[str, Any] = {
        "voice-studio": {
            "command": py_exe,
            "args": [str((base_dir / "studio.py").resolve()), "mcp"],
        }
    }
    colab_cmd, colab_args = resolve_colab_mcp_command(base_dir)
    if colab_cmd:
        entry: Dict[str, Any] = {"command": colab_cmd, "args": colab_args, "timeout": 30000}
        servers["colab-mcp"] = entry
    return servers


def sync_mcp_config(mcp_config_path: Optional[Path] = None, base_dir: Path = PROJECT_ROOT) -> Dict[str, Any]:
    """Merges voice-studio and colab-mcp entries into mcp_config.json cleanly."""
    target = mcp_config_path or DEFAULT_GLOBAL_MCP_CONFIG
    target.parent.mkdir(parents=True, exist_ok=True)
    current: Dict[str, Any] = {}
    if target.is_file() and target.stat().st_size > 0:
        current = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            current = {}
    servers = dict(current.get("mcpServers") or {})
    servers.update(build_mcp_servers_dict(base_dir))
    current["mcpServers"] = servers
    target.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"path": str(target), "servers": sorted(servers.keys())}


def _probe_gdrive_auth() -> Dict[str, Any]:
    bin_path = resolve_gdrive_cli_binary()
    if not bin_path:
        return {"installed": False, "signed_in": False, "detail": "gdrive CLI not on PATH"}
    try:
        proc = subprocess.run([bin_path, "readonly", "ls", "--max", "1", "--json"], capture_output=True, text=True, timeout=15.0)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"installed": True, "signed_in": False, "binary": bin_path, "detail": f"gdrive probe timed out: {exc}"}
    if proc.returncode == 0:
        return {"installed": True, "signed_in": True, "binary": bin_path}
    err_txt = (proc.stderr or proc.stdout or "").strip()
    return {"installed": True, "signed_in": False, "binary": bin_path, "detail": err_txt}


def _update_dotenv(env_path: Path, updates: Dict[str, str]) -> None:
    lines: List[str] = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    kept = [ln for ln in lines if "=" not in ln or ln.partition("=")[0].strip() not in updates]
    for k, v in updates.items():
        if v:
            kept.append(f'{k}="{v}"')
    env_path.write_text("\n".join(kept).strip() + "\n", encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError as exc:
        print(f"[mcp-setup] Warning setting 0600 on {env_path}: {exc}")


def inspect_auth_and_mcp_status(
    base_dir: Path = PROJECT_ROOT,
    mcp_config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Reports complete authentication, Colab bridge, and MCP registration status."""
    load_dotenv_file(base_dir / ".env")
    cfg = load_pipeline_config(base_dir=base_dir)
    gdrive_info = _probe_gdrive_auth()
    colab_cli_info = check_colab_cli_auth(resolve_colab_cli_binary(base_dir), base_dir=base_dir)
    secrets_p = resolve_repo_path(cfg.colab.client_secrets_path, base_dir)
    token_p = resolve_repo_path(cfg.colab.token_path, base_dir)
    colab_cmd, colab_args = resolve_colab_mcp_command(base_dir)
    mcp_path = mcp_config_path or DEFAULT_GLOBAL_MCP_CONFIG
    registered: List[str] = []
    if mcp_path.is_file() and mcp_path.stat().st_size > 0:
        loaded = json.loads(mcp_path.read_text(encoding="utf-8"))
        registered = sorted((loaded.get("mcpServers") or {}).keys())

    ready: List[str] = []
    if colab_cli_info.get("signed_in"):
        ready.append("colab_cli(oauth2)")
    if gdrive_info.get("signed_in"):
        ready.append("colab_drive(gdrive_cli)")
    if token_p.is_file():
        ready.append("colab_drive(oauth_token)")
    if cfg.colab.agent_url and cfg.colab.agent_token:
        ready.append("colab_agent(http)")
    if colab_cmd:
        ready.append("colab_mcp(official)")

    next_steps: List[str] = []
    if not colab_cli_info.get("signed_in"):
        next_steps.append("For direct config-driven L4/A100 GPU provisioning (colab_cli), run `.venv-colab-mcp/bin/colab usage` once to authorize Google OAuth.")
    if not ready:
        next_steps.append("Run first-run auth: studio.py mcp --setup (or connect Google Drive / Colab CLI / Colab Agent).")
    if "colab-mcp" not in registered or "voice-studio" not in registered:
        next_steps.append("Run studio.py mcp --setup to register voice-studio + colab-mcp in mcp_config.json.")

    return {
        "authenticated": len(ready) > 0,
        "ready_backends": ready,
        "colab_cli": colab_cli_info,
        "gdrive": gdrive_info,
        "oauth_drive": {"client_secrets_exists": secrets_p.is_file(), "token_exists": token_p.is_file()},
        "colab_agent": {"url_set": bool(cfg.colab.agent_url), "token_set": bool(cfg.colab.agent_token)},
        "colab_mcp": {"installed": bool(colab_cmd), "command": colab_cmd, "args": colab_args},
        "mcp_config": {"path": str(mcp_path), "registered_servers": registered},
        "next_steps": next_steps,
    }


def _sync_colab_token_to_dotenv(base_dir: Path) -> bool:
    """Exports active colab_cli OAuth token and gdrive path into .env (0600) for portable reuse."""
    bin_path = resolve_colab_cli_binary(base_dir)
    if not bin_path:
        return False
    py_exe = str(Path(bin_path).parent / "python")
    proc = subprocess.run(
        [py_exe, "-c", "import keyring; t = keyring.get_password('google-colab-cli', 'oauth_token'); print(t or '')"],
        capture_output=True,
        text=True,
        timeout=15.0,
    )
    tok = proc.stdout.strip()
    updates: Dict[str, str] = {}
    if tok:
        updates["COLAB_CLI_OAUTH_TOKEN_JSON"] = tok
        updates["COLAB_CLI_SESSION_NAME"] = "voice-studio-pro"
    g_bin = resolve_gdrive_cli_binary()
    if g_bin:
        updates["GDRIVE_CLI_BINARY"] = g_bin
    if updates:
        _update_dotenv(base_dir / ".env", updates)
    return bool(tok)


def _handle_colab_cli_mcp_oauth(base_dir: Path, oauth_code: str = "") -> Dict[str, Any]:
    """Starts or completes non-interactive colab_cli OAuth2 authorization for MCP tools."""
    bin_path = resolve_colab_cli_binary(base_dir)
    if not bin_path:
        return {"ok": False, "detail": "colab CLI binary not found"}
    py_exe = str(Path(bin_path).parent / "python")
    clean_code = (oauth_code or "").strip()
    if clean_code:
        script = (
            "import json, sys, keyring\n"
            "from importlib import resources\n"
            "from google_auth_oauthlib.flow import InstalledAppFlow\n"
            "from colab_cli.auth import PUBLIC_SCOPES, REMOTE_REDIRECT_URI, KEYRING_SERVICE_NAME, KEYRING_USERNAME\n"
            "cfg = json.loads(resources.files('colab_cli').joinpath('oauth_config.json').read_text())\n"
            "pend = json.load(open('/tmp/colab_cli_oauth_pending.json'))\n"
            "flow = InstalledAppFlow.from_client_config(cfg, PUBLIC_SCOPES, state=pend.get('state'))\n"
            "flow.redirect_uri = REMOTE_REDIRECT_URI\n"
            "flow.code_verifier = pend.get('code_verifier')\n"
            "flow.oauth2session.scope = None\n"
            "tok = flow.fetch_token(code=sys.argv[1])\n"
            "flow.oauth2session.scope = tok.get('scope')\n"
            "keyring.set_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME, flow.credentials.to_json())\n"
            "print('SAVED_OK')\n"
        )
        proc = subprocess.run([py_exe, "-c", script, clean_code], capture_output=True, text=True, timeout=30.0)
        if proc.returncode != 0:
            raise RuntimeError(f"Failed exchanging colab_cli OAuth code: {(proc.stderr or proc.stdout).strip()}")
        _sync_colab_token_to_dotenv(base_dir)
        return {"ok": True, "completed": True}

    script_init = (
        "import json, webbrowser\n"
        "from importlib import resources\n"
        "from google_auth_oauthlib.flow import InstalledAppFlow\n"
        "from colab_cli.auth import PUBLIC_SCOPES, REMOTE_REDIRECT_URI\n"
        "cfg = json.loads(resources.files('colab_cli').joinpath('oauth_config.json').read_text())\n"
        "flow = InstalledAppFlow.from_client_config(cfg, PUBLIC_SCOPES)\n"
        "flow.redirect_uri = REMOTE_REDIRECT_URI\n"
        "url, st = flow.authorization_url(prompt='consent', token_usage='remote')\n"
        "json.dump({'state': st, 'code_verifier': getattr(flow, 'code_verifier', None), 'auth_url': url}, open('/tmp/colab_cli_oauth_pending.json', 'w'))\n"
        "webbrowser.open_new_tab(url)\n"
        "print(url)\n"
    )
    proc = subprocess.run([py_exe, "-c", script_init], capture_output=True, text=True, timeout=20.0)
    url = proc.stdout.strip()
    return {"ok": True, "completed": False, "auth_url": url}


def perform_first_run_login(
    base_dir: Path = PROJECT_ROOT,
    mode: str = "auto",
    secrets_path: str = "",
    colab_url: str = "",
    colab_token: str = "",
    mcp_config_path: Optional[Path] = None,
    oauth_code: str = "",
) -> Dict[str, Any]:
    """Performs first-run MCP onboarding, authentication, and global mcp_config sync."""
    load_dotenv_file(base_dir / ".env")
    actions: List[str] = []
    oauth_auth_url = ""
    if colab_url:
        token_val = colab_token or os.environ.get("COLAB_AGENT_TOKEN") or secrets.token_urlsafe(24)
        _update_dotenv(base_dir / ".env", {"COLAB_AGENT_URL": colab_url.strip(), "COLAB_AGENT_TOKEN": token_val})
        os.environ["COLAB_AGENT_URL"] = colab_url.strip()
        os.environ["COLAB_AGENT_TOKEN"] = token_val
        actions.append("Saved COLAB_AGENT_URL + Bearer token to .env (0600)")

    cfg = load_pipeline_config(base_dir=base_dir)
    sec_p = resolve_repo_path(secrets_path or cfg.colab.client_secrets_path, base_dir)
    tok_p = resolve_repo_path(cfg.colab.token_path, base_dir)
    if mode in ("oauth", "auto") and sec_p.is_file() and not tok_p.is_file():
        authenticate_google_drive(sec_p, tok_p)
        actions.append(f"Completed Google Drive OAuth2 sign-in -> {tok_p}")
    if mode in ("colab_cli", "auto"):
        if oauth_code.strip():
            _handle_colab_cli_mcp_oauth(base_dir, oauth_code=oauth_code.strip())
            actions.append("Completed Colab CLI OAuth2 token exchange and saved to keyring")
        cli_status = check_colab_cli_auth(resolve_colab_cli_binary(base_dir), base_dir=base_dir)
        if not cli_status.get("signed_in"):
            if mode == "colab_cli" and sys.stdin is not None and sys.stdin.isatty() and cli_status.get("binary"):
                subprocess.run([cli_status["binary"], "usage"], check=False)
                _sync_colab_token_to_dotenv(base_dir)
                actions.append("Triggered interactive Colab CLI OAuth2 authorization and saved token to .env (0600)")
            else:
                res_init = _handle_colab_cli_mcp_oauth(base_dir)
                oauth_auth_url = str(res_init.get("auth_url") or "")
                actions.append("Opened Google OAuth2 sign-in tab for Colab CLI GPU provisioning; pass the returned code via oauth_code")
        else:
            _sync_colab_token_to_dotenv(base_dir)
            actions.append("Verified Colab CLI OAuth2 session already authorized and synced to .env (0600)")

    sync_target = mcp_config_path or (DEFAULT_GLOBAL_MCP_CONFIG if base_dir.resolve() == PROJECT_ROOT.resolve() else (base_dir / "mcp_config.json"))
    sync_info = sync_mcp_config(mcp_config_path=sync_target, base_dir=base_dir)
    actions.append(f"Synced MCP config ({', '.join(sync_info['servers'])}) -> {sync_info['path']}")
    ex_path = base_dir / "configs" / "mcp_config.example.json"
    sync_mcp_config(mcp_config_path=ex_path, base_dir=base_dir)
    status = inspect_auth_and_mcp_status(base_dir=base_dir, mcp_config_path=sync_target)
    out: Dict[str, Any] = {"actions": actions, "status": status}
    if oauth_auth_url:
        out["oauth_auth_url"] = oauth_auth_url
    return out


def _format_yaml_value(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    return json.dumps(str(val), ensure_ascii=False)


def _patch_yaml_lines(raw_text: str, updates: List[tuple[tuple[str, ...], Any]]) -> str:
    lines = raw_text.splitlines()
    for path_keys, val in updates:
        section = path_keys[0]
        leaf = path_keys[-1]
        in_section = False
        replaced = False
        for i, ln in enumerate(lines):
            stripped = ln.strip()
            if ln and not ln[0].isspace() and not stripped.startswith("#"):
                in_section = stripped.startswith(f"{section}:")
                continue
            if in_section:
                m = re.match(rf"^(\s+{re.escape(leaf)}:\s*)([^#\n]*?)(\s*(?:#.*)?)$", ln)
                if m and (len(path_keys) == 2 or path_keys[1] in ("rvc",)):
                    lines[i] = f"{m.group(1)}{_format_yaml_value(val)}{m.group(3)}"
                    replaced = True
                    break
        if not replaced:
            return ""
    return "\n".join(lines) + "\n"


def update_studio_config(
    profile: str = "colab_pro",
    updates: Optional[Dict[str, Any]] = None,
    base_dir: Path = PROJECT_ROOT,
) -> Dict[str, Any]:
    """Reads and persists configuration overrides while preserving inline YAML comments."""
    fname = "colab_pro.yaml" if profile in ("colab", "colab_pro", "cloud") else "studio.yaml"
    cfg_path = base_dir / "configs" / fname
    raw_text = cfg_path.read_text(encoding="utf-8") if cfg_path.is_file() else ""
    data = yaml.safe_load(raw_text) if raw_text else {}
    if not isinstance(data, dict):
        data = {}

    u = dict(updates or {})
    mapping = {
        "target": ("runtime", "target"),
        "gpu_tier": ("runtime", "gpu_tier"),
        "high_ram": ("runtime", "high_ram"),
        "voice": ("voice", "profile"),
        "sample_rate": ("output", "sample_rate"),
        "bit_depth": ("output", "bit_depth"),
        "resampler_precision": ("output", "resampler_precision"),
        "target_lufs": ("output", "target_lufs"),
        "highpass_hz": ("output", "highpass_hz"),
        "lowpass_hz": ("output", "lowpass_hz"),
        "eq_preset": ("output", "eq_preset"),
        "noise_gate": ("output", "noise_gate"),
        "noise_gate_threshold": ("output", "noise_gate_threshold"),
        "gate_ratio": ("output", "gate_ratio"),
        "gate_release_ms": ("output", "gate_release_ms"),
        "limiter_db": ("output", "limiter_db"),
        "pre_rvc_conditioning": ("output", "pre_rvc_conditioning"),
        "video_codec": ("output", "video_codec"),
        "progress_bar": ("output", "progress_bar"),
        "f0_stabilizer": ("voice", "rvc", "f0_stabilizer"),
        "rvc_fp32": ("voice", "rvc", "rvc_fp32"),
        "intermediate_float": ("voice", "rvc", "intermediate_float"),
        "colab_url": ("colab", "agent_url"),
        "colab_token": ("colab", "agent_token"),
        "root_folder_name": ("colab", "root_folder_name"),
        "session_name": ("colab", "session_name"),
        "disconnect_mismatched": ("colab", "disconnect_mismatched"),
    }
    applied: List[tuple[tuple[str, ...], Any]] = []
    for key, path_keys in mapping.items():
        if key in u and u[key] is not None:
            cursor = data
            for part in path_keys[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor[path_keys[-1]] = u[key]
            applied.append((path_keys, u[key]))

    if applied:
        patched = _patch_yaml_lines(raw_text, applied) if raw_text else ""
        cfg_path.write_text(patched or yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"config_path": str(cfg_path), "updated": [k for k in u if k in mapping], "data": data}


def probe_stdio_mcp_server(command: str, args: Optional[List[str]] = None, cwd: Path = PROJECT_ROOT) -> Dict[str, Any]:
    """Verifies an MCP stdio server responds cleanly to initialize + tools/list."""
    t0 = time.monotonic()
    init_msg = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "studio-probe", "version": "1.0"}}}) + "\n"
    notif_msg = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
    list_msg = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n"
    proc = subprocess.Popen(
        [command, *(args or [])],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(cwd),
    )
    assert proc.stdin is not None and proc.stdout is not None
    tools: List[str] = []
    try:
        proc.stdin.write(init_msg)
        proc.stdin.flush()
        deadline = time.monotonic() + 12.0
        sent_list = False
        while time.monotonic() < deadline:
            ln = proc.stdout.readline()
            if not ln:
                break
            s = ln.strip()
            if not s.startswith("{"):
                continue
            resp = json.loads(s)
            if resp.get("id") == 1 and not sent_list:
                proc.stdin.write(notif_msg + list_msg)
                proc.stdin.flush()
                sent_list = True
            elif resp.get("id") == 2 and "result" in resp:
                tools = [t["name"] for t in resp["result"].get("tools", [])]
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
    return {"ok": len(tools) > 0, "returncode": proc.returncode or 0, "tools": tools, "elapsed_sec": round(time.monotonic() - t0, 2)}
