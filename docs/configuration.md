# 配置参考

本文说明 Waterfall AI 公开 Beta 的配置结构、凭据保存方式和迁移边界。安全假设与部署要求分别
见[安全模型](./security-model.md)和[部署指南](./deployment.md)。

## 配置文件

应用从 JSON 文件读取项目和连接配置，其中可能包含密码：

- 本地开发默认读取应用目录下的 `config.json`，也可通过
  `PLATFORM_CONFIG_PATH` 指定其他路径；
- 源码检出只通过 `./deploy/platform-compose` 操作；经过验证的 Release 安装副本只
  通过 `./bin/platform-compose` 操作。两条路径最终都委托同一部署包装脚本，读取
  模式为 `0600` 的宿主配置，严格校验后暂存为 Compose file-backed secret；
- `config.json`、`.env`、secret 文件和私有 runbook 不得提交到平台源码仓库
  或项目工作区 Git；
- URL 只接受 HTTP(S)，并拒绝 `user:password@host` 形式的内嵌凭据；
- 公开示例只使用保留域名、通用容器主机名和通用路径。

本地无数据库示例见 [`config.example.json`](../config.example.json)，Docker
Compose 示例见 [`deploy/config.example.json`](../deploy/config.example.json)。
两者都不包含可用秘密。

Docker 包装脚本默认读取仓库根目录 `config.json`，也可用
`PLATFORM_CONFIG_FILE` 指定宿主源文件。它将规范化副本写入
`deploy/.runtime/secrets/platform-config.json`：两个私有目录模式为 `0700`，
副本模式为 `0444`，再以只读 secret 挂载到容器。`0444` 只在无法被其他用户遍历
的私有父目录内使用，用于兼容容器非 root UID；宿主源文件仍必须保持 `0600`。

`.runtime` 已被 Git 忽略但仍是含秘密的持久运行数据，不得提交、公开分享或进入
未加密/公开备份。不要直接编辑暂存副本。修改源文件后运行对应入口（不要混用）：

```bash
# 源码检出
./deploy/platform-compose apply-config

# 已安装的 Release bundle
./bin/platform-compose apply-config
```

直接调用 `docker compose` 会缺少包装脚本生成的运行时 secret 路径，属于不支持
的操作方式。

## Docker 示例结构

下面是 Compose 示例的关键字段摘要：

```json
{
  "project_workspace_root": "/data/playwright-workspaces",
  "default_project_language": "en",
  "opencode_server_url": "http://opencode:4096",
  "opencode_username": "opencode",
  "opencode_password": "",
  "auth": {
    "enabled": true,
    "initial_admin_username": "admin"
  },
  "platform_database": {
    "enabled": true,
    "type": "mysql",
    "host": "mysql",
    "port": 3306,
    "user": "playwright",
    "password": "",
    "database": "playwright_platform",
    "create_database": false
  },
  "projects": [
    {
      "key": "default",
      "name": "Default project",
      "playwright_project_root": "/data/playwright-projects/default",
      "specs_dir": "specs",
      "tests_dir": "tests",
      "target_system": {
        "base_url": "https://test.example.invalid",
        "login_url": "/login",
        "username": "",
        "password": ""
      },
      "database_baseline": {
        "enabled": false
      },
      "plan_generation": {
        "default_coverage_profile": "core"
      },
      "is_default": true,
      "status": "active"
    }
  ],
  "default_project_key": "default"
}
```

`example.invalid` 是保留域名。运行自动化前必须改成已获授权、隔离且可恢复的
测试系统地址。

## 运行时环境变量

### 平台和 Compose

| 名称 | 必需条件 | 用途 |
| --- | --- | --- |
| `PLATFORM_CONFIG_FILE` | Docker 可选 | 包装脚本读取的宿主 `0600` JSON；默认根目录 `config.json` |
| `PLATFORM_RUNTIME_DIR` | Docker 可选 | 私有暂存目录；默认 `deploy/.runtime` |
| `PLATFORM_CONFIG_PATH` | 本地开发可选 | 应用读取的 JSON；公开 Compose 固定为容器 secret 路径 |
| `PLATFORM_AUTH_ENABLED` | 可选 | 覆盖 `auth.enabled` |
| `PLATFORM_SESSION_SECRET` | 启用认证时必需 | 会话签名；至少 32 字符高熵值 |
| `PLATFORM_ADMIN_PASSWORD` | 初始化管理员时必需 | 独立强密码 |
| `PLATFORM_DB_PASSWORD` | Compose 启用 MySQL 时必需 | 初始化 MySQL 应用账号；同一值还需写入 `platform_database.password` |
| `MYSQL_ROOT_PASSWORD` | Compose 首次初始化必需 | 只由 MySQL 容器使用 |
| `OPENCODE_SERVER_PASSWORD` | Compose 启用 OpenCode 认证时必需 | OpenCode 服务端密码；同一值还需写入 `opencode_password` |
| `PLATFORM_COOKIE_SECURE` | HTTPS 部署必需 | 为会话 Cookie 设置 `Secure` |
| `PLATFORM_BIND_ADDRESS` | Compose 可选 | 默认只绑定 `127.0.0.1` |
| `PLATFORM_PORT` | Compose 可选 | 默认 `5000` |
| `PLATFORM_ALLOW_TEST_EXECUTION` | 显式选择 | 允许执行可信 Playwright 代码 |
| `PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION` | 自定义部署显式选择 | 允许执行可信准备 shell |

