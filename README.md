# IndigoVat-01 · 染缸还原台

FastAPI + PostgreSQL + Jinja2：主界面是横向**缸位条**（Alpine 反应式），不是工坊/染缸/批次三表导航。Session Cookie 登录；规则在 `app/services/vat_rules.py`。

## 技术栈

- FastAPI、SQLAlchemy 2、PostgreSQL
- 启动时 `create_all` + 幂等种子（蓝靛湾一号坊 / 清水江二号坊）
- Session Cookie 认证（Starlette SessionMiddleware）
- Jinja2 + Alpine.js + Pico（叠靛蓝水墨自定义样式）
- Docker Compose：`web` + `db`

## 端口与数据库

| 服务 | 端口 |
|------|------|
| Web  | **4720** |
| Postgres | **6120**（容器内 5432） |

数据库账号：`indigovat` / `indigovat` / 库名 `indigovat`

## 快速启动

```bash
cd IndigoVat/IndigoVat-01
docker compose up --build -d
```

浏览器打开：http://localhost:4720

演示账号（登录页已预填）：

- `admin` / `123456`
- `worker` / `123456`

## 交互（信息架构）

1. **染缸还原台**：横滑缸位条，每缸显示状态、最近电位与 redox sparkline；有进行中交接卷的缸位条上标注「交接中」
2. **工坊 chip**：仅作缸位筛选，无独立工坊 CRUD 页
3. **点缸展开**：同页内登记浸染批次、改状态、看近几笔；无平行「染缸表 / 批次表」
4. **清缸交接专页**（顶栏「清缸交接」）：进行中交接卷列表、开新卷、主管完成、最近完成

**业务规则**（见 `app/services/vat_rules.py`）：

- 状态改为 `ready`（可染色）时，最新批次 `redoxMv` 须已填且 ≤ -500。
- 状态改为 `idle`（闲置）时走同一校验函数 `validate_vat_status_change`：
  - 该缸有**进行中**交接卷 → 禁止改闲置；
  - 从**可染色**退回闲置 → 须已有一张**已完成**的清缸交接卷。

## 清缸交接卷

可染色缸退回闲置前，必须先在专页开卷并完成交接。字段：染缸、开卷人、清出米数、接收班组、开卷时间、完工时间（起初不填）；**完工时间为空即进行中、非空即已完成**，无独立状态列。

- **开卷条件**：仅「可染色」缸可开卷；同缸进行中只许一张。
- **清出上限**：清出米数须为正，且不超过该缸**最近 5 笔浸染布米合计**；开卷新建与更新共用同一校验（`validate_handover_ticket`）。
- **完成条件**：仅主管可点完成；完成时落下完工时间，并**再次核对该缸最新浸染读数仍 ≤ -500 mV**——与改状态入口读同一函数 `assert_can_mark_ready`。
- **并发**：开卷时对该缸行加锁（`SELECT ... FOR UPDATE`）串行化「查重-开卷」，数据库再以部分唯一索引 `uniq_active_handover_per_vat`（`finishedAt IS NULL` 时 `vat_id` 唯一）兜底——两人几乎同时给同一缸开卷，至多一张成功；被拒者看到正常渲染的专页与错误提示，还原台与专页均不空白。

## 本地开发（可选）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
set POSTGRES_HOST=localhost
set POSTGRES_PORT=6120
uvicorn app.main:app --host 0.0.0.0 --port 4720 --reload
```

## 业务模型

1. **Workshop**：`name`、`region`、`notes`（UI 上仅为筛选片）
2. **Vat**：归属工坊、`code`、`dyeType`、`volumeL`、状态 `idle|reducing|ready`
3. **DipLot**：归属染缸、`dippedAt`、`clothMeters`、`redoxMv`（可空）
4. **CleanHandover**：归属染缸、`opener`（开卷人）、`clearedMeters`、`receivingTeam`、`openedAt`、`finishedAt`（空=进行中）；种子含可染色缸 V-12 的一张进行中卷

## 目录结构

```
IndigoVat-01/
  Dockerfile
  entrypoint.sh
  docker-compose.yml
  requirements.txt
  app/
    main.py
    db.py
    models.py
    schemas.py
    auth.py
    seed.py
    routers/     # auth / pages / handovers
    services/vat_rules.py
    templates/   # base / bay / handover / login
```
