# 需求到模块与测试计划生成方案

> 状态：已实施（历史设计方案）
>
> 当前系统边界和模块职责以 `ARCHITECTURE.md` 与 `docs/architecture.md` 为准；本文保留为需求解析与计划生成能力的设计记录。

## 1. 背景

当前测试平台已经具备以下主链路：

```text
测试计划 -> 生成测试脚本 -> 执行脚本 / 修复脚本 -> 测试集批量执行
```

测试计划保存在 Playwright 工作区的 `specs/<模块名>/*.md`，测试脚本保存在 `tests/<模块名>/*.spec.ts`，平台数据库只保存资产索引、版本元数据、任务日志和执行记录。

本方案要新增的是主链路之前的可选上游：

```text
需求文档 -> 模块候选 -> planner prompt -> 生成测试计划
```

新增能力不替代现有“新增计划”“生成脚本”“执行脚本”“修复脚本”流程，而是帮助用户从需求文档更快进入现有测试计划链路。

## 2. 目标

- 新增“需求”页面，先支持上传 Markdown 需求文件。
- 使用 OpenCode 解析需求，生成模块候选、测试点和每个模块的 planner prompt 草稿。
- 支持用户人工确认、修改、删除模块候选。
- 对用户选中的模块，复用现有 planner 生成测试计划能力。
- 引入页面 inventory 概念，用作需求解析和 prompt 生成的上下文。
- 保持现有测试计划、测试脚本、测试集、执行和修复流程不变。

## 3. 非目标

- 不在第一阶段直接生成测试脚本。
- 不让需求解析任务直接操作页面或修改被测系统数据。
- 不强制所有测试计划都必须来源于需求。
- 不改变 `specs`、`tests` 目录结构。
- 不重做现有资产版本、Git commit、执行记录和测试集模型。
- 不把完整需求正文、测试计划正文、脚本源码长期重复保存在 MySQL 中。

## 4. 总体原则

### 4.1 需求页是可选上游

现有用户仍然可以直接进入“测试计划”页面，使用“新增计划”生成计划；也可以直接编辑计划、生成脚本、执行脚本。新增需求页只是多一个入口。

### 4.2 planner 仍负责正式计划生成

需求解析只生成候选模块和 prompt 草稿。正式测试计划仍然调用现有 `playwright-test-planner` agent，由 planner 通过 `playwright-test` MCP 登录系统、探索页面并保存计划。

### 4.3 inventory 是平台概念，不是 MCP 固定产出

`playwright-test` MCP 提供浏览器操作和页面观测能力，例如 `browser_snapshot`、点击、输入、导航等。inventory 是平台基于人工录入、文档整理或 MCP 探索结果沉淀出的“被测系统页面清单/能力地图”。

### 4.4 inventory 是提示和约束，不是最终真相

inventory 可以帮助需求匹配页面、账号、控件和写库风险，但正式生成计划时 planner 仍需要进入页面复核，避免页面变化、权限变化或数据状态变化导致计划失真。

## 5. 推荐用户流程

### 5.1 需求上传与解析

```text
需求页
-> 上传 .md 需求文件
-> 保存需求资产元数据
-> OpenCode 解析需求
-> 生成模块候选列表
```

模块候选包含：

- 模块名
- 业务目标
- 关联需求段落
- 推荐页面或菜单
- 推荐账号或角色
- 关键测试点
- 写库风险
- 是否需要数据库基线
- 生成测试计划的 prompt 草稿
- 不确定点

### 5.2 人工确认模块候选

用户在需求页确认模块候选：

- 修改模块名
- 修改计划名
- 合并或拆分候选模块
- 删除暂不生成的模块
- 编辑 planner prompt 草稿
- 选择单个或多个模块生成测试计划

### 5.3 调用现有 planner 生成计划

用户确认后，平台对每个选中的模块调用现有计划生成链路：

```text
需求候选模块
-> planner prompt
-> /api/plan-generation-stream
-> playwright-test-planner
-> specs/<模块名>/<计划名>.md
-> sync_plan_asset
-> test_asset_revisions
-> Git commit
```

