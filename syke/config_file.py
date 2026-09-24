"""TOML configuration file — ~/.syke/config.toml loading and defaults."""

from __future__ import annotations

import getpass
import json
import logging
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, get_origin, get_type_hints

log = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".syke" / "config.toml"
THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


# ---------------------------------------------------------------------------
# Typed config sections (frozen dataclasses)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SynthesisConfig:
    thinking_level: str = "medium"
    timeout: int = 600
    first_run_timeout: int = 1500


@dataclass(frozen=True)
class DaemonConfig:
    interval: int = 900


@dataclass(frozen=True)
class AskConfig:
    timeout: int = 600
    max_parallel: int = 8


@dataclass(frozen=True)
class DistributionPathsConfig:
    skills_dirs: tuple[str, ...] = (
        "~/.agents/skills",
        "~/.pi/agent/skills",
        "~/.claude/skills",
        "~/.gemini/antigravity-cli/skills",
        "~/.hermes/skills",
        "~/.codex/skills",
        "~/.cursor/skills",
        "~/.config/opencode/skills",
    )


@dataclass(frozen=True)
class PathsConfig:
    distribution: DistributionPathsConfig = field(default_factory=DistributionPathsConfig)


@dataclass(frozen=True)
class SykeConfig:
    """Top-level Syke configuration."""

    user: str = ""
    timezone: str = "auto"
    synthesis: SynthesisConfig = field(default_factory=SynthesisConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    ask: AskConfig = field(default_factory=AskConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)


# ---------------------------------------------------------------------------
# Path expansion
# ---------------------------------------------------------------------------


def expand_path(p: str) -> Path:
    """Expand ~ and resolve a config path string to an absolute Path."""
    return Path(p).expanduser().resolve()


# ---------------------------------------------------------------------------
# TOML → dataclass construction
# ---------------------------------------------------------------------------


def _matches_type(value: object, expected: object) -> bool:
    if expected is int:
        return type(value) is int
    if expected is str:
        return isinstance(value, str)
    if get_origin(expected) is tuple:
        return isinstance(value, tuple) and all(isinstance(item, str) for item in value)
    return isinstance(expected, type) and isinstance(value, expected)


def _build_nested(cls: Any, raw: dict[str, Any]) -> Any:
    """Construct a frozen dataclass from a raw TOML dict, ignoring unknown keys."""
    kwargs: dict[str, Any] = {}
    valid_names = {f.name for f in fields(cls)}
    resolved_hints = get_type_hints(cls)
    for key, value in raw.items():
        py_key = key.replace("-", "_")
        if py_key not in valid_names:
            log.warning("config.toml: ignoring unknown key %r in [%s]", key, cls.__name__)
            continue
        field_type = resolved_hints[py_key]
        if isinstance(value, dict) and hasattr(field_type, "__dataclass_fields__"):
            kwargs[py_key] = _build_nested(field_type, value)
            continue
        if py_key == "skills_dirs" and isinstance(value, list):
            value = tuple(value)
        if not _matches_type(value, field_type):
            raise ValueError(f"{cls.__name__}.{py_key} has the wrong type")
        kwargs[py_key] = value
    return cls(**kwargs)


def _build_config(raw: dict[str, Any]) -> SykeConfig:
    """Build SykeConfig from raw TOML dict."""
    kwargs: dict[str, Any] = {}

    # Scalar top-level keys
    for key in ("user", "timezone"):
        if key in raw:
            if not isinstance(raw[key], str):
                raise ValueError(f"SykeConfig.{key} has the wrong type")
            kwargs[key] = raw[key]

    # Nested sections → sub-dataclasses
    section_map: dict[str, type[Any]] = {
        "synthesis": SynthesisConfig,
        "daemon": DaemonConfig,
        "ask": AskConfig,
        "paths": PathsConfig,
    }
    known_keys = {"user", "timezone", *section_map}
    for key in raw:
        if key not in known_keys:
            log.warning("config.toml: ignoring unknown top-level key %r", key)
    for section_name, section_cls in section_map.items():
        if section_name in raw:
            section_raw = raw[section_name]
            if not isinstance(section_raw, dict):
                raise ValueError(f"config.toml: [{section_name}] must be a table")
            kwargs[section_name] = _build_nested(section_cls, section_raw)

    config = SykeConfig(**kwargs)
    positive_values = {
        "synthesis.timeout": config.synthesis.timeout,
        "synthesis.first_run_timeout": config.synthesis.first_run_timeout,
        "daemon.interval": config.daemon.interval,
        "ask.timeout": config.ask.timeout,
    }
    invalid_positive = [name for name, value in positive_values.items() if value <= 0]
    if invalid_positive:
        raise ValueError(f"config.toml values must be positive: {', '.join(invalid_positive)}")
    if config.ask.max_parallel < 0:
        raise ValueError("config.toml ask.max_parallel cannot be negative")
    if config.synthesis.thinking_level not in THINKING_LEVELS:
        raise ValueError(
            "config.toml synthesis.thinking_level must be one of " + ", ".join(THINKING_LEVELS)
        )
    return config


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(path: Path | None = None) -> SykeConfig:
    """Load config from TOML file, falling back to defaults.

    Args:
        path: Override config file path (for testing). Defaults to ~/.syke/config.toml.

    Returns:
        SykeConfig with values from file merged over defaults.
    """
    config_path = path or CONFIG_PATH

    if not config_path.exists():
        log.debug("No config file at %s, using defaults", config_path)
        # Still apply system defaults for user
        return SykeConfig(user=getpass.getuser())

    try:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)
        log.debug("Loaded config from %s", config_path)
        cfg = _build_config(raw)
        # Default user to system username if not set
        if not cfg.user:
            cfg = SykeConfig(
                user=getpass.getuser(),
                timezone=cfg.timezone,
                synthesis=cfg.synthesis,
                daemon=cfg.daemon,
                ask=cfg.ask,
                paths=cfg.paths,
            )
        return cfg
    except tomllib.TOMLDecodeError as e:
        log.error("Failed to parse %s: %s — using defaults", config_path, e)
        return SykeConfig(user=getpass.getuser())
    except OSError as e:
        log.warning("Cannot read %s: %s — using defaults", config_path, e)
        return SykeConfig(user=getpass.getuser())
    except (TypeError, ValueError) as e:
        log.error("Invalid configuration in %s: %s — using defaults", config_path, e)
        return SykeConfig(user=getpass.getuser())


