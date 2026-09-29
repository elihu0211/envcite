# env-law-proto

倉庫根 `proto/` 的 Python 產生碼（`env_law.ontology.v1`、`env_law.consult.v2`），由各服務共用。產生碼提交進版控，不手改；改完 `proto/` 後重新產生：

```bash
./scripts/generate.sh
```

Renovate 每週的鎖檔維護若升級了 `grpcio-tools`、`mypy-protobuf` 或 `protobuf`，CI 的 `python-proto` 會因產生碼過期而失敗：在該 MR 的分支上執行上面的指令並提交，下界（`pyproject.toml`）跟著產生碼裡的版本調整。
