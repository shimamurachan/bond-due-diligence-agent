# 公司债智能尽调 Agent（Google Cloud 版）

这是原 Cloudflare MVP 的 Python 重构版本。它保留“流程编排、材料抽取、证据检索、风险审阅”四个角色，并将扫描件 OCR、文本向量化、混合 RAG 与异步任务纳入同一条证据链。

## 架构

```text
浏览器
  ↓
React + Vite 单页工作台
  ↓ JSON API
Cloud Run / FastAPI
  ├─ Cloud Storage：PDF、DOCX 原件（私有）
  ├─ Cloud SQL PostgreSQL + pgvector：项目、文本、向量、审阅历史、证据引用
  └─ Cloud Tasks：索引与审阅任务
       ↓
  OCR：PaddleOCR PP-StructureV3（容器内）
  Embedding：Vertex AI gemini-embedding-001（1024 维）
  Agents：DeepSeek V4 Pro
```

前端使用 React + Vite 构建并随 FastAPI 镜像部署；它通过 JSON API 展示项目、材料状态、Agent 轨迹、风险证据与项目记忆。Cloud Tasks 使用 OIDC 令牌调用 Cloud Run 的内部任务端点；网页请求只负责创建任务和读取状态。这样 OCR 与模型调用不会因浏览器请求断开而丢失。Cloud SQL 支持 `pgvector`，并为向量检索保留 HNSW 索引。

## 本地运行

1. 安装 Docker Desktop。
2. 在本目录复制 `.env.example` 为 `.env`，填入新的 `DEEPSEEK_API_KEY`，并设置随机 `TASKS_SHARED_SECRET`。执行一次 `gcloud auth application-default login`，供 Docker 调用 Vertex AI。
3. 运行 `docker compose up --build`。
4. 打开 `http://127.0.0.1:8000`。

本地原文件存于未纳入 Git 的 `data/uploads`。Docker 启动 PostgreSQL 16 + pgvector；不需要本机 Python。
首页的“加载脱敏试用材料”会创建一个独立项目和三份虚构 DOCX，用来演示索引、审阅和证据定位；它不会与用户上传的文件或项目记忆混合。

## Google Cloud 部署

默认项目为 `bond-agent-509210`，默认区域为 `asia-east1`。部署前需要：

- 已安装并登录 Google Cloud CLI；
- 已开通计费；
- Cloud SQL PostgreSQL 16 实例，数据库中执行 `CREATE EXTENSION vector;`；
- 两个 Secret Manager Secret：`database-url`、`deepseek-api-key`；
- Cloud Run 服务账号 `bond-agent-runtime@<project>.iam.gserviceaccount.com` 拥有 Cloud SQL Client、Storage Object Admin、Cloud Tasks Enqueuer、Secret Manager Secret Accessor、Vertex AI User 权限。

### 首次部署顺序

1. 登录 CLI 后，在本目录运行 `./infra/setup-gcp.ps1`。它只创建运行所需的私有桶、任务队列、镜像仓库与服务账号，不接触模型密钥。
2. 运行 `./infra/provision-cloud-sql.ps1` 创建 PostgreSQL 16 实例 `bond-agent-db`、数据库 `bond_agent`，并将随机生成的数据库连接 URL 写入 Secret Manager。应用首次启动会创建 `vector`、`pg_trgm` 扩展和业务表。本项目通过 Cloud Run 附加的 Cloud SQL Unix Socket 连接实例；初版使用默认公共 IP 但不配置授权网络，连接仍由 Cloud SQL Connector 加密并以服务账号授权。若有企业网络隔离要求，再升级为私有 IP 与 Direct VPC Egress。实例规格为 2 vCPU / 8 GiB。
3. 在 Secret Manager 创建并填写 `deepseek-api-key`；数据库密钥 `database-url` 已由上一步自动创建。`gemini-embedding-001` 通过 Cloud Run 的运行时服务账号调用，不需要 API Key。
4. 运行：`./infra/deploy.ps1 -CloudSqlInstance "bond-agent-509210:asia-east1:bond-agent-db" -Bucket "bond-agent-509210-materials" -TaskInvokerServiceAccount "bond-agent-task@bond-agent-509210.iam.gserviceaccount.com" -RuntimeServiceAccount "bond-agent-runtime@bond-agent-509210.iam.gserviceaccount.com"`。
5. 数据保留策略当前由应用的项目级删除功能控制；如需自动清理，需先单独评审并配置 Cloud Scheduler，避免误删尽调材料。

部署脚本默认将 Cloud Run 设为私有服务。首次自测可运行 `gcloud run services proxy bond-agent --project bond-agent-509210 --region asia-east1`，再在浏览器打开 `http://127.0.0.1:8080`；不要为了测试而公开上传端点。

## RAG 与证据规则

- 原生 PDF 页面优先用 PyMuPDF 提取；文字层不足时以 200 DPI 渲染并由 PaddleOCR 识别。
- 文本按页和表格切块，保存文件名、页码/段落位置、抽取方式、内容哈希和由 Vertex AI 生成的 1024 维向量。
- 查询走“向量 Top 12 + 中文关键词 Top 12 → RRF 融合 → 检索 Agent 精排”。
- 风险 Agent 的每项结论必须引用当前项目的 chunk ID；服务端会拒绝无效或跨项目引用。
- 每次复审创建新版本，按 `rule_code` 标识新增、变化、未变化和已解除的风险。

## 安全边界

- 不提交 `.env`、API Key、服务账号密钥或材料原件。
- Google Cloud 使用运行时服务账号和 Secret Manager；不把云凭证放入镜像或数据库。
- 项目最后活动 30 天后由 Cloud Scheduler 触发的维护任务清理原文件、向量、风险和审阅记录；用户也可手动删除项目。单个已被审阅引用的文件会受到保护，避免历史风险结论失去证据来源。
- 这是辅助核查工具，不构成发行、法律、合规或投资结论。
