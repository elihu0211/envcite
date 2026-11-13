"""環境變數設定：一次列出所有問題變數、不洩漏值，且與 .env.example 同步。"""

from pathlib import Path

import pytest

from envcite_ontology.settings import (
    PREFIX,
    DatabaseSettings,
    ServerSettings,
    SnapshotSettings,
    load,
)

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"

ALL_NAMES = (
    "ENVCITE_ONTOLOGY_DSN",
    "ENVCITE_ONTOLOGY_SNAPSHOT_DIR",
    "ENVCITE_ONTOLOGY_GRPC_PORT",
    "ENVCITE_ONTOLOGY_MODELS",
    "ENVCITE_ONTOLOGY_LEXICAL_MAX_DF",
    "ENVCITE_ONTOLOGY_GUIDANCE_MAX_DF",
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
    assert "ENVCITE_ONTOLOGY_DSN" in message
    assert "ENVCITE_ONTOLOGY_GRPC_PORT" not in message  # 有預設值，不算缺


def test_snapshot_settings_require_the_snapshot_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVCITE_ONTOLOGY_DSN", "postgresql://u:p@h/db")
    with pytest.raises(SystemExit, match="ENVCITE_ONTOLOGY_SNAPSHOT_DIR"):
        load(SnapshotSettings)
    monkeypatch.setenv("ENVCITE_ONTOLOGY_SNAPSHOT_DIR", "/tmp/snap")
    assert load(SnapshotSettings).snapshot_dir == Path("/tmp/snap")


def test_empty_values_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVCITE_ONTOLOGY_DSN", "")
    with pytest.raises(SystemExit, match="ENVCITE_ONTOLOGY_DSN"):
        load(DatabaseSettings)


@pytest.mark.parametrize("port", ["0", "65536", "-1", "http", ""])
def test_invalid_port_is_rejected(monkeypatch: pytest.MonkeyPatch, port: str) -> None:
    monkeypatch.setenv("ENVCITE_ONTOLOGY_DSN", "postgresql://u:p@h/db")
    monkeypatch.setenv("ENVCITE_ONTOLOGY_GRPC_PORT", port)
    with pytest.raises(SystemExit, match="ENVCITE_ONTOLOGY_GRPC_PORT"):
        load(ServerSettings)


@pytest.mark.parametrize("ratio", ["-0.1", "1.5", "many", ""])
def test_invalid_lexical_ratio_is_rejected(monkeypatch: pytest.MonkeyPatch, ratio: str) -> None:
    monkeypatch.setenv("ENVCITE_ONTOLOGY_DSN", "postgresql://u:p@h/db")
    monkeypatch.setenv("ENVCITE_ONTOLOGY_LEXICAL_MAX_DF", ratio)
    with pytest.raises(SystemExit, match="ENVCITE_ONTOLOGY_LEXICAL_MAX_DF"):
        load(ServerSettings)


def test_lexical_ratio_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVCITE_ONTOLOGY_DSN", "postgresql://u:p@h/db")
    assert load(ServerSettings).lexical_max_df > 0
    monkeypatch.setenv("ENVCITE_ONTOLOGY_LEXICAL_MAX_DF", "0")
    assert load(ServerSettings).lexical_max_df == 0


def test_defaults_and_names_are_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("envcite_ontology_dsn", "postgresql://u:p@h/db")
    settings = load(ServerSettings)
    assert settings.dsn == "postgresql://u:p@h/db"
    assert settings.grpc_port == 50051


def test_unrelated_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVCITE_ONTOLOGY_DSN", "postgresql://u:p@h/db")
    monkeypatch.setenv("NOT_A_SETTING", "x")
    assert load(DatabaseSettings).dsn == "postgresql://u:p@h/db"


def test_error_message_has_no_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVCITE_ONTOLOGY_DSN", "postgresql://u:p@h/db")
    monkeypatch.setenv("ENVCITE_ONTOLOGY_GRPC_PORT", "not-a-port")
    with pytest.raises(SystemExit) as raised:
        load(ServerSettings)
    assert "ENVCITE_ONTOLOGY_GRPC_PORT" in str(raised.value)
    assert "not-a-port" not in str(raised.value)


def _env_names(*classes: type[DatabaseSettings]) -> tuple[set[str], set[str]]:
    """回傳（全部環境變數名稱, 其中有預設值的）。"""
    names: set[str] = set()
    optional: set[str] = set()
    for cls in classes:
        for field, info in cls.model_fields.items():
            names.add(f"{PREFIX}{field.upper()}")
            if not info.is_required():
                optional.add(f"{PREFIX}{field.upper()}")
    return names, optional


def test_env_example_matches_settings() -> None:
    keys = {
        line.partition("=")[0].strip()
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }
    names, optional = _env_names(DatabaseSettings, SnapshotSettings, ServerSettings)
    expected_required = names - optional
    hint = "settings.py 與 .env.example 不一致，請更新 .env.example"
    assert expected_required <= keys, f"{hint}：缺 {sorted(expected_required - keys)}"
    assert keys <= names, f"{hint}：多出 {sorted(keys - names)}"


@pytest.mark.parametrize("ratio", ["-0.1", "1.5", "many", ""])
def test_invalid_guidance_ratio_is_rejected(monkeypatch: pytest.MonkeyPatch, ratio: str) -> None:
    monkeypatch.setenv("ENVCITE_ONTOLOGY_DSN", "postgresql://u:p@h/db")
    monkeypatch.setenv("ENVCITE_ONTOLOGY_GUIDANCE_MAX_DF", ratio)
    with pytest.raises(SystemExit, match="ENVCITE_ONTOLOGY_GUIDANCE_MAX_DF"):
        load(ServerSettings)


def test_guidance_ratio_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVCITE_ONTOLOGY_DSN", "postgresql://u:p@h/db")
    assert load(ServerSettings).guidance_max_df > 0
    monkeypatch.setenv("ENVCITE_ONTOLOGY_GUIDANCE_MAX_DF", "0")
    assert load(ServerSettings).guidance_max_df == 0
