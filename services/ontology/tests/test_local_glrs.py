"""測試 LocalGlrs 本地法規開放資料快照讀取與解析。"""

import json
import zipfile
from pathlib import Path

import pytest

from envcite_ontology.acquire.engine import InvalidSnapshot, NotAnArchive
from envcite_ontology.acquire.local_glrs import LocalGlrs, canonical_sha256, load_local_laws


def test_canonical_sha256_is_deterministic() -> None:
    item_a = {"b": 2, "a": 1}
    item_b = {"a": 1, "b": 2}
    assert canonical_sha256(item_a) == canonical_sha256(item_b)


def test_local_glrs_reads_valid_snapshot(tmp_path: Path) -> None:
    zip_path = tmp_path / "glrs.zip"
    sample_laws = [
        {
            "Id": "TPE-ENV-001",
            "LawName": "臺北市宜居城市環境保護自治條例",
            "Category": "環境保護",
            "Jurisdiction": "TW-TPE",
            "Articles": [
                {"ArticleNo": "第 1 條", "ArticleContent": "為維護環境生活品質，特制定本自治條例。"}
            ],
        },
        {
            "Id": "TXG-ENV-002",
            "LawName": "臺中市環境清潔維護自治條例",
            "Category": "環境保護",
            "Jurisdiction": "TW-TXG",
        },
    ]
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("LocalLaws.json", json.dumps({"Laws": sample_laws}, ensure_ascii=False))

    today, loaded = load_local_laws(zip_path)
    assert len(loaded) == 2
    assert loaded[0]["Id"] == "TPE-ENV-001"

    source = LocalGlrs()
    contents = source.read(zip_path)
    assert contents.data_date == today
    assert len(contents.items) == 2
    assert contents.items[0][0] == "TPE-ENV-001"
    assert contents.items[1][0] == "TXG-ENV-002"


def test_local_glrs_invalid_zip(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(b"not-a-zip")

    with pytest.raises(NotAnArchive):
        load_local_laws(corrupt)


def test_local_glrs_missing_json(tmp_path: Path) -> None:
    empty_zip = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty_zip, "w") as z:
        z.writestr("readme.txt", "hello")

    with pytest.raises(InvalidSnapshot):
        load_local_laws(empty_zip)
