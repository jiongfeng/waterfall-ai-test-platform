# Playwright 测试平台开源部署阻断缺陷修复方案

> 状态：已实施（历史修复方案）
>
> 编制日期：2026-08-05
>
> 适用基线：公开候选提交 `f108d893e949e18b61396b6853ebff42f9c9b1a3`
>
> 缺陷来源：`.codex-audits/2026-08-04-tencent-deployment-validation/report.md`
>
> 范围：DPL-001、DPL-002、DPL-003、DPL-004

> 当前部署与升级契约以 `docs/deployment.md`、`docs/upgrade-policy.md` 和实际发布脚本为准；本文保留为修复决策记录。

## 1. 总体结论

四个阻断项应分成两类处理：

| 缺陷 | 问题类型 | 修复决定 |
| --- | --- | --- |
| DPL-001 | 发布制品缺失 | 从准备发布的 tag 只构建一次，产出当前版本镜像、在线安装包和完整离线包；旧包不得复用 |
| DPL-002 | 历史升级契约失效 | 退役旧增量升级链；首个公开版本只承诺全新安装，历史私有部署使用独立导出/导入迁移工具 |
| DPL-003 | OpenCode 运行时目录遗漏 | 镜像预创建 state 目录，Compose 增加持久化 named volume，并提供旧卷权限修复工具 |
| DPL-004 | 宿主与容器 UID/GID 不兼容 | 保留原始 `config.json` 的 `0600`；包装脚本在 `0700` 私有目录生成持久只读副本，再通过 file-backed Compose secret 挂载 |

不建议继续修补 `2ed3518 → 26b532a → … → b3d4743 → ee61528` 的旧升级链。它不只是漏了两个文件，而是混合了 CentOS、Compose v1、宿主 MySQL、host network、固定端口以及 DM8/GLM 私有依赖，与当前公开 Compose 已经不是同一个部署契约。

本方案完成后，平台可以达到“当前版本可从零安装”的状态，但不自动代表“已经可以公开”。部署报告中的高等级问题仍需单独关闭或明确降级范围，特别是 Provider 未配置、执行子进程继承平台秘密以及数据库 baseline 契约不一致。

## 2. 目标发布契约

### 2.1 唯一版本身份

每个公开 Release 必须把以下身份绑定为同一版本：

```text
受保护的 Git tag
  = 完整 Git commit SHA
  = OCI image revision
  = GHCR image manifest digest
  = RELEASE-METADATA.json
  = 安装包清单、SBOM、签名和校验和
```

部署时以镜像 digest 为最终身份，tag 只用于人类阅读。发布流水线不得对同一 tag 重建第二份镜像，也不得覆盖已经发布的同名制品。

### 2.2 发行资产

同一个发布流水线生成两种安装包，首期只承诺 `linux/amd64`：

1. 在线安装包：包含 Compose、模板、包装脚本、预检、安装和验收脚本；平台镜像引用 GHCR 不可变 digest。
2. 离线安装包：包含在线包全部内容，并包含平台镜像和固定 digest 的 MySQL 运行镜像；安装时不得访问网络。

推荐目录结构：

```text
playwright-test-platform-${VERSION}-linux-amd64/
├── RELEASE-METADATA.json
├── RELEASE-NOTES.md
├── SOURCE-SNAPSHOT.md
├── SHA256SUMS
├── deploy/
│   ├── compose.yaml
│   ├── config.example.json
│   └── env.example
├── bin/
│   ├── platform-compose
│   ├── preflight
│   ├── install
│   ├── verify
│   ├── logs
│   └── stop
├── images/
│   ├── platform-linux-amd64.tar.zst
│   └── mysql-linux-amd64.tar.zst
├── sbom/
│   ├── platform.spdx.json
│   └── platform.cdx.json
├── licenses/
└── provenance/
```

备份和恢复脚本只有在 DPL-014 的一致性恢复契约完成并经过恢复演练后才能加入正式资产，不能把旧的 `backup.sh` 当作已支持能力。

