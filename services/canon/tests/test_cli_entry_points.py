"""指令入口：唯一的 console script 解析得到，每個子指令 `--help` 可用，文件載入入口走得通。"""

import importlib
import json
import logging
import sys
import tomllib
import types
from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest

from canon.cli import SUBCOMMANDS
from canon.cli import main as canon

Conn = psycopg.Connection[tuple[object, ...]]

SCRIPTS: dict[str, str] = tomllib.loads(
    (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
)["project"]["scripts"]
#: 啟動就做事、不吃參數的子指令：只確認解析得到。
NO_ARGUMENTS = {"serve", "migrate"}
EXIT_INVALID = 2


def _run(monkeypatch: pytest.MonkeyPatch, name: str, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["canon", name, *argv])
    canon()


def test_the_only_console_script_is_canon() -> None:
    assert SCRIPTS == {"canon": "canon.cli:main"}
    module, attr = SCRIPTS["canon"].split(":")
    assert callable(getattr(importlib.import_module(module), attr))


@pytest.mark.parametrize("name", sorted(SUBCOMMANDS))
def test_every_subcommand_resolves_to_a_callable(name: str) -> None:
    module, _ = SUBCOMMANDS[name]
    entry: Callable[[], None] = importlib.import_module(module).main
    assert callable(entry)


@pytest.mark.parametrize("name", sorted(set(SUBCOMMANDS) - NO_ARGUMENTS))
def test_help_prints_the_usage_and_exits_cleanly(
    name: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        _run(monkeypatch, name, "--help")

    assert raised.value.code == 0
    assert f"usage: canon {name}" in capsys.readouterr().out.lower()


def test_without_a_subcommand_it_lists_them_and_exits_with_the_usage_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["canon"])
    with pytest.raises(SystemExit) as raised:
        canon()

    assert raised.value.code == EXIT_INVALID
    listed = capsys.readouterr().err
    assert all(name in listed for name in SUBCOMMANDS)


def test_the_dispatcher_configures_logging_once_before_the_subcommand_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """日誌格式是與日誌管線的契約：只在處理程序的入口設定一次，子指令不再各設各的。"""
    events: list[object] = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: events.append(kwargs))
    monkeypatch.setitem(
        sys.modules, "canon_fake_command", types.SimpleNamespace(main=lambda: events.append("ran"))
    )
    monkeypatch.setitem(SUBCOMMANDS, "fake", ("canon_fake_command", "測試用"))

    _run(monkeypatch, "fake")

    assert events == [
        {"level": logging.INFO, "format": "%(asctime)s %(levelname)s %(message)s"},
        "ran",
    ]


def test_an_unknown_subcommand_exits_with_the_usage_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        _run(monkeypatch, "deploy")

    assert raised.value.code == EXIT_INVALID
    assert "deploy" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["ruling", "guidance", "judgment"])
def test_a_subcommand_is_required(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit) as raised:
        _run(monkeypatch, name)

    assert raised.value.code == EXIT_INVALID


#: 文件載入入口 → 最小的有效文件（沒有任何裁判／公文／陳述，載入不寫東西）。
EMPTY_DOCUMENTS = {
    "ruling": {"rulings": []},
    "guidance": {
        "derivation": {"method": "human", "agent": "cli", "agent_version": "1"},
        "documents": [],
    },
}
MALFORMED_DOCUMENTS = {
    "ruling": {"rulings": [{"ruling_no": "釋字第1號"}]},
    "guidance": {"derivation": {"method": "human"}, "documents": []},
}


@pytest.mark.parametrize("name", sorted(EMPTY_DOCUMENTS))
def test_a_valid_document_is_loaded(
    name: str, conn: Conn, dsn: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ENVCITE_CANON_DSN", dsn)
    document = tmp_path / "document.json"
    document.write_text(json.dumps(EMPTY_DOCUMENTS[name]), encoding="utf-8")

    _run(monkeypatch, name, "load", str(document))  # 正常結束：不丟 SystemExit


@pytest.mark.parametrize("name", sorted(MALFORMED_DOCUMENTS))
def test_a_malformed_or_unreadable_document_exits_with_the_invalid_code(
    name: str, conn: Conn, dsn: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ENVCITE_CANON_DSN", dsn)
    wrong_shape = tmp_path / "wrong-shape.json"
    wrong_shape.write_text(json.dumps(MALFORMED_DOCUMENTS[name]), encoding="utf-8")
    not_json = tmp_path / "not-json.json"
    not_json.write_text("{not json", encoding="utf-8")

    for path in (wrong_shape, not_json):
        with pytest.raises(SystemExit) as raised:
            _run(monkeypatch, name, "load", str(path))
        assert raised.value.code == EXIT_INVALID


def test_annotate_loads_a_document_and_rejects_a_malformed_one(
    conn: Conn, dsn: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ENVCITE_CANON_DSN", dsn)
    valid = tmp_path / "valid.json"
    valid.write_text(
        json.dumps({"derivation": {"method": "human", "agent": "cli", "agent_version": "1"}}),
        encoding="utf-8",
    )
    malformed = tmp_path / "malformed.json"
    malformed.write_text(json.dumps({"statements": []}), encoding="utf-8")

    _run(monkeypatch, "annotate", str(valid))
    with pytest.raises(SystemExit) as raised:
        _run(monkeypatch, "annotate", str(malformed))

    assert raised.value.code == EXIT_INVALID
