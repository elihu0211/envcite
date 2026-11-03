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
