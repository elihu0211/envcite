"""結構守門：模組不得長成巨石。

單一模組超過 `MAX_LINES` 行，就是在一個檔案裡放了不只一個職責。已經過大的模組列在 `OVERSIZED`，
上限是目前的行數（取整到 50）：只能縮、不能增。拆完就從清單刪掉；新模組一律以 `MAX_LINES` 為限。
"""

from pathlib import Path

import pytest

import envcite_ontology

SRC = Path(envcite_ontology.__file__).parent
MAX_LINES = 500

#: 已知過大的模組 → 上限（行）。拆分時刪掉對應的項目，不要調高。
OVERSIZED = {
    "reasoning/facts.py": 1000,
    "verification/extract.py": 950,
    "reasoning_codec.py": 850,
    "claims.py": 800,
    "guidance_sync.py": 750,
    "reasoning/scoring.py": 700,
    "service.py": 600,
    "ingest/backfill.py": 600,
    "verification/model_gates.py": 550,
    "verification/run.py": 550,
}


def _lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_module_outgrows_its_limit() -> None:
    too_big = {
        rel: (_lines(path), OVERSIZED.get(rel, MAX_LINES))
        for path in _modules()
        if _lines(path) > OVERSIZED.get(rel := path.relative_to(SRC).as_posix(), MAX_LINES)
    }

    assert not too_big, (
        f"超過行數上限的模組（行數, 上限）：{too_big}。"
        f"把職責拆到各自的模組；不要調高 `OVERSIZED` 的上限。"
    )


@pytest.mark.parametrize("rel", sorted(OVERSIZED))
def test_a_module_that_was_split_leaves_the_oversized_list(rel: str) -> None:
    path = SRC / rel

    assert path.exists(), f"{rel} 已不存在：從 `OVERSIZED` 刪掉"
    assert _lines(path) > MAX_LINES, f"{rel} 已在 {MAX_LINES} 行以內：從 `OVERSIZED` 刪掉"
