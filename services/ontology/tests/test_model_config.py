"""The model configuration file: roles, declared families, secrets by reference.

No provider is called except a loopback server that records what it was sent."""

import subprocess
from pathlib import Path

import pytest

from envcite_ontology.verification import config
from envcite_ontology.verification.config import (
    ConfigError,
    keychain_lookup,
    load,
    load_embedder,
    parse,
    parse_embedder,
    resolve_secret,
)
from envcite_ontology.verification.run import SameModel, gates_from, main
from tests.test_chat_adapter import Endpoint

EXAMPLE = Path(__file__).resolve().parents[1] / "models.toml"
KEY = "ENVCITE_ONTOLOGY_HOSTED_API_KEY"
ENV = {KEY: "secret-value"}


def no_keychain(name: str) -> None:
    """測試不碰真實的鑰匙圈：環境變數沒有就當作取不到。"""
    return None


MINIMAL = """
[endpoint.e]
base_url = "https://models.invalid/v1"

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
    assert models.roundtrip.name == "nvidia:nemotron-3.5-super-vl"
    assert models.independent_test.name == "meta:muse-spark-open"
    assert [m.name for m in models.panel] == [
        "meta:muse-spark-open",
        "nvidia:nemotron-3.5-super-vl",
    ]


def test_verifiers_are_listed_once_in_the_order_their_roles_come() -> None:
    models = load(EXAMPLE, ENV)

    assert [m.name for m in models.verifiers] == [
        "nvidia:nemotron-3.5-super-vl",
        "meta:muse-spark-open",
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
    assert by_method["independent_test"].tester.name == "meta:muse-spark-open"  # type: ignore[attr-defined]
    assert len(by_method["panel"].judges) == 2  # type: ignore[attr-defined]


def test_a_secret_never_appears_in_what_a_model_prints() -> None:
    models = load(EXAMPLE, ENV)

    assert "secret-value" not in repr(models) + repr(models.extractor) + models.extractor.name


def test_a_model_talks_to_its_declared_endpoint_with_its_declared_name() -> None:
    served = Endpoint()
    try:
        models = parse(MINIMAL.replace("https://models.invalid/v1", served.url), {})

        models.extractor.complete("問")
    finally:
        served.server.shutdown()

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
        (('base_url = "https://models.invalid/v1"', ""), "base_url is required"),
        (
            ('base_url = "https://models.invalid/v1"', 'base_url_env = "X"'),
            "unknown keys",
        ),
        (
            ('base_url = "https://models.invalid/v1"', 'base_url = "http://x"\napi_key_env = "X"'),
            "unknown keys",
        ),
        (
            ('base_url = "https://models.invalid/v1"', 'base_url = "http://x"\napi_key_file = "k"'),
            "unknown keys",
        ),
        (('name = "model-a"', 'name = "model-a"\ncolour = "red"'), "unknown keys"),
        (('panel = ["b"]', "panel = []"), "non-empty"),
    ],
)
def test_a_bad_file_is_refused_with_the_reason(change: tuple[str, str], message: str) -> None:
    old, new = change
    assert old in MINIMAL
    with pytest.raises(ConfigError, match=message):
        parse(MINIMAL.replace(old, new, 1), ENV, keychain=no_keychain)


def test_text_that_is_not_toml_is_refused() -> None:
    with pytest.raises(ConfigError, match="not valid TOML"):
        parse("[[[", ENV)


def test_the_command_line_names_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVCITE_ONTOLOGY_DSN", "postgresql://unused")
    monkeypatch.setattr("sys.argv", ["envcite-pipeline", "--law", "X"])

    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 2  # argparse: --models is required


def test_a_bad_file_stops_the_command_line_with_its_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad = tmp_path / "models.toml"
    bad.write_text("[roles]\n", encoding="utf-8")
    monkeypatch.setenv("ENVCITE_ONTOLOGY_DSN", "postgresql://unused")
    monkeypatch.setattr("sys.argv", ["envcite-pipeline", "--law", "X", "--models", str(bad)])

    with pytest.raises(SystemExit, match=r"models\.toml"):
        main()


def test_check_asks_every_model_and_needs_no_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """替身端點對每個問題都答同一句，所以別名檢查會擋下；擋下就表示每個模型都被問到了。"""
    served = Endpoint()
    models = tmp_path / "models.toml"
    models.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace("https://api.ithu.tw/v1", served.url),
        encoding="utf-8",
    )
    monkeypatch.delenv("ENVCITE_ONTOLOGY_DSN", raising=False)
    monkeypatch.setenv(KEY, "k")
    monkeypatch.setattr("sys.argv", ["envcite-pipeline", "--models", str(models), "--check"])
    try:
        with pytest.raises(SameModel):
            main()
    finally:
        served.server.shutdown()

    assert len(served.requests) == 2 * len(load(EXAMPLE, ENV).everyone)


def test_the_embedder_is_declared_in_the_same_file_and_reuses_an_endpoint() -> None:
    embedder = load_embedder(EXAMPLE, ENV, timeout=5.0)

    assert embedder is not None and embedder.model == "bge-m3-embedding"
    assert "secret-value" not in repr(embedder)
    assert load(EXAMPLE, ENV).extractor.name == "mistral:mistral-small-4"  # 管線照常讀同一份


def test_no_embedding_section_means_no_semantic_arm() -> None:
    assert parse_embedder(MINIMAL, ENV, timeout=5.0) is None


def test_the_embedder_needs_only_its_own_endpoint() -> None:
    """伺服器只拿得到嵌入端點的金鑰時，別的端點缺環境變數不影響它。"""
    text = (
        MINIMAL
        + '\n[endpoint.other]\nbase_url = "http://other.invalid"\napi_key = "MISSING_KEY"\n'
        + '\n[embedding]\nendpoint = "e"\nname = "m3"\n'
    )

    embedder = parse_embedder(text, ENV, timeout=5.0, keychain=no_keychain)

    assert embedder is not None and embedder.model == "m3"
    with pytest.raises(ConfigError, match="endpoint 'nope'"):
        parse_embedder(
            MINIMAL + '\n[embedding]\nendpoint = "nope"\nname = "m3"\n',
            ENV,
            timeout=5.0,
        )
    with pytest.raises(ConfigError, match="unknown keys"):
        parse_embedder(
            MINIMAL + '\n[embedding]\nendpoint = "e"\nname = "m3"\ndim = 1024\n',
            ENV,
            timeout=5.0,
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


# ---- 金鑰：設定檔只寫名稱，依序取同名環境變數、鑰匙圈同名 service ----


def test_the_key_comes_from_the_variable_of_the_same_name() -> None:
    models = load(EXAMPLE, ENV, keychain=no_keychain)

    assert models.extractor._api_key == "secret-value"  # type: ignore[attr-defined]


def test_the_environment_wins_and_the_keychain_is_not_asked() -> None:
    asked: list[str] = []

    def keychain(name: str) -> str | None:
        asked.append(name)
        return "from-keychain"

    assert resolve_secret(KEY, {KEY: "from-env"}, keychain=keychain) == "from-env"
    assert asked == []


def test_the_keychain_is_used_when_the_variable_is_missing_or_empty() -> None:
    seen: list[str] = []

    def keychain(name: str) -> str | None:
        seen.append(name)
        return "from-keychain"

    assert resolve_secret(KEY, {}, keychain=keychain) == "from-keychain"
    assert resolve_secret(KEY, {KEY: ""}, keychain=keychain) == "from-keychain"
    assert seen == [KEY, KEY]
    models = load(EXAMPLE, {}, keychain=keychain)
    assert models.extractor._api_key == "from-keychain"  # type: ignore[attr-defined]


def test_a_missing_key_is_refused_naming_it_and_where_it_was_looked_for() -> None:
    with pytest.raises(ConfigError) as raised:
        load(EXAMPLE, {"OTHER": "not-this-one"}, keychain=no_keychain)

    message = str(raised.value)
    assert KEY in message and "endpoint.hosted" in message
    assert "keychain" in message and "environment variable" in message
    assert "not-this-one" not in message


def test_an_endpoint_without_api_key_needs_none() -> None:
    models = parse(MINIMAL, {}, keychain=no_keychain)

    assert models.extractor._api_key is None  # type: ignore[attr-defined]


def test_api_key_must_be_a_name() -> None:
    text = MINIMAL.replace(
        'base_url = "https://models.invalid/v1"',
        'base_url = "https://models.invalid/v1"\napi_key = 7',
    )

    with pytest.raises(ConfigError, match="api_key is the name"):
        parse(text, ENV, keychain=no_keychain)


class _Done:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_the_keychain_command_follows_the_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> _Done:
        calls.append(command)
        assert kwargs["timeout"] == config.KEYCHAIN_TIMEOUT
        return _Done(0, "  value\n")

    monkeypatch.setattr(subprocess, "run", run)

    assert keychain_lookup("N", platform="darwin") == "value"
    assert keychain_lookup("N", platform="linux") == "value"
    assert calls == [
        ["security", "find-generic-password", "-s", "N", "-w"],
        ["secret-tool", "lookup", "service", "N"],
    ]


def test_there_is_no_keychain_on_other_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*args: object, **kwargs: object) -> _Done:
        raise AssertionError("no command to run on this platform")

    monkeypatch.setattr(subprocess, "run", run)

    assert keychain_lookup("N", platform="win32") is None


@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param(_Done(44, ""), id="not-found"),
        pytest.param(_Done(0, "\n"), id="empty"),
        pytest.param(FileNotFoundError("security"), id="no-cli"),
        pytest.param(subprocess.TimeoutExpired("security", 1), id="timeout"),
    ],
)
def test_a_keychain_that_cannot_answer_is_a_miss_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, outcome: object
) -> None:
    def run(*args: object, **kwargs: object) -> _Done:
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, _Done)
        return outcome

    monkeypatch.setattr(subprocess, "run", run)

    assert keychain_lookup("N", platform="darwin") is None
    with pytest.raises(ConfigError, match="N not found"):
        resolve_secret("N", {}, keychain=lambda n: keychain_lookup(n, platform="darwin"))


def test_a_stalled_keychain_is_cut_off_by_the_timeout() -> None:
    import sys

    if sys.platform not in {"darwin", "linux"}:
        pytest.skip("no keychain command on this platform")
    # 用一個不會回來的子行程驗證逾時真的生效，不碰真實的鑰匙圈。
    original = config._KEYCHAIN_COMMANDS
    try:
        config._KEYCHAIN_COMMANDS = {sys.platform: lambda name: ["sleep", "5"]}
        assert keychain_lookup("N", timeout=0.2) is None
    finally:
        config._KEYCHAIN_COMMANDS = original
