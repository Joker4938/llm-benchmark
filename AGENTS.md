# 仓库贡献指南

## 项目结构与模块划分

- `llm_benchmark.py`：异步单轮压测核心；`run_benchmarks.py`：分阶段并发压测入口。
- `backend/app.py`：Flask/JWT API，同时托管构建后的 Vue 页面；API 预设保存在 `backend/api_configs.json`。
- `frontend/src/`：Vue 2 前端源码，其中页面位于 `views/`，通用组件位于 `components/`，请求封装位于 `api/`，路由位于 `router/`。
- `assets/` 存放提示词等测试资源，`openspec/specs/` 描述功能预期；`webui.py` 为旧版界面，仅供参考。
- `reports/` 中的 JSON、XLSX、CSV 均为运行产物，不得提交。

## 构建、测试与开发命令

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r backend/requirements.txt
python backend/app.py                 # API 与静态页面，端口 8080
python llm_benchmark.py --help        # 查看 CLI 参数
cd frontend && npm install
npm run serve                         # 前端开发服务，端口 8000
npm run lint                          # ESLint 检查
npm run build                         # 输出到 frontend/dist/
docker compose up -d --build          # 容器化启动完整服务
```

停止容器使用 `docker compose down`。验证 Flask 静态页面前，应重新执行前端构建。

## 分支与提交规范

- `master` 为稳定分支，不直接在其上开发；开始工作前基于最新 `master` 创建短生命周期分支。
- 分支名采用小写英文和连字符：`feature/report-export`、`fix/proxy-port`、`docs/contributor-guide`。
- 提交信息保持简短、祈使句式，沿用 `feat:`、`fix:`、`add:`、`update:`、`docs:`、`refactor:` 等前缀，例如：`fix: 修复并发任务状态更新`。
- 每个提交只处理一个主题，不混入格式化、生成文件或无关修改。

## 代码规范

- Python 使用 4 空格缩进并遵循 PEP 8；函数和变量使用 `snake_case`，常量使用 `UPPER_CASE`。公共函数及复杂逻辑需提供简短、准确的 docstring。
- Vue/JavaScript 使用 2 空格缩进、单引号和无分号风格；组件文件使用 PascalCase，如 `ResultsView.vue`；变量、props 和方法使用 camelCase。
- API 字段命名保持与现有接口一致。新增接口需校验输入、返回明确 HTTP 状态码，并避免在日志或响应中暴露 API Key、JWT 密钥等敏感信息。
- 用户界面和提示信息默认使用中文。避免无关重构，保持函数职责单一，并复用现有 API 封装与组件。
- 前端修改提交前必须运行 `npm run lint` 和 `npm run build`。

## 测试与验收

当前未配置 pytest、Jest 等自动化测试框架。每次修改至少执行相关 lint/build，并手动验证受影响的 CLI、API 或 UI 流程。新增 Python 测试使用 `tests/test_*.py`，Vue 测试使用 `*.spec.js`；引入测试框架时同步更新 README 和本文档。

## Pull Request 要求

PR 应说明变更目的、主要实现和兼容性影响，列出已执行的验证命令，并关联对应 Issue 或 OpenSpec 变更。界面修改需附截图。不得提交 `.env`、凭据、`.venv/`、`node_modules/`、`reports/`、本地归档或其他生成文件。

## Agent 专用说明

仓库存在 `.codegraph/` 时，在使用 grep/find 或大范围读取文件前，先运行 `codegraph explore "<问题或符号>"` 定位代码。始终保留工作区中与当前任务无关的修改。
