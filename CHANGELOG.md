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
- Agent/OpenCode 模型 delta 和高频工具 log 均按 4 KiB 或 500 ms 聚合，单批最多
  16 KiB；完整任务日志改为工作区文件事实源，两类输出的 MySQL 日志快照
  均按 30 秒或新增 1 MiB 检查点及任务终态刷新；
- OpenCode 事件流不可用时的同步 fallback 改为有界等待，等待期间仍可响应
  跨进程取消；本地回归的取消终态延迟为 539 ms；
- Agent 事件 SSE 会先快速追平每页 200 条的历史积压，浏览器仅环形保留最近 3,000 条
  普通日志事件并合并同一帧渲染，同时完整保留关键状态、错误和产物事件；
- 计划文件经本次任务变更、稳定性和 cases 校验后即可结束 OpenCode 等待；恢复任务可
  复用内容一致的旧索引及已拆分计划，冲突文件不会被覆盖；
- 英文项目的状态、弹窗、Agent 事件与提示词改为项目语言输出；英文单用例计划使用
  英文文件名和 Markdown 标题，计划生成进度统一按模块计数，并在写入前拒绝大小写或
  Unicode 规范等价的文件名碰撞；
- 上述流式持久化修复未新增配置项或数据库迁移；原有任务日志下载、REST/SSE 结构、
  `event_id`/`after_id` 和历史逐条事件保持兼容；
- 开源 Beta 的支持边界明确为可信环境、单租户、Linux/amd64 Docker；
- 发布流水线采用 Minisign 签名的候选、审批与最终 Release manifest，配合受保护审批、
  草稿附件复验、公开包匿名拉取和唯一的 Draft-to-Public 门禁；
- 镜像身份校验同时支持 classic 与 Docker 29 containerd image store，并以受审阅的
  manifest/config 摘要、真实 archive blob 和运行中容器描述符交叉验证。

### Security

- 建立 GitHub Private Vulnerability Reporting 优先的漏洞报告流程；
- OpenCode 工具日志在写入工作区文件、SSE 和数据库事件前脱敏当前目标系统密码；
- 明确秘密、模型数据、诊断包、运行产物和第三方发行物的处理要求。
