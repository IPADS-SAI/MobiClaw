# scripts 使用说明

本目录包含 Seneschal 常用运维与验证脚本。

## 脚本列表

- `bootstrap_one_click.sh`：一键启动本地依赖与服务
- `stop_all.sh`：一键停止本地服务
- `run_gateway_demo.sh`：快速验证 Gateway 提交链路
- `run_gateway_feishu_demo.sh`：模拟飞书 webhook 入口调用
- `run_llm_route_timing_test.sh`：运行路由时延测试
- `test_mobi_cli_real.sh`：真实 Mobi CLI 链路测试
- `weknora_export.sh`：导出 WeKnora 配置
- `weknora_import.sh`：导入 WeKnora 配置（支持可选重置租户 API key）

---

## weknora_import.sh 详细用法

### 作用

将 `configs/` 下导出的租户、用户、模型、知识库、Agent 配置导入到 WeKnora 的 Postgres。

并支持在导入前可选生成新的租户 API key，再自动写回导入数据和 `.env`。

### 基本命令

```bash
cd /workspace/Seneschal
ENV_FILE=./.env CONFIG_DIR=./configs bash ./scripts/weknora_import.sh
```

### 常用环境变量

- `ENV_FILE`：用于 `${VAR}` 渲染的环境文件，默认 `./.env`
- `CONFIG_DIR`：配置目录，默认 `./configs`
- `CONTAINER_NAME`：Postgres 容器名，默认 `WeKnora-postgres-dev`
- `DB_NAME`：数据库名，默认 `WeKnora`
- `DB_USER`：数据库用户，默认 `postgres`

覆盖具体文件路径：

- `MODELS_JSON`
- `KB_JSON`
- `AGENTS_JSON`
- `TENANTS_JSON`
- `USERS_JSON`

### 可选：导入前生成并导入新 API key

#### 1) 仅导入，不生成新 key（默认）

```bash
ENV_FILE=./.env CONFIG_DIR=./configs bash ./scripts/weknora_import.sh
```

#### 2) 导入前生成新 key，并自动写回 `.env`（推荐）

```bash
TENANT_AES_KEY='your-tenant-aes-key' \
GENERATE_API_KEY=1 \
UPDATE_ENV_FILE_KEY=1 \
ENV_FILE=./.env \
CONFIG_DIR=./configs \
bash ./scripts/weknora_import.sh
```

#### 3) 指定租户 ID 生成 key

```bash
TENANT_AES_KEY='your-tenant-aes-key' \
GENERATE_API_KEY=1 \
WEKNORA_TENANT_ID=10000 \
UPDATE_ENV_FILE_KEY=1 \
ENV_FILE=./.env \
CONFIG_DIR=./configs \
bash ./scripts/weknora_import.sh
```

### key 轮换相关参数

- `GENERATE_API_KEY`
  - `0`：不生成（默认）
  - `1`：生成新 key 后导入
- `TENANT_AES_KEY`
  - 当 `GENERATE_API_KEY=1` 时必需
  - 必须与当前 WeKnora 实例运行时使用的 `TENANT_AES_KEY` 一致
- `WEKNORA_TENANT_ID`
  - 可选
  - 不填时默认取 `TENANTS_JSON` 第一个租户的 `id`
- `UPDATE_ENV_FILE_KEY`
  - `1`：把新 key 写回 `ENV_FILE`（默认）
  - `0`：不回写（仅导入到数据库）

### 结果检查

成功后脚本会输出：

- 导入统计：`tenants/users/models/knowledge_bases/custom_agents`
- 若启用了 key 生成：`Generated WEKNORA_API_KEY=...`

建议随后执行：

```bash
curl --location 'http://localhost:8080/api/v1/knowledge-bases' \
  --header 'Content-Type: application/json' \
  --header 'X-API-Key: <WEKNORA_API_KEY>'
```

若返回 `success=true`，说明认证和导入均成功。
