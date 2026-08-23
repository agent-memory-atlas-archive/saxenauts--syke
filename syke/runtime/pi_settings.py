"""Pi process environment for Syke's control-held runtime state."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from syke.pi_state import build_pi_agent_env

SELF_LEARN_SKILL_SOURCE = Path(__file__).parent / "skills" / "self-learn" / "SKILL.md"


def _install_self_learn_skill(pi_agent_dir: Path) -> Path:
    """Install Syke's fixed private skill into its Pi agent directory."""
    content = SELF_LEARN_SKILL_SOURCE.read_text(encoding="utf-8")
    target = pi_agent_dir / "skills" / "self-learn" / "SKILL.md"
    if not target.is_symlink() and target.is_file():
        try:
            if target.read_text(encoding="utf-8") == content:
                return target
        except OSError:
            pass

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target


def configure_pi_workspace(
    workspace_root: Path,
    *,
    session_dir: Path,
) -> dict[str, str]:
    """Prepare Syke's private Pi resources and return its environment."""
    workspace = workspace_root.expanduser().resolve()
    sessions = session_dir.expanduser().resolve()
    runtime_tmp = sessions.parent / "runtime" / "tmp"
    if runtime_tmp == workspace or runtime_tmp.is_relative_to(workspace):
        raise ValueError("Pi runtime state must be outside the owned workspace")
    runtime_tmp.mkdir(parents=True, exist_ok=True)
    env = build_pi_agent_env(
        {
            "SYKE_PI_TMPDIR": str(runtime_tmp),
            "TMPDIR": str(runtime_tmp),
            "TMP": str(runtime_tmp),
            "TEMP": str(runtime_tmp),
        }
    )
    _install_self_learn_skill(Path(env["PI_CODING_AGENT_DIR"]))
    return env
