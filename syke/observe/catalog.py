from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class DiscoverRoot:
    path: str
    include: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DiscoverConfig:
    roots: list[DiscoverRoot]


@dataclass(frozen=True)
class SourceSpec:
    source: str
    format_cluster: str
    discover: DiscoverConfig


_CATALOG: tuple[SourceSpec, ...] = (
    SourceSpec(
        source="claude-code",
        format_cluster="jsonl",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(path="~/.claude/projects", include=["**/*.jsonl"]),
                DiscoverRoot(path="~/.claude/transcripts", include=["*.jsonl"]),
            ]
        ),
    ),
    SourceSpec(
        source="codex",
        format_cluster="mixed",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.codex",
                    include=["**/*.jsonl", "**/*.db", "**/*.sqlite", "config.toml"],
                ),
            ]
        ),
    ),
    SourceSpec(
        source="pi",
        format_cluster="jsonl",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.pi/agent",
                    include=["sessions/**/*.jsonl"],
                ),
            ]
        ),
    ),
    SourceSpec(
        source="opencode",
        format_cluster="sqlite",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.local/share/opencode",
                    include=["*.db", "*.sqlite"],
                )
            ]
        ),
    ),
    SourceSpec(
        source="cursor",
        format_cluster="mixed",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/Library/Application Support/Cursor/User/workspaceStorage",
                    include=[
                        "**/chatSessions/*.json",
                        "**/chatSessions/*.jsonl",
                        "**/state.vscdb",
                        "**/state.vscdb_backup",
                    ],
                ),
                DiscoverRoot(
                    path="~/Library/Application Support/Cursor/User/globalStorage",
                    include=["state.vscdb", "state.vscdb_backup"],
                ),
                DiscoverRoot(
                    path="~/.config/Cursor/User/workspaceStorage",
                    include=[
                        "**/chatSessions/*.json",
                        "**/chatSessions/*.jsonl",
                        "**/state.vscdb",
                        "**/state.vscdb_backup",
                    ],
                ),
                DiscoverRoot(
                    path="~/.config/Cursor/User/globalStorage",
                    include=["state.vscdb", "state.vscdb_backup"],
                ),
            ]
        ),
    ),
    SourceSpec(
        source="copilot",
        format_cluster="mixed",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.copilot/session-state",
                    include=["**/events.jsonl", "**/workspace.yaml"],
                ),
                DiscoverRoot(
                    path="~/Library/Application Support/Code/User/workspaceStorage",
                    include=["**/chatSessions/*.json", "**/chatSessions/*.jsonl"],
                ),
                DiscoverRoot(
                    path=(
                        "~/Library/Application Support"
                        "/Code/User/globalStorage/emptyWindowChatSessions"
                    ),
                    include=["*.json", "*.jsonl"],
                ),
                DiscoverRoot(
                    path="~/.config/Code/User/workspaceStorage",
                    include=["**/chatSessions/*.json", "**/chatSessions/*.jsonl"],
                ),
                DiscoverRoot(
                    path="~/.config/Code/User/globalStorage/emptyWindowChatSessions",
                    include=["*.json", "*.jsonl"],
                ),
            ]
        ),
    ),
    SourceSpec(
        source="antigravity",
        format_cluster="mixed",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.gemini/antigravity",
                    include=[
                        "brain/*/.system_generated/logs/transcript.jsonl",
                        "brain/**/*.md",
                        "brain/**/*.md.metadata.json",
                        "browser_recordings/*/metadata.json",
                    ],
                ),
                DiscoverRoot(
                    path="~/.gemini/antigravity-cli",
                    include=[
                        "brain/*/.system_generated/logs/transcript.jsonl",
                        "brain/**/*.md",
                        "brain/**/*.md.metadata.json",
                    ],
                ),
                DiscoverRoot(
                    path="~/.gemini/antigravity-ide",
                    include=[
                        "brain/*/.system_generated/logs/transcript.jsonl",
                        "brain/**/*.md",
                        "brain/**/*.md.metadata.json",
                    ],
                ),
            ]
        ),
    ),
    SourceSpec(
        source="hermes",
        format_cluster="mixed",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.hermes",
                    include=["state.db", "sessions/*.json"],
                )
            ]
        ),
    ),
)


def active_sources() -> tuple[SourceSpec, ...]:
    return _CATALOG


def get_source(source: str) -> SourceSpec | None:
    for spec in _CATALOG:
        if spec.source == source:
            return spec
    return None


def _resolve_root_path(raw_path: str, *, home: Path | None = None) -> Path:
    if home is not None and raw_path.startswith("~/"):
        return home / raw_path[2:]
    return Path(raw_path).expanduser()


def iter_discovered_files(spec: SourceSpec, *, home: Path | None = None) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for root in spec.discover.roots:
        root_path = _resolve_root_path(root.path, home=home)
        if root_path.is_file():
            try:
                resolved = root_path.resolve()
            except OSError:
                continue
            if resolved not in seen:
                seen.add(resolved)
                files.append(resolved)
            continue
        if not root_path.exists() or not root_path.is_dir():
            continue
        for pattern in root.include or ["**/*"]:
            for match in root_path.glob(pattern):
                if not match.is_file():
                    continue
                try:
                    resolved = match.resolve()
                except OSError:
                    continue
                if resolved in seen:
                    continue
                seen.add(resolved)
                files.append(resolved)
    return sorted(files)


def discovered_roots(spec: SourceSpec, *, home: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    for root in spec.discover.roots:
        root_path = _resolve_root_path(root.path, home=home)
        if root_path.exists():
            try:
                roots.append(root_path.resolve())
            except OSError:
                continue
    return roots
