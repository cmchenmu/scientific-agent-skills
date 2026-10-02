# 启动数据库并执行迁移

本文说明如何启动本地 PostgreSQL（带 pgvector）、配置 `lab-agent`，并执行数据库迁移。

## 1. 配置连接

```bash
cd /GIT/scientific-agent-skills/lab-agent
grep '^DATABASE_URL=' .env | sed 's#://[^@]*@#://<redacted>@'
```

`migrations/env.py` 会自动加载项目 `.env` 中的 `DATABASE_URL`。不要把密码写入 `alembic.ini` 或提交 `.env`。

## 2. 启动 PostgreSQL

没有现成服务时，在宿主机执行：

```bash
docker network inspect lab-agent-net >/dev/null 2>&1 || docker network create lab-agent-net
docker run -d --name lab-postgres --network lab-agent-net \
  -e POSTGRES_USER=labuser -e POSTGRES_PASSWORD=labpass \
  -e POSTGRES_DB=labagent -p 5432:5432 \
  -v lab-pg-data:/var/lib/postgresql/data pgvector/pgvector:pg17
until docker exec lab-postgres pg_isready -U labuser -d labagent >/dev/null 2>&1; do sleep 1; done
docker exec lab-postgres psql -U labuser -d labagent -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

将 `DATABASE_URL=postgresql+psycopg://labuser:labpass@HOST_IP:5432/labagent` 写入 `.env`，其中 `HOST_IP` 必须是开发容器可访问的宿主机地址。

## 3. 验证连接并迁移

```bash
uv run --no-sync python - <<'PY'
import os
import psycopg
from dotenv import load_dotenv
load_dotenv('.env')
url = os.environ['DATABASE_URL'].replace('+psycopg', '')
with psycopg.connect(url) as connection:
    print(connection.execute('SELECT 1').fetchone())
PY

uv run --no-sync alembic upgrade head
uv run --no-sync alembic current
```

成功时连接测试输出 `(1,)`，迁移版本为 `0001_initial_governance_schema (head)`。

如果 `uv run` 因无关的 Git 依赖同步或网络代理失败，使用 `--no-sync`；这不会跳过数据库连接或迁移。
