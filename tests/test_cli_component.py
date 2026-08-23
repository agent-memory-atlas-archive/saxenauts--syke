from __future__ import annotations

from click.testing import CliRunner

from syke.config import user_control_dir, user_syke_db_path
from syke.control import list_records
from syke.entrypoint import cli


def test_record_persists_protected_input_without_creating_graph(cli_runner: CliRunner) -> None:
    sensitive = "Decision: keep $(literal) and `quoted` chars; path='~/A B' & <xml>."
    result = cli_runner.invoke(cli, ["--user", "test", "record"], input=sensitive)

    assert result.exit_code == 0
    assert "Record accepted" in result.output
    assert not user_syke_db_path("test").exists()
    assert [record["payload"] for record in list_records(user_control_dir("test"))] == [sensitive]


def test_connect_installs_adapters_without_creating_graph(cli_runner: CliRunner) -> None:
    from syke.runtime import workspace

    result = cli_runner.invoke(cli, ["--user", "test", "connect"])

    assert result.exit_code == 0
    installed_adapters = sorted(
        path.name for path in (workspace.WORKSPACE_ROOT / "adapters").glob("*.md")
    )
    assert installed_adapters
    assert not workspace.SYKE_DB.exists()


def test_observe_renders_an_empty_real_store(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(cli, ["--user", "test", "observe"])

    assert result.exit_code == 0
    assert result.output.splitlines()[0].startswith("Syke ")
    assert result.output.splitlines()[0].endswith("test")
    assert "## Memory" in result.output
    assert "## Operations" in result.output