生成完成后，计划会像普通测试计划一样出现在“测试计划”页面，后续脚本生成和执行完全走现有流程。

## 6. inventory 设计

### 6.1 inventory 用途

inventory 用来回答“真实系统里有什么页面、入口、账号、控件和风险”。它和需求文档一起作为输入，提升模块识别和 prompt 生成质量。

需求负责回答：

```text
业务要测什么
```

inventory 负责回答：

```text
系统中真实存在什么页面、谁能访问、怎么进入、哪些操作有风险
```

### 6.2 inventory 来源

第一阶段可以先支持以下来源：

- 人工维护的系统说明文档，例如 `test-plan-viewer/被测系统与测试平台使用说明.md`。
- 用户在页面上手工录入或编辑。

后续阶段再增加：

- 使用 raw `playwright-test` MCP 扫描菜单和页面。
- 使用 planner 或专门的 scanner agent 采集页面 snapshot 后整理。
- 从已有测试计划和脚本中抽取页面、控件、账号和数据样例。

### 6.3 inventory 建议字段

```json
{
  "page_name": "清收数据审核",
  "url": "/recovery-audit.html",
  "menu_path": ["清收管理", "清收数据审核"],
  "roles": ["adminsh", "admin", "sadmin"],
  "accounts": [
    {
      "username": "adminsh",
      "password_ref": "default_admin_password",
      "purpose": "贷款数据审核"
    }
  ],
  "stable_selectors": [
    "#searchKeyword",
    "#searchAuditStatus",
    "#startDate",
    "#endDate"
  ],
  "actions": ["查询", "重置", "查看", "审核", "驳回", "通过"],
  "read_only_actions": ["查询", "重置", "查看", "导出"],
  "write_actions": ["审核通过", "驳回", "修改并通过"],
  "write_risk": true,
  "baseline_required": true,
  "sample_data": [
    {
      "name": "待审核记录",
      "value": "GY202301001"
    }
  ],
  "notes": "审核动作会写库，生成脚本和执行脚本必须走数据库基线。",
  "source": "manual",
  "confidence": 0.8,
  "last_scanned_at": "2026-07-01T00:00:00+08:00"
}
```

### 6.4 什么时候使用 raw playwright-test MCP

raw `playwright-test` MCP 适合窄任务，不建议直接替代 planner：

- 扫描系统菜单，生成或更新页面地图。
- 验证某个需求候选模块是否存在对应页面。
- 采集页面 snapshot、稳定控件、角色菜单差异。
- 批量更新 inventory 缓存。

正式测试计划生成仍然走 `playwright-test-planner`，因为 planner 负责测试策略、场景拆分、预期结果和计划落盘。

## 7. agent 分工

### 7.1 requirement analyst

新增需求解析 agent，建议名称：

```text
requirement-analyst
```

职责：

- 读取需求 Markdown。
- 读取 inventory 摘要。
- 读取已有模块和计划摘要，避免重复生成。
- 输出结构化模块候选 JSON。
- 不操作浏览器。
- 不写入 `specs` 或 `tests`。

输出示例：

```json
{
  "modules": [
    {
      "module_name": "清收数据审核",
      "plan_name": "清收数据审核",
      "business_goal": "验证审核人员可以查看、通过和驳回待审核清收记录。",
      "requirement_refs": ["## 清收审核", "### 驳回原因"],
      "matched_inventory": {
        "page_name": "清收数据审核",
        "url": "/recovery-audit.html",
        "roles": ["adminsh"]
      },
      "test_points": [
        "审核员可进入清收数据审核页面",
        "可按审核状态筛选待审核记录",
        "驳回时必须填写原因",
        "通过或驳回动作需要数据库基线"
      ],
      "write_risk": true,
      "baseline_required": true,
      "confidence": 0.86,
      "open_questions": [],
      "planner_prompt": "@playwright-test-planner\n..."
    }
  ]
}
```

### 7.2 playwright-test-planner

继续使用现有 planner agent。

职责：

