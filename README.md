<!--
type: reference
applies-to: all
owns: [service-map]
-->

# 臺灣環境知識的檢索與引用接地

[![ci](https://github.com/elihu0211/envcite/actions/workflows/ci.yml/badge.svg)](https://github.com/elihu0211/envcite/actions/workflows/ci.yml)

將可引用的材料收成封閉證據集交給呼叫端；證據不足時拒答。回答由呼叫端的模型生成。

## 目錄

| 路徑 | 職責 | 文件 |
|------|------|------|
| `services/platform/` | 多租戶控制平面：對外 REST、API key 授權、配額與計量 | [README](services/platform/README.md#概念) · [AGENTS](services/platform/AGENTS.md) |
| `services/consult/` | 接地：封閉證據集與可答性判定，租戶隔離 | [README](services/consult/README.md#概念) · [AGENTS](services/consult/AGENTS.md) |
| `services/ontology/` | 本體：知識的集中存放、檢索與推論，不分租戶 | [README](services/ontology/README.md#概念) · [AGENTS](services/ontology/AGENTS.md) |
| `proto/` | 服務間 gRPC 契約，唯一真值 | [README](proto/README.md#概念) |
| `libs/dotnet/` | .NET 服務共用的主機設定：設定繫結、gRPC 用戶端、遙測；不發佈 | [README](libs/dotnet/README.md) |
| `libs/python/` | 可獨立測試的 Python 套件，uv workspace 成員；不發佈 | [AGENTS](libs/python/AGENTS.md) |

## 入口

| 任務 | 文件 |
|------|------|
| 語料範圍 | [platform「語料範圍」](services/platform/README.md#語料範圍) |
| 服務組合與呼叫端接法 | [proto](proto/README.md#概念) |
| 本機開發 | [CONTRIBUTING](CONTRIBUTING.md#概念) |
| 建立營運者 | [CONTRIBUTING「本機第一個帳號」](CONTRIBUTING.md#本機第一個帳號) |
| 環境變數與機密來源 | [CONFIGURATION](CONFIGURATION.md#概念) |
| 代理作業規範 | [AGENTS](AGENTS.md) |
