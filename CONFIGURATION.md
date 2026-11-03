---
type: reference
applies-to: all
owns: [config-naming, secret-sources]
---

# 設定與金鑰

各服務的環境變數命名，以及機密的來源。回[倉庫總覽](README.md)；各服務的具體鍵見其 README。

## 命名

- **一律 `ENVCITE_` 開頭**，只有兩類例外：第三方工具自己定義的標準名稱照舊（`OTEL_*`、libpq 的 `PG*`、`ASPNETCORE_*`、`DOTNET_*`、`ANTHROPIC_API_KEY`），改了它們就不再被該工具讀到。
- **Python：`ENVCITE_<服務>_<名稱>`**，全大寫、單底線（如 `ENVCITE_ONTOLOGY_DSN`）。設定類別的 `env_prefix` 是 `ENVCITE_<服務>_`，欄位名即 `<名稱>` 小寫。
- **.NET：`ENVCITE_` ＋ 設定路徑，`:` 寫成 `__`**（如 `ENVCITE_Platform__AdminKeySha256` 對應 `Platform:AdminKeySha256`）。路徑沿用 `appsettings.json` 的區段與大小寫；.NET 設定不分大小寫，但文件與部署清單照 `appsettings.json` 寫。
- **共用函式庫**用 `ENVCITE_<套件>_<名稱>`（如 `ENVCITE_JOBKIT_OPEN_SECONDS`）。
- **工具腳本**沿用所屬服務的前綴（如 `ENVCITE_ONTOLOGY_DEV_DSN`、`ENVCITE_ONTOLOGY_TEST_DSN`）。
- **機密**的名稱以 `_API_KEY`、`_PASSWORD` 或 `_SECRET` 結尾，一看名稱就知道不能進設定檔、日誌與測試輸出；非機密不用這些結尾。

## 機密的來源

設定檔不放機密值，只放金鑰的**名稱**，欄位一律叫 `api_key`：

```toml
[endpoint.hosted]
base_url = "https://…/v1"
api_key = "ENVCITE_ONTOLOGY_HOSTED_API_KEY"
```

名稱依序從兩處解析，取第一個非空者；兩處都沒有則載入失敗，訊息列出名稱與找過的位置（不含值）：

1. **同名環境變數**——容器、CI、Linux 伺服器。
2. **作業系統鑰匙圈裡 service 同名的通用密碼**——開發者機器。macOS 用 `security find-generic-password -s <名稱> -w`，Linux 用 `secret-tool lookup service <名稱>`（libsecret）；Windows 與沒有鑰匙圈的環境只有第 1 項。

同一個 `models.toml` 在各環境因此寫法相同；容器沒有鑰匙圈，缺環境變數時直接失敗。金鑰經子行程的標準輸出回到本行程，不進命令列、不進日誌。

存入鑰匙圈（值由終端機提示輸入，不經命令列）：

```bash
security add-generic-password -s ENVCITE_ONTOLOGY_HOSTED_API_KEY -a "$USER" -w      # macOS
secret-tool store --label=ENVCITE_ONTOLOGY_HOSTED_API_KEY service ENVCITE_ONTOLOGY_HOSTED_API_KEY   # Linux
```

.NET 服務只經環境變數取機密，不讀鑰匙圈。其設定來源只認 `ENVCITE_` 前綴的環境變數（`EnvCite.ServiceDefaults` 的 `AddServiceEnvironment`，取代預設不帶前綴的來源）；`OTEL_*` 以原名保留給 OpenTelemetry。