- 登录被测系统。
- 根据需求、inventory 和用户确认的 prompt 聚焦探索页面。
- 复核导航路径、账号权限、关键控件和写库风险。
- 调用 `planner_save_plan` 保存 Markdown 测试计划。

### 7.3 playwright-test-generator 和 healer

保持现状，不因需求页改变职责。

## 8. 数据模型建议

### 8.1 requirements

保存需求资产元数据。

```sql
CREATE TABLE requirements (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  project_id BIGINT NOT NULL,
  requirement_uid VARCHAR(64) NOT NULL,
  title VARCHAR(255) NOT NULL,
  filename VARCHAR(255) NOT NULL,
  file_path TEXT NOT NULL,
  content_sha256 CHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  source_type VARCHAR(32) NOT NULL,
  created_by VARCHAR(128) NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uk_requirement_uid (project_id, requirement_uid)
);
```

建议需求原文保存在 Playwright 工作区，例如：

```text
requirements/<requirement_uid>/<filename>.md
```

MySQL 只保存路径、hash、标题、状态。

### 8.2 requirement_modules

保存需求解析出的模块候选。

```sql
CREATE TABLE requirement_modules (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  project_id BIGINT NOT NULL,
  requirement_id BIGINT NOT NULL,
  module_uid VARCHAR(64) NOT NULL,
  module_name VARCHAR(255) NOT NULL,
  plan_name VARCHAR(255) NOT NULL,
  status VARCHAR(32) NOT NULL,
  confidence DECIMAL(5, 4) NULL,
  business_goal TEXT NULL,
  test_points_json LONGTEXT NULL,
  matched_inventory_json LONGTEXT NULL,
  baseline_required TINYINT(1) NOT NULL DEFAULT 0,
  write_risk TINYINT(1) NOT NULL DEFAULT 0,
  planner_prompt LONGTEXT NULL,
  source_job_id VARCHAR(128) NULL,
  generated_plan_asset_id BIGINT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uk_requirement_module_uid (project_id, module_uid)
);
```

### 8.3 page_inventory

保存页面 inventory。

```sql
CREATE TABLE page_inventory (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  project_id BIGINT NOT NULL,
  inventory_uid VARCHAR(64) NOT NULL,
  page_name VARCHAR(255) NOT NULL,
  url VARCHAR(512) NULL,
  menu_path_json LONGTEXT NULL,
  roles_json LONGTEXT NULL,
  accounts_json LONGTEXT NULL,
  stable_selectors_json LONGTEXT NULL,
  actions_json LONGTEXT NULL,
  read_only_actions_json LONGTEXT NULL,
  write_actions_json LONGTEXT NULL,
  sample_data_json LONGTEXT NULL,
  write_risk TINYINT(1) NOT NULL DEFAULT 0,
  baseline_required TINYINT(1) NOT NULL DEFAULT 0,
  notes TEXT NULL,
  source VARCHAR(32) NOT NULL,
  confidence DECIMAL(5, 4) NULL,
  snapshot_hash CHAR(64) NULL,
  last_scanned_at DATETIME NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uk_page_inventory_uid (project_id, inventory_uid)
);
```

### 8.4 计划来源关系

不要强制改造 `test_assets`。建议先在 `requirement_modules.generated_plan_asset_id` 上建立弱关联。后续如需多对多追溯，再加关系表：

```sql
CREATE TABLE requirement_plan_links (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  project_id BIGINT NOT NULL,
  requirement_id BIGINT NOT NULL,
  requirement_module_id BIGINT NOT NULL,
  plan_asset_id BIGINT NOT NULL,
  source_job_id VARCHAR(128) NULL,
  created_at DATETIME NOT NULL
);
```

## 9. API 建议

### 9.1 需求文件

```text
GET  /api/requirements
POST /api/requirements/upload
GET  /api/requirements/<requirement_uid>
GET  /api/requirements/<requirement_uid>/download
DELETE /api/requirements/<requirement_uid>
```

### 9.2 需求解析