公开 Compose 清单把测试执行默认设为 `false`，并始终禁用宿主准备脚本。执行
开关不是隔离措施；开启后，代码仍与平台共享容器操作系统边界。

### 文件中的连接与被测系统凭据

OpenCode、平台数据库和被测系统凭据按原有配置字段保存：

```json
{
  "opencode_password": "<OpenCode 服务密码>",
  "platform_database": {
    "password": "<平台数据库密码>"
  },
  "projects": [
    {
      "target_system": {
        "username": "demo-user",
        "password": "<专用测试密码>"
      }
    }
  ]
}
```

页面清单沿用 `username` 和可选的 `password_ref` 元数据，例如：

```json
{
  "accounts": [
    {
      "username": "demo-admin",
      "password_ref": "admin-test-account",
      "purpose": "管理员登录"
    }
  ]
}
```

`password_ref` 是页面清单中的业务标识，不会自动解析环境变量。项目设置中的
用户名和密码可能进入计划/脚本生成 Prompt、seed 脚本、工作区 Git 和执行产物。
因此只能使用隔离非生产系统中的可撤销、最小权限测试账号，并将模型服务和所有
相关产物纳入同一敏感数据边界。

不要把秘密放入命令行、Compose YAML、镜像层、公开示例、Issue 或诊断附件。
实际 `config.json` 和 `.env` 必须保持未跟踪、权限受限；正式部署还应加密其备份。

## 配置优先级

1. `PLATFORM_CONFIG_PATH` 决定 JSON 路径。
2. `PLATFORM_AUTH_ENABLED` 覆盖 `auth.enabled`。
3. `PLATFORM_SESSION_SECRET` 和 `PLATFORM_ADMIN_PASSWORD` 分别覆盖
   `auth.session_secret` 与 `auth.initial_admin_password`；未设置时读取文件值。
4. 平台数据库密码从 `platform_database.password` 读取。
5. OpenCode 密码从全局或项目级 `opencode_password` 读取。
6. 被测系统凭据从项目的 `target_system.username` 和
   `target_system.password` 读取。
7. 项目级 OpenCode、被测系统、数据库基线和计划生成配置覆盖全局默认值。

## 项目配置

每个 `projects[]` 项至少包含：

- 唯一的 `key`；
- 可读的 `name`；
- `playwright_project_root`；
- 简单目录名形式的 `specs_dir` 和 `tests_dir`。

项目 key 只允许字母、数字、点、下划线和连字符。`specs_dir` 与 `tests_dir`
不能是绝对路径、`.`、`..` 或包含路径分隔符。

`status` 支持 `active` 和 `disabled`。禁用项目不会删除工作区、Git 历史或
数据库记录。`default_project_key` 必须指向已配置项目；未提供时选择标记为
默认的项目，或回退到第一个项目。

`default_project_language` 支持 `zh-CN` 和 `en`，未配置时默认为 `en`。
配置中也兼容大小写不同的 `zh-cn`，运行时会统一为 `zh-CN`。该值用于默认项目
首次初始化以及新增项目弹窗的默认选项；新增项目可显式选择其他语言。已经保存到
数据库的项目语言不会因为修改该配置或重启平台而被覆盖。

## OpenCode

全局或项目级配置记录：

- `opencode_server_url`；
- `opencode_username`，它是服务标识，不是密码；
- `opencode_password`。

URL 不得内嵌用户名或密码。OpenCode/模型属于外部数据处理边界，发送需求、
页面内容和日志前应完成授权与合规评估。

## 计划生成和超时

`plan_generation.default_coverage_profile` 支持：

| 值 | 用途 |
| --- | --- |
| `core` | 核心正向流程，默认 |
| `standard` | 明确的正向、异常、边界和权限规则 |
| `comprehensive` | 更广的回归与探索场景 |

`opencode_task_timeout_seconds` 和 `script_execution_timeout_seconds` 必须是
正整数。超时不是资源隔离；部署仍应限制 CPU、内存、PID、磁盘、上传和日志。

