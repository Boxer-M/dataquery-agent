# 本地安装与运行

本手册面向全新的本机安装。运行时使用 Python 3.12、Node 22.12+、npm 和 Docker Compose。扫描型 PDF 需要 macOS Apple Vision 及 Xcode Command Line Tools（`xcode-select --install`）。文本型 PDF 由 pypdf 提取，其他平台的完整工作台和 OCR 流程尚未验收。Chrome 用于自动浏览器验收。

## 首次初始化

在项目根目录执行。已有环境可跳过已完成的凭据创建与角色初始化。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-pdf.txt
npm --prefix frontend ci
cp -n .env.example .env
chmod 600 .env
```

用本机编辑器填写 `.env` 的 `POSTGRES_PASSWORD`、`GEMINI_API_KEY` 和可用的 `GEMINI_MODEL`。保留 `POSTGRES_DB=olist` 作为容器初始化库，仓库脚本会创建独立的 `sqlshed`。三个演示只使用 SQL Shed 数据。

```sh
docker compose -f compose.yaml -f compose.rag.yaml up -d --wait db
docker compose exec -T db pg_isready
export PYTHONPATH=src
.venv/bin/python -u scripts/download_sqlshed.py
.venv/bin/python scripts/ingest_sqlshed.py
.venv/bin/python scripts/prepare_release_warehouse.py
for service in metric_reader detail_reader dynamic_reader agent_meta query_assets knowledge; do
  .venv/bin/python "scripts/provision_${service}.py" || break
done
```

数仓准备脚本调用现有 DWD、DIM、DWS 和 ADS 构建器。重复执行会验证现有对象。它依赖 SQL Shed ODS，旧 Olist 数据及审计脚本可独立复现。角色脚本把凭据写入本地受保护的 `.env.*` 文件。若已有数据库角色和凭据，保留并检查现有配置。

首次生成 API Bearer 及本地 PDF 签名密钥。已有文件时保留原配置。

```sh
.venv/bin/python - <<'PY'
import os
from secrets import token_hex
with os.fdopen(os.open(".env.api", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
    stream.write("DATAQUERY_HTTP_TOKEN=" + token_hex(32) + "\n")
PY
.venv/bin/python -m dataquery_agent.rag.pdf_cli init-key
```

在正式口径引用演示前，确认公开指标目录可发给 Gemini，然后建立 Catalog 索引。PDF 发布在工作台独立确认授权。

```sh
.venv/bin/python -m dataquery_agent.rag.cli seed --all-metrics --allow-external
```

## 启停

```sh
.venv/bin/python scripts/local_workspace.py --check
.venv/bin/python scripts/local_workspace.py
```

脚本检查配置键、前端依赖和 PostgreSQL 监听，启动 API 和 Vite 后通过代理请求健康接口。默认地址为 [工作台](http://127.0.0.1:5173/)，API 只绑定 `127.0.0.1:8000`。Ctrl-C 清理该脚本启动的进程。遇到端口被占用，脚本会显示端口并退出。分别开发时可在两个终端启动 `PYTHONPATH=src .venv/bin/python -m dataquery_agent.api` 与 `npm --prefix frontend run dev`。停止数据库可执行 `docker compose -f compose.yaml -f compose.rag.yaml stop db`，数据卷保留。

`/api/health` 检查 HTTP 存活。验证数据库和模型时，请提交一条实际问题并查看 Trace。签名密钥、Bearer、各 DSN、Gemini 密钥和详细日志保存在本机。

## 独立 ODS 验证

综合评测通过独立 PostgreSQL 审计账号查询同一快照的 ODS。运行评测前在 0600 的 `.env.sqlshed-reader` 配置 `SQLSHED_HOST`、`SQLSHED_PORT`、`SQLSHED_DATABASE`、`SQLSHED_USER`、`SQLSHED_PASSWORD`。账号需要数据库 `CONNECT`、ODS `USAGE` 和现有 ODS 表 `SELECT`。手工演示可用已有 ODS 读取账号在 psql 独立对账：

```sql
BEGIN READ ONLY;
SELECT count(*) FROM ods.orders
WHERE ordered_at >= TIMESTAMP '2025-01-01'
  AND ordered_at < TIMESTAMP '2025-02-01';
COMMIT;
```

Olist 原始数据仅用于历史审计及明确依赖它的测试。[Olist 与 SQL Shed 数据说明](../../data/README.md)
