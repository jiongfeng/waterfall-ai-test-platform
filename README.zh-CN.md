# Playwright 测试平台

[English](./README.md)

Playwright 测试平台是一个自托管工作台，用于管理测试需求、Markdown
测试计划、Playwright 脚本、执行记录，以及 AI 辅助的生成与修复流程。
测试资产保存在带本地 Git 历史的项目工作区中，平台元数据保存在 MySQL。

> **公开 Beta**
>
> 当前版本只支持同一信任域内的团队，在 Linux Docker 上以单租户、单实例方式
> 部署。它不是经过强化的公网 SaaS、多租户隔离系统或安全沙箱。平台入口应位于
> TLS 反向代理和组织访问控制之后。

## 主要能力

- 项目、需求、测试计划、脚本和测试集管理；
- 工作区测试资产的本地 Git 版本；
- 基于 OpenCode 的计划、生成、审查、修复和 Agent 流程；
- Playwright 执行，以及日志、报告、截图、视频和 trace；
- 项目级准备脚本、绑定和执行记录；
- 登录认证、角色、菜单权限和按 HTTP method 授权的 API；
- MySQL 元数据、项目导入导出、诊断和恢复记录。

界面和工作流仍在演进。采用 Beta 前请阅读[路线图](./ROADMAP.md)、
[变更日志](./CHANGELOG.md)和[支持矩阵](./docs/support-matrix.md)。
代码边界和扩展规则见 [ARCHITECTURE.md](./ARCHITECTURE.md)。

## 安全边界

受支持的部署假设：

- 只有一个组织和一个信任域；
- 运维人员、平台用户、仓库、生成代码和准备脚本均可信；
- Linux Docker 上只运行一个应用实例；
- 被测系统已获授权、隔离、可恢复且不是生产环境；
- 网络只允许访问必要的 MySQL、OpenCode/模型服务和被测系统；
- 平台前方配置 TLS 和外部访问控制。

Playwright 测试和准备脚本会执行代码。它们与平台共享应用容器的操作系统边界，
**不能**作为敌对代码运行沙箱。环境变量白名单和默认关闭开关只能减少误暴露，
不能把容器变成沙箱。不要给不可信用户执行权限，不要挂载 Docker Socket，也
不要连接生产凭据或生产数据。

完整边界见[安全模型](./docs/security-model.md)和
[部署指南](./docs/deployment.md)。

## 15 分钟 Docker 快速开始

首次构建需要下载固定摘要的 Playwright 基础镜像，并按仓库锁文件解析固定版本，
网络较慢时可能超过 15 分钟。

公开 Beta 仅发布源代码，不提供预构建容器镜像。Compose 快速开始会从
`deploy/Dockerfile` 在本地构建镜像。任何对该镜像的再分发，都应先为最终制品
生成并审查完整 SBOM 与许可证包。

### 前置条件

- Linux 主机；
- Docker Engine 和 Compose v2 插件；
- Git；
- Python 3，用于下面的本地秘密生成片段；
- 足够容纳镜像、浏览器、MySQL 卷、工作区和测试产物的磁盘空间。

### 1. 准备配置

在仓库根目录中执行：

```bash
cp deploy/config.example.json config.json
cp .env.example .env
chmod 600 config.json .env
```

生成相互独立的快速开始秘密，秘密值不会出现在进程命令参数中：

```bash
python3 - <<'PY'
from pathlib import Path
from secrets import token_urlsafe

path = Path(".env")
secret_names = {
    "PLATFORM_SESSION_SECRET",
    "PLATFORM_ADMIN_PASSWORD",
    "PLATFORM_DB_PASSWORD",
    "OPENCODE_SERVER_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
}
result = []
for line in path.read_text(encoding="utf-8").splitlines():
    name, separator, value = line.partition("=")
    if separator and name in secret_names and not value:
        line = f"{name}={token_urlsafe(36)}"
    result.append(line)
path.write_text("\n".join(result) + "\n", encoding="utf-8")
PY
```