MySQL 镜像进入离线包前必须完成再分发与许可证复核。若尚未完成，制品只能称为“平台镜像离线包”，不能宣称是可断网安装的完整包。

### 2.3 支持边界

首个修复版明确声明：

- 支持从空白主机安装当前公开 Release；
- 支持同一公开发布线中、Release Notes 明确列出的升级来源；
- 不支持从 2026 年 7—8 月的内部离线包直接原地升级；
- 检测到旧 Compose 项目名、旧镜像 revision 或旧目录结构时，公开安装器必须停止并给出迁移说明；
- rootless Docker、user namespace remap、SELinux 主机以及非 x86_64 架构在实际通过矩阵测试前标为“未验证”，不能默认为支持。

## 3. DPL-001：建立当前版本完整制品

### 3.1 根因

当前 `.github/workflows/release.yml` 只上传 Compose、示例配置、许可证和它们的校验和，没有构建、启动或导出镜像。旧 `deploy-packages/` 又被根仓库忽略，其最后一个完整包停在 `2ed3518`，不能证明或安装当前候选 `f108d89`。

### 3.2 实施项

1. 在公开仓库中增加可审计的制品生成代码：
   - `scripts/release/build-bundle.sh`；
   - `scripts/release/verify-bundle.sh`；
   - 确定性归档规则，固定文件排序、时间戳、UID/GID 和压缩参数；
   - `RELEASE-METADATA.json` schema。
2. 将运行 Compose 与源码构建 Compose 分开：
   - `deploy/compose.yaml` 只消费明确的 `PLATFORM_IMAGE` 与 `MYSQL_IMAGE`；
   - `deploy/compose.build.yaml` 仅用于从源码构建；
   - Release 和离线安装始终使用 `--build=false`、`--pull=never` 或等价的 fail-closed 设置。
3. 固定 MySQL 镜像 digest；Dockerfile 构建时强制写入：
   - `VERSION`；
   - `REVISION=${GITHUB_SHA}`；
   - `SOURCE_URL`。
4. 重写 Release 流水线，按顺序执行：
   - 校验 tag 格式、版本号和 commit 来源；
   - 执行 Python、JavaScript、shell、Compose 和 secret 扫描；
   - 使用 Buildx 从 tag 只构建一次并推送到 GHCR digest；
   - 后续扫描、打包、验收全部消费该 digest，不允许重建；
   - 生成 SPDX、CycloneDX、许可证集合、漏洞报告和 provenance；
   - 对镜像 digest 与安装包做 Sigstore/cosign 或等价签名；
   - 生成在线包与离线包；
   - 在干净主机上反向安装离线包；
   - 先创建 Draft Release，重新下载并验签后才发布。
5. 删除当前“Release 已存在就成功退出”的行为。已有同名 Release、tag 或资产时应失败，禁止静默留下半成品。
6. 所有第三方 GitHub Actions 在公开前固定到经过审查的完整 commit SHA。

Docker 官方支持通过 Buildx/GitHub Actions 为镜像生成 SBOM 和 provenance；attestation 需要与推送到 registry 的最终镜像绑定，不能仅依赖本地 `load` 后的镜像。参考：

