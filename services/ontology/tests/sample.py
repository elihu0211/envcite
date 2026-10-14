"""Two laws shaped like the 全國法規資料庫 export, for tests that need a corpus to read.

The text excerpts the real laws (2026-09-18 data, 政府資料開放授權條款第 1 版). They go
through the real importer, so the corpus the tests read is the corpus the importer builds.
"""

import json
import zipfile
from pathlib import Path
from typing import Any

import psycopg

from envcite_ontology.acquire.moj import content_sha256, pcode

Conn = psycopg.Connection[tuple[object, ...]]

#: 空氣污染防制法：2018-08-01 修正公布全文，自公布日施行；第 1、100 條。
AIR = "O0020001"
#: 水污染防治法：2018-06-13 修正公布第 7、40 條，自公布日施行；各兩項。
WATER = "O0040001"


def _law(
    pcode: str, name: str, histories: str, modified: str, articles: list[tuple[str, str]]
) -> dict[str, Any]:
    return {
        "LawLevel": "法律",
        "LawName": name,
        "LawURL": f"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={pcode}",
        "LawModifiedDate": modified,
        "LawEffectiveDate": "",
        "LawEffectiveNote": "",
        "LawAbandonNote": "",
        "LawHistories": histories,
        "LawArticles": [
            {"ArticleType": "A", "ArticleNo": f"第 {no} 條", "ArticleContent": text}
            for no, text in articles
        ],
    }


def sample_laws() -> list[dict[str, Any]]:
    return [
        _law(
            AIR,
            "空氣污染防制法",
            "1.中華民國六十四年五月二十三日總統令制定公布全文 100 條\r\n"
            "2.中華民國一百零七年八月一日總統令修正公布全文 100 條；並自公布日施行",
            "20180801",
            [
                ("1", "為防制空氣污染，維護生活環境及國民健康，以提高生活品質，特制定本法。"),
                ("100", "本法自公布日施行。"),
            ],
        ),
        _law(
            WATER,
            "水污染防治法",
            "1.中華民國六十三年七月十一日總統令制定公布全文 100 條\r\n"
            "2.中華民國一百零七年六月十三日總統令修正公布第 7、40 條條文；並自公布日施行",
            "20180613",
            [
                (
                    "7",
                    "事業、污水下水道系統或建築物污水處理設施，排放廢（污）水於地面水體者，應符合放流水標準。\r\n前項放流水標準，由中央主管機關會商相關目的事業主管機關定之，其內容應包括適用範圍、管制方式、項目、濃度或總量限值、研訂基準及其他應遵行之事項。直轄市、縣（市）主管機關得視轄區內環境特殊或需特予保護之水體，就排放總量或濃度、管制項目或方式，增訂或加嚴轄內之放流水標準，報請中央主管機關會商相關目的事業主管機關後核定之。",
                ),
                (
                    "40",
                    "事業或污水下水道系統排放廢（污）水，違反第七條第一項或第八條規定者，處新臺幣六萬元以上二千萬元以下罰鍰，並通知限期改善，屆期仍未完成改善者，按次處罰；情節重大者，得令其停工或停業；必要時，並得廢止其水污染防治許可證（文件）或勒令歇業。\r\n畜牧業違反第七條第一項或第八條之規定者，處新臺幣六千元以上六十萬元以下罰鍰，並通知限期改善，屆期仍未完成改善者，按次處罰；情節重大者，得令其停工或停業；必要時，並得廢止其水污染防治許可證（文件）或勒令歇業。",
                ),
            ],
        ),
    ]


def stored_snapshot(
    conn: Conn, root: Path, laws: list[dict[str, Any]], *, feed: str = "moj_law"
) -> None:
    """A stored snapshot the way the acquisition step leaves it."""
    name = f"{feed}.zip"
    with zipfile.ZipFile(root / name, "w") as z:
        z.writestr("data.json", json.dumps({"UpdateDate": "2026/9/18 上午 12:00:00", "Laws": laws}))
    conn.execute("DELETE FROM source.import_problem")
    conn.execute("DELETE FROM source.item WHERE feed = %s", (feed,))
    conn.execute("DELETE FROM source.snapshot WHERE feed = %s", (feed,))
    row = conn.execute(
        "INSERT INTO source.snapshot (feed, data_date, sha256, size, location, license_notice)"
        " VALUES (%s, '2026-09-18', %s, 1, %s, 'x') RETURNING id",
        (feed, bytes(32), name),
    ).fetchone()
    assert row is not None
    for law in laws:
        conn.execute(
            "INSERT INTO source.item"
            " (feed, key, content_sha256, first_snapshot_id, last_snapshot_id)"
            " VALUES (%s, %s, %s, %s, %s)",
            (feed, pcode(law), content_sha256(law), row[0], row[0]),
        )
    conn.commit()
