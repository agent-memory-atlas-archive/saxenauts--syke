"""Hidden commands used by Syke-owned background jobs."""

from __future__ import annotations

from pathlib import Path

import click


@click.command(name="_macos-filesystem-probe", hidden=True)
@click.option("--result", "result_path", type=click.Path(path_type=Path), required=True)
def macos_filesystem_probe(result_path: Path) -> None:
    """Run one trusted protected-folder check for the setup LaunchAgent."""
    from syke.runtime.macos_filesystem_access import run_probe_worker

    raise SystemExit(run_probe_worker(result_path))
