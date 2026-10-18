# 数据库、账号、权限与 RAG 配置

## 1. 当前已接入的存储

| 数据                       | 当前位置                                                      | 如何查看                                                                      |
| -------------------------- | ------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| 账号、密码哈希、会话       | PostgreSQL / Neon                                             | Neon Console 的 `user`、`account`、`session` 表                               |
| 角色与权限                 | PostgreSQL / Neon                                             | `role`、`permission`、`user_role`、`role_permission` 表；或 `/zh/admin/roles` |
| 案件基础数据               | PostgreSQL / Neon 的 `cra_case`                               | Neon Console 或 `npm run db:studio`                                           |
| 工作流、人工任务、审计事件 | PostgreSQL / Neon 的 `cra_workflow_state` 等 CRA 表           | Neon Console 或 `npm run db:studio`                                           |
| 上传与导出文件             | `services/cra_api/runtime/uploads` 和 `exports`               | 本地文件系统                                                                  |
| RAG 原文与可引用分段       | PostgreSQL 的 `cra_knowledge_document`、`cra_knowledge_chunk` | Neon Console；启动时从受控源文件同步                                          |
| RAG 索引                   | 运行时的 BM25，可选本地 Chroma                                | `services/cra_api/src/cra/retrieve`                                           |

Python CRA 服务使用同一个 `DATABASE_URL`。首次安装 `psycopg` 后会创建/使用 CRA 表，并将旧的 `runtime/workbench.db` 一次性迁移到 PostgreSQL；迁移标记保存在 `cra_data_migration`。若 Python 环境缺少 `psycopg`，健康检查会显示 SQLite 临时回退警告。

## 2. 本地环境变量

`.env.local` 已被 `.gitignore` 忽略。不要将真实密钥写入 `.env.example`。

```dotenv
NEXT_PUBLIC_APP_URL="http://localhost:3100"
NEXT_PUBLIC_CRA_API_URL="/api/cra"
CRA_API_INTERNAL_URL="http://127.0.0.1:8001"
CRA_API_SHARED_SECRET="请使用 openssl rand -base64 32 生成"

DATABASE_PROVIDER="postgresql"
DATABASE_URL="postgresql://..."
DB_SINGLETON_ENABLED="true"
DB_MAX_CONNECTIONS="5"

AUTH_URL="http://localhost:3100"
AUTH_SECRET="请使用 openssl rand -base64 32 生成"
INITIAL_ROLE_ENABLED="false"
```

Neon 连接池地址建议保持 `sslmode=require`。Serverless/开发环境先使用小连接池，避免超出 Neon 连接限制。

## 3. 建表与 RBAC 初始化

在项目根目录执行：

```bash
cd /Users/yangby/Desktop/CRA
npm run db:push
npm run rbac:init
```

`db:push` 会创建 Better Auth、RBAC 和 CRA 业务表。也可以执行已生成的版本化迁移：

```bash
npm run db:migrate
```

CRA 增量迁移为 `src/config/db/migrations/0001_big_infant_terrible.sql`。`rbac:init` 会创建 CRA 权限和下列角色：

| 角色                    | 用途                               |
| ----------------------- | ---------------------------------- |
| `credit_analyst`        | 建案、处理案件、读取制度和公开资料 |
| `senior_credit_analyst` | 增加人工签署与历史案件权限         |
| `credit_manager`        | 完整 CRA 管理权限                  |
| `quality_manager`       | 案件、历史证据与 Bad Case 质量审查 |
| `admin` / `super_admin` | 系统管理，同时拥有 CRA 权限        |

数据库变更如需版本化，使用：

```bash
npm run db:generate
npm run db:migrate
```

账号/RBAC 初始 SQL 在 `src/config/db/migrations/0000_nebulous_spirit.sql`，CRA 业务表在 `0001_big_infant_terrible.sql`，RAG 原文字段在 `0002_flat_hemingway.sql`。

## 4. 创建登录账号并分配权限

1. 启动项目：`npm run dev`。
2. 打开 `http://localhost:3100/zh/sign-up`，用姓名、邮箱和密码注册。密码由 Better Auth 哈希后写入 `account.password`，不要手工插入明文密码。
3. 给首个管理员分配角色：

   ```bash
   npm run rbac:assign -- --email=admin@example.com --role=super_admin
   ```

4. 给业务用户分配角色：

   ```bash
   npm run rbac:assign -- --email=analyst@example.com --role=credit_analyst
   npm run rbac:assign -- --email=reviewer@example.com --role=senior_credit_analyst
   ```

