<p align="right"><a href="./README.md">English</a></p>

<h1 align="center">Waterfall AI</h1>
<p align="center"><strong>基于 Playwright Test Agents 的开源可视化测试工作台</strong></p>

<p align="center">
  <a href="https://github.com/jiongfeng/waterfall-ai-test-platform/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/jiongfeng/waterfall-ai-test-platform/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/jiongfeng/waterfall-ai-test-platform/releases"><img alt="Release" src="https://img.shields.io/github/v/release/jiongfeng/waterfall-ai-test-platform?include_prereleases"></a>
  <a href="./LICENSE"><img alt="Apache License 2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg"></a>
  <a href="./docs/support-matrix.md"><img alt="Linux amd64" src="https://img.shields.io/badge/platform-Linux%2Famd64-informational"></a>
</p>

<p align="center">
  <a href="#playwright-test-agents-的可视化工作台"><strong>了解工作流</strong></a> ·
  <a href="./docs/deployment.md"><strong>安装已签名 Beta</strong></a> ·
  <a href="./docs/security-model.md"><strong>了解安全边界</strong></a>
</p>

<!-- GitHub 附件地址需要独占一行，README 才会渲染原生视频播放控件。 -->
https://github.com/user-attachments/assets/98697c2f-9b2a-45c5-bd0a-7dd617e7573c

<p align="center">
  <a href="./docs/assets/waterfall-ai-introduction-zh-CN.mp4"><strong>▶ 观看中文版介绍视频（MP4）</strong></a>
</p>

## Playwright Test Agents 的可视化工作台

Playwright Test Agents 主要通过对话内容和文件目录交付结果；Waterfall AI
把 Agent 能力组织为有状态、可操作、可追溯的测试流程：

| Playwright Test Agents 的对话式使用方式 | Waterfall AI |
| --- | --- | --- |
| 进度隐藏在聊天记录中 | 七阶段流程和任务状态清晰可见 |
| 交付物散落在文件目录中 | 可视化管理模块、计划、脚本、测试集和报告 |
| 修改依赖继续对话 | 在页面中直接查看、编辑或重新生成 |
| 中断后需要重新解释上下文 | 保留执行阶段、版本、任务和已有产物 |
| 失败信息散落在对话和文件中 | 集中关联日志、报告、视频、截图和追踪文件 |

> **公开 Beta：**当前已签名预发布版本仅支持可信团队在 Linux/amd64 Docker
> 上进行单租户部署；只支持全新安装，不是敌对代码安全沙箱。

Waterfall AI 是基于 Playwright 构建的独立开源项目，与 Microsoft 或 Playwright
项目不存在隶属、赞助或官方背书关系。

## 主要能力

- **自动推进，人工可控：**既可以由 Agent 一键推进完整流程，也可以在任意阶段查看、编辑、重新生成或人工接管。
- **需求生成测试资产：**从产品需求出发，完成测试模块拆分、测试计划生成、UI 探索、Playwright 脚本生成和测试集组装。
- **真实浏览器验证：**生成的测试会在真实浏览器中执行，只有验证通过的脚本才能进入测试集。
- **失败修复闭环：**保留失败现场，支持重试、智能修复和重新验证，使修复结果能够被再次执行确认。
- **测试资产版本化：**模块、计划和脚本均可查看与修改，并通过本地 Git 记录版本，支持历史追溯和恢复。
- **完整执行证据：**每次测试执行都关联汇总结果、Run ID、日志、测试报告，以及按 Playwright 配置生成的视频、截图和追踪文件。

界面和工作流仍在演进。采用 Beta 前请阅读[路线图](./ROADMAP.md)、
[变更日志](./CHANGELOG.md)和[支持矩阵](./docs/support-matrix.md)。
代码边界和扩展规则见 [ARCHITECTURE.md](./ARCHITECTURE.md)。

## Docker 快速开始（源码检出）

本源码快速开始适合在 Linux/amd64 上进行隔离评估，需要 Docker Engine 与 Compose
v2、Git、Python 3，以及足够容纳镜像、浏览器、数据库、工作区和测试产物的磁盘
空间。所有命令应使用同一个账号执行。