- [Docker：在 GitHub Actions 中添加 SBOM 与 provenance](https://docs.docker.com/build/ci/github-actions/attestations/)
- [Docker：Build attestations](https://docs.docker.com/build/metadata/attestations/)

### 3.3 可复现性分级

P0 先保证“发布真实性”：

- 镜像 digest 唯一；
- 基础镜像与 MySQL 镜像固定 digest；
- tag、commit、OCI label、安装包 metadata 一致；
- 安装和测试消费同一镜像字节。

P1 再实现 bit-for-bit 重建：

- Python 使用带哈希锁文件或经过校验的 wheelhouse；
- OpenCode CLI/plugin 使用独立 npm lock，不再 `--no-package-lock` 在线解析；
- APT 使用固定 snapshot/包版本或经过校验的 deb 集合；
- 使用 `SOURCE_DATE_EPOCH` 和确定性归档。

P1 完成前，文档只能宣称“按 digest 可验证”，不能宣称“任意时间均可逐字节复现构建”。

### 3.4 完成标准

- 在一个仅安装受支持 Docker/Compose 的全新 Ubuntu x86_64 主机上，离线包断网安装成功；
- 实际运行镜像 revision 等于发布 tag 的完整 commit SHA；
- 三个服务健康，登录、数据库读取、Chromium、OpenCode、重启恢复均通过；
- GHCR digest、Release metadata、包内镜像、SBOM、许可证、签名和校验和互相一致；
- 旧 `2ed3518`、`b3d4743`、`ee61528` 包不出现在公开安装入口和 GitHub Release 中。

## 4. DPL-002：退役旧升级链，建立新迁移边界

### 4.1 公开版处理

首个公开 Beta 不修补历史增量包，采用 fresh-install-only：

1. `deploy-packages/` 继续只留在私有归档，不复制到公开仓库；
2. `README`、部署指南和 Release Notes 明确写出不支持的内部 revision；
3. 新安装器在做任何写入前识别现有部署；检测到旧包即退出；
4. 通用预检只验证能力和公开契约，不检查 DM8 SSH 文件、GLM 固定地址、固定端口或 Docker data-root；
5. 私有站点特有检查只能放在私有部署仓库的可选 hook 中；
6. 从首个公开 Release 开始建立 `upgrade-matrix.json`，只有矩阵列出的来源才能升级，未知来源默认拒绝。

这可以关闭公开版的 DPL-002：不是伪造一条无法支持的升级路径，而是停止承诺从未公开过的内部版本原地升级。

### 4.2 历史私有部署迁移

若现有内部环境需要保留数据，应在私有部署仓库中另做一次性迁移工具，不进入公开包。迁移采用蓝绿方式：

```text
旧环境（只读冻结）
  → legacy-analyze/export
  → 带版本、schema 指纹和 SHA-256 的迁移包
  → current-import（全新空库、全新卷）
  → 对账与验收
  → 仅切换反向代理/端口
```

`legacy-analyze/export` 必须：

- 精确识别来源 image ID、OCI revision 和 schema；
- 未知 revision 立即拒绝；
- 在暂停新任务和写入后生成一致性 MySQL 快照；
- 导出项目工作区及 `.git` 历史；
- 生成记录数、文件摘要和 Git revision manifest；
- 不修改旧数据库、旧 Compose、旧卷或旧镜像。

`current-import` 必须：

- 只允许写入空白的新环境；
- 使用显式、幂等、带版本的迁移步骤；
- 对记录数、关联、文件摘要、Git revision 和孤儿数据逐类对账；
- 默认不迁移 `.env`、SSH 私钥、OpenCode `auth.json`、平台 session/admin secret、DM8/GLM 私有配置；
- 准备脚本和绑定先进入人工审查队列，不能自动把内网命令带入新环境；
- 不得像旧 `ee61528` 包一样静默删除 Agent 历史。

### 4.3 回滚原则

- 新旧环境使用不同 Compose 项目、数据库、卷和端口；
- 旧环境冻结后保持原样，新 schema 不在旧数据库上执行；
- 切换前失败时直接停止新环境，旧环境无需逆迁移；
- 切换后若新环境已经产生新数据，不能宣称无损回到旧版；需明确选择丢弃新数据或再做受控迁移；
- 回滚单位是数据库、工作区、Git、配置的同一恢复点，不是只替换镜像。

### 4.4 完成标准

公开 fresh install 的门槛：

- 安装过程不读取或依赖任何旧增量包；
- 公开代码、包和文档不含 DM8 私钥名、GLM 固定端点或私有拓扑；
- 旧部署被明确识别并 fail closed；
- 支持边界与 Release Notes 一致。

只有公开宣称“支持历史迁移”时，迁移工具才成为该 Release 的阻断门槛；届时每个受支持 revision 都必须使用固定夹具完成导出、导入、重复执行、完整对账、切换和回滚演练。

## 5. DPL-003：补齐 OpenCode state 持久化契约

### 5.1 实施项

1. 在 Dockerfile 中显式设置：

   ```text
   HOME=/home/pwuser
   XDG_STATE_HOME=/home/pwuser/.local/state
   ```

2. 构建镜像时创建 `/home/pwuser/.local/state/opencode`，所有者为 `pwuser:pwuser`，模式为 `0700`；目录中保留一个可安全复制到空卷的初始化文件。
3. 在 `opencode` 服务增加：

   ```yaml
   volumes:
     - opencode_state:/home/pwuser/.local/state
   ```

   并在顶层声明 `opencode_state`。
4. `entrypoint.sh` 在启动 OpenCode 前执行：
   - 创建 `${XDG_STATE_HOME}/opencode`；
   - 验证目录属于预期运行用户；
   - 验证可写；
   - 失败时输出路径和权限诊断，但不输出 secret。
5. 安装/升级脚本提供一次性旧卷修复：
   - 从最终镜像动态读取 `pwuser` UID/GID；
   - helper 只挂载 `opencode_state`；
   - 禁止网络，只授予修复所有权所需的最小 capability；
   - 修复完成即退出，正式 OpenCode 服务继续使用非 root、`cap_drop: ALL`、只读根文件系统。

默认不使用 tmpfs。OpenCode state 包含模型选择、偏好和历史状态，正式部署需要跨容器重建与宿主重启保留。tmpfs 只可用于明确标记的临时评估 override。

Docker 对空 named volume 会默认复制镜像挂载点的现有内容；本实现仍需用真实容器测试确认目录元数据和运行用户可写，不能只做 YAML 静态校验。参考 [Docker volumes](https://docs.docker.com/engine/storage/volumes/)。

### 5.2 当前腾讯云部署的切换

1. 保留现有 `compose.validation.override.yaml` 和已经初始化的 state 卷，直到正式镜像回归通过；
2. 正式 Compose 使用相同 Compose 项目与 `opencode_state` 卷名，避免创建第二份状态；
3. 切换前记录卷名、所有者、文件摘要和容器镜像 digest；
4. 先备份 state 卷，再重建 OpenCode；
5. 验证 state 中 sentinel/模型选择在 `restart`、`force-recreate` 和宿主重启后仍存在；
6. 验收后才删除现场 override；任何步骤都不得执行 `down -v`。

### 5.3 完成标准

- 空卷首次安装时 OpenCode healthy；
- `ReadonlyRootfs=true` 且容器为非 root；
- OpenCode 可写 state，platform 服务不能挂载或访问该卷；
- restart、force-recreate、宿主重启后 state 保留；
- 旧的 root-owned state 卷能通过受限 helper 无损修复；
- 根文件系统其他路径仍不可写。

## 6. DPL-004：消除 config.json 的 UID/GID 偶合

### 6.1 方案选择

原方案准备采用 environment-backed Compose secret，但真实 Compose v2.39.2 容器测试发现，它在 `read_only: true` 服务启动时会被拒绝并要求 `file` source。为保留只读根文件系统，默认方案调整为“私有运行目录中的派生只读文件 + file-backed Compose secret”，并新增唯一受支持的 Compose 包装脚本：

```text
宿主 config.json（0600）
  → platform-compose 读取、校验和规范化
  → .runtime/secrets/platform-config.json
     （父目录0700、文件0444、持久存在且不进入Git）
  → Compose file-backed secret
  → /run/secrets/platform-config.json（容器内只读）
  → 非 root platform 进程读取
```

不采用以下方案作为默认值：

| 方案 | 不采用原因 |
| --- | --- |
| `chmod 644` | 会让宿主其他用户读取含密码的 JSON |
| `0640` 加碰巧一致的 GID | 依赖宿主组、user namespace 和现场 UID/GID |
| POSIX ACL | 可作为 Linux 运维回退，但不是所有文件系统和 rootless 环境都一致 |
| 动态修改容器用户 UID | 会影响 HOME、浏览器、缓存和现有 named volume |
| 直接把原始 `0600` 文件改成 file-backed secret/config | Compose 仍用 bind mount，`uid/gid/mode` 对 file source 被忽略，原始 UID 问题不变 |
| 正式服务以 root entrypoint 复制配置 | 扩大常驻容器权限，不符合当前加固边界 |

Docker Compose 官方说明：secret 来源为 `file` 时底层使用 bind mount，`uid/gid/mode` 会被忽略。因此不能直接挂载宿主 `0600` 原件；派生文件使用 `0444` 让非 root 容器可读，同时依靠宿主 `0700` 父目录阻止其他宿主用户遍历和读取。参考 [Compose service secrets](https://docs.docker.com/reference/compose-file/services/#secrets)。

### 6.2 Compose 与包装脚本

Compose 改为：

```yaml
services:
  platform:
    environment:
      PLATFORM_CONFIG_PATH: /run/secrets/platform-config.json
    secrets:
      - source: platform_config
        target: platform-config.json

secrets:
  platform_config:
    file: ${PLATFORM_CONFIG_SECRET_FILE:?Use deploy/platform-compose}
```

`deploy/platform-compose` 负责：

1. `umask 077`，并明确禁止 `set -x`；
2. 检查 `config.json` 存在、是普通文件、当前调用者可读；
3. 拒绝 group/other 可读写的权限，默认要求宿主模式 `0600`；
4. 校验 JSON 语法、必要字段、占位符和最大尺寸；首版建议上限 64 KiB，实际值在实施时按真实配置样本确认；
5. 创建并验证 `.runtime` 与 `.runtime/secrets` 为当前用户所有且模式 `0700`；任何符号链接、错误所有者或过宽权限都 fail closed；
6. 用 JSON 解析器在同一目录生成临时规范 UTF-8 文件，设置 `0444` 后原子替换 `.runtime/secrets/platform-config.json`；不把 JSON 放进命令参数或环境变量；
7. 导出绝对路径 `PLATFORM_CONFIG_SECRET_FILE` 后调用 `docker compose`；运行副本必须保留，确保 daemon/container/宿主重启后 bind source 仍存在；
8. 为 `up`、`ps`、`logs`、`stop`、`verify`、`apply-config` 提供受控子命令；
9. `apply-config` 显式 force-recreate platform，避免用户误以为修改宿主文件后运行中 secret 会自动变化；
10. 任何错误只输出文件路径和原因，不输出 JSON、密码或完整环境。

运行副本的文件自身可被容器任意非 root UID 读取，但宿主上的其他普通用户无法穿过 `0700` 父目录。Compose 不再需要宿主与容器 UID/GID 相等，也不依赖被 file source 忽略的 `uid/gid/mode` 属性。该机制只作为 Docker Compose v2 部署契约使用，不扩展为 `docker stack deploy`/Swarm 支持。

`entrypoint.sh` 把当前的“文件存在”检查加强为：

- 是普通文件；
- 当前用户可读；
- 当前用户不可写；
- 解析失败时不回显原始内容。

### 6.3 当前腾讯云部署的切换

1. 切换前保留现场 `0640 + 匹配 GID` 方案作为可恢复回退点；
2. 使用新包装脚本生成私有运行副本和 file-backed secret，并重建 platform；
3. 验证容器环境、`docker inspect`、`docker compose config` 和日志均不含完整 JSON；
4. 验证宿主派生文件位于 `0700` 私有目录、模式 `0444`，容器内 secret 可读且不可写；
5. 验证平台登录和项目读取后，把宿主 `config.json` 恢复为 `0600`；
6. 重启宿主并再次验证 secret 挂载和平台启动；
7. 失败时回到已验证的 `0640 + 匹配 GID` override，绝不能临时改成 `0644`。

### 6.4 完成标准

- 宿主配置归属 UID 1000、模式 `0600`，容器 UID 与其不同，平台仍能启动；
- 中文、换行、引号、反斜杠和 `$` 在规范化前后语义一致，宿主与容器内规范 JSON 的 SHA-256 一致；
- 宿主派生副本位于无符号链接的 `0700` 私有目录、模式 `0444`；容器内 secret 可读且不可写；
- 缺失、空文件、非法 JSON、权限过宽和超限配置均在容器启动前失败；
- `docker inspect`、容器环境、Compose 渲染结果和日志中均没有完整 JSON；
- 配置轮换后 platform 使用新值，旧内容不再挂载；
- 宿主重启与容器自动重启后仍可正常读取配置。

## 7. 自动化验证矩阵

### 7.1 必须场景

| 层级 | 验证内容 |
| --- | --- |
| 静态 | shellcheck、Compose config、JSON schema、禁止内部关键字、secret scan、许可证清单 |
| 镜像 | OCI labels、非 root、固定架构、依赖版本、state 目录所有权、镜像内无配置/凭据 |
| 容器契约 | read-only rootfs、capabilities、state 可写与持久、私有派生 config secret 权限、服务隔离 |
| 全新安装 | 断网导入镜像、预检、初始化、三服务 healthy、无现场 override |
| 应用冒烟 | 错误密码 401、正确登录 200、项目读取、MySQL、Chromium、Agent Pipeline v3 |
| OpenCode | HTTP health、版本、state 持久；Provider 未配置时必须显示 `NOT_CONFIGURED`，不能冒充推理就绪 |
| 生命周期 | restart、force-recreate、宿主重启、配置轮换、停止后再启动 |
| 恢复 | 在 DPL-014 完成后验证一致性备份和隔离恢复；完成前不得宣称内置恢复受支持 |

### 7.2 环境范围

第一轮至少验证：

- Ubuntu 24.04 x86_64；
- 现场 Docker Engine 29.1.3、Compose 2.40.3；
- 全新空卷与已有 root-owned state 卷；
- 宿主 UID 1000、容器 UID 不同；
- 在线安装与完全断网离线安装。

在公开文档声称更广泛支持前，再补：

- Ubuntu 22.04；
- Docker Engine 27 与当前发布线所选上限版本；
- 计划声明的最低与最高 Compose v2；
- 如要支持，再增加 rootless、userns-remap、Rocky/RHEL SELinux 和 arm64。

最低 Compose 版本必须由 file-backed secret、只读根文件系统、健康依赖和重启测试共同确定。在完成矩阵前，保守地只声明现场已验证的 Compose 2.40.3，不凭经验下调版本。

## 8. 实施顺序与提交拆分

建议用独立、可回滚的提交或 PR 执行：

1. `fix(deploy): persist and validate OpenCode state`
   - Dockerfile、Compose、entrypoint、旧卷 helper、容器契约测试；
   - 关闭 DPL-003。
2. `fix(deploy): inject platform config through Compose secret`
   - 私有派生文件、file-backed secret、`platform-compose`、配置负向测试、双语文档；
   - 关闭 DPL-004。
3. `test(deploy): add clean-host and lifecycle smoke tests`
   - 构建真实镜像并启动，不再只做 `compose config`；
   - 覆盖 restart、force-recreate、权限差异和日志泄露检查。
4. `build(release): produce immutable online and offline bundles`
   - release scripts、metadata、digest、SBOM、许可证、签名、GHCR、Draft Release；
   - 关闭 DPL-001。
5. `docs(upgrade): retire legacy internal upgrade chain`
   - fresh-install-only、旧版本探测、支持矩阵、Release Notes；
   - 关闭公开范围的 DPL-002。
6. 私有任务：`legacy-export/current-import`
   - 仅在仍有历史内部部署必须迁移时执行；
   - 不阻断 fresh-install-only 的公开 Beta。
7. 在 Private staging 创建新的候选 tag，完成干净 clone、断网安装、腾讯云现场替换和回滚演练；全部通过后才切换仓库可见性。

粗略工作量：DPL-003/004 与契约测试约 2—3 人日；DPL-001 发布流水线与离线反向安装约 3—4 人日；文档、staging 和现场切换约 1—2 人日。历史迁移工具另计，通常至少 3—5 人日并取决于真实旧数据数量和 schema 差异。

## 9. 发布门槛与 No-go 条件

以下任一项存在时，不得发布“可安装”的公开 Release：

- 当前 tag 没有对应的完整、不可变、可验签制品；
- 安装过程仍会联网拉取未固定依赖，或离线包缺少运行所需镜像；
- 仍需现场 override 才能让 OpenCode/platform 启动；
- state 卷在 restart、recreate 或宿主重启后丢失或不可写；
- `config.json` 仍依赖宿主 UID/GID 偶然相等，或要求 `0644`；
- 配置、密码、内部地址出现在镜像、日志、Compose 渲染结果、Release 资产或 provenance 中；
- 安装器会尝试执行旧 DM8/GLM 增量链；
- 未知旧 revision 不会 fail closed；
- tag、commit、OCI revision、digest、SBOM 或 checksum 不一致；
- 全新安装、登录、数据库、Chromium、OpenCode、重启任一关键冒烟失败；
- Critical 漏洞或许可证阻断未处理；High 例外没有负责人、理由和到期日。

如果 Release 宣称 Agent 功能“可用”，还必须配置受支持 Provider 并完成一次最小真实推理；只有 OpenCode HTTP health 不够。若 Provider 未配置，Release 和 UI 必须明确标记“平台可用、Agent 未就绪”。

## 10. 现场回滚方案

修复部署到当前腾讯云服务器时采用以下顺序：

1. 记录当前镜像 digest、Compose 渲染结果、卷清单和配置摘要；
2. 保留现有 override、state 卷以及 `0640 + 匹配 GID` 配置方案；
3. 做 MySQL、项目工作区、Git 和 OpenCode state 的同一恢复点备份；
4. 先在不同 Compose project/端口做候选安装验收；
5. 再替换正式 Compose，保留原卷且不执行 `down -v`；
6. 完成登录、项目读取、Chromium、state、配置权限和宿主重启验证；
7. 观察期内保留上一镜像 digest和旧 Compose 文件；
8. 若 DPL-003 失败，恢复原 override 并重新挂载原 state 卷；
9. 若 DPL-004 失败，恢复 `0640 + 匹配 GID` override；
10. 若发生 schema 或数据变化，按完整恢复点恢复，不能只回退镜像。

发布本身不覆盖已有 tag。候选阶段使用 Draft/Prerelease；失败时废弃该候选版本并创建新版本号，确保已经下载的资产不会与后来重建的同名文件混淆。

## 11. 最终完成定义

四个阻断项同时满足以下条件才算完成：

- DPL-001：当前 tag 有可验证、可断网从零安装的完整制品；
- DPL-002：旧内部升级链从公开入口退役，公开支持边界 fail closed；如承诺历史迁移，则迁移和回滚夹具全部通过；
- DPL-003：OpenCode state 在只读根文件系统下可写、持久且有旧卷修复路径；
- DPL-004：宿主配置保持 `0600`，跨 UID/GID 可读，secret 不进入容器环境、日志或 Compose 输出；
- 干净主机 CI 与当前腾讯云现场均通过相同验收脚本；
- 不再依赖 `compose.validation.override.yaml`；
- 新 Release 在 Private staging 完整演练后再公开。
