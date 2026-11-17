"""Two laws shaped like the 全國法規資料庫 export, for tests that need a corpus to read.

The text excerpts the real laws (2026-09-18 data, 政府資料開放授權條款第 1 版). They go
through the real importer, so the corpus the tests read is the corpus the importer builds.
"""

import json
import zipfile
from pathlib import Path
from typing import Any

from canon.db import Conn
from canon.law.moj import content_sha256, pcode

#: 空氣污染防制法：2018-08-01 修正公布全文，自公布日施行；第 1、100 條。
AIR = "O0020001"
#: 水污染防治法：2018-06-13 修正公布第 7、40 條，自公布日施行；各兩項。
WATER = "O0040001"
#: 噪音管制法：只收第 9、24 條，供依場所類別分級的標註樣本（`noise_law()`）；
#: 不在 `sample_laws()` 裡，以免改變其他測試所見的語料。
NOISE = "O0030001"


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


def noise_law() -> dict[str, Any]:
    """噪音管制法第 9、24 條原文（全國法規資料庫，2026-11 查證）：同一違反依場所類別分五級罰鍰。"""
    return _law(
        NOISE,
        "噪音管制法",
        "1.中華民國七十二年五月二十日總統令制定公布全文 37 條\r\n"
        "2.中華民國一百十二年六月二十一日總統令修正公布第 9、24 條條文；並自公布日施行",
        "20230621",
        [
            (
                "9",
                "噪音管制區內之下列場所、工程及設施，所發出之聲音不得超出噪音管制標準：\r\n"
                "一、工廠（場）。\r\n二、娛樂場所。\r\n三、營業場所。\r\n四、營建工程。\r\n"
                "五、擴音設施。\r\n六、其他經主管機關公告之場所、工程及設施。\r\n"
                "前項各款噪音管制之音量及測定之標準，由中央主管機關定之。",
            ),
            (
                "24",
                "違反第九條第一項規定，經限期改善仍未符合噪音管制標準者，得依下列規定按次或按日連續處罰，"
                "或令其停工、停業或停止使用，至符合噪音管制標準時為止；其為第十條第一項取得許可證之設施，"
                "必要時並得廢止其許可證：\r\n"
                "一、工廠（場）：處新臺幣六千元以上六萬元以下罰鍰。\r\n"
                "二、娛樂或營業場所：處新臺幣三千元以上三萬元以下罰鍰。\r\n"
                "三、營建工程：處新臺幣一萬八千元以上十八萬元以下罰鍰。\r\n"
                "四、擴音設施：處新臺幣三千元以上三萬元以下罰鍰。\r\n"
                "五、其他經公告之場所、工程及設施：處新臺幣三千元以上三萬元以下罰鍰。\r\n"
                "前項限期改善之期限規定如下：\r\n"
                "一、工廠（場）不得超過九十日。\r\n二、娛樂或營業場所不得超過三十日。\r\n"
                "三、營建工程不得超過四日。\r\n四、擴音設施不得超過十分鐘。\r\n"
                "五、依本法第九條第一項第六款公告之場所、工程及設施，其改善期限由主管機關於公告時定之，"
                "最長不得超過九十日。\r\n"
                "法人或非法人之場所、工程或設施有第一項各款情事之一者，除處罰其實際從事行為之自然人外，"
                "並對該法人或非法人之負責人處以各該款之罰鍰。",
            ),
        ],
    )


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
        "INSERT INTO source.snapshot"
        " (feed, data_date, sha256, size_bytes, location, license_notice)"
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
