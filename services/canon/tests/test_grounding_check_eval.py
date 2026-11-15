"""端到端作答量測與入口的對齊：主題詞的判準與任務句、系統提示，必須與 platform 逐項相同。

量測工具移植了 platform 的 GroundedAnswerer 與 BareTopic；那邊改了而這裡沒跟上，
量到的就不是入口實際的行為。
這裡不呼叫任何模型，也不連資料庫。
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# 動態載入 tools/grounding_check_eval.py，避免非套件目錄引發 mypy 模組路徑衝突
_tool_path = Path(__file__).resolve().parents[1] / "tools" / "grounding_check_eval.py"
_spec = importlib.util.spec_from_file_location("grounding_check_eval_module", _tool_path)
assert _spec is not None and _spec.loader is not None
_tool = importlib.util.module_from_spec(_spec)
sys.modules["grounding_check_eval_module"] = _tool
_spec.loader.exec_module(_tool)

INTERROGATIVES = _tool.INTERROGATIVES
NAME_SUFFIXES = _tool.NAME_SUFFIXES
QUESTION_MIN_LENGTH = _tool.QUESTION_MIN_LENGTH
SYSTEM_PROMPT = _tool.SYSTEM_PROMPT
Row = _tool.Row
bare_topic_table = _tool.bare_topic_table
is_bare_topic = _tool.is_bare_topic
is_search_question = _tool.is_search_question
load_keyword_questions = _tool.load_keyword_questions
task_of = _tool.task_of

PLATFORM = Path(__file__).resolve().parents[3] / "services/platform/src"
ANSWERER = PLATFORM / "EnvCite.Platform.Modules.Consult/GroundedAnswerer.cs"
BARE_TOPIC = PLATFORM / "EnvCite.Platform.Modules.Consult/BareTopic.cs"
SEARCH_OPTIONS = PLATFORM / "EnvCite.Platform.Modules.Ontology/LawSearchOptions.cs"


@pytest.mark.parametrize(
    "text",
    ["露天燃燒", "  露天燃燒  ", "噪音 罰則", "一二三四五六七八九", "水污染防治法施行細則"],
)
def test_a_keyword_or_a_name_without_a_question_is_a_bare_topic(text: str) -> None:
    assert is_bare_topic(text)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "一二三四五六七八九十",  # 十個字：搜尋端當問句
        "未經許可設置固定污染源",  # 十一個字：搜尋端當問句，入口不能當主題詞
        "露天燃燒如何處罰",
        "露天燃燒會罰嗎",
        "是否可以露天燃燒",
        "露天燃燒。",
        "露天燃燒？",
        "水污染防治法？",
    ],
)
def test_a_question_or_a_sentence_is_not(text: str | None) -> None:
    assert not is_bare_topic(text)


@pytest.mark.parametrize(
    "text",
    [
        "露天燃燒",
        "一二三四五六七八九十",
        "未經許可設置固定污染源",
        "水污染防治法施行細則",
        "噪音？",
    ],
)
def test_a_bare_topic_is_never_a_question_to_the_search_side(text: str) -> None:
    assert not (is_bare_topic(text) and is_search_question(text))


def test_a_law_name_is_never_a_question_even_with_a_question_mark() -> None:
    assert not is_search_question("水污染防治法？")
    assert is_search_question("噪音？")


def test_only_a_bare_topic_is_restated_as_a_task() -> None:
    task = task_of("露天燃燒")
    assert "使用者只輸入了主題詞「露天燃燒」，沒有提出具體問題" in task
    assert "不要因為缺了某一項就整個回「證據不足」" in task
    assert "行為、義務與罰則" not in task
    assert task_of("露天燃燒如何處罰") == "露天燃燒如何處罰"


# 以下是漂移守門：platform 的原始碼是真值，這裡的移植逐項對得上。


def test_the_system_prompt_is_the_one_the_answerer_sends() -> None:
    source = ANSWERER.read_text(encoding="utf-8")
    for line in SYSTEM_PROMPT.split("\n"):
        assert line in source, f"GroundedAnswerer 的系統提示沒有這一行：{line}"


def test_the_task_sentence_is_the_one_the_answerer_sends() -> None:
    source = ANSWERER.read_text(encoding="utf-8")
    task = task_of("露天燃燒")
    assert "請依證據說明與它有關的規定；" in source
    for part in task.split("「露天燃燒」，", 1)[1].split("；"):
        assert part in source, f"GroundedAnswerer.TaskOf 沒有這一段：{part}"


def test_the_bare_topic_words_are_the_ones_the_portal_checks() -> None:
    source = BARE_TOPIC.read_text(encoding="utf-8")
    for word in INTERROGATIVES:
        assert f'"{word}"' in source, f"BareTopic 沒有疑問詞：{word}"
    assert source.count('"') >= 2 * len(INTERROGATIVES)


def test_the_name_suffixes_and_the_cut_are_the_ones_search_uses() -> None:
    source = SEARCH_OPTIONS.read_text(encoding="utf-8")
    for suffix in NAME_SUFFIXES:
        assert f'"{suffix}"' in source, f"LawSearchOptions 沒有類型後綴：{suffix}"
    assert f"DefaultQuestionMinLength = {QUESTION_MIN_LENGTH};" in source


def _keyword_golden(tmp_path: Path) -> Path:
    doc = {
        "kind": "keyword-golden",
        "snapshots": [],
        "items": [
            {
                "id": "kw-def-1",
                "bucket": "definition",
                "query": "一個輪次",
                "split": "test",
                "expected": [{"article": "F0110025-art-2"}],
            },
            {
                "id": "kw-abs-1",
                "bucket": "absent",
                "query": "電競選手",
                "split": "test",
                "expected": [],
            },
            {
                "id": "kw-def-2",
                "bucket": "definition",
                "query": "地面水體",
                "split": "dev",
                "expected": [{"article": "A0000001-art-3"}],
            },
        ],
    }
    path = tmp_path / "keyword-golden.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def test_the_keyword_golden_set_is_read_by_split_with_absent_as_the_negative_bucket(
    tmp_path: Path,
) -> None:
    questions, _, sha = load_keyword_questions(_keyword_golden(tmp_path), "test")

    assert [(q.id, q.bucket, q.relevant) for q in questions] == [
        ("kw-def-1", "definition", {"F0110025-art-2": 1}),
        ("kw-abs-1", "negative", {}),
    ]
    assert [q.scored for q in questions] == [True, False]
    assert len(sha) == 64


def test_a_file_that_is_not_a_keyword_golden_set_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"kind": "native-golden", "items": []}), encoding="utf-8")

    with pytest.raises(SystemExit):
        load_keyword_questions(path, "test")


def test_the_report_splits_the_outcomes_by_how_the_portal_put_the_question() -> None:
    rows = [
        Row("a", "definition", "x", "answered", bare_topic=True),
        Row("b", "definition", "x", "model_insufficient", bare_topic=True),
        Row("c", "definition", "x", "refused_below_floor", bare_topic=True),
        Row("d", "definition", "x", "answered", bare_topic=False),
    ]

    table = bare_topic_table(rows)

    assert table[2].startswith("| 主題詞的任務句 | 3 | 1 (33.3%) | 1 (33.3%) | 1 (33.3%) | 0 |")
    assert table[3].startswith("| 原句 | 1 | 1 (100.0%) | 0 (0.0%) | 0 (0.0%) | 0 |")
