"""「只對有標註的法規解析時點」這類查詢的測試共用：大量無標註的填充法規，與看執行計畫的輔助。

線上事故：對全庫上萬部法規各呼叫一次時點解析函式，函式被內聯後規劃器排出二次方的巢狀迴圈，
本機叢集上單條查詢跑十幾分鐘；有標註的法規只有少數。測試在可丟棄的資料庫造出大量沒有標註的法規，
斷言結果與對全部法規解析相同，且執行計畫只替有標註的法規解析。
"""

import time
from datetime import date
from typing import Any, LiteralString

import psycopg

Conn = psycopg.Connection[tuple[object, ...]]

ON = date(2019, 1, 1)  # 範本法規第一版生效中
PARAMS = {"lang": "zho", "language": "zho", "on": ON}

#: 沒有標註的填充法規數量與每部的條文數。
FILLER_LAWS = 400
FILLER_PROVISIONS = 25

#: 以範本法規（`T0000001`）的資源、語文版本、條文與版本列為樣板，複製出沒有標註的法規。
_FILLER = """
    WITH tmpl AS (
        SELECT lr.id_scheme, lr.norm_kind, lr.document_type,
               e.language, e.version_date, e.title, e.text_period, e.text_status, e.text_authority,
               p.provision_type, v.force_period, v.commencement_rule
          FROM corpus.legal_resource AS lr
          JOIN corpus.legal_expression AS e ON e.resource_id = lr.id
          JOIN corpus.provision AS p ON p.resource_id = lr.id
          JOIN corpus.provision_version AS v ON v.provision_id = p.id AND v.expression_id = e.id
         WHERE lr.local_id = 'T0000001' AND v.superseded_at IS NULL
         LIMIT 1
    ), res AS (
        INSERT INTO corpus.legal_resource (id_scheme, local_id, norm_kind, document_type)
        SELECT t.id_scheme, 'F' || lpad(g::text, 7, '0'), t.norm_kind, t.document_type
          FROM tmpl AS t CROSS JOIN generate_series(1, %(laws)s) AS g
        RETURNING id
    ), expr AS (
        INSERT INTO corpus.legal_expression
            (resource_id, language, version_date, title, text_period, text_status, text_authority)
        SELECT res.id, t.language, t.version_date, t.title, t.text_period, t.text_status,
               t.text_authority
          FROM res CROSS JOIN tmpl AS t
        RETURNING id, resource_id
    ), prov AS (
        INSERT INTO corpus.provision (resource_id, provision_type, path)
        SELECT res.id, t.provision_type, 'art_' || n
          FROM res CROSS JOIN tmpl AS t CROSS JOIN generate_series(1, %(provisions)s) AS n
        RETURNING id, resource_id
    )
    INSERT INTO corpus.provision_version
        (provision_id, expression_id, resource_id, content, force_period, commencement_rule)
    SELECT prov.id, expr.id, prov.resource_id, '填充條文 ' || prov.id, t.force_period,
           t.commencement_rule
      FROM prov
      JOIN expr ON expr.resource_id = prov.resource_id
     CROSS JOIN tmpl AS t
"""


def add_filler_laws(conn: Conn) -> None:
    """在已匯入範本法規 `T0000001` 的資料庫上，加 `FILLER_LAWS` 部沒有標註的法規並更新統計。"""
    conn.execute(_FILLER, {"laws": FILLER_LAWS, "provisions": FILLER_PROVISIONS})
    conn.commit()
    # 規劃器的列數估計來自統計；不分析的話，新表用的是預設估計，計畫與線上不是同一種。
    # 整個測試庫共用，前面的測試留下的列數會被背景的自動分析在不確定的時間點記成統計，計畫跟著
    # 變；所以這幾個綱要的表一律在這裡分析，不只分析夾具新增的表。
    tables = conn.execute(
        "SELECT format('%I.%I', schemaname, tablename) FROM pg_tables"
        " WHERE schemaname IN ('corpus', 'annotation', 'vocab')"
    ).fetchall()
    for (table,) in tables:
        conn.execute(f"ANALYZE {table}")
    conn.commit()


def one(conn: Conn, sql: LiteralString) -> Any:
    row = conn.execute(sql, PARAMS).fetchone()
    assert row is not None
    return row[0]


def timed(conn: Conn, sql: LiteralString) -> tuple[float, list[tuple[object, ...]]]:
    started = time.perf_counter()
    rows = conn.execute(sql, PARAMS).fetchall()
    return time.perf_counter() - started, rows


def difference(conn: Conn, left: LiteralString, right: LiteralString) -> int:
    """`left` 有而 `right` 沒有的列數；兩個方向都是零才是相同。"""
    return int(one(conn, f"SELECT count(*) FROM (({left}) EXCEPT ({right})) AS d"))


def provision_lookups_by_resource(conn: Conn, sql: LiteralString) -> int:
    """執行計畫裡「以法規取條文」的次數：解析函式每為一部法規解析，就會在這裡查一次。

    數次數而不是量時間：線上的二次方成本只在百萬列的表上出現，小夾具上規劃器會換計畫，
    舊查詢反而很快；但無論哪種計畫，舊查詢都得替每一部法規解析，新查詢只替有標註的法規解析。
    """
    explained = one(conn, f"EXPLAIN (ANALYZE, FORMAT JSON) {sql}")
    total = 0
    stack: list[dict[str, Any]] = [explained[0]["Plan"]]
    while stack:
        node = stack.pop()
        conds = str(node.get("Index Cond", "")) + str(node.get("Filter", ""))
        if node.get("Relation Name") == "provision" and "resource_id" in conds:
            total += int(node["Actual Loops"])
        stack.extend(node.get("Plans", []))
    return total
