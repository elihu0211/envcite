"""環境變數設定：各入口的環境變數在此宣告、驗證，是唯一來源。

只讀行程環境（本機以 `uv run --env-file .env <入口>` 載入）；欄位名稱即環境變數名稱，
大小寫不拘。入口用 `load(<類別>)` 取得設定，缺漏或不合法時一次列出所有變數後結束。
"""

from pathlib import Path

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """所有入口都需要的資料庫連線。"""

    model_config = SettingsConfigDict(extra="ignore")

    ontology_dsn: str = Field(min_length=1, description="PostgreSQL 連線字串")


class EmbeddingSettings(BaseSettings):
    """語意檢索臂的嵌入模型（OpenAI 相容端點）。沒有端點時語意臂關閉，只用字面臂。"""

    model_config = SettingsConfigDict(extra="ignore")

    embedding_base_url: str = Field(
        default="", description="嵌入端點的 /v1 網址；空值為不用語意臂（不送文字到未指定的第三方）"
    )
    embedding_model: str = Field(
        default="bge-m3-embedding", min_length=1, description="嵌入模型識別"
    )
    embedding_api_key: str = Field(default="", description="嵌入端點的金鑰；自架可空")


class SnapshotSettings(DatabaseSettings, EmbeddingSettings):
    """讀寫快照的入口（envcite-sync、envcite-acquire、envcite-import）。"""

    acquire_snapshot_dir: Path = Field(description="法規快照目錄")


class ServerSettings(DatabaseSettings, EmbeddingSettings):
    """gRPC 伺服器（envcite-ontology）。"""

    ontology_grpc_port: int = Field(default=50051, ge=1, le=65535, description="gRPC 監聽埠")


def load[S: BaseSettings](cls: type[S]) -> S:
    """自環境建出設定；失敗時以一則訊息列出所有問題變數（只列名稱與原因，不含值）後結束。"""
    try:
        return cls()
    except ValidationError as e:
        problems = ", ".join(
            f"{str(err['loc'][0]).upper()} ({err['msg'].lower().rstrip('.')})" for err in e.errors()
        )
        raise SystemExit(f"invalid environment: {problems}") from None
