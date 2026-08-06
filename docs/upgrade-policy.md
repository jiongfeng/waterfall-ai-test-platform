# 安装与升级策略

本文定义候选公开发行物的安装来源识别和升级边界。任何未来的首个公开 Beta 只
支持全新安装，不提供内部版或旧版原地升级，也不提供公开的历史数据迁移工具。

## 当前策略

| 场景 | 决策 | 说明 |
| --- | --- | --- |
| Release 安装目标不存在 | 允许 | 空目录也应先确认并显式移除；必须同时使用全新的数据库、工作区和 OpenCode config/data/cache/state 四卷 |
| 旧内部安装包或增量包 | 拒绝 | 已退役，不属于公开兼容性契约 |
| 缺少有效 Release 元数据的非空目录 | 拒绝 | 来源未知，默认关闭 |
| 未在升级矩阵精确列出的来源 | 拒绝 | 不允许猜测兼容性或跳过中间版本 |
| 当前候选或首个公开 Beta 的原地升级 | 拒绝 | `deploy/upgrade-matrix.json` 的路径列表为空 |

“拒绝”表示不得通过删除版本标记、重新标记镜像、复用旧卷或加跳过参数来继续。
如果需要保留旧环境数据，应把 `mysql_data`、`platform_projects`、
`platform_workspaces`（含所有 `.git`）、OpenCode config/data/cache/state 四卷和
`config.json`/`.env`/Release 元数据保存在同一个加密恢复点，然后等待该来源对应的
独立导出/导入方案。不要让新版本直接迁移旧数据库。

## 只读预检

安装器必须在复制文件、创建目录、启动容器或创建卷之前调用：

```bash
python3 deploy/preflight-install.py \
  --target /srv/playwright-platform-next \
  --release-metadata ./RELEASE-METADATA.json
```

只读预检可把“不存在或为空”判定为新目标；但 Release 安装器为避免目录竞态只接受
不存在的目标，所以空目录也必须先人工确认并显式移除。Compose project 没有任何
既有容器、卷或网络时返回 `0`。拒绝策略时返回 `10`，输入或命令行无效时返回 `2`。脚本只读取目标、
Release 元数据、矩阵和 Docker labels，不会修复、删除、改名或写入任何旧文件或
Docker 资源。Docker 状态无法查询也按未知来源拒绝。

源码检出没有 `RELEASE-METADATA.json`。仅用于全新源码部署时可以省略
`--release-metadata`；非空目标永远不能省略。正式在线/离线包必须提供并传入自身
Release 元数据。

预检默认检查 Compose project `playwright-test-platform`。安装器若使用其他合法
project 名，必须通过 `--compose-project` 传入实际值；不能为绕过旧资源检测而临时
更名。发现任何无法与允许路径精确关联的容器、卷或网络时都应停止。

## 身份与矩阵

来源与目标身份都由以下三项共同决定：

- `version`：用户可读版本；
- `revision`：小写、40 位 Git commit SHA；
- `deploymentContractVersion`：正整数部署契约版本。

`RELEASE-METADATA.json` 中缺少任一项即视为未知来源。未来增加路径时，
`deploy/upgrade-matrix.json` 必须同时固定来源和目标的三项身份，例如：

```json
{
  "from": {
    "version": "<source-version>",
    "revision": "<40-character-source-revision>",
    "deployment_contract_version": 1
  },
  "to": {
    "version": "<target-version>",
    "revision": "<40-character-target-revision>",
    "deployment_contract_version": 2
  },
  "mode": "in_place",
  "decision": "allow"
}
```

同版本不同 revision、同 revision 不同契约版本，或仅匹配 tag/镜像名称均不得
放行。允许路径还必须配套数据库、卷、配置、备份、恢复和回滚测试，不能只修改
JSON 矩阵。

## 历史环境处理

旧内部包及其私有站点检查已经退出公开发行物。公共预检不得依赖特定数据库产品、
模型供应商、固定端口、内部服务地址、SSH 文件名或 Docker data-root。站点特有
检查只能留在该站点的私有部署层。

在重新录入旧环境中的配置前，应轮换所有曾进入配置、日志、脚本、数据库备份、
镜像、导出包或 Git 历史的秘密。新旧环境应使用不同 Compose project、数据库、
卷和入口端口，验证完成前保持旧环境冻结而不是原地覆盖。

卷权限修复不等于版本升级。已有 OpenCode 卷需要修复时，先停止 OpenCode，并对
config/data/cache/state 四卷创建加密、访问受限的一致快照；记录卷 identity 及
非秘密 sentinel 的 SHA-256，再使用 `platform-compose repair-opencode-volumes`。
完成镜像、capability、project 与四卷解析后，修复器才会停止 OpenCode；从开始改变
卷状态起，任何修复或重建失败都会让服务保持停止，此时应排查或恢复快照。预检
阶段失败不会改变原服务状态。不得删除卷或把 `repair-state` 当作四卷迁移工具；
后者只保留原有 state-only 兼容语义。

## 未来开放某条升级路径的门槛

1. 来源和目标三项身份均固定，Release Notes 与矩阵一致。
2. 在来源版本固定夹具上完成只读分析和一致性备份。
3. 在隔离副本验证 schema、配置、MySQL、工作区和 Git revision。
4. 重复执行不会破坏数据，未知来源仍被拒绝。
5. 登录、授权、项目读取、Chromium 与 Agent Provider 冒烟分别验证。
6. 完成升级失败、切流前回滚和恢复演练。

在全部门槛通过前，保持 `upgrade_paths` 为空。
