"""指令入口：每個 console script 解析得到、`--help` 可用，文件載入入口走得通。"""

import importlib
import json
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest

Conn = psycopg.Connection[tuple[object, ...]]

SCRIPTS: dict[str, str] = tomllib.loads(
    (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
)["project"]["scripts"]
#: 啟動就做事、不吃參數的入口：只確認解析得到。
NO_ARGUMENTS = {"envcite-ontology", "envcite-migrate"}
EXIT_INVALID = 2


def _entry(name: str) -> Callable[[], None]:
    module, attr = SCRIPTS[name].split(":")
    entry: Callable[[], None] = getattr(importlib.import_module(module), attr)
    return entry


def _run(monkeypatch: pytest.MonkeyPatch, name: str, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", [name, *argv])
    _entry(name)()


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_every_console_script_resolves_to_a_callable(name: str) -> None:
    assert callable(_entry(name))


@pytest.mark.parametrize("name", sorted(set(SCRIPTS) - NO_ARGUMENTS))
def test_help_prints_the_usage_and_exits_cleanly(
    name: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        _run(monkeypatch, name, "--help")

    assert raised.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("name", ["envcite-ruling", "envcite-guidance"])
def test_a_subcommand_is_required(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit) as raised:
        _run(monkeypatch, name)

    assert raised.value.code == EXIT_INVALID


#: 文件載入入口 → 最小的有效文件（沒有任何裁判／公文／陳述，載入不寫東西）。
EMPTY_DOCUMENTS = {
    "envcite-ruling": {"rulings": []},
    "envcite-guidance": {
        "derivation": {"method": "human", "agent": "cli", "agent_version": "1"},
        "documents": [],
    },
}
MALFORMED_DOCUMENTS = {
    "envcite-ruling": {"rulings": [{"ruling_no": "釋字第1號"}]},
    "envcite-guidance": {"derivation": {"method": "human"}, "documents": []},
}


@pytest.mark.parametrize("name", sorted(EMPTY_DOCUMENTS))
def test_a_valid_document_is_loaded(
    name: str, conn: Conn, dsn: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ENVCITE_ONTOLOGY_DSN", dsn)
    document = tmp_path / "document.json"
    document.write_text(json.dumps(EMPTY_DOCUMENTS[name]), encoding="utf-8")

    _run(monkeypatch, name, "load", str(document))  # 正常結束：不丟 SystemExit


@pytest.mark.parametrize("name", sorted(MALFORMED_DOCUMENTS))
def test_a_malformed_or_unreadable_document_exits_with_the_invalid_code(
    name: str, conn: Conn, dsn: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ENVCITE_ONTOLOGY_DSN", dsn)
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
    monkeypatch.setenv("ENVCITE_ONTOLOGY_DSN", dsn)
    valid = tmp_path / "valid.json"
    valid.write_text(
        json.dumps({"derivation": {"method": "human", "agent": "cli", "agent_version": "1"}}),
        encoding="utf-8",
    )
    malformed = tmp_path / "malformed.json"
    malformed.write_text(json.dumps({"statements": []}), encoding="utf-8")

    _run(monkeypatch, "envcite-annotate", str(valid))
    with pytest.raises(SystemExit) as raised:
        _run(monkeypatch, "envcite-annotate", str(malformed))

    assert raised.value.code == EXIT_INVALID
