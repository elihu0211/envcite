"""環境變數設定：一次列出所有問題變數、不洩漏值，且與 .env.example 同步。"""

from pathlib import Path

import pytest

from envcite_ontology.settings import (
    DatabaseSettings,
    ServerSettings,
    SnapshotSettings,
    load,
)

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"

# 不經 Settings 讀取的 .env.example 條目：models.toml 以 `*_env` 引用的模型金鑰。
NON_SETTINGS_KEYS = {"CHAT_API_KEY"}

ALL_NAMES = (
    "ONTOLOGY_DSN",
    "ACQUIRE_SNAPSHOT_DIR",
    "ONTOLOGY_GRPC_PORT",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ALL_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_missing_variables_are_all_reported_in_one_message() -> None:
    with pytest.raises(SystemExit) as raised:
        load(ServerSettings)
    message = str(raised.value)
    assert message.startswith("invalid environment: ")
    assert "ONTOLOGY_DSN" in message
    assert "ONTOLOGY_GRPC_PORT" not in message  # 有預設值，不算缺


def test_snapshot_settings_require_the_snapshot_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONTOLOGY_DSN", "postgresql://u:p@h/db")
    with pytest.raises(SystemExit, match="ACQUIRE_SNAPSHOT_DIR"):
        load(SnapshotSettings)
    monkeypatch.setenv("ACQUIRE_SNAPSHOT_DIR", "/tmp/snap")
    assert load(SnapshotSettings).acquire_snapshot_dir == Path("/tmp/snap")


def test_empty_values_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONTOLOGY_DSN", "")
    with pytest.raises(SystemExit, match="ONTOLOGY_DSN"):
        load(DatabaseSettings)


@pytest.mark.parametrize("port", ["0", "65536", "-1", "http", ""])
def test_invalid_port_is_rejected(monkeypatch: pytest.MonkeyPatch, port: str) -> None:
    monkeypatch.setenv("ONTOLOGY_DSN", "postgresql://u:p@h/db")
    monkeypatch.setenv("ONTOLOGY_GRPC_PORT", port)
    with pytest.raises(SystemExit, match="ONTOLOGY_GRPC_PORT"):
        load(ServerSettings)


def test_defaults_and_names_are_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ontology_dsn", "postgresql://u:p@h/db")
    settings = load(ServerSettings)
    assert settings.ontology_dsn == "postgresql://u:p@h/db"
    assert settings.ontology_grpc_port == 50051


def test_unrelated_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONTOLOGY_DSN", "postgresql://u:p@h/db")
    monkeypatch.setenv("CHAT_API_KEY", "x")
    assert load(DatabaseSettings).ontology_dsn == "postgresql://u:p@h/db"


def test_error_message_has_no_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONTOLOGY_DSN", "postgresql://u:p@h/db")
    monkeypatch.setenv("ONTOLOGY_GRPC_PORT", "not-a-port")
    with pytest.raises(SystemExit) as raised:
        load(ServerSettings)
    assert "ONTOLOGY_GRPC_PORT" in str(raised.value)
    assert "not-a-port" not in str(raised.value)


def _env_names(*classes: type[DatabaseSettings]) -> tuple[set[str], set[str]]:
    """回傳（全部環境變數名稱, 其中有預設值的）。"""
    names: set[str] = set()
    optional: set[str] = set()
    for cls in classes:
        for field, info in cls.model_fields.items():
            names.add(field.upper())
            if not info.is_required():
                optional.add(field.upper())
    return names, optional


def test_env_example_matches_settings() -> None:
    keys = {
        line.partition("=")[0].strip()
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }
    names, optional = _env_names(DatabaseSettings, SnapshotSettings, ServerSettings)
    expected_required = (names - optional) | NON_SETTINGS_KEYS
    hint = "settings.py 與 .env.example 不一致，請更新 .env.example"
    assert expected_required <= keys, f"{hint}：缺 {sorted(expected_required - keys)}"
    assert keys <= names | NON_SETTINGS_KEYS, (
        f"{hint}：多出 {sorted(keys - names - NON_SETTINGS_KEYS)}"
    )