签名 Release 验证、正式团队部署、备份、修复和升级限制见完整的
[部署指南](./docs/deployment.md)。

### 1. 准备配置

在仓库根目录中执行：

```bash
cp deploy/config.example.json config.json
cp .env.example .env
chmod 600 config.json .env
./deploy/platform-compose init-config
```

`init-config` 会填充空白的快速开始秘密，不会打印秘密，并把数据库和 OpenCode
密码同步到 `config.json`。此时两个文件都含秘密，不要提交、粘贴、上传或分享。

### 2. 校验并启动

```bash
./deploy/platform-compose preflight-install
./deploy/platform-compose validate-config
./deploy/platform-compose up --build --detach
./deploy/platform-compose ps
```

首次构建可能需要几分钟。该快速开始只支持全新安装；如果运行目录或 Compose
项目已经存在，`preflight-install` 会拒绝继续。

服务健康后，打开
[http://127.0.0.1:5000](http://127.0.0.1:5000)，使用 `admin` 登录。
密码是本机 `.env` 中的 `PLATFORM_ADMIN_PASSWORD`。

Docker 示例故意把被测地址设为保留域名
`https://test.example.invalid`。运行浏览器自动化前，必须在 `config.json` 中将其
替换为已获授权的测试系统地址。依赖 Agent 功能前，还应配置经批准的模型 Provider，
并完成一次不含凭据的最小推理冒烟测试。

常用命令：

```bash
./deploy/platform-compose verify
./deploy/platform-compose logs --follow platform
./deploy/platform-compose down
```

> **可信执行默认值：**演示部署默认允许执行准备脚本和 Playwright 测试。这些能力
> 会运行代码，并不提供敌对代码安全沙箱。只能用于可信用户、可信仓库和隔离测试
> 环境。公开、共享或存在不可信用户的部署，应在 `.env` 中设置以下值并重建平台
> 服务：

```dotenv
PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION=false
PLATFORM_ALLOW_TEST_EXECUTION=false
```

```bash
./deploy/platform-compose up --detach --force-recreate platform
```

## 配置与秘密

为兼容原有行为，`config.json` 保存 OpenCode 密码、平台数据库密码，以及各被测
系统的登录用户名和密码。该文件不得进入源码仓库，只允许服务账号读取，并且只
能进入加密且访问受限的备份。

| 环境变量 | 用途 |
| --- | --- |
| `PLATFORM_SESSION_SECRET` | 覆盖文件中的 `auth.session_secret` |
| `PLATFORM_ADMIN_PASSWORD` | 覆盖文件中的 `auth.initial_admin_password` |
| `PLATFORM_DB_PASSWORD` | Compose 的 MySQL 账号密码；同一值还要写入 `platform_database.password` |
| `MYSQL_ROOT_PASSWORD` | Compose 初始化 MySQL 的 root 密码 |
| `OPENCODE_SERVER_PASSWORD` | Compose 的 OpenCode 服务密码；同一值还要写入 `opencode_password` |
| `PLATFORM_COOKIE_SECURE` | HTTPS 部署时设为 `true` |
| `PLATFORM_ALLOW_TEST_EXECUTION` | 允许执行 Playwright 代码；演示部署默认开启 |
| `PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION` | 允许执行可信准备 shell；演示部署默认开启 |

“生成 Seed”提供“访问目标系统（不登录）”和“带登录”两种模式。访问 Seed 使用
固定脚本，只访问 `base_url`，不创建模型任务，也不会把登录地址、用户名或密码
写入 Seed；登录 Seed 沿用模型生成流程，会把配置的登录信息提供给模型。两种
模式都会覆盖同一个 `tests/seed/seed.spec.ts`。

平台仍可能把被测系统用户名和密码放入规划/脚本生成 Prompt 或登录 Seed。只能
使用隔离非生产环境中的可撤销、最小权限测试账号，并把模型提供方、工作区 Git、
日志和执行产物都视为该凭据的暴露边界。

Compose 快速开始固定使用 `deploy/config.example.json` 中的数据库名和应用
账号。如需修改任一标识，必须同时更新 `deploy/compose.yaml` 的 MySQL 服务，
以及复制后的根 `config.json` 中的 `platform_database.database` /
`platform_database.user`；只修改一侧会导致平台无法连接数据库。

完整字段和优先级见[配置参考](./docs/configuration.md)。

## 安全边界

受支持的部署假设：

- 只有一个组织和一个信任域；
- 运维人员、平台用户、仓库、生成代码和准备脚本均可信；
- Linux/amd64 Docker 上只运行一个应用实例；
- 被测系统已获授权、隔离、可恢复且不是生产环境；
- 网络只允许访问必要的 MySQL、OpenCode/模型服务和被测系统；
- 平台前方配置 TLS 和外部访问控制。

Playwright 测试和准备脚本会执行代码。它们与平台共享应用容器的操作系统边界，
**不能**作为敌对代码运行沙箱。执行开关和容器限制只能减少误暴露，
不能把容器变成沙箱。不要给不可信用户执行权限，不要挂载 Docker Socket，也
不要连接生产凭据或生产数据。

完整边界见[安全模型](./docs/security-model.md)和
[部署指南](./docs/deployment.md)。

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
诊断包都可能包含 Cookie、表单值、个人信息、页面内容或内部 URL。不存在能够
可靠覆盖所有自由文本和二进制产物的自动清理机制。

- 只允许可信用户访问产物；
- 配置保留期限和安全删除；
- 加密备份，并把 `mysql_data`、`platform_projects`（含 file baseline）、
  `platform_workspaces` 及所有工作区 Git 历史、OpenCode 四个 XDG 卷和
  `config.json`/`.env`/Release 元数据视为同一恢复点；
- 分享前人工复核每个诊断包；
- 不要把原始报告、trace、视频、配置或数据库转储附到公开 Issue。

## 品牌与发布兼容性

项目已由 `playwright-test-platform` 更名为 `waterfall-ai-test-platform`，GitHub 会将
旧仓库地址重定向到新地址。不可变的 `v0.1.0-beta.3` Release 会保留原有
`playwright-test-platform-*` 制品名和
`ghcr.io/jiongfeng/playwright-test-platform` 镜像地址；改名后的新 Release 使用
Waterfall AI 命名。为保障现有部署兼容，`playwright_platform` 数据库、Python 包路径、
容器内部路径和既有 Session Cookie 名称保持不变。

## 仅支持全新安装的升级边界

当前公开 Beta **只支持全新安装**，不支持从内部安装包、旧部署、源码检出版本，
或者缺失/无法识别 Release 元数据的环境原地升级。旧内部增量包已经退役，不属于
公开 Release 资产，也不构成公开兼容性承诺。

- Release 安装目标必须不存在；即使目录为空，也要先人工确认并显式移除，同时使用
  全新的数据库卷和应用卷；
- 不得把本版本直接指向旧数据库、工作区、Compose 项目或卷；
- 只读检查 `deploy/preflight-install.py` 与
  `deploy/upgrade-matrix.json` 会拒绝所有未明确列出的来源；当前矩阵没有任何受支持
  的原地升级路径；
- 不得通过只删除版本标记或给旧镜像重新打标签来绕过拒绝；
- 如需保留历史数据，应把旧环境完整保存为一个加密恢复点，其中同时包含
  `mysql_data`、`platform_projects`、`platform_workspaces` 及所有工作区 Git 历史、
  OpenCode 四个 XDG 卷和 `config.json`/`.env`/Release 元数据。当前 Release 不提供
  公开的旧版导出/导入工具。

Release 安装器会在写入目标前自动执行只读预检。如需在已解包的 Release bundle
中手工核对决策，可运行：

```bash
python3 deploy/preflight-install.py \
  --target /srv/waterfall-ai-next \
  --release-metadata ./RELEASE-METADATA.json
```

退出码 `10` 表示策略拒绝。如果同名 Compose project 已存在容器、卷或网络，
全新安装也会被拒绝。不得绕过拒绝，应改用真正独立的目标和资源集合。

在全新环境中手工重新录入经过批准的配置前，应轮换所有曾进入旧配置、脚本、
日志、归档、数据库备份、镜像或 Git 历史的秘密。只删除最新文件中的秘密不会
清除历史副本。

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
deploy/                 Docker、安装预检、升级策略和健康检查
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
