# LLM Benchmark

面向 **OpenAI Chat Completions 兼容接口**的单机性能压测与分析工具。

项目同时提供 Web 管理界面和独立 CLI，支持六类测试计划、可复现数据集、响应断言、阈值与自动停止、历史基线、多模型顺序/同步比较，以及 JSON、压缩 JSONL、自包含 HTML、XLSX、CSV 报告。生产部署采用单容器、SQLite 和本地执行器，可在完全无法访问互联网的企业内网运行。

## 项目边界

- 仅完善 OpenAI 兼容协议的性能压测；
- 当前重点是吞吐、延迟、TTFT、生成速度、成功率、稳定性和容量拐点；
- 不生成不透明的模型综合分；权威模型能力评测属于后续独立模块；
- 保持纯单机个人工具，不引入 Redis、Celery、外部数据库或云服务；
- Web 登录仅用于避免内网无关人员误用或滥用，不作为复杂多租户权限系统；
- Windows 7 只作为浏览器客户端，不要求服务器运行在 Windows 7；
- 不支持 Internet Explorer。

## 核心能力

### 专业压测计划

- `smoke`：小规模连接和协议冒烟；
- `baseline`：单并发延迟与生成速度基线；
- `fixed_concurrency`：固定并发吞吐测试；
- `stepped`：多阶段阶梯容量测试；
- `constant_rate`：恒定到达率、排队与背压测试；
- `stability`：长时间稳定性测试。

支持预热、冷却、请求数或时长结束条件、目标 QPS、安全硬上限、高风险确认和协作式停止。

### 指标与结果

- 端到端延迟：mean、P50、P90、P95、P99；
- 首 Token 延迟（TTFT）；
- 生成阶段时长；
- 单请求输出 TPS 与聚合输出 TPS；
- 实际 QPS；
- 传输成功、协议有效、响应断言通过三层统计；
- Token usage 来源与不可用状态；
- 结构化错误分类和脱敏失败信息；
- successive time windows 和负载阶段事件。

### 可复现负载

- 内置 `general`、`structured`、`reasoning` 提示类型；
- `short`、`medium`、`long` 输入规模；
- 自定义 System/User 消息；
- OpenAI `messages` JSONL 数据集；
- 数据集 SHA-256、版本、随机种子和实际样本顺序快照。

### 质量与安全控制

- 非空、Token 范围、finish reason、包含、正则、精确匹配；
- JSON 解析、JSON Schema、响应字段断言；
- 完成后逐指标阈值和稳定退出码；
- P95、错误率、队列、生成器 CPU/内存、事件循环延迟持续窗口自动停止；
- 历史基线兼容性检查和逐指标回归结论；
- API Key 本地加密保存，日志、响应和报告统一脱敏。

### 模型比较

- 默认顺序运行，同一时间只执行一个模型压测；
- 可显式确认后同步运行；
- 所有模型复用相同数据集、随机种子和样本顺序；
- 明确区分独立端点测量与共享资源竞争；
- 同步模式展示每模型负载和聚合负载，不隐藏资源竞争影响。

## 架构概览

```text
浏览器（Chrome / Firefox）
          │
          ▼
Vue 3 + Vite + Pinia + Vue Router
          │ HTTP / SSE
          ▼
FastAPI ─────────────── SQLite + 本地报告目录
  │                           ▲
  │ 持久化任务队列             │ 状态、事件、结果
  ▼                           │
本地 Executor ── benchmark_core ── OpenAI 兼容模型端点

独立 CLI ─────── benchmark_core ── OpenAI 兼容模型端点
```

Web 与 CLI 共享 `benchmark_core`，避免两套指标和调度逻辑产生不同结论。容器内 API 与 executor 是两个独立进程，由轻量 supervisor 统一启动和回收；元数据、凭据密文、任务和报告索引保存在本地 SQLite，报告文件保存在同一数据卷。

## 完全离线部署

### 环境要求

目标服务器已具备：

