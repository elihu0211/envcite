---
name: bootstrap-stack
description: >-
  Wire a new service into the envcite monorepo and keep agent ops accurate.
  Use when adding a service under services/, picking its framework or package
  manager, or writing a new service's install/build/test/lint commands into its AGENTS.md.
---

# Bootstrap a service in this monorepo

## Goal

Turn a new `services/<name>/` directory into a runnable, checked service without breaking the ones that already exist.

## Precedent

The services already under `services/` are wired; follow whichever is closest rather than inventing a new pattern. Versions come from `global.json`, `.python-version` and `uv.lock`, not from this list.

- `services/platform` — C# / .NET, ASP.NET Core modular monolith, `dotnet` 原生工具
- `services/ontology` — Python, `uv`, gRPC server
- `services/consult` — C# / .NET, ASP.NET Core gRPC server（無資料庫；經 gRPC 呼叫 ontology；共用 `libs/dotnet/`）

All consume the **same** contract at the repo root `proto/`. The contract is **gRPC (protobuf)**, not OpenAPI — generated stubs are produced from `proto/`, never hand-written, and never copied into a service.

## Steps

1. Confirm open decisions with the user before wiring anything（語言、套件管理器、框架、測試執行器、資料庫）。動任何函式庫之前先查官方文件。
2. Scaffold only what was agreed, under `services/<name>/`.
3. Add a nested `AGENTS.md` in that service; put its exact、可直接複製的指令寫進 **Commands**。倉庫層規範留在根 `AGENTS.md`，不要複製下來。
4. 依根 `AGENTS.md`「目錄配置與擴充判準」接上 CI（job、`rules:changes` 錨點、`proto` 觸發路徑；要部署的再加 `Dockerfile` 與 `<name>-image` job，清單先在部署庫合併）——契約一改，**每個**消費它的服務都要重驗。
5. 同一次提交更新根 `README.md` 的目錄與根 `AGENTS.md` 的索引表；新服務的 `README.md` 與 `AGENTS.md` 開頭各放索引與回總覽的連結。
6. List generated/vendored paths under **Safety**（產生的 stub、遷移檔、鎖定檔），讓代理人略過它們。
7. Add a skill under `.agents/skills/` only if a workflow is longer than a short AGENTS.md section.

## Do not

- 把服務拆成獨立倉庫，或引入多語言建置系統（Bazel／Pants／Nx）——理由見根 `AGENTS.md`
- 在服務內另存一份 `proto/`
- 把根 `AGENTS.md` 的內容複製進 `CLAUDE.md` 或其他廠商檔（`CLAUDE.md` 只匯入）
