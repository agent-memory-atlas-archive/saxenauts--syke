"""Configuration — config.toml loading, .env loading, paths, and runtime knobs."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from syke.config_file import THINKING_LEVELS, SykeConfig, expand_path, load_config

# Syke home directory (persisted config, credentials)
SYKE_HOME = Path.home() / ".syke"

# Load ~/.syke/.env first (persisted daemon-safe environment config).
_syke_env = SYKE_HOME / ".env"
if _syke_env.exists():
    load_dotenv(_syke_env)

# Root of the syke project
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Load config.toml (after ~/.syke/.env so env vars can override) ───────────

CFG: SykeConfig = load_config()


def _is_source_install() -> bool:
    """True when running from a git clone (pyproject.toml exists at PROJECT_ROOT)."""
    return (PROJECT_ROOT / "pyproject.toml").exists()


# ── Paths (config.toml → env var override) ──────────────────────────────────


# Capability installation paths
SKILLS_DIRS = [expand_path(p) for p in CFG.paths.distribution.skills_dirs]


# ── Helper: env var or config value ─────────────────────────────────────────


def _env_str(var: str, cfg_val: str | None) -> str | None:
    """Return env var if set, else config value. None if both empty."""
    env = os.getenv(var)
    if env:
        return env
    return cfg_val if cfg_val else None


def _env_int(var: str, cfg_val: int) -> int:
    """Return env var as int if set, else config value."""
    env = os.getenv(var)
    return int(env) if env else cfg_val


# ── Agent settings ─────────────────────────────────────────────────────────

# Ask agent
# The ask timeout is durable config, not caller-local environment. Agents often
# launch Syke from transient shells; they must not be able to shorten daemon asks.
ASK_TIMEOUT: int = CFG.ask.timeout
ASK_MAX_PARALLEL: int = _env_int("SYKE_MAX_PARALLEL_ASKS", CFG.ask.max_parallel)

# Synthesis agent
SYNC_TIMEOUT: int = _env_int("SYKE_SYNC_TIMEOUT", CFG.synthesis.timeout)
FIRST_RUN_SYNC_TIMEOUT: int = _env_int(
    "SYKE_SYNC_FIRST_RUN_TIMEOUT",
    CFG.synthesis.first_run_timeout,
)
SYNC_THINKING_LEVEL = _env_str("SYKE_SYNC_THINKING_LEVEL", CFG.synthesis.thinking_level) or "medium"
if SYNC_THINKING_LEVEL not in THINKING_LEVELS:
    SYNC_THINKING_LEVEL = "medium"

# Daemon
DAEMON_INTERVAL: int = _env_int("SYKE_DAEMON_INTERVAL", CFG.daemon.interval)

# Web UI server (read-only timeline / memex / memory / trace viewer)
WEB_PORT: int = _env_int("SYKE_WEB_PORT", 8765)
WEB_ENABLED: bool = os.getenv("SYKE_WEB_ENABLED", "1") not in {"0", "false", "False"}

# Timezone
SYKE_TIMEZONE: str = os.getenv("SYKE_TIMEZONE", "") or CFG.timezone

# Default user — env var > config.toml > system username
DEFAULT_USER: str = os.getenv("SYKE_USER", "") or CFG.user


# ── Single-person paths ──────────────────────────────────────────────────────
#
# SYKE_HOME is the installation root. The controller can mutate workspace/
# only; control/ is owned by the trusted host runtime.


def user_workspace_dir(user_id: str) -> Path:
    """Return the controller-writable workspace for this installation."""
    _ = user_id
    override = os.getenv("SYKE_WORKSPACE_ROOT")
    path = Path(override).expanduser().resolve() if override else SYKE_HOME / "workspace"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_control_dir(user_id: str) -> Path:
    """Return the host-owned control directory for this installation."""
    _ = user_id
    override = os.getenv("SYKE_CONTROL_ROOT")
    path = Path(override).expanduser().resolve() if override else SYKE_HOME / "control"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_data_dir(user_id: str) -> Path:
    """Return the installation root, creating it if needed."""
    _ = user_id
    SYKE_HOME.mkdir(parents=True, exist_ok=True)
    return SYKE_HOME


def user_syke_db_path(user_id: str) -> Path:
    """Return the controller-writable graph database path without changing state.

    Override: SYKE_DB env var bypasses the standard path resolution.
    """
    _ = user_id
    env_override = os.getenv("SYKE_DB")
    if env_override:
        return Path(env_override).expanduser().resolve()
    workspace_override = os.getenv("SYKE_WORKSPACE_ROOT")
    workspace = (
        Path(workspace_override).expanduser().resolve()
        if workspace_override
        else SYKE_HOME / "workspace"
    )
    return workspace / "syke.db"