```text
POST /api/requirements/<requirement_uid>/analysis-stream
GET  /api/requirements/<requirement_uid>/modules
PUT  /api/requirements/<requirement_uid>/modules/<module_uid>
DELETE /api/requirements/<requirement_uid>/modules/<module_uid>
```

解析任务使用 SSE，任务类型建议为：

```text
requirement_analysis
```

任务日志继续复用 `.test-plan-viewer/jobs/<job_id>.log` 和 `test_jobs`。

### 9.3 从候选模块生成测试计划

```text
POST /api/requirements/<requirement_uid>/modules/<module_uid>/generate-plan-stream
POST /api/requirements/<requirement_uid>/modules/generate-plans-batch
```

内部优先复用现有 `/api/plan-generation-stream` 的实现逻辑，避免复制 planner 调用和资产同步代码。

### 9.4 inventory

```text
GET  /api/page-inventory
POST /api/page-inventory
PUT  /api/page-inventory/<inventory_uid>
DELETE /api/page-inventory/<inventory_uid>
POST /api/page-inventory/import-from-doc
POST /api/page-inventory/scan-stream
```

`scan-stream` 放到后续阶段实现。

## 10. 前端页面建议

### 10.1 新增一级菜单

新增“需求”菜单，和“计划、脚本、测试集”并列。

菜单权限新增：

```text
menu.requirements
```

管理员默认拥有该权限；已有计划、脚本、测试集权限不变。

### 10.2 需求页面布局

建议采用三栏或左右布局：

```text
左侧：需求列表
中间：需求预览 / 解析日志
右侧：模块候选列表 / prompt 编辑 / 生成计划按钮
```

最小可用功能：

- 上传 Markdown。
- 查看需求 Markdown 预览。
- 点击“解析需求”。
- 查看解析日志。
- 查看模块候选。
- 编辑模块名、计划名和 prompt。
- 单个生成测试计划。
- 跳转到生成后的测试计划。

### 10.3 inventory 页面入口

第一阶段不一定需要单独页面。可以先在需求页解析时展示“匹配到的页面 inventory”，后续再加“页面 inventory”管理页或放在项目设置中。

## 11. prompt 设计

### 11.1 需求解析 prompt 模板

```text
@requirement-analyst
你是测试需求分析助手。请读取下面的需求 Markdown、页面 inventory 摘要和已有测试计划摘要，生成模块候选。

要求：
1. 只做需求分析，不操作浏览器，不写入 specs 或 tests。
2. 输出 JSON，对象顶层包含 modules 数组。
3. 每个模块包含 module_name、plan_name、business_goal、test_points、matched_inventory、write_risk、baseline_required、confidence、open_questions、planner_prompt。
4. 如果需求无法匹配真实页面，保留 open_questions，不要臆造 URL。
5. planner_prompt 必须可以直接交给 playwright-test-planner 使用。

需求 Markdown：
...

页面 inventory 摘要：
...

已有测试计划摘要：
...
```

### 11.2 planner prompt 草稿结构

```text
@playwright-test-planner
请根据需求文档和页面 inventory，生成“<模块名>”模块测试计划。

需求要点：
- ...

已知页面 inventory：
- 页面路径：...
- 菜单入口：...
- 推荐账号：...
- 关键控件：...
- 写库风险：...

要求：
1. 使用 tests/seed/seed.spec.ts 作为入口。
2. 实际登录系统并复核页面。
3. 记录进入该界面的导航路径。
4. 优先使用稳定定位器。
5. 写库操作必须在测试计划中标记需要数据库基线。
6. 不要保存或提交真实业务数据，除非该用例明确要求并说明基线恢复。
7. 生成 3-5 条可转脚本的测试用例。
```

## 12. 分阶段实施计划

### 阶段 1：需求上传和需求解析

交付内容：

- 新增“需求”菜单和权限。
- 新增需求上传、列表、预览。
- 新增需求解析 job。
- 保存模块候选。
- 支持编辑模块候选和 planner prompt。

验收标准：

