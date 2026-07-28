# 变更日志

本文件记录面向使用者和部署者的重要变化。格式参考 Keep a Changelog；项目达到稳定版前，所有发布均视为 Beta。

## Unreleased

### Added

- 分层后端模块和按功能拆分的前端结构；
- Agent 失败分析、诊断包、单项重试和人工验证流程；
- 项目级测试准备脚本、绑定和执行记录；
- Linux Docker 项目脚手架与离线部署加固；
- Apache-2.0 许可证、社区治理、安全报告和支持文档；
- GitHub Issue、Pull Request 模板和 Dependabot 配置。

### Changed

- 测试准备动作统一到项目级准备脚本；数据库基线仅保留文件复制模式，旧命令
  模式和批处理辅助文件不再受支持；
- 生成、执行和重试流程使用更明确的项目、作者和资产版本上下文；
- 开源 Beta 的支持边界明确为可信环境、单租户、Linux Docker。

### Security

- 建立 GitHub Private Vulnerability Reporting 优先的漏洞报告流程；
- 明确秘密、模型数据、诊断包、运行产物和第三方发行物的处理要求。
