"""環境變數設定：各入口的環境變數在此宣告、驗證，是唯一來源。

只讀行程環境（本機以 `uv run --env-file .env <入口>` 載入）；環境變數名稱是
`ENVCITE_ONTOLOGY_` 加欄位名，大小寫不拘。
入口用 `load(<類別>)` 取得設定，缺漏或不合法時一次列出所有變數後結束。
"""

from pathlib import Path

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

PREFIX = "ENVCITE_ONTOLOGY_"


class DatabaseSettings(BaseSettings):
    """所有入口都需要的資料庫連線。"""

    model_config = SettingsConfigDict(env_prefix=PREFIX, extra="ignore")

    dsn: str = Field(min_length=1, description="PostgreSQL 連線字串")


class ModelsSettings(BaseSettings):
    """模型配置檔的位置；語意檢索臂的嵌入模型宣告在它的 `[embedding]`。

    沒設、或檔裡沒有 `[embedding]` 時語意臂關閉，只用字面臂。標註管線另以 `--models` 指定。
    """

    model_config = SettingsConfigDict(env_prefix=PREFIX, extra="ignore")

    models: Path | None = Field(default=None, description="models.toml 的路徑")


class SnapshotSettings(DatabaseSettings, ModelsSettings):
    """讀寫快照的入口（envcite-sync、envcite-acquire、envcite-import）。"""

    snapshot_dir: Path = Field(description="法規快照目錄")


class ServerSettings(DatabaseSettings, ModelsSettings):
    """gRPC 伺服器（envcite-ontology）。"""

    grpc_port: int = Field(default=50051, ge=1, le=65535, description="gRPC 監聽埠")
    # 預設值取自黃金題庫的量測：在這個比例下召回不降、整句問題的延遲約降一個數量級；越低越快，
    # 但越接近開始丟掉有鑑別力的詞。改預設值要重跑 `tools/golden_retrieval_eval.py` 對照。
    lexical_max_df: float = Field(
        default=0.005,
        ge=0,
        le=1,
        description="字面臂略過出現在超過此比例的段落的詞；0 為不略過",
    )
    # 預設值取自 staging 的函釋：有鑑別力的詞組（露天燃燒、噪音、繞流排放）命中約 1% 的可用函釋，
    # 常見詞（固定污染源、水污染防治、廢棄物清理）在 7% 以上；界線落在其間。樣本小，沒有題庫可量，
    # 改預設值要先有以函釋用語查詢的題。
    guidance_max_df: float = Field(
        default=0.03,
        ge=0,
        le=1,
        description="函釋入口：命中的函釋占可用函釋的比例超過此值，查詢就不用函釋入口；0 為不限制",
    )
    # 精排看多深：兩臂取這麼多條候選再精排，取前 limit 條。越深越可能撈回原本在 limit 之外的
    # 相關條文，但精排的延遲與候選數成正比（端點實測 3 條約 0.7 秒、30 條約 1.3 秒）。
    # 上限與兩臂的候選池（`store.SEARCH_POOL`）一致，再深也沒有候選。預設值沒有題庫的量測，
    # 改它要先用 `tools/golden_retrieval_eval.py --rerank` 在開發集量過。
    rerank_candidates: int = Field(
        default=30,
        ge=1,
        le=50,
        description="請求帶 rerank 時，兩臂取幾條候選交給精排模型",
    )


def load[S: BaseSettings](cls: type[S]) -> S:
    """自環境建出設定；失敗時以一則訊息列出所有問題變數（只列名稱與原因，不含值）後結束。"""
    try:
        return cls()
    except ValidationError as e:
        prefix = cls.model_config["env_prefix"]
        problems = ", ".join(
            f"{prefix}{str(err['loc'][0]).upper()} ({err['msg'].lower().rstrip('.')})"
            for err in e.errors()
        )
        raise SystemExit(f"invalid environment: {problems}") from None
