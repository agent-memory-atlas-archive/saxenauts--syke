from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import click
import pytest

from syke.cli_support import installers


def _result(returncode: int = 0, stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout)


def test_managed_install_requires_source_checkout() -> None:
    with patch("syke.cli_support.installers._is_source_install", return_value=False):
        with pytest.raises(click.ClickException, match="source checkout"):
            installers.run_managed_checkout_install(
                user_id="test", installer="auto", restart_daemon=True, prompt=False
            )


def test_managed_install_refuses_to_install_until_daemon_stops() -> None:
    with (
        patch("syke.cli_support.installers._is_source_install", return_value=True),
        patch("syke.cli_support.installers.resolve_managed_installer", return_value="uv"),
        patch("syke.daemon.daemon.is_running", return_value=(True, {"pid": 9})),
        patch("syke.daemon.daemon.stop_and_unload"),
        patch(
            "syke.cli_support.installers.wait_for_daemon_shutdown",
            return_value={"running": True, "registered": False},
        ),
        patch("syke.cli_support.installers.subprocess.run") as run_install,
    ):
        with pytest.raises(click.ClickException, match="did not stop cleanly"):
            installers.run_managed_checkout_install(
                user_id="test", installer="auto", restart_daemon=True, prompt=False
            )
    run_install.assert_not_called()


def test_failed_managed_install_restores_previous_daemon() -> None:
    with (
        patch("syke.cli_support.installers._is_source_install", return_value=True),
        patch("syke.cli_support.installers.resolve_managed_installer", return_value="uv"),
        patch("syke.daemon.daemon.is_running", return_value=(True, {"pid": 9})),
        patch("syke.daemon.daemon.stop_and_unload"),
        patch(
            "syke.cli_support.installers.wait_for_daemon_shutdown",
            return_value={"running": False, "registered": False},
        ),
        patch(
            "syke.cli_support.installers.subprocess.run",
            return_value=_result(returncode=1, stdout="build failed"),
        ),
        patch("syke.daemon.daemon.install_and_start") as restart,
        patch(
            "syke.cli_support.installers.wait_for_daemon_startup",
            return_value={"running": True, "ipc": {"ok": True}},
        ),
    ):
        with pytest.raises(click.ClickException, match="Previous daemon restored"):
            installers.run_managed_checkout_install(
                user_id="test", installer="auto", restart_daemon=True, prompt=False
            )
    restart.assert_called_once_with("test")


def test_successful_managed_install_requires_healthy_restart() -> None:
    with (
        patch("syke.cli_support.installers._is_source_install", return_value=True),
        patch("syke.cli_support.installers.resolve_managed_installer", return_value="uv"),
        patch("syke.daemon.daemon.is_running", return_value=(True, {"pid": 9})),
        patch("syke.daemon.daemon.stop_and_unload"),
        patch(
            "syke.cli_support.installers.wait_for_daemon_shutdown",
            return_value={"running": False, "registered": False},
        ),
        patch(
            "syke.cli_support.installers.subprocess.run", return_value=_result(returncode=0)
        ) as run_install,
        patch("syke.daemon.daemon.install_and_start") as restart,
        patch(
            "syke.cli_support.installers.wait_for_daemon_startup",
            return_value={"running": True, "ipc": {"ok": True}},
        ),
    ):
        installers.run_managed_checkout_install(
            user_id="test", installer="auto", restart_daemon=True, prompt=False
        )

    assert run_install.call_args.args[0] == [
        "uv",
        "tool",
        "install",
        "--force",
        "--reinstall",
        "--refresh",
        "--no-cache",
        ".",
    ]
    restart.assert_called_once_with("test")


def test_managed_install_reports_unhealthy_restart() -> None:
    with (
        patch("syke.cli_support.installers._is_source_install", return_value=True),
        patch("syke.cli_support.installers.resolve_managed_installer", return_value="uv"),
        patch("syke.daemon.daemon.is_running", return_value=(True, {"pid": 9})),
        patch("syke.daemon.daemon.stop_and_unload"),
        patch(
            "syke.cli_support.installers.wait_for_daemon_shutdown",
            return_value={"running": False, "registered": False},
        ),
        patch("syke.cli_support.installers.subprocess.run", return_value=_result()),
        patch("syke.daemon.daemon.install_and_start"),
        patch(
            "syke.cli_support.installers.wait_for_daemon_startup",
            return_value={"running": True, "ipc": {"ok": False, "detail": "warming"}},
        ),
    ):
        with pytest.raises(click.ClickException, match="warm ask is not ready"):
            installers.run_managed_checkout_install(
                user_id="test", installer="auto", restart_daemon=True, prompt=False
            )


def test_restart_disabled_does_not_stop_existing_daemon() -> None:
    with (
        patch("syke.cli_support.installers._is_source_install", return_value=True),
        patch("syke.cli_support.installers.resolve_managed_installer", return_value="uv"),
        patch("syke.daemon.daemon.is_running", return_value=(True, {"pid": 9})),
        patch("syke.daemon.daemon.stop_and_unload") as stop,
        patch("syke.cli_support.installers.subprocess.run", return_value=_result()),
        patch("syke.daemon.daemon.install_and_start") as restart,
    ):
        installers.run_managed_checkout_install(
            user_id="test", installer="auto", restart_daemon=False, prompt=False
        )

    stop.assert_not_called()
    restart.assert_not_called()