- 上传 `.md` 后可以在需求页看到预览。
- 解析后可以看到模块候选。
- 不会创建或修改任何 `specs` 和 `tests` 文件。
- 现有计划、脚本、测试集页面行为不变。

### 阶段 2：从候选模块生成测试计划

交付内容：

- 单个候选模块生成测试计划。
- 批量选择候选模块生成测试计划。
- 目标文件存在时提示冲突，默认不覆盖。
- 生成成功后关联需求模块和计划资产。
- 支持跳转到生成后的测试计划。

验收标准：

- 生成的计划出现在现有“测试计划”页面。
- 计划资产 revision 和 Git commit 正常创建。
- 后续可以使用现有“生成脚本”流程。
- 老流程仍可直接新增计划。

### 阶段 3：inventory 管理

交付内容：

- 支持导入或人工维护 page inventory。
- 需求解析时使用 inventory 做模块匹配。
- 在模块候选中展示匹配页面、账号、控件和写库风险。

验收标准：

- 有 inventory 时，模块候选能带出页面路径、推荐账号、关键控件。
- 无 inventory 时，需求解析仍可工作，但会标记不确定点。

### 阶段 4：inventory 扫描和更新

交付内容：

- 使用 raw `playwright-test` MCP 或 scanner agent 扫描菜单。
- 采集页面 snapshot，整理为压缩后的 inventory。
- 支持按角色扫描菜单差异。
- 扫描任务只读优先，默认不触发保存、提交、删除、审核等写库动作。

验收标准：

- 能生成页面清单。
- 能更新页面控件和菜单路径。
- 不破坏业务数据。

## 13. 风险和控制

### 13.1 影响现有流程

风险：需求页实现时改动现有计划生成、脚本生成主链路。

控制：

- 需求页作为可选上游。
- 复用现有 planner 生成能力，不重写保存计划逻辑。
- 数据库迁移只做加法。
- 现有 API 行为不变。

### 13.2 覆盖已有测试计划

风险：从需求批量生成计划时覆盖已有 `.md`。

控制：

- 默认检查目标文件是否存在。
- 存在时提示用户改名或明确覆盖。
- 覆盖必须保留 revision 和 Git commit。

### 13.3 需求解析臆造页面

风险：需求中没有页面信息时，模型编造 URL 或控件。

控制：

- 解析 prompt 明确要求无法匹配时输出 open_questions。
- inventory 只作为匹配来源。
- 正式计划生成时 planner 必须复核页面。

### 13.4 inventory 扫描写库

风险：扫描页面时误点保存、提交、删除等按钮。

控制：

- 扫描任务默认只做登录、导航、查询、展开、查看。
- 对 write_actions 做黑名单。
- 写库页面只采集控件，不提交表单。
- 需要写库验证时必须走测试计划和数据库基线。

### 13.5 OpenCode 并发占用

风险：需求解析、inventory 扫描和脚本生成同时运行，互相影响。

控制：

- job 类型隔离：`requirement_analysis`、`inventory_scan`、`planner`、`generator`、`healer`。
- 每个 job 独立日志文件。
- 支持取消任务。
- 后续可加项目级并发限制。

## 14. 对现有功能的影响结论

如果按本方案实施，现有流程不需要改变：

```text
测试计划 -> 生成脚本 -> 执行/修复 -> 测试集
```

新增需求页只提供一条可选入口：

```text
需求 -> 模块候选 -> planner prompt -> 测试计划
```

因此实施边界应坚持：

- 不改 `specs` 和 `tests` 存储规则。
- 不改现有计划和脚本页面的默认行为。
- 不强制计划必须绑定需求。
- 不把需求解析结果直接当最终测试计划。
- 正式测试计划仍由 planner 真实探索页面后生成。

## 15. 后续开发优先级

建议优先开发顺序：

1. 数据库加表和菜单权限。
2. 需求上传、列表、预览。
3. 需求解析 job 和模块候选保存。
4. 模块候选编辑。
5. 单个候选模块调用现有 planner 生成计划。
6. 批量生成计划。
7. inventory 人工维护和导入。
8. inventory 扫描更新。