- Docker；
- Docker Compose 插件，即 `docker compose`；
- POSIX shell；
- `sha256sum` 或 `shasum`；
- 无需访问 Docker Hub、PyPI、npm、CDN 或公网字体。

建议离线部署包包含：

```text
llm-benchmark/
├── images/
│   └── llm-benchmark-<version>.tar
├── checksums.sha256
├── docker-compose.yml
├── .env.example
├── install.sh
├── run.sh
├── stop.sh
└── update.sh
```

### 安装

```bash
chmod +x install.sh run.sh stop.sh update.sh
./install.sh
```

`install.sh` 会：

1. 检查 Docker 和 Docker Compose；
2. 校验镜像归档 SHA-256；
3. 使用 `docker load` 加载本地镜像；
4. 创建权限为 `600` 的 `.env`；
5. 首次安装时生成随机登录密码和会话密钥；
6. 创建持久化数据卷和本地 `backups/` 目录；
7. 输出初始登录凭据和下一步命令。

安装脚本 **不会自动启动服务**。请妥善保存首次输出的密码；也可以在启动前修改 `.env`。

### 启动

```bash
./run.sh
```

脚本只使用本地镜像，以 `--no-build` 启动 Compose，并等待 `/health/ready` 通过。默认访问地址：

```text
http://<服务器IP>:8080
```

默认绑定地址、端口、容器名、数据卷名和会话设置可在 `.env` 中调整。

### 停止

```bash
./stop.sh
```

停止会保留容器和数据卷，不执行 `down -v`。再次执行 `./run.sh` 即可恢复服务。

### 更新

把新版镜像归档和校验文件放入部署目录后执行：

```bash
./update.sh
```

更新流程会先备份 SQLite、报告数据、`.env`、Compose 配置和旧镜像信息，再加载新版镜像、离线执行数据库初始化/迁移并等待健康检查。任一步骤失败时会自动恢复旧数据、旧配置和旧镜像并重启旧版本。

完整操作、环境变量、归档命名、数据持久化和自动回滚说明见 [完全离线部署说明](docs/离线部署说明.md)。

## 浏览器支持

前端构建目标：

- Chrome 80 及以上；
- Firefox 78 及以上；
- 重点验收 Windows 7 上的 Chrome 109 和 Firefox 115 ESR；
- 目标分辨率包含 1366 × 768；
- 不支持 Internet Explorer。

前端资源全部随镜像提供，不使用 CDN、公网字体、遥测或运行时包下载。

## CLI 快速开始

CLI 不依赖 Web 服务或浏览器，可在源码环境或独立 Python 安装环境中运行。

```bash
export LLM_BENCHMARK_BASE_URL='http://10.0.0.20:8000/v1'
export LLM_BENCHMARK_MODEL='internal-model'
export LLM_BENCHMARK_API_KEY='replace-me'

python3 -m benchmark_cli run \
  --plan smoke \
  --requests 3 \
  --concurrency 1 \
  --format json \
  --format html
```

顺序比较两个模型：

```bash
export MODEL_A_KEY='key-a'
export MODEL_B_KEY='key-b'

python3 -m benchmark_cli compare \
  --target '模型A|http://10.0.0.21:8000/v1|model-a|MODEL_A_KEY' \
  --target '模型B|http://10.0.0.22:8000/v1|model-b|MODEL_B_KEY' \
  --resource-semantics independent \
  --comparison-mode sequential \
  --plan fixed_concurrency \
  --requests 100 \
  --concurrency 10 \
  --seed 42
```

详细参数、六类计划、数据集、断言、阈值、自动停止、同步比较、导出和退出码见 [CLI 使用说明](docs/CLI使用说明.md)。

## 本地开发

### Python

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

启动 API 前必须设置登录与会话环境变量：

```bash
export LLM_BENCHMARK_USER='admin'
export LLM_BENCHMARK_PASSWORD='change-me'
export LLM_BENCHMARK_SESSION_SECRET='replace-with-at-least-16-characters'
export LLM_BENCHMARK_DATA_DIR='./data'
```