Agent 流式输出采用固定的安全阈值：模型 delta 和高频工具 log 均按
4 KiB/500 ms 聚合，单批上限 16 KiB；两类输出关联的任务日志数据库快照均按
30 秒或新增 1 MiB 刷新，并在终态强制刷新。OpenCode 工具日志中的当前目标系统
密码会在写入完整日志文件、SSE 和数据库事件前脱敏。这些阈值是当前版本的内部
持久化契约，不新增 JSON 字段或环境变量，也不要求数据库 schema 迁移。升级后原有
项目配置、`test_jobs`/`agent_run_events` 数据以及 REST/SSE 客户端继续兼容；完整日志
文件仍是权威来源，数据库中的 tail 和 size 只作为低频缓存。

## 准备脚本

准备脚本是任意 shell 代码，默认禁止执行，只允许可信管理员配置。它们不是安全
沙箱，不得运行来自上传文件、模型输出或不可信用户的未评审代码。

子进程环境通过 `environment_overrides` 直接配置：

```json
{
  "environment_overrides": {
    "TEST_USERNAME": "demo-user",
    "TEST_PASSWORD": "<专用测试密码>"
  }
}
```

这些值会保存到平台数据库，并在准备脚本执行时加入子进程环境。只允许可信管理
员维护，不要复用平台、数据库、OpenCode、云服务或生产系统凭据。准备脚本和其
输出应按含秘密内容管理。

## 数据库基线：仅 file

`database_baseline` 只支持文件复制模式，默认 `enabled: false`：

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

行为如下：

1. 首次运行且基线不存在时，把 `database_path` 复制到 `baseline_path`。
2. 基线存在时，把基线复制回运行数据库。
3. `lock_path` 作为锁目录，避免同实例并发复制。

旧命令模式已被解析器拒绝，旧批处理辅助文件也不再属于发行物。MySQL 等服务型
数据库如需重置，应使用单独评审的准备流程、最小权限测试账号和可恢复测试数据，
不能把命令字段重新塞回 baseline 配置。

## 数据库配置

公开 Beta 的平台元数据只支持 MySQL：

- 使用只能访问平台数据库的独立账号；
- 密码保存在受保护的 `platform_database.password` 字段；Compose 中还要把同一
  值作为 `PLATFORM_DB_PASSWORD` 提供给 MySQL 容器初始化账号；
- 推荐预先创建数据库并设 `create_database: false`；
- 数据库名和表前缀必须符合解析器标识符规则；
- 备份必须把 `mysql_data`、`platform_projects`（含 file baseline）、
  `platform_workspaces` 及每个工作区的 `.git`、OpenCode config/data/cache/state 四卷
  和 `config.json`/`.env`/Release 元数据作为同一恢复点。

## 旧版配置边界

当前公开 Beta 不支持旧环境原地升级，也不支持把旧数据库或工作区直接挂到新
环境。旧内部安装包及其配置转换逻辑已退役；未知来源必须按
[安装与升级策略](./upgrade-policy.md)拒绝。

如需在全新环境手工重建配置：

1. 先把旧 `mysql_data`、`platform_projects`、`platform_workspaces` 及其 `.git`、
   OpenCode 四个 XDG 卷和 `config.json`/`.env`/Release 元数据保存为同一个加密、
   只读恢复点，但不要让新版本迁移它。
2. 轮换历史明文秘密，不要复制旧 `.env`、SSH 文件、Provider 认证文件或平台会话秘密。
3. 只按当前 `deploy/config.example.json` 重新录入经过批准的字段。
4. 删除 database baseline 的旧命令字段，只保留 file 配置。
5. 准备脚本和环境绑定必须重新人工评审，不能自动带入旧内网命令。
6. 分别验证平台健康和 Provider 认证推理，后者不能由 OpenCode 健康检查替代。

## 启动前检查

- JSON 可以严格解析，没有重复项目 key；
- 示例保留域名已替换为授权测试目标；
- 认证秘密和管理员密码相互独立并满足强度要求；
- 实际配置文件未被 Git 跟踪、权限受限，仓库和待发布历史通过秘密扫描；
- MySQL 账号只能访问平台数据库；
- 工作区属于非 root 容器用户，且未挂载宿主机敏感目录或 Docker Socket；
- 测试和准备执行保持关闭，直到完成显式风险评审；
- 日志、报告、截图、视频、trace 和诊断包按敏感数据管理。

## 相关文档

- [英文 README](../README.md)
- [中文 README](../README.zh-CN.md)
- [架构概览](./architecture.md)
- [安全模型](./security-model.md)
- [部署指南](./deployment.md)
- [安装与升级策略](./upgrade-policy.md)
- [支持矩阵](./support-matrix.md)
