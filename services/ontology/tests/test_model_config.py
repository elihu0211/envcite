"""The model configuration file: roles, declared families, secrets by reference.

No provider is called except a loopback server that records what it was sent."""

from pathlib import Path

import pytest

from env_law_ontology.verification.config import ConfigError, load, parse
from env_law_ontology.verification.run import gates_from, main
from tests.test_chat_adapter import Endpoint

EXAMPLE = Path(__file__).resolve().parents[1] / "models.example.toml"
ENV = {"CHAT_BASE_URL": "https://models.invalid/v1", "CHAT_API_KEY": "secret-value"}

MINIMAL = """
[endpoint.e]
base_url_env = "CHAT_BASE_URL"

[model.a]
endpoint = "e"
name = "model-a"
family = "fam-a"

[model.b]
endpoint = "e"
name = "model-b"
family = "fam-b"

[roles]
extractor = "a"
roundtrip = "b"
independent_test = "b"
panel = ["b"]
"""


def test_the_example_file_loads_and_assigns_every_role() -> None:
    models = load(EXAMPLE, ENV)

    assert models.extractor.name == "mistral:mistral-small-4"
    assert models.roundtrip.name == "google:gemma-4-31b"
    assert models.independent_test.name == "inkling:vibe"
    assert [m.name for m in models.panel] == [
        "google:gemma-4-31b",
        "meta:llama4scout",
        "inkling:vibe",
    ]


def test_verifiers_are_listed_once_in_the_order_their_roles_come() -> None:
    models = load(EXAMPLE, ENV)

    assert [m.name for m in models.verifiers] == [
        "google:gemma-4-31b",
        "inkling:vibe",
        "meta:llama4scout",
    ]
    assert models.everyone[0] is models.extractor


def test_families_are_declared_not_guessed_from_the_name() -> None:
    models = parse(MINIMAL, ENV)

    assert models.extractor.family == "fam-a"
    assert models.roundtrip.family == "fam-b"


def test_the_gates_follow_the_roles_not_the_order() -> None:
    models = load(EXAMPLE, ENV)

    by_method = {g.method: g for g in gates_from(models)}

    assert by_method["roundtrip"].reformalizer.name == "google:gemma-4-31b"  # type: ignore[attr-defined]
    assert by_method["independent_test"].tester.name == "inkling:vibe"  # type: ignore[attr-defined]
    assert len(by_method["panel"].judges) == 3  # type: ignore[attr-defined]


def test_a_secret_never_appears_in_what_a_model_prints() -> None:
    models = load(EXAMPLE, ENV)

    assert "secret-value" not in repr(models) + repr(models.extractor) + models.extractor.name


def test_a_model_talks_to_its_declared_endpoint_with_its_declared_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    served = Endpoint()
    other = Endpoint()
    monkeypatch.setenv("CHAT_BASE_URL", other.url)  # 設定檔明寫的端點優先，不被舊的環境變數搶走
    monkeypatch.setenv("CHAT_API_KEY", "from-the-environment")
    env = {"CHAT_BASE_URL": served.url, "CHAT_API_KEY": "from-the-file-reference"}
    try:
        models = parse(MINIMAL, env)

        models.extractor.complete("問")
    finally:
        served.server.shutdown()
        other.server.shutdown()

    assert other.requests == []
    (request,) = served.requests
    assert request["body"]["model"] == "model-a"
    assert request["path"] == "/v1/chat/completions"


def test_two_endpoints_can_be_used_in_one_run() -> None:
    first, second = Endpoint(), Endpoint()
    text = f"""
[endpoint.one]
base_url = "{first.url}"

[endpoint.two]
base_url = "{second.url}"

[model.a]
endpoint = "one"
name = "m1"
family = "f1"

[model.b]
endpoint = "two"
name = "m2"
family = "f2"

[roles]
extractor = "a"
roundtrip = "b"
independent_test = "b"
panel = ["a", "b"]
"""
    try:
        models = parse(text, {})
        models.extractor.complete("x")
        models.roundtrip.complete("y")
    finally:
        first.server.shutdown()
        second.server.shutdown()

    assert len(first.requests) == 1 and len(second.requests) == 1


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("[roles]\n", "[roles]\nextra = 1\n"), "unknown keys"),
        (('extractor = "a"\n', ""), "missing"),
        (('extractor = "a"', 'extractor = "nobody"'), "not defined"),
        (('family = "fam-a"\n', ""), "family is required"),
        (
            ('endpoint = "e"\nname = "model-a"', 'endpoint = "nowhere"\nname = "model-a"'),
            "endpoint",
        ),
        (('base_url_env = "CHAT_BASE_URL"', 'base_url_env = "UNSET_VARIABLE"'), "is not set"),
        (
            ('base_url_env = "CHAT_BASE_URL"', 'base_url_env = "X"\nbase_url = "http://x"'),
            "exactly one",
        ),
        (('name = "model-a"', 'name = "model-a"\ncolour = "red"'), "unknown keys"),
        (('panel = ["b"]', "panel = []"), "non-empty"),
    ],
)
def test_a_bad_file_is_refused_with_the_reason(change: tuple[str, str], message: str) -> None:
    old, new = change
    assert old in MINIMAL
    with pytest.raises(ConfigError, match=message):
        parse(MINIMAL.replace(old, new, 1), ENV)


def test_a_declared_key_variable_that_is_not_set_is_refused() -> None:
    text = MINIMAL.replace(
        'base_url_env = "CHAT_BASE_URL"',
        'base_url_env = "CHAT_BASE_URL"\napi_key_env = "MISSING_KEY"',
    )

    with pytest.raises(ConfigError, match="MISSING_KEY"):
        parse(text, ENV)


def test_text_that_is_not_toml_is_refused() -> None:
    with pytest.raises(ConfigError, match="not valid TOML"):
        parse("[[[", ENV)


def test_the_command_line_takes_the_file_or_the_flags_not_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ONTOLOGY_DSN", "postgresql://unused")
    monkeypatch.setattr(
        "sys.argv",
        ["env-law-pipeline", "--law", "X", "--models", str(EXAMPLE), "--extractor", "chat:a/b"],
    )

    with pytest.raises(SystemExit, match="replaces"):
        main()


def test_the_command_line_names_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONTOLOGY_DSN", "postgresql://unused")
    monkeypatch.setattr("sys.argv", ["env-law-pipeline", "--law", "X"])

    with pytest.raises(SystemExit, match="--models"):
        main()


def test_a_bad_file_stops_the_command_line_with_its_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad = tmp_path / "models.toml"
    bad.write_text("[roles]\n", encoding="utf-8")
    monkeypatch.setenv("ONTOLOGY_DSN", "postgresql://unused")
    monkeypatch.setattr("sys.argv", ["env-law-pipeline", "--law", "X", "--models", str(bad)])

    with pytest.raises(SystemExit, match=r"models\.toml"):
        main()