5. 管理员登录后也可在 `/zh/admin/users`、`/zh/admin/roles`、`/zh/admin/permissions` 查看与管理。

Demo 环境不发送验证邮件。忘记密码时可在项目终端安全重置，密码只会以 Better Auth 哈希写入 `account.password`：

```bash
npm run auth:set-password -- --email=admin@example.com
```

命令会隐藏密码输入并要求再次确认；如果现有密码已经一致，不会修改数据库。

`INITIAL_ROLE_ENABLED=false` 是安全默认值：新注册用户没有角色，不能访问工作台，需由管理员显式授权。

## 5. 当前请求链的权限模型

```text
浏览器 -> Next.js 会话 -> /api/cra 权限代理 -> FastAPI
```

- `/workspace` 需要 `cra.workspace.access`。
- 读取案件/下载文件需要 `cra.cases.read`。
- 建案、运行 Agent、修改风险意见需要 `cra.cases.write`。
- 解决人工任务和签署需要 `cra.cases.approve`。
- RAG 目录需要 `cra.knowledge.read`；知识库页、案件问答和风险意见生成都在 FastAPI 内按角色过滤。
- 只有 `admin` 和 `super_admin` 可以通过 RAG 页上传、修改和删除知识文档；Next.js 代理要求 `admin.access`，FastAPI 再校验角色。
- FastAPI 除 `/health` 外验证 `CRA_API_SHARED_SECRET`；密钥缺失时会拒绝保护请求，不应将 8001 端口直接暴露到公网。

## 6. CRA PostgreSQL 表与仍保留在文件系统的数据

已实现的业务表：

- `cra_case`：案件主数据。
- `cra_workflow_state`：当前状态机位置。
- `cra_stage_snapshot`：已完成步骤的不可变回看快照；与流程状态在同一事务中提交。启用快照前已完成的旧步骤会显示“历史快照不可用”，不会用当前数据回填。
- `cra_human_task`：人工关卡与处理结果。
- `cra_agent_event`：Agent 和人工操作审计事件。
- `cra_case_note`：财务证据、主体选择、风险草稿和导出元数据。
- `cra_bad_case`：自动防线与质量回流记录。
- `cra_knowledge_document` / `cra_knowledge_chunk`：RAG 文档目录和可引用正文分段。

## 7. 管理员维护 RAG

1. 用 `admin` 或 `super_admin` 账号登录。
2. 打开 `/zh/workspace/knowledge`，点击“上传文档”。
3. 选择公司制度、公开资料或历史案件，上传 `.md`、`.txt` 或带文字层的 `.pdf`（上限 10 MB）。
4. 后端按 Markdown 标题和段落自动分块；PDF 按页保留页码。文档与全部 chunks 在同一数据库事务中写入。
5. 选中文档后点击“编辑当前文档”，修改原文并保存时会删除旧 chunks、重新分块。Agent 检索直接读取数据库，不需重启。

数据库中的原文位于 `cra_knowledge_document.source_content`，分段正文位于 `cra_knowledge_chunk.content`。扫描型 PDF 当前不会在上传请求中自动调用外部 OCR，需先转成带文字层的 PDF 或 UTF-8 文本。

原始 Excel/PDF 和生成的 Excel 二进制文件目前仍放在 `services/cra_api/runtime`。生产部署应把它们迁入 S3/R2 等对象存储，数据库仅保存对象键、版本、SHA-256 与审计元数据。向量索引仍可使用现有 BM25/Chroma；如切换 Neon `pgvector`，必须继续保留 source/section/page 引用。

## 8. 上线前的其他配置

- Agent 起草：配置 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`CHAT_MODEL` 和 `REASONER_MODEL`。
- 扫描件 OCR：安装 PaddleOCR，或配置 `GLM_API_KEY`、`GLM_BASE_URL` 和 `OCR_BACKEND=glm`。
- Demo 账号不要求邮箱验证，注册后直接创建会话。Resend 仅在未来接入密码重置等通知邮件时才需要配置。
- 生产文件存储：在 `/zh/admin/settings/storage` 配置 R2/S3 兼容存储，并将 Python 案件附件层接入同一存储。
- 部署：更换 `NEXT_PUBLIC_APP_URL` 和 `AUTH_URL`，为 Web 和 API 配置 HTTPS，只对外暴露 Web，不直接暴露 FastAPI 8001 端口。
