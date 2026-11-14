"""The model adapter, and detecting one model under two names."""

import types
from pathlib import Path
from typing import Any

import pytest

from envcite_ontology.verification.distinct import PROBES, SameModel, assert_distinct
from envcite_ontology.verification.models import AnthropicModel, ModelError
from envcite_ontology.verification.run import run
from tests.model_gates_support import (
    TEXT,
    Conn,
    Scripted,
)
from tests.test_annotation import LAW
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1


def test_the_anthropic_model_names_its_family() -> None:
    model = AnthropicModel("claude-sonnet-5-5")

    assert (model.name, model.family) == ("anthropic:claude-sonnet-5-5", "anthropic")


def test_the_anthropic_adapter_sends_one_user_message_and_joins_the_text_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, Any] = {}

    class Messages:
        def create(self, **kwargs: Any) -> Any:
            sent.update(kwargs)
            block = types.SimpleNamespace
            return block(
                content=[
                    block(type="text", text="a"),
                    block(type="tool_use"),
                    block(type="text", text="b"),
                ]
            )

    fake_sdk = types.SimpleNamespace(Anthropic=lambda: types.SimpleNamespace(messages=Messages()))
    monkeypatch.setattr(
        "envcite_ontology.verification.models.importlib",
        types.SimpleNamespace(import_module=lambda name: fake_sdk),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    text = AnthropicModel("claude-sonnet-5-5", max_tokens=100).complete("問題")

    assert text == "ab"
    assert sent == {
        "model": "claude-sonnet-5-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "問題"}],
    }


class _ApiError(Exception):
    """替身 SDK 的 `anthropic.APIError`。"""


def _failing_sdk(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    class Messages:
        def create(self, **kwargs: Any) -> Any:
            raise error

    fake_sdk = types.SimpleNamespace(
        APIError=_ApiError, Anthropic=lambda: types.SimpleNamespace(messages=Messages())
    )
    monkeypatch.setattr(
        "envcite_ontology.verification.models.importlib",
        types.SimpleNamespace(import_module=lambda name: fake_sdk),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")


def test_an_sdk_api_error_is_a_model_error_so_the_pipeline_records_it_and_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _failing_sdk(monkeypatch, _ApiError("overloaded"))

    with pytest.raises(ModelError, match="anthropic:claude-sonnet-5-5: _ApiError: overloaded"):
        AnthropicModel("claude-sonnet-5-5").complete("x")


def test_a_programming_error_inside_the_call_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _failing_sdk(monkeypatch, ValueError("bad arguments"))

    with pytest.raises(ValueError, match="bad arguments"):
        AnthropicModel("claude-sonnet-5-5").complete("x")


def test_the_anthropic_adapter_refuses_to_run_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "envcite_ontology.verification.models.importlib",
        types.SimpleNamespace(import_module=lambda name: types.SimpleNamespace()),
    )

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicModel("claude-sonnet-5-5").complete("x")


def test_two_names_that_answer_every_probe_identically_are_one_model() -> None:
    """託管服務常把同一個模型掛在幾個名字下：名字不同不等於家族不同。"""
    twin = {p: f"答：{i}" for i, p in enumerate(PROBES)}
    a = Scripted("fam-a:alias-of-b", lambda prompt: twin[prompt])
    b = Scripted("fam-b:model-b", lambda prompt: twin[prompt])

    with pytest.raises(SameModel, match="same model under two names"):
        assert_distinct([a, b])


def test_models_that_answer_differently_are_distinct() -> None:
    a = Scripted("fam-a:model-a", lambda prompt: "甲" + prompt[:3])
    b = Scripted("fam-b:model-b", lambda prompt: "乙" + prompt[:3])

    assert_distinct([a, b])


def test_the_pipeline_can_refuse_to_start_when_two_of_its_models_are_one(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))

    def same(prompt: str) -> str:
        return "同一個答案"

    models = [Scripted("fake-a:x", same), Scripted("fake-b:y", same)]

    with pytest.raises(SameModel):
        run(conn, models[0], models[1:], [LAW], agent_version="1", check_distinct=True)
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement") == 0  # 一個單元都沒抽
