"""資料庫連線的型別：各模組標註 `Conn`，不各自重新定義。"""

import psycopg

type Conn = psycopg.Connection[tuple[object, ...]]
