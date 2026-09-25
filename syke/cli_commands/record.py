"""Record command for the Syke CLI."""

from __future__ import annotations

import json
import sys

import click

from syke.cli_support.render import console
from syke.config import user_control_dir
from syke.control import admit_record


@click.command(short_help="Send a note or observation to Syke.")
@click.argument("text", required=False)
@click.option(
    "--json",
    "use_json",
    is_flag=True,
    help="Validate TEXT or stdin as one JSON record",
)
@click.option(
    "--jsonl",
    "use_jsonl",
    is_flag=True,
    help="Validate stdin as newline-delimited JSON records",
)
@click.pass_context
def record(
    ctx: click.Context,
    text: str | None,
    use_json: bool,
    use_jsonl: bool,
) -> None:
    """Send an observation, note, or research dump to Syke.

    For long or shell-sensitive content, pipe stdin instead of putting the
    content directly in your shell history.

    A record is protected input. A later synthesis decides whether it changes
    memory, workspace artifacts, or nothing.
    """
    user_id = ctx.obj["user"]
    control_dir = user_control_dir(user_id)

    if use_jsonl:
        if not sys.stdin.isatty():
            lines = sys.stdin.read().strip().splitlines()
        elif text:
            lines = text.strip().splitlines()
        else:
            raise click.UsageError("--jsonl requires piped input or text argument")

        accepted = 0
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as e:
                raise click.UsageError(f"Line {i + 1}: invalid JSON — {e}") from None

            admit_record(control_dir, line)
            accepted += 1

        console.print(f"Accepted [green]{accepted}[/green] records")
        return

    if use_json:
        raw = text or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
        if not raw:
            raise click.UsageError("--json requires a JSON string as argument or stdin")

        try:
            json.loads(raw)
        except json.JSONDecodeError as e:
            raise click.UsageError(f"Invalid JSON: {e}") from None

        record_id = admit_record(control_dir, raw)
        console.print(f"Record accepted. [dim]({record_id})[/dim]")
        return

    content = text or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
    if not content:
        raise click.UsageError("Nothing to record. Pass text as argument or pipe stdin.")

    record_id = admit_record(control_dir, content)
    console.print(f"Record accepted. [dim]({record_id})[/dim]")
