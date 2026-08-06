# 变更日志

本文件记录面向使用者和部署者的重要变化。格式参考 Keep a Changelog；项目达到稳定版前，所有发布均视为 Beta。

## Unreleased

### Added

- 分层后端模块和按功能拆分的前端结构；
- Agent 失败分析、诊断包、单项重试和人工验证流程；
- 项目级测试准备脚本、绑定和执行记录；
- Linux/amd64 Docker 项目脚手架、源码/发行 Compose 分离与离线部署加固；
- Apache-2.0 许可证、社区治理、安全报告和支持文档；
- GitHub Issue、Pull Request 模板和 Dependabot 配置。

### Changed

- 产品名改为 Waterfall AI，仓库名改为 `waterfall-ai-test-platform`，副标题为
  “Agent-driven test automation platform”；数据库、Python 模块、容器内部路径和
  Session Cookie 等运行时兼容标识保持不变；
- 后续 Release 制品、镜像和 Compose 默认项目使用 Waterfall AI 命名；不可变的
  `v0.1.0-beta.3` Release 保留改名前的制品与镜像名称；
- 测试准备动作统一到项目级准备脚本；数据库基线仅保留文件复制模式，旧命令
  模式和批处理辅助文件不再受支持；
- 生成、执行和重试流程使用更明确的项目、作者和资产版本上下文；
- 开源 Beta 的支持边界明确为可信环境、单租户、Linux/amd64 Docker；
- 发布流水线采用 Minisign 签名的候选、审批与最终 Release manifest，配合受保护审批、
  草稿附件复验、公开包匿名拉取和唯一的 Draft-to-Public 门禁；
- 镜像身份校验同时支持 classic 与 Docker 29 containerd image store，并以受审阅的
  manifest/config 摘要、真实 archive blob 和运行中容器描述符交叉验证。

### Security

- 建立 GitHub Private Vulnerability Reporting 优先的漏洞报告流程；
- 明确秘密、模型数据、诊断包、运行产物和第三方发行物的处理要求。
