"""The model configuration file: roles, declared families, secrets by reference.

No provider is called except a loopback server that records what it was sent."""

from pathlib import Path

import pytest

from envcite_ontology.verification.config import (
    ConfigError,
    load,
    load_embedder,
    parse,
    parse_embedder,
)
from envcite_ontology.verification.run import SameModel, gates_from, main
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

    assert models.extractor.name == "meta:muse-spark-open"
    assert models.roundtrip.name == "nvidia:nemotron-3.5-super-vl"
    assert models.independent_test.name == "mistral:mistral-small-4"
    assert [m.name for m in models.panel] == [
        "nvidia:nemotron-3.5-super-vl",
        "mistral:mistral-small-4",
    ]


def test_verifiers_are_listed_once_in_the_order_their_roles_come() -> None:
    models = load(EXAMPLE, ENV)

    assert [m.name for m in models.verifiers] == [
        "nvidia:nemotron-3.5-super-vl",
        "mistral:mistral-small-4",
    ]
    assert models.everyone[0] is models.extractor


def test_families_are_declared_not_guessed_from_the_name() -> None:
    models = parse(MINIMAL, ENV)

    assert models.extractor.family == "fam-a"
    assert models.roundtrip.family == "fam-b"


def test_the_gates_follow_the_roles_not_the_order() -> None:
    models = load(EXAMPLE, ENV)

    by_method = {g.method: g for g in gates_from(models)}

    assert by_method["roundtrip"].reformalizer.name == "nvidia:nemotron-3.5-super-vl"  # type: ignore[attr-defined]
    assert by_method["independent_test"].tester.name == "mistral:mistral-small-4"  # type: ignore[attr-defined]
    assert len(by_method["panel"].judges) == 2  # type: ignore[attr-defined]


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


def test_the_command_line_names_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONTOLOGY_DSN", "postgresql://unused")
    monkeypatch.setattr("sys.argv", ["envcite-pipeline", "--law", "X"])

    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 2  # argparse: --models is required


def test_a_bad_file_stops_the_command_line_with_its_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad = tmp_path / "models.toml"
    bad.write_text("[roles]\n", encoding="utf-8")
    monkeypatch.setenv("ONTOLOGY_DSN", "postgresql://unused")
    monkeypatch.setattr("sys.argv", ["envcite-pipeline", "--law", "X", "--models", str(bad)])

    with pytest.raises(SystemExit, match=r"models\.toml"):
        main()


# ---- the key can live in a file inside the project, never in the config itself ----


def _with_key_file(name: str = ".secrets/key") -> str:
    return MINIMAL.replace(
        'base_url_env = "CHAT_BASE_URL"',
        f'base_url = "https://x.invalid/v1"\napi_key_file = "{name}"',
    )


def _key_file(root: Path, text: str) -> None:
    (root / ".secrets").mkdir(exist_ok=True)
    (root / ".secrets" / "key").write_text(text, encoding="utf-8")


