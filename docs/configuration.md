# 配置参考

本文说明公开 Beta 的配置结构、秘密注入方式和迁移边界。安全假设与部署要求分别
见[安全模型](./security-model.md)和[部署指南](./deployment.md)。

## 配置文件

应用从 JSON 文件读取非秘密配置：

- 默认路径是应用目录下的 `config.json`；
- 推荐通过 `PLATFORM_CONFIG_PATH` 指向部署者维护的只读文件；
- `config.json`、`.env`、secret 文件和私有 runbook 不得提交到平台源码仓库
  或项目工作区 Git；
- URL 只接受 HTTP(S)，并拒绝 `user:password@host` 形式的内嵌凭据；
- 公开示例只使用保留域名、通用容器主机名和通用路径。

本地无数据库示例见 [`config.example.json`](../config.example.json)，Docker
Compose 示例见 [`deploy/config.example.json`](../deploy/config.example.json)。
两者都不包含可用秘密。

## Docker 示例结构

下面是 Compose 示例的关键字段摘要：

```json
{
  "project_workspace_root": "/data/playwright-workspaces",
  "opencode_server_url": "http://opencode:4096",
  "opencode_username": "opencode",
  "opencode_password_env": "OPENCODE_SERVER_PASSWORD",
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
        "username_env": "TARGET_SYSTEM_USERNAME",
        "password_env": "TARGET_SYSTEM_PASSWORD"
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
| `PLATFORM_CONFIG_PATH` | 推荐 | 指向仓库外或只读挂载的 JSON |
| `PLATFORM_AUTH_ENABLED` | 可选 | 覆盖 `auth.enabled` |
| `PLATFORM_SESSION_SECRET` | 启用认证时必需 | 会话签名；至少 32 字符高熵值 |
| `PLATFORM_ADMIN_PASSWORD` | 初始化管理员时必需 | 独立强密码 |
| `PLATFORM_DB_PASSWORD` | 启用 MySQL 时必需 | 平台数据库账号密码 |
| `MYSQL_ROOT_PASSWORD` | Compose 首次初始化必需 | 只由 MySQL 容器使用 |
| `OPENCODE_SERVER_PASSWORD` | OpenCode 启用认证时必需 | 模型服务密码 |
| `PLATFORM_COOKIE_SECURE` | HTTPS 部署必需 | 为会话 Cookie 设置 `Secure` |
| `PLATFORM_BIND_ADDRESS` | Compose 可选 | 默认只绑定 `127.0.0.1` |
| `PLATFORM_PORT` | Compose 可选 | 默认 `5000` |
| `PLATFORM_ALLOW_TEST_EXECUTION` | 显式选择 | 允许执行可信 Playwright 代码 |
| `PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION` | 自定义部署显式选择 | 允许执行可信准备 shell |

公开 Compose 清单把测试执行默认设为 `false`，并始终禁用宿主准备脚本。执行
开关不是隔离措施；开启后，代码仍与平台共享容器操作系统边界。

### 被测系统凭据

示例使用：

```dotenv
TARGET_SYSTEM_USERNAME=
TARGET_SYSTEM_PASSWORD=
```

项目的 `username_env` 和 `password_env` 必须引用 `TARGET_` 前缀变量。自定义
引用还需要由部署者显式注入容器。模型和配置 API只能接触变量名，不能接触实际
值。

页面清单中的账号也只记录引用，例如：

```json
{
  "accounts": [
    {
      "username_ref": "TARGET_ADMIN_USERNAME",
      "password_ref": "TARGET_ADMIN_PASSWORD",
      "purpose": "管理员登录"
    }
  ]
}
```

`username_ref` 和 `password_ref` 同样必须使用 `TARGET_` 前缀。新的明文
`username`、`password` 或 `sample_data` 凭据字段会被拒绝；旧记录只返回迁移
标记，不会把值发送到 API、Prompt 或执行进程。

不要把秘密放入命令行、Compose YAML、JSON、镜像层、Prompt、测试脚本、日志、
截图、Issue 或诊断包。`.env` 只适合本机快速开始；正式部署应使用 secret store
或权限受限的 secret 文件。

## 配置优先级

1. `PLATFORM_CONFIG_PATH` 决定 JSON 路径。
2. `PLATFORM_AUTH_ENABLED` 覆盖 `auth.enabled`。
3. 会话、初始管理员和数据库密码分别从对应 `PLATFORM_*` 变量读取。
4. OpenCode 密码从 `opencode_password_env` 指定的变量读取。
5. 被测系统凭据只在执行时从 `TARGET_*` 引用解析。
6. 项目级 OpenCode、被测系统、数据库基线和计划生成配置覆盖全局默认值。

旧明文字段只作为迁移输入识别，不应继续使用。发现明文后先轮换，再迁移并清理
Git 历史、数据库、备份、导出包和旧镜像。

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

## OpenCode

全局或项目级配置只记录：

- `opencode_server_url`；
- `opencode_username`，它是服务标识，不是密码；
- `opencode_password_env`，默认 `OPENCODE_SERVER_PASSWORD`。

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

## 准备脚本

准备脚本是任意 shell 代码，默认禁止执行，只允许可信管理员配置。它们不是安全
沙箱，不得运行来自上传文件、模型输出或不可信用户的未评审代码。

秘密通过 `environment_refs` 映射：

```json
{
  "environment_refs": {
    "TEST_USERNAME": "TARGET_SYSTEM_USERNAME",
    "TEST_PASSWORD": "TARGET_SYSTEM_PASSWORD"
  }
}
```

左侧是子进程变量名，右侧必须是 `TARGET_` 前缀的平台变量名。不能覆盖
`PATH`、`HOME` 等受保护基础变量。旧 `environment_overrides` 不再接受、
返回或执行。schema 升级会幂等清除当前准备脚本表中的实际值，只保留变量名和
迁移标记；部署者仍需重新绑定引用，并轮换、清理升级前备份中的旧值。

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
- 密码通过 `PLATFORM_DB_PASSWORD` 注入；
- 推荐预先创建数据库并设 `create_database: false`；
- 数据库名和表前缀必须符合解析器标识符规则；
- 备份必须同时覆盖 MySQL、项目工作区和每个工作区的 `.git`。

## 旧版迁移

升级前：

1. 停止新的生成和执行任务。
2. 一致备份 MySQL 与工作区。
3. 轮换所有历史明文秘密。
4. 将密码、目标账号和 OpenCode 密码迁移到环境引用。
5. 将准备脚本环境改成 `environment_refs`，确认 schema 已清除当前表中的旧值，
   再轮换并清理升级前备份。
6. 删除数据库基线旧命令字段，只保留 file 配置。
7. 在隔离副本验证 schema、登录、授权、项目读取和执行默认关闭。

## 启动前检查

- JSON 可以严格解析，没有重复项目 key；
- 示例保留域名已替换为授权测试目标；
- 认证秘密和管理员密码相互独立并满足强度要求；
- 所有凭据都由运行时注入，仓库和 Git 历史通过秘密扫描；
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
- [支持矩阵](./support-matrix.md)
