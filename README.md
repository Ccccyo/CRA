# CRA Agent Workbench

一个以 CRA Excel 为交接边界的企业信用评审 Agent。它将建案、企业主体核验、财务证据、RAG 检索、风险意见和 Excel 回填连成可审计工作流。

## 产品边界

- Agent 负责重复的读取、检索、校验和起草。
- 企业主体合并、财务证据、风险意见和最终额度由人签署。
- SAP 往年财务只从上传的 CRA Excel 读取，不伪装成已接入生产 SAP。
- 制度、案件证据、历史案件与公开资料分库检索，回答必须带出处。
- Bad Case 按“流出层级 × 后果影响”记录，被防线截停的错误不会被当成事故。

## 启动

1. 复制 `.env.example` 为 `.env.local`，至少配置 `DATABASE_URL`、`AUTH_SECRET`、`CRA_API_SHARED_SECRET`和 `DEEPSEEK_API_KEY`。
2. 初次使用 PostgreSQL 时，建表并初始化权限：

   ```bash
   npm run db:push
   npm run rbac:init
   ```

   数据库、账号、角色和 RAG 配置详见 `docs/08-database-auth-and-rag.md`。

3. 首次运行：

   ```bash
   python3.11 -m venv services/cra_api/.venv
   services/cra_api/.venv/bin/python -m pip install -r services/cra_api/requirements.txt
   pnpm install
   ```

4. 启动前后端：

   ```bash
   ./scripts/dev-all.sh
   ```

5. 打开 `http://localhost:3100/zh/sign-up` 注册账号，分配 CRA 角色后访问 `http://localhost:3100/zh/workspace`。API 状态页为 `http://127.0.0.1:8001/health`。

也可以直接运行 `pnpm dev`；它会同时启动前端和 CRA Agent API。不要只运行 `pnpm dev:web`，否则页面可以打开，但案件列表和 Excel 上传会因 API 未启动而显示 `Failed to fetch`。

如果本机没有安装 pnpm，且依赖已经安装，可以直接运行 `npm run dev`，效果相同。

## 验证

```bash
pnpm test:api
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/next build
```

可在“新建案件”页下载演示 Excel，完整测试预检、重复 BP 分支、建案和 Agent 处理。

## 文档

- `PRODUCT.md`：产品真值与边界
- `DESIGN.md`：视觉和交互规范
- `docs/01-product-flow.md`：用户与业务流程
- `docs/02-architecture-and-data.md`：架构、数据与集成边界
- `docs/03-agent-and-rag.md`：Agent、RAG、人工卡点与 Bad Case
- `docs/04-development-plan.md`：开发阶段与验收门槛
- `docs/05-test-and-user-review.md`：测试与用户验收方法
- `docs/06-final-review.md`：最终实测结论
- `docs/08-database-auth-and-rag.md`：数据库、账号、权限、案件和 RAG 配置