初始管理员用户名是 `admin`，密码是本机 `.env` 中的
`PLATFORM_ADMIN_PASSWORD`。不要提交、粘贴或上传该文件。

### 2. 校验并启动

```bash
docker compose --env-file .env -f deploy/compose.yaml config --quiet
docker compose --env-file .env -f deploy/compose.yaml up --build --detach
docker compose --env-file .env -f deploy/compose.yaml ps
```

三个服务均健康后，打开
[http://127.0.0.1:5000](http://127.0.0.1:5000)，使用 `admin` 登录。

Docker 示例故意把被测地址设为保留域名
`https://test.example.invalid`。运行或生成浏览器自动化前，必须在
`config.json` 中替换为已获授权的测试系统地址。

常用命令：

```bash
docker compose --env-file .env -f deploy/compose.yaml logs --follow platform
docker compose --env-file .env -f deploy/compose.yaml down
```

`down` 会保留命名卷。删除卷也会删除 MySQL 数据和工作区状态，只能在明确重置
时执行。

### 3. 显式开启可信测试执行

测试执行默认关闭。完成被测系统、仓库、脚本、网络、挂载和产物策略检查后，在
`.env` 中设置：

```dotenv
PLATFORM_ALLOW_TEST_EXECUTION=true
```

然后重建平台服务：

```bash
docker compose --env-file .env -f deploy/compose.yaml up --detach --force-recreate platform
```

公开 Compose 清单始终保持
`PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION=false`。准备脚本是任意 shell 代码，
只有可信运维人员提供经过专门加固的自定义部署时才应启用。开启任何执行开关都
代表接受代码执行风险，不代表获得安全沙箱。

## 配置与秘密

`config.json` 只保存结构和环境变量引用。秘密值应由运行时 secret store
或环境注入提供，不能进入 JSON、源码仓库、Prompt、脚本、截图、日志或 Issue。

| 环境变量 | 用途 |
| --- | --- |
| `PLATFORM_SESSION_SECRET` | 会话签名秘密 |
| `PLATFORM_ADMIN_PASSWORD` | 初始管理员密码 |
| `PLATFORM_DB_PASSWORD` | 平台 MySQL 账号密码 |
| `MYSQL_ROOT_PASSWORD` | Compose 初始化 MySQL 的 root 密码 |
| `OPENCODE_SERVER_PASSWORD` | OpenCode 服务密码 |
| `TARGET_SYSTEM_USERNAME` | 示例被测系统用户名引用 |
| `TARGET_SYSTEM_PASSWORD` | 示例被测系统密码引用 |
| `PLATFORM_COOKIE_SECURE` | HTTPS 部署时设为 `true` |
| `PLATFORM_ALLOW_TEST_EXECUTION` | 显式允许执行可信 Playwright 代码 |
| `PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION` | 显式允许可信准备 shell；公开 Compose 中保持禁用 |

被测系统凭据引用必须使用 `TARGET_` 前缀。自定义变量名还必须显式注入平台
容器。模型可以看到引用名称，但不能接收实际值。

Compose 快速开始固定使用 `deploy/config.example.json` 中的数据库名和应用
账号。如需修改任一标识，必须同时更新 `deploy/compose.yaml` 的 MySQL 服务，
以及复制后的根 `config.json` 中的 `platform_database.database` /
`platform_database.user`；只修改一侧会导致平台无法连接数据库。

完整字段和优先级见[配置参考](./docs/configuration.md)。

## 数据库基线：仅支持 file 模式

旧 command 模式以及 `backup.bat`、`restore.bat` 已删除。
`database_baseline` 只接受 `mode: "file"`，用于复制文件型测试数据库：

- `baseline_path` 不存在时，将当前 `database_path` 复制为基线；
- 基线已存在时，在相关测试流程前把基线复制回运行数据库；
- 使用锁目录串行化操作。

示例：

```json
{
  "database_baseline": {
    "enabled": true,
    "mode": "file",
    "database_path": "/data/playwright-projects/default/data/test.db",
    "baseline_path": "/data/playwright-projects/default/.baseline/test.db",
    "lock_path": "/data/playwright-projects/default/.baseline/restore.lock",
    "timeout_seconds": 300
  }
}
```

禁止指向生产数据。MySQL 等服务型数据库不是 command baseline 目标；如确需
自动重置，应使用单独评审的准备流程和最小权限测试凭据。

## 敏感产物

Playwright 报告、截图、视频、trace、浏览器下载、工作区 Git 历史、日志和
诊断包都可能包含 Cookie、表单值、个人信息、页面内容或内部 URL。自动文本
脱敏无法可靠处理二进制产物。

- 只允许可信用户访问产物；
- 配置保留期限和安全删除；
- 加密备份，并把 MySQL 与工作区 Git 视为同一恢复点；
- 分享前人工复核每个诊断包；
- 不要把原始报告、trace、视频、配置或数据库转储附到公开 Issue。

## 从内部版或旧版迁移

1. 将 MySQL 和每个项目工作区（包括 `.git`）备份为同一个可恢复快照。
2. 轮换所有曾进入 `config.json`、脚本、日志、文档、归档或 Git 历史的秘密。
3. 按本文把文件密码改成环境变量引用。
4. 把被测系统明文用户名/密码改成 `TARGET_*` 引用。
5. 把准备脚本的 `environment_overrides` 改成 `environment_refs`。schema
   升级会从当前准备脚本表中清除旧实际值，只保留变量名和迁移标记；重新绑定后
   才能执行。升级前备份仍可能包含旧值。
6. 删除 database baseline 的命令字段，改用 file 模式或经过评审的准备流程，
   不要恢复已删除的批处理脚本。
7. 在隔离副本上完成升级验证后再运行新镜像。

只删除最新文件中的秘密，不会清除 Git 历史、数据库备份、项目导出包或旧镜像。

## 本地开发与 demo workspace

无凭据的 [`examples/demo-workspace`](./examples/demo-workspace) 只包含一个
内存页面 Playwright 测试，不包含 `node_modules`。根目录
`config.example.json` 指向它，Docker 则使用独立的
`deploy/config.example.json` 和持久化工作区卷。

后端测试：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_*.py' -v
```

demo 测试：

```bash
cd examples/demo-workspace
npm ci
npx playwright install chromium
npm test
```

开发测试中不要使用真实凭据或生产被测系统。

## 仓库结构

```text
app.py                  兼容装配入口
test_plan_viewer/       领域、Web、仓储和基础设施模块
static/                 浏览器代码和样式
templates/              Jinja 模板
project-template/       新建项目工作区模板
examples/demo-workspace 无凭据本地示例
deploy/                 Docker 镜像、Compose、入口和健康检查
docs/                   架构、配置、部署和安全文档
tests/                  Python 与 JavaScript 回归测试
```

## 贡献、支持与安全

- 提交 Pull Request 前阅读 [CONTRIBUTING.md](./CONTRIBUTING.md) 和
  [行为准则](./CODE_OF_CONDUCT.md)。
- 可复现且已脱敏的使用问题按 [SUPPORT.md](./SUPPORT.md) 提交。
- 安全漏洞必须按 [SECURITY.md](./SECURITY.md) 私密报告。不要在公开 Issue
  或 Pull Request 中披露凭据或未修复漏洞细节。
- 项目决策遵循 [GOVERNANCE.md](./GOVERNANCE.md)。

平台源码（包括仓库内的项目模板和演示工作区）采用
[Apache License 2.0](./LICENSE)。平台复制模板创建用户工作区时，会把生成工作区
标记为 `private` 和 `UNLICENSED`，由工作区所有者自行选择合适许可证。
