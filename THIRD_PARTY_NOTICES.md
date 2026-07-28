# 第三方软件声明

本项目按 Apache License 2.0 发布。第三方组件仍分别受其上游许可证约束；项目许可证不会替代或改变这些条款。

本清单依据当前依赖清单和锁文件整理，用于源代码仓库审查。它不是对 Docker 镜像、操作系统包、浏览器和离线交付物的完整法律清单。

仓库内的 `project-template` 与 `examples/demo-workspace` 源码属于本项目的
Apache-2.0 源码发行范围。平台创建用户工作区时才把复制出的根包清单改写为
`private` 和 `UNLICENSED`；这不会改变其中第三方依赖各自的许可证。

## Python 运行时依赖

来源：`requirements.txt`。

| 组件 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| Flask | 3.1.3 | BSD-3-Clause | Web 应用框架 |
| Python-Markdown | 3.10.2 | BSD-3-Clause | Markdown 转换 |
| nh3 | 0.3.6 | MIT | HTML allowlist 清理 |
| PyMySQL | 1.2.0 | MIT | MySQL 客户端 |

这些包的传递依赖由 Python 包管理器解析。构建二进制发行物时，必须从实际安装环境生成完整依赖与许可证清单，而不能只依赖上表。

## Playwright 项目模板依赖

来源：`project-template/package-lock.json`。

| 组件 | 版本 | 许可证 | 说明 |
| --- | --- | --- | --- |
| `@playwright/test` | 1.61.1 | Apache-2.0 | Playwright 测试运行器 |
| `playwright` | 1.61.1 | Apache-2.0 | 浏览器自动化包 |
| `playwright-core` | 1.61.1 | Apache-2.0 | Playwright 核心 |
| `fsevents` | 2.3.2 | MIT | 可选 macOS 文件事件依赖；受支持的 Linux 部署不使用 |

## Docker 构建直接安装的组件

来源：`deploy/Dockerfile`。

| 组件 | 版本 | 许可证 | 说明 |
| --- | --- | --- | --- |
| Gunicorn | 23.0.0 | MIT | 平台 WSGI Server |
| `opencode-ai` | 1.17.18 | MIT | OpenCode Server CLI |
| `@opencode-ai/plugin` | 1.17.18 | MIT | 项目模板的 OpenCode 插件 |

Dockerfile 还基于固定摘要的 Microsoft Playwright
`v1.61.1-noble` 镜像。该基础镜像包含 Ubuntu、Chromium、浏览器运行库、
字体以及其他系统组件，其许可证和版权声明以实际镜像中的上游材料及最终 SBOM
为准。

`deploy/compose.yaml` 引用独立的 MySQL 8.4 镜像；它不是本仓库源码或平台镜像
的一部分，使用和再分发必须单独遵守 MySQL 镜像及其组件的许可条款。

## 二进制制品中的传递组件

当前官方 release 流程只发布源代码和源代码配套材料，不发布 GHCR 或其他预构建
容器镜像。`deploy/Dockerfile` 是供部署者本地构建和审查的构建清单。

如果维护者或下游分发方今后公开发布容器镜像或离线包，制品可能包含：

- 上述 Python、Gunicorn、OpenCode 及其传递依赖；
- Chromium、浏览器运行库和字体；
- Python、Node.js 及 Linux 系统包；
- Git、tini 和其他运行时系统包。

这些组件可能包含 Apache-2.0、MIT、BSD、GPL、LGPL、MPL 和其他许可证。特别是 Chromium 与 Linux 镜像包含大量传递组件，必须以实际镜像扫描结果为准。

任何 Docker 镜像或离线包公开发布前都应：

1. 从最终制品生成 SPDX 或 CycloneDX SBOM；
2. 汇总实际组件的许可证、版权和 NOTICE 文件；
3. 保留上游要求随分发提供的完整许可证文本；
4. 对源代码提供、修改声明、商标和再分发条件做单独复核；
5. 确认制品不含凭据、内部地址、客户数据和仅限内部使用的软件；
6. 将 SBOM、许可证包和校验和与对应制品一起发布。

## 更新规则

新增、升级、替换或移除依赖时，应在同一个 Pull Request 中：

- 更新依赖清单和锁文件；
- 更新本文件；
- 记录许可证兼容性审查；
- 重新生成发行物 SBOM；
- 确认新增素材、提示词、代码或二进制具有可分发来源。

如果本文件与上游组件附带的许可证文本不一致，以上游原始许可证文本为准，并请提交 Issue 或 Pull Request 修正清单。
