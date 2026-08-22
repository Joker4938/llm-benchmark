# 仓库贡献指南

## 项目结构与模块划分

- `benchmark_core/`：Web、CLI 共用的 OpenAI Chat Completions 压测核心、调度、校验、阈值和报告能力。
- `benchmark_cli/`：主 CLI；`llm_benchmark.py` 与 `run_benchmarks.py` 仅保留旧命令兼容包装。
- `benchmark_server/`：FastAPI、SQLite 仓储、本地 executor、旧数据迁移和单容器进程监督入口。
- `frontend/src/`：Vue 3、Pinia、Vue Router 前端；页面位于 `views/`，通用组件位于 `components/`，请求封装位于 `api/`。
- `assets/` 存放内置测试资源，`openspec/` 描述功能预期，`docs/` 存放部署、CLI、架构和迁移文档。
- `data/`、`reports/`、`output/`、`frontend/dist/` 均为本地或构建产物，不得提交。

## 构建、测试与开发命令

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
python -m benchmark_cli --help

# 本地 Web 开发需分别启动 executor 和 API，并设置登录环境变量
python -m benchmark_server.executor_main
python -m uvicorn benchmark_server.main:app --host 127.0.0.1 --port 8080

cd frontend && pnpm install --frozen-lockfile
pnpm run serve                       # 前端开发服务，端口 8000
pnpm run lint                        # ESLint 检查
pnpm run build                       # 输出到 frontend/dist/

docker compose up -d --build         # 容器化启动完整服务
docker compose down                  # 优雅停止
```

生产入口要求设置 `LLM_BENCHMARK_USER`、`LLM_BENCHMARK_PASSWORD` 和 `LLM_BENCHMARK_SESSION_SECRET`。验证 FastAPI 托管的静态页面前，应重新执行前端构建。

## 分支与提交规范

- `master` 为稳定分支，不直接在其上开发；开始工作前基于最新 `master` 创建短生命周期分支。
- 分支名采用小写英文和连字符：`feature/report-export`、`fix/proxy-port`、`docs/contributor-guide`。
- 提交信息保持简短、祈使句式，沿用 `feat:`、`fix:`、`add:`、`update:`、`docs:`、`refactor:` 等前缀，例如：`fix: 修复并发任务状态更新`。
- 每个提交只处理一个主题，不混入格式化、生成文件或无关修改。

## 代码规范

- Python 使用 4 空格缩进并遵循 PEP 8；函数和变量使用 `snake_case`，常量使用 `UPPER_CASE`。公共函数及复杂逻辑需提供简短、准确的 docstring。
- Vue/JavaScript 使用 2 空格缩进、单引号和无分号风格；组件文件使用 PascalCase；变量、props 和方法使用 camelCase。
- API 字段命名保持与现有接口一致。新增接口需校验输入、返回明确 HTTP 状态码，并避免在日志或响应中暴露 API Key、会话密钥等敏感信息。
- 用户界面和提示信息默认使用中文。避免无关重构，保持函数职责单一，并复用现有 API 封装与组件。
- 前端修改提交前必须运行 `pnpm run lint` 和 `pnpm run build`。

## 测试与验收

Python 自动化测试位于 `tests/test_*.py`，使用 pytest。每次修改至少执行相关测试；涉及共享核心、API、存储或执行器时应运行 `python -m pytest -q`。前端除 lint/build 外，还需手动验证受影响的登录、工作台、监控、历史或资源流程。目标浏览器为 Chrome 80+、Firefox 78+；Windows 7 重点验收 Chrome 109 与 Firefox 115 ESR，不支持 IE。

## Pull Request 要求

PR 应说明变更目的、主要实现和兼容性影响，列出已执行的验证命令，并关联对应 Issue 或 OpenSpec 变更。界面修改需附截图。不得提交 `.env`、凭据、`.venv/`、`node_modules/`、`frontend/dist/`、`data/`、`reports/`、本地归档或其他生成文件。

## Agent 专用说明

仓库存在 `.codegraph/` 时，在使用 grep/find 或大范围读取文件前，先运行 `codegraph explore "<问题或符号>"` 定位代码。始终保留工作区中与当前任务无关的修改，并按小闭环精确暂存、验证和提交。
