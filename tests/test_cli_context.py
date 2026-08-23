from __future__ import annotations

from pathlib import Path

from syke.cli_support import context


def test_get_db_prepares_and_reconciles_before_open(monkeypatch, tmp_path: Path) -> None:
    calls: list[object] = []
    db_path = tmp_path / "workspace" / "syke.db"
    fake_db = object()

    monkeypatch.setattr(context, "user_syke_db_path", lambda _user_id: db_path)
    monkeypatch.setattr(
        context,
        "user_workspace_dir",
        lambda _user_id: db_path.parent,
    )

    def reconcile(user_id, *, memex_path, expected_db_path):
        calls.append(("reconcile", user_id, memex_path, expected_db_path))

    def open_db(path, *, user_id):
        calls.append(("open", path, user_id))
        return fake_db

    monkeypatch.setattr(context, "try_reconcile_before_database_use", reconcile)
    monkeypatch.setattr(context, "SykeDB", open_db)

    assert context.get_db("person") is fake_db
    assert calls == [
        ("reconcile", "person", db_path.parent / "MEMEX.md", db_path),
        ("open", db_path, "person"),
    ]
