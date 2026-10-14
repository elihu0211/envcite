# 模型配置（`models.toml`）

`env-law-pipeline` 的抽取與驗證用哪些模型、各擔任什麼角色，全部寫在一個 TOML 檔。範本是同目錄的 `models.example.toml`。檔案不含金鑰，只寫金鑰放在哪裡。

```bash
env-law-pipeline --pending --limit 10 --models models.toml
```

## 檔案結構

三個區塊，缺任何一個角色、有不認得的鍵、參照不存在的名稱，載入時都報錯並拒絕開跑。

```toml
[endpoint.<端點名>]   # 一個 OpenAI 相容的服務：網址與金鑰的位置
[model.<模型別名>]    # 一個模型：用哪個端點、送出什麼名稱、屬於哪個家族
[roles]               # 四個角色各指名一個模型別名
```

### `[endpoint.<名>]`

| 鍵 | 說明 |
|----|------|
| `base_url` | 服務網址，結尾含 `/v1`。與 `base_url_env` 擇一 |
| `base_url_env` | 存放網址的環境變數名稱。與 `base_url` 擇一 |
| `api_key_env` | 存放金鑰的環境變數名稱 |
| `api_key_file` | 金鑰檔路徑，相對於 `models.toml` 所在目錄 |

- `api_key_env` 與 `api_key_file` 至多一個；都不給表示該端點不需金鑰（自架服務）。
- `base_url` 與 `base_url_env` 必須恰好給一個。
- 環境變數或金鑰檔不存在、金鑰檔為空，載入時報錯。

### `[model.<別名>]`

| 鍵 | 必填 | 說明 |
|----|------|------|
| `name` | 是 | 原樣送給端點的模型識別 |
| `provider` | 否 | `chat`（預設）或 `anthropic` |
| `endpoint` | `chat` 必填 | 引用的 `[endpoint.<名>]` |
| `family` | `chat` 必填 | 模型家族，由人宣告 |
| `max_tokens` | 否 | 回答長度上限；`chat` 預設 8192（推理模型的思考計入輸出長度） |
| `max_retries` | 否 | 暫時性失敗的重試次數，預設 3，`0` 不重試 |

`provider = "anthropic"` 的模型不經端點：不可寫 `endpoint` 與 `family`（家族固定為 `anthropic`），金鑰取自環境變數 `ANTHROPIC_API_KEY`，需要 `uv sync --extra llm`。

**`family` 為什麼要人宣告。** 獨立性規則依家族判定：驗證者不得與抽取者同一家族，評審團至少要兩個不同家族。託管服務上的模型名稱可能是別名（同一個模型掛在幾個名字下），不能從名字推測。宣告前先查清楚模型實際屬於哪一家。

### `[roles]`

| 角色 | 擔任者 | 做什麼 |
|------|--------|--------|
| `extractor` | 一個別名 | 讀條文，寫出結構化陳述 |
| `roundtrip` | 一個別名 | 沒看過原文，只依陳述的確定性描述重抽，比對結構 |
| `independent_test` | 一個別名 | 只讀原文設計案例，丟進推論程式比對 |
| `panel` | 別名清單 | 評審團，三分之二多數 |

驗證者（`roundtrip`、`independent_test`、`panel`）與抽取者同一家族時，該閘門不採計，結果停在 `inconclusive`，所以配置至少要有兩個家族不同於抽取者的驗證者，評審團內要有不同家族。管線開跑前還會對所有模型問兩個固定的問題，答案逐字相同就拒絕開跑（抓同一模型的別名）。

## 金鑰檔

- 檔案只放金鑰一行，前後空白會被去掉。
- 本機：放在 `services/ontology/.secrets/`（已被 `.gitignore` 忽略），`api_key_file = ".secrets/chat_key"`。金鑰檔位於 git 工作樹內且未被忽略時，載入會被拒絕，避免被提交。
- 不在 git 工作樹內的位置（例如容器內 `/etc/…`）不受此檢查影響。
- 不想用檔案時改用 `api_key_env`。`models.toml` 本身也列在 `.gitignore`。

## 本機使用

```bash
cd services/ontology
cp models.example.toml models.toml          # 改端點、模型、家族、角色
mkdir -p .secrets && printf '%s' '<金鑰>' > .secrets/chat_key
# 在 models.toml 把端點改為：base_url = "https://…/v1"  與  api_key_file = ".secrets/chat_key"
uv run env-law-pipeline --law <pcode> --models models.toml
```

## 叢集（Kubernetes）

排程的 CronJob（`env-law-annotation-pipeline`）把 `Secret/env-law-pipeline` 掛成目錄 `/etc/env-law/pipeline/`，並以 `--models=/etc/env-law/pipeline/models.toml` 讀取。Secret 的每個鍵成為該目錄下的一個檔案，所以 `models.toml` 裡的 `api_key_file` 直接寫金鑰的鍵名。

```bash
kubectl -n env-law create secret generic env-law-pipeline \
  --from-file=models.toml=./models.toml \
  --from-file=chat_key=./.secrets/chat_key
```

對應的 `models.toml` 在叢集上這樣寫：

```toml
[endpoint.hosted]
base_url = "https://…/v1"
api_key_file = "chat_key"
```

- 更新 Secret 後，下一次排程的 Job 就用新內容；不需要重新部署。
- `provider = "anthropic"` 的模型需要 `ANTHROPIC_API_KEY` 環境變數，目前的 CronJob 沒有注入它；要用就先在 CronJob 加上來自 Secret 的環境變數。
- 每次最多處理的法規數是 CronJob 的 `--limit`，即每次的模型費用上限。