# ---------------------------------------------------------------------------
# Template generation (for `syke config init`)
# ---------------------------------------------------------------------------


def generate_default_config(user: str = "") -> str:
    """Generate a default config.toml with comments."""
    user = user or getpass.getuser()
    encoded_user = json.dumps(user)
    return f"""\
# Syke configuration
# Docs: https://github.com/saxenauts/syke

# ── Identity ────────────────────────────────────────────────────────────────
user = {encoded_user}
timezone = "auto"

# ── Synthesis agent ─────────────────────────────────────────────────────────
[synthesis]
thinking_level = "medium"  # off|minimal|low|medium|high|xhigh|max
timeout = 600            # wall-clock timeout (seconds)
first_run_timeout = 1500 # wall-clock timeout for the first synthesis

# ── Background service ─────────────────────────────────────────────────────
[daemon]
interval = 900           # seconds between sync cycles

# ── Ask agent (syke ask "question") ─────────────────────────────────────────
[ask]
timeout = 600            # seconds
max_parallel = 8         # max daemon-owned temporary ask workers (0 = unlimited)

# ── Capability installation paths ──────────────────────────────────────────
[paths.distribution]
skills_dirs = [
    "~/.agents/skills",
    "~/.pi/agent/skills",
    "~/.claude/skills",
    "~/.gemini/antigravity-cli/skills",
    "~/.hermes/skills",
    "~/.codex/skills",
    "~/.cursor/skills",
    "~/.config/opencode/skills",
]
"""
