"""提示詞：模板在 `templates/*.j2`（Jinja2），渲染只經這個模組，呼叫端傳具名、有型別的引數。

模板以繼承組出共同的骨架（`base.j2`：任務、本體的約定、規則、材料）：要放約定的提示繼承
`with_conventions.j2`，用語定義的兩個提示繼承 `definition.j2`，「有才放」的區塊是 `macros.j2`
的巨集。渲染結果是送給模型的純文字，所以不跳脫 HTML；`StrictUndefined` 讓漏傳的變數成為錯誤，
而不是悄悄送出缺一段的提示。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from jinja2 import Environment, PackageLoader, StrictUndefined

from envcite_ontology.claims import LEGAL_EFFECTS, TRIGGER_KINDS

_env = Environment(
    loader=PackageLoader("envcite_ontology.verification", "templates"),
    undefined=StrictUndefined,
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


@dataclass(frozen=True, slots=True)
class Candidate:
    """罰則所處罰的候選陳述：編號、所在條文與原文。"""

    id: int
    path: str
    quoted: str


def _render(name: str, **variables: object) -> str:
    return _env.get_template(name).render(**variables)


def conventions() -> str:
    """本體的約定全文：抽取、往返重抽與評審團的提示共用的片段。"""
    return _render("conventions.j2")


def extract(
    *,
    law: str,
    path: str,
    text: str,
    context: str,
    defined: Mapping[str, str] | None,
    punished_by: Sequence[str],
    review: Sequence[str],
) -> str:
    """抽取一個條文單元的提示；`context` 是上層條文，`review` 是重抽時審查者的意見。"""
    return _render(
        "extract.j2",
        law=law,
        path=path,
        text=text,
        context=context,
        defined=defined,
        punished_by=punished_by,
        notes=review,
        effects=LEGAL_EFFECTS,
        trigger_kinds=TRIGGER_KINDS,
    )


def repair(*, prompt: str, previous: str, issues: Sequence[str]) -> str:
    """請模型修正上一次輸出的提示；抽取與用語定義共用。"""
    return _render("repair.j2", prompt=prompt, previous=previous, issues=issues)


def definitions(*, law: str, path: str, text: str, context: str) -> str:
    """抽取一款用語定義的提示。"""
    return _render("definitions.j2", law=law, path=path, text=text, context=context)


def reread(*, text: str, context: str) -> str:
    """請另一個模型重讀一款用語定義、供往返比對的提示。"""
    return _render("reread.j2", text=text, context=context)


def choose(*, text: str, candidates: Sequence[Candidate]) -> str:
    """請模型從候選陳述挑出這則罰則處罰的全部陳述的提示。"""
    return _render("choose.j2", text=text, candidates=candidates)


def reformalize(*, description: str) -> str:
    """請重抽者只依標註的確定性描述還原結構的提示。"""
    return _render("reformalize.j2", description=description)


def write_tests(*, text: str, concepts: Sequence[str]) -> str:
    """請測試設計者只依原文設計案例的提示；`concepts` 是附型別的概念清單，一項一行。"""
    if isinstance(concepts, str):  # str 也是 Sequence[str]，模板會逐字元拆開
        raise TypeError("concepts must be a sequence of lines, not one string")
    return _render("write_tests.j2", text=text, concepts=concepts)


def judge(*, context: str, text: str, quote: str, description: str) -> str:
    """請評審判斷標註是否忠於引文與所在單元的提示。"""
    return _render("judge.j2", context=context, text=text, quote=quote, description=description)
