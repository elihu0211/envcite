# envcite-jobkit

批次 Job 的宣告式韌性政策：斷路器、重試退避、預算、進度心跳、單項隔離。只用標準庫，不引入第三方依賴。

斷路判斷需要領域知識，所以放在應用層而不是網格：處理函式（或 `classify`）說這次失敗屬於「單項問題」還是「端點問題」，框架據此處置。批次 Job 短命，斷路器的記憶體狀態重啟即歸零，所以預設把「斷路器開啟」表達成結束碼 75，交給 K8s／排程器稍後重試。

## 用法

```python
from envcite_jobkit import ExitCode, Policy, raise_for_status, run


def handle(batch):
    resp = post(batch)  # 你的 HTTP 呼叫
    raise_for_status(resp.status)  # 413 → 隔離；408/429/5xx → 計入斷路器；401/403 → 致命


policy = Policy.load("job-policy.toml")  # 可省略；環境變數 JOBKIT_* 優先
raise SystemExit(run(batches(), handle, policy, item_id=lambda b: b.id))
```

`run(source, handler, policy, *, item_id, classify, sink, breaker_store, metrics, stats, clock, sleep, rng) -> ExitCode`。`source` 是任何可迭代物；一項就是一個工作單位（逐筆或一批）。

處置由例外決定（`classify` 可替換，預設如下）：

| 例外 | 處置 | 說明 |
|------|------|------|
| `ItemError` | 隔離 | 記入 `DeadLetterSink`，不計入斷路器，繼續 |
| `EndpointError`、`TimeoutError`、`ConnectionError` | 計入斷路器並退避重試 | 斷路器開啟即停；重試用盡而斷路器未開，該項記入隔離（`kind=retries_exhausted`） |
| `FatalError` | 結束碼 2 | 資料不合預期 |
| 其他例外 | 結束碼 1 | 未分類，記錄後結束，不吞掉 |

`raise_for_status(status)` 的對應：413 與其他 4xx → `ItemError`；408、429、5xx → `EndpointError`；401、403 → `FatalError`。

## 結束碼契約

| 碼 | 意義 | K8s 建議 |
|----|------|----------|
| 0 | 完成，或預算用完（剩餘留待下次） | 成功 |
| 75 | 斷路器開啟（EX_TEMPFAIL），端點有問題 | 稍後重試，不計入 `backoffLimit` |
| 2 | 致命，資料不合預期，重試無用 | `FailJob` |
| 1 | 其他暫時性失敗 | 依 `backoffLimit` 重試 |

數值定義在 `exitcodes.py`，不可更動。

## 政策欄位

來源優先序：環境變數 `JOBKIT_<欄位大寫>` > TOML（頂層或 `[jobkit]` 表）> `Policy` 預設。空字串視為未設定；未知的 TOML 欄位報錯。

| 欄位 | 預設 | 說明 |
|------|------|------|
| `name` | `job` | 識別；預設心跳檔名 |
| `failure_threshold` | 0.5 | 視窗內失敗率達此值開啟斷路器 |
| `min_calls` | 10 | 視窗內至少幾次呼叫才判斷 |
| `window_seconds` | 60 | 失敗率的滑動視窗 |
| `open_seconds` | 300 | 開啟多久後轉半開 |
| `half_open_probes` | 3 | 半開需連續成功幾次才關閉；任一次失敗重新開啟 |
| `open_action` | `exit` | `exit`：以 75 結束；`wait`：就地等到半開 |
| `max_attempts` | 3 | 單項的端點錯誤最多嘗試次數 |
| `backoff_base_seconds`、`backoff_max_seconds` | 0.5、30 | 退避 `[0, min(max, base·2^(n-1))]`（full jitter） |
| `budget_seconds`、`budget_items` | 不限 | 用完以 0 結束；在取下一項前檢查，不多消耗來源 |
| `heartbeat_path` | `/tmp/<name>.heartbeat` | 心跳檔 |
| `heartbeat_min_interval_seconds` | 0 | 心跳最短間隔，項目很細時節流 |
| `dead_letter_table` | `jobkit_dead_letter` | 資料庫實作的隔離表名 |
| `dead_letter_path` | 無 | 設定時用 JSONL 檔實作；否則記憶體 |
| `max_dead_letters` | 不限 | 隔離數超過即視為資料不合預期，結束碼 2 |

## 進度心跳

每完成一項更新心跳檔的 mtime。livenessProbe 看「多久沒進度」，取代寫死的 `activeDeadlineSeconds` 總期限：

```yaml
livenessProbe:
  exec:
    command: ["python", "-m", "envcite_jobkit.heartbeat", "/tmp/sync-moj.heartbeat", "300"]
  periodSeconds: 60
```

檔案尚不存在視為健康（第一項還沒完成）；最大間隔要大於單項最慢的處理時間。

## 隔離（dead-letter）

`DeadLetterSink.record(DeadLetter(item_id, error, kind, attempts, at))`。內建 `MemoryDeadLetterSink`、`JsonlDeadLetterSink`。

資料庫實作（尚未提供，也不在此建立遷移）建議的資料表，表名取自 `dead_letter_table`：

| 欄位 | 型別 | 說明 |
|------|------|------|
| `job` | text | `Policy.name` |
| `item_id` | text | 項目識別 |
| `kind` | text | `item`／`retries_exhausted` |
| `error` | text | 例外型別與訊息 |
| `attempts` | int | 本次嘗試次數 |
| `first_seen_at`、`last_seen_at` | timestamptz | |
| `seen_count` | int | 跨次執行累計 |

主鍵 `(job, item_id)`；以 upsert 累計 `seen_count`，同一項反覆隔離時一眼可見。

## 跨次執行的斷路器狀態

預設只在記憶體。要讓「開啟」跨次執行有效（重試時不再打爆端點），實作 `BreakerStore.load/save`，每個 `job` 一列：

| 欄位 | 型別 |
|------|------|
| `job` | text，主鍵 |
| `state` | text（`closed`／`open`／`half_open`） |
| `opened_at` | double precision，Unix 秒 |

載入到 `open` 且未過 `open_seconds` 時，`run` 在消耗來源之前就回 75。

## 指標

`Metrics.gauge(name, value)`：`jobkit_breaker_state`（0 closed、1 half_open、2 open）、`jobkit_dead_letter_total`、`jobkit_budget_remaining_seconds`／`_items`。預設 `LogMetrics` 寫日誌；接 OTel 由服務自己實作該介面。

## 與 K8s 的對應

`podFailurePolicy`（Job `restartPolicy: Never` 時才可用）：

```yaml
podFailurePolicy:
  rules:
    - action: FailJob
      onExitCodes: {containerName: job, operator: In, values: [2]}
    - action: Ignore      # 斷路器開啟：不消耗 backoffLimit，由排程器下次再跑
      onExitCodes: {containerName: job, operator: In, values: [75]}
```

退出 1 走 `backoffLimit`。`Ignore` 會立刻重建 Pod，若要拉開間隔，改由 CronJob 的下個排程重試（`Ignore` 換成 `Count` 並設小的 `backoffLimit`）。

## 測試

時鐘與 sleep 可注入，測試不真的等待。

```bash
uv run python -m pytest libs/python/envcite-jobkit
uv run ruff check libs/python/envcite-jobkit
uv run python -m mypy libs/python/envcite-jobkit/src
```
