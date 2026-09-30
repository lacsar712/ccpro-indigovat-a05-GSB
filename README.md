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

1. **染缸还原台**：横滑缸位条，每缸显示状态、最近电位与 redox sparkline；有进行中交接卷的缸挂「交接中」标
2. **工坊 chip**：仅作缸位筛选，无独立工坊 CRUD 页
3. **点缸展开**：同页内登记浸染批次、改状态、看近几笔；无平行「染缸表 / 批次表」
4. **清缸交接专页**（顶栏 `/handoff`）：进行中卷列表、开卷、改卷、主管点完成

**业务规则**：

- 状态改为 `ready`（可染色）时，最新批次 `redoxMv` 须已填且 ≤ -500（见 `vat_rules.py`）。
- 可染色缸退回闲置前，必须先在清缸交接专页**开卷并由主管完工**；卷进行中时点改闲置会被同一校验函数 `validate_vat_status_change` 拒绝（顶栏入口与任何表单共用此函数）。
- **清缸交接卷**：字段为染缸、开卷人、清出米数、接收班组、开卷时间、完工时间（起初不填，主管完成时落下）；状态由完工时间隐含（进行中 / 已完成）。
  - 仅 `ready`（可染色）缸可开卷；
  - 清出米数须为正，上限 = 该缸**最近 5 笔浸染布米合计**；新建与更新共用同缸进行中唯一与米数上限校验；
  - 同缸进行中卷至多一张：`cleaning_tickets` 上建有部分唯一索引（`completedAt IS NULL`）兜底，两人几乎同时开卷只有一张成功，被拒一方刷新后还原台与交接专页仍可正常打开；
  - 主管点完成时会**再次核对该缸最新浸染读数仍 ≤ -500 mV**（复用可染色电位门槛），达标才落完工时间。
- 种子数据：可染色缸 V-12 自带一张进行中交接卷。

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
4. **CleaningTicket**：清缸交接卷，归属染缸；开卷人、`clothMeters`、`receiveTeam`、`openedAt`、`completedAt`（空=进行中）；同缸进行中唯一靠部分唯一索引保证

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
    routers/
    services/vat_rules.py
    templates/   # base / bay / handoff / login
```