def test_a_key_file_is_read_relative_to_the_config_and_trimmed(tmp_path: Path) -> None:
    _key_file(tmp_path, "  file-secret \n")
    (tmp_path / "models.toml").write_text(_with_key_file(), encoding="utf-8")

    models = load(tmp_path / "models.toml", {})

    assert models.extractor._api_key == "file-secret"  # type: ignore[attr-defined]
    assert "file-secret" not in repr(models.extractor)


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        pytest.param("empty", "is empty", id="empty"),
        pytest.param("missing", "cannot read the key file", id="missing"),
    ],
)
def test_an_empty_or_missing_key_file_is_refused(tmp_path: Path, setup: str, message: str) -> None:
    if setup == "empty":
        _key_file(tmp_path, "  \n")
    (tmp_path / "models.toml").write_text(_with_key_file(), encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load(tmp_path / "models.toml", {})


def test_a_key_file_and_a_key_variable_together_are_refused(tmp_path: Path) -> None:
    _key_file(tmp_path, "k")
    text = _with_key_file().replace("api_key_file", 'api_key_env = "CHAT_API_KEY"\napi_key_file')

    with pytest.raises(ConfigError, match="at most one"):
        parse(text, ENV, tmp_path)


def test_a_key_file_inside_a_git_repository_must_be_ignored(tmp_path: Path) -> None:
    """A key that `git add` would pick up is a leaked key: refuse to load it."""
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git is not installed (the guard is a no-op without it)")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _key_file(tmp_path, "file-secret")
    (tmp_path / "models.toml").write_text(_with_key_file(), encoding="utf-8")

    with pytest.raises(ConfigError, match="not ignored"):
        load(tmp_path / "models.toml", {})

    (tmp_path / ".gitignore").write_text(".secrets/\n", encoding="utf-8")
    assert load(tmp_path / "models.toml", {}).extractor._api_key == "file-secret"  # type: ignore[attr-defined]


def test_check_asks_every_model_and_needs_no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """替身端點對每個問題都答同一句，所以別名檢查會擋下；擋下就表示每個模型都被問到了。"""
    served = Endpoint()
    monkeypatch.delenv("ONTOLOGY_DSN", raising=False)
    monkeypatch.setenv("CHAT_BASE_URL", served.url)
    monkeypatch.setenv("CHAT_API_KEY", "k")
    monkeypatch.setattr("sys.argv", ["envcite-pipeline", "--models", str(EXAMPLE), "--check"])
    try:
        with pytest.raises(SameModel):
            main()
    finally:
        served.server.shutdown()

    assert len(served.requests) == 2 * len(load(EXAMPLE).everyone)


def test_the_embedder_is_declared_in_the_same_file_and_reuses_an_endpoint() -> None:
    embedder = load_embedder(EXAMPLE, ENV, timeout=5.0, max_retries=0)

    assert embedder is not None and embedder.model == "bge-m3-embedding"
    assert "secret-value" not in repr(embedder)
    assert load(EXAMPLE, ENV).extractor.name == "meta:muse-spark-open"  # 管線照常讀同一份


def test_no_embedding_section_means_no_semantic_arm() -> None:
    assert parse_embedder(MINIMAL, ENV, timeout=5.0, max_retries=0) is None


def test_the_embedder_needs_only_its_own_endpoint() -> None:
    """伺服器只拿得到嵌入端點的金鑰時，別的端點缺環境變數不影響它。"""
    text = (
        MINIMAL
        + '\n[endpoint.other]\nbase_url_env = "MISSING"\n'
        + '\n[embedding]\nendpoint = "e"\nname = "m3"\n'
    )

    embedder = parse_embedder(text, ENV, timeout=5.0, max_retries=0)

    assert embedder is not None and embedder.model == "m3"
    with pytest.raises(ConfigError, match="endpoint 'nope'"):
        parse_embedder(
            MINIMAL + '\n[embedding]\nendpoint = "nope"\nname = "m3"\n',
            ENV,
            timeout=5.0,
            max_retries=0,
        )
    with pytest.raises(ConfigError, match="unknown keys"):
        parse_embedder(
            MINIMAL + '\n[embedding]\nendpoint = "e"\nname = "m3"\ndim = 1024\n',
            ENV,
            timeout=5.0,
            max_retries=0,
        )


def test_a_model_can_declare_constrained_extraction() -> None:
    text = MINIMAL.replace(
        'name = "model-a"\nfamily = "fam-a"',
        'name = "model-a"\nfamily = "fam-a"\nconstrained_extraction = true',
    )

    models = parse(text, ENV)

    assert models.extractor.constrained_extraction is True  # type: ignore[attr-defined]
    assert models.roundtrip.constrained_extraction is False  # type: ignore[attr-defined]


def test_constrained_extraction_must_be_a_boolean() -> None:
    text = MINIMAL.replace(
        'name = "model-a"\nfamily = "fam-a"',
        'name = "model-a"\nfamily = "fam-a"\nconstrained_extraction = "yes"',
    )

    with pytest.raises(ConfigError, match="constrained_extraction must be true or false"):
        parse(text, ENV)
