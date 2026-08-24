# 贡献指南

感谢你参与改进 Waterfall AI。提交贡献即表示你有权提供相关内容，并同意按项目的 [Apache License 2.0](./LICENSE) 授权该贡献。

## 开始之前

本项目处于 Beta 阶段，当前只支持可信环境、单租户、Linux Docker 部署。设计和实现不应暗示已经支持公网不可信用户、多租户隔离、Windows 原生生产部署或高可用集群。

请先阅读：

- [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)
- [SECURITY.md](./SECURITY.md)
- [ARCHITECTURE.md](./ARCHITECTURE.md)
- [GOVERNANCE.md](./GOVERNANCE.md)

修复明确缺陷可以直接提交 Pull Request。新增较大功能、改变数据模型、权限语义、API 契约、部署边界或依赖前，请先创建 Issue 说明问题和方案，避免重复或方向冲突。

安全漏洞不得通过公开 Issue 或 Pull Request 首次披露，请按 [SECURITY.md](./SECURITY.md) 私密报告。

## 本地开发

建议在 Linux 环境中开发。克隆仓库后，在仓库根目录运行基础 Python 测试：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_*.py' -v
```

涉及 Playwright 模板或浏览器执行时，还需要兼容版本的 Node.js，并在 `project-template` 中安装锁定依赖：

```bash
npm ci --prefix project-template
```

不要在测试中连接真实生产系统或使用真实凭据。集成测试应使用隔离数据库、一次性账号和可恢复数据。

## 代码约定

- 保持 `app.py` 为兼容装配入口，新业务规则放入对应领域模块。
- 遵守 `ARCHITECTURE.md` 的依赖方向；领域代码不得依赖 Flask 请求上下文或反向导入 `app.py`。
- 对请求控制的路径、归档文件名、HTML、Markdown 和命令参数执行明确校验。
- 授权逻辑必须默认拒绝；新增路由时同步添加权限映射和覆盖测试。
- 秘密只能通过运行时注入，不能写入 Prompt、日志、测试脚本、示例配置或快照。
- 后台任务必须携带明确的项目和作者上下文，并持久化可恢复状态。
- 保持现有 HTTP、SSE、JSON 和文件契约，除非变更已经在 Issue 中达成共识并提供迁移说明。
- 用户可见文本、运维文档和错误信息应优先使用清晰中文。

## 测试要求

每个修复至少应包含能够在修复前失败、修复后通过的回归测试。按改动范围检查：

- 领域规则：单元测试；
- HTTP 路由：状态码、JSON 契约、认证和授权测试；
- 前端行为：现有 Node VM 或静态契约测试；
- 文件、归档和路径：穿越、符号链接、大小与编码边界；
- 数据库迁移：新旧 schema 的幂等测试；
- 安全相关：负向用例和默认拒绝行为。

提交前至少运行受影响测试；条件允许时运行完整测试集。Pull Request 中应记录实际执行的命令和结果，未执行的检查也要说明原因。

## 依赖和许可证

新增或升级依赖时：

1. 解释为什么现有能力不能满足需求。
2. 固定可重复安装的版本并更新锁文件。
3. 核对维护状态、安全风险和许可证兼容性。
4. 更新 [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)。
5. 若依赖进入 Docker 镜像或离线包，确保发行物包含对应许可证、版权声明和 SBOM。

不要提交来源不明的代码、提示词、图片、字体、音视频、数据集或二进制文件。

## 提交和 Pull Request

提交应保持单一目的，说明“为什么改变”，而不只是罗列文件。Pull Request 需要：

- 描述问题、方案和影响范围；
- 关联相关 Issue（如适用）；
- 说明兼容性、数据迁移和回滚方式；
- 列出测试命令与结果；
- 说明安全、隐私和许可证影响；
- 更新用户文档或 `CHANGELOG.md`（如适用）；
- 不包含凭据、真实业务数据、个人信息和内部基础设施信息。

维护者可能要求拆分过大的变更。合并方式由维护者根据历史可读性决定。

## 评审标准

维护者主要检查：

- 是否符合 Beta 支持边界和路线图；
- 是否保持架构边界与可维护性；
- 是否有充分测试和失败回滚；
- 是否默认安全并避免扩大数据暴露；
- 是否保留兼容性或提供清晰迁移路径；
- 是否完成依赖与许可证审查。

贡献行为同时受 [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md) 约束。