分别启动 executor 和 API：

```bash
python3 -m benchmark_server.executor_main
```

```bash
python3 -m uvicorn benchmark_server.main:app --host 127.0.0.1 --port 8080
```

### 前端

项目使用 pnpm 锁定依赖：

```bash
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm run serve
```

前端开发服务默认监听 `8000`，并把 `/api` 和 `/health` 代理到 `127.0.0.1:8080`。

生产构建：

```bash
cd frontend
pnpm run lint
pnpm run build
```

构建产物位于 `frontend/dist/`，由 FastAPI 托管；该目录属于生成文件，不应提交。

## 测试与验证

Python 全量测试：

```bash
python -m pytest -q
```

前端静态检查和构建：

```bash
cd frontend
pnpm run lint
pnpm run build
```

生产镜像构建：

```bash
docker build -t llm-benchmark:local .
```

Compose 启动前需要准备 `.env` 和本地镜像。正式离线部署应使用 `install.sh`、`run.sh`、`stop.sh`、`update.sh`，而不是在目标服务器在线安装依赖或重新构建镜像。

## 目录结构

```text
benchmark_core/        Web 与 CLI 共用的负载、调度、指标、断言、阈值和报告核心
benchmark_cli/         独立命令行入口
benchmark_server/      FastAPI、SQLite、认证、迁移、本地执行器和任务运行器
frontend/              Vue 3 / Vite / Pinia / Vue Router 前端
assets/                内置或示例测试资源
checksums.sha256       离线镜像校验清单（交付包生成）
docs/                  CLI、部署、架构、迁移、排障和设计文档
tests/                 Python 单元与集成测试
Dockerfile             前端构建与 Python 生产运行镜像
docker-compose.yml     单服务离线 Compose
install.sh             校验并加载离线镜像、生成配置
run.sh                 启动并等待健康检查
stop.sh                优雅停止并保留数据
update.sh              备份、更新、健康检查和失败自动回滚
```

以下属于本地或运行生成内容，不应提交：

- `.env`、凭据和会话密钥；
- `.venv/`、`frontend/node_modules/`、`frontend/dist/`；
- `reports/`、`data/`、`backups/`；
- Docker 镜像归档和其他离线交付产物。

## 数据与安全

- 登录账号、密码和会话密钥由环境变量提供；
- 登录会话默认有效 12 小时，可通过 `.env` 调整；
- API Key 使用本机数据目录中的密钥材料加密后保存；
- API、日志、报告和导出不得返回完整 API Key；
- HTTPS 部署时应启用安全 Cookie，并由内网反向代理配置 TLS；
- 默认一次只运行一个压测任务，避免单机执行器和目标服务被无意叠加负载；
- 同步模型比较必须显式确认，其总负载等于各模型负载之和。

## 旧数据迁移

启动时会尝试把旧 `backend/api_configs.json` 和旧 `reports/` 文件登记或迁移到新存储。迁移按源路径和内容 SHA-256 去重；单个旧文件失败只会记录错误，不会阻止新版本启动。原文件不会被删除。

数据库初始化也采用容错启动策略：现有 SQLite 无法正常打开或迁移时，会先保留故障文件并尝试恢复一个可运行数据库。更新脚本在此基础上还提供部署级备份和自动回滚。

## 文档

- [贡献与开发规范](AGENTS.md)
- [CLI 使用说明](docs/CLI使用说明.md)
- [完全离线部署说明](docs/离线部署说明.md)
- [系统架构说明](docs/架构说明.md)
- [旧版本迁移说明](docs/迁移说明.md)
- [故障排查指南](docs/故障排查.md)
- [重构交付验收记录](docs/重构交付验收记录.md)
- [前端设计规范](docs/design/前端设计规范.md)
- [项目优化与重构方案](docs/design/项目优化与重构方案.md)

## License

本项目采用 [MIT License](LICENSE)。
