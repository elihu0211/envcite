---
name: bootstrap-stack
description: >-
  Wire a new service into the env-law monorepo and keep agent ops accurate.
  Use when adding a service under services/, picking its framework or package
  manager, or writing a new service's install/build/test/lint commands into its AGENTS.md.
---

# Bootstrap a service in this monorepo

## Goal

Turn a new `services/<name>/` directory into a runnable, checked service without breaking the ones that already exist.

## Precedent

Three services are already wired; follow whichever is closest rather than inventing a new pattern.

- `services/platform` — C# / .NET 10, ASP.NET Core modular monolith, `dotnet` 原生工具
- `services/ontology` — Python 3.14, `uv`, gRPC server
- `services/consult` — Python 3.14, `uv`, gRPC server（無資料庫；經 gRPC 呼叫 ontology）

All consume the **same** contract at the repo root `proto/`. The contract is **gRPC (protobuf)**, not OpenAPI — generated stubs are produced from `proto/`, never hand-written, and never copied into a service.

## Steps

1. Confirm open decisions with the user before wiring anything（語言、套件管理器、框架、測試執行器、資料庫）。動任何函式庫之前先查官方文件。
2. Scaffold only what was agreed, under `services/<name>/`.
3. Add a nested `AGENTS.md` in that service; put its exact、可直接複製的指令寫進 **Commands**。倉庫層規範留在根 `AGENTS.md`，不要複製下來。
4. 依根 `AGENTS.md`「目錄配置與擴充判準」接上 CI（job、`rules:changes` 錨點、`proto` 觸發路徑；要部署的再加 `Dockerfile` 與 `<name>-image` job，清單先在 `envcite-deploy` 合併）——契約一改，**每個**消費它的服務都要重驗。
5. List generated/vendored paths under **Safety**（產生的 stub、遷移檔、鎖定檔），讓代理人略過它們。
6. Add a skill under `.agents/skills/` only if a workflow is longer than a short AGENTS.md section.

## Do not

- 把服務拆成獨立倉庫，或引入多語言建置系統（Bazel／Pants／Nx）——理由見根 `AGENTS.md`
- 在服務內另存一份 `proto/`
- 把根 `AGENTS.md` 的內容複製進 `CLAUDE.md` 或其他廠商檔（`CLAUDE.md` 只匯入）
