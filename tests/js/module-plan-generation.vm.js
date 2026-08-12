const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");
const context = { window: {} };
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(
    path.join(appDir, "static/js/features/module-plan-generation.js"),
    "utf8",
  ),
  context,
);

function domElement() {
  return {
    children: [],
    className: "",
    textContent: "",
    title: "",
    disabled: false,
    checked: false,
    indeterminate: false,
    classList: {
      add() {},
      remove() {},
      toggle() {},
    },
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    append(...children) {
      this.children.push(...children);
    },
    replaceChildren(...children) {
      this.children = [...children];
    },
    addEventListener() {},
    setAttribute() {},
  };
}

const elements = {
  modulePlanSummary: domElement(),
  modulePlanActions: domElement(),
  modulePlanBulkActions: domElement(),
  modulePlanSelectHeader: domElement(),
  modulePlanSelectionCount: domElement(),
  modulePlanBulkToggle: domElement(),
  modulePlanBulkGenerate: domElement(),
  modulePlanBulkDelete: domElement(),
  modulePlanSelectAll: domElement(),
  modulePlanTableBody: domElement(),
  modulePlanScriptBatchEmpty: domElement(),
  modulePlanScriptBatchList: domElement(),
  modulePlanScriptBatchHeader: domElement(),
  modulePlanScriptBatchSummary: domElement(),
};
const state = {
  activeSection: "scripts",
  plans: {
    selectedModule: "账户",
    selectedPlanFile: null,
    selectedPlanFiles: new Set(["登录.md", "已删除.md"]),
    bulkSelectionMode: false,
    bulkDeletingPlans: false,
    activeTab: "content",
    modules: [
      {
        name: "账户",
        plans: [
          { filename: "_索引.md", name: "索引", is_index: true },
          { filename: "登录.md", name: "登录" },
          { filename: "注册.md", name: "注册" },
        ],
      },
    ],
    scriptGenerationBatches: {},
    scriptGenerationRecords: {},
  },
  scripts: {
    modules: [
      {
        name: "账户",
        scripts: [{ name: "登录.spec.ts" }],
      },
    ],
  },
  scriptGeneration: { isRunning: false },
  generation: { isRunning: false },
};
const persistedBatchKeys = [];
const generationRecordUpdates = [];
const windowMock = {
  confirm: () => true,
  requestAnimationFrame: (callback) => callback(),
  setInterval: () => 1,
  clearInterval() {},
};
const getPlanModuleRecordKey = (moduleName = state.plans.selectedModule) =>
  moduleName ? `module:${moduleName}` : "";
const getPlanRecordKey = (
  moduleName = state.plans.selectedModule,
  planFilename = state.plans.selectedPlanFile,
) => `${moduleName}/${planFilename}`;

const feature = context.window.createModulePlanGenerationFeature({
  state,
  elements,
  SECTION: { PLANS: "plans" },
  PLAN_VIEW_TAB: { SCRIPT_GENERATION: "script-generation" },
  SCRIPT_PROMPT_NOTE_DEFAULT: "补充说明",
  document: { createElement: () => domElement() },
  window: windowMock,
  fetch: async () => {
    throw new Error("focused VM paths must not fetch");
  },
  TextDecoder,
  generation: {
    setPlanScriptGenerationRecord: (moduleName, planFilename, updates) => {
      generationRecordUpdates.push({ moduleName, planFilename, updates });
    },
    renderScriptPromptFromTemplate: () => "固定提示词",
    openScriptGenerationModal() {},
  },
  moduleExecution: {
    formatModuleRepairDuration: () => "00:01",
  },
  getSelectedPlanModule: (moduleName = state.plans.selectedModule) =>
    state.plans.modules.find((item) => item.name === moduleName) || null,
  getGeneratedScriptFilenameFromPlan: (filename) =>
    filename.replace(/\.md$/, ".spec.ts"),
  getSelectedScriptModule: (moduleName) =>
    state.scripts.modules.find((item) => item.name === moduleName) || null,
  requestJson: async () => ({}),
  encodePathPart: encodeURIComponent,
  stripMarkdownSuffix: (filename) => filename.replace(/\.md$/, ""),
  loadPlanModules: async () => {},
  setNotice() {},
  renderContent() {},
  selectPlan: async () => {},
  getPlanModuleRecordKey,
  normalizePlanScriptGenerationBatch: (value) => ({
    ...value,
    items: { ...(value.items || {}) },
  }),
  persistPlanScriptGenerationBatches: (key) => persistedBatchKeys.push(key),
  parseSseBlock: () => null,
  getDefaultScriptTargetPath: (moduleName) => `tests/${moduleName}`,
  getProjectRequestHeaders: (headers) => headers,
  createClientJobId: () => "generator-test",
  persistViewState() {},
  loadScriptTree: async () => {},
  renderSideList() {},
  createStatusBadge: () => domElement(),
  getGenerationStatusInfo: (record) => ({ status: record?.status || "idle" }),
  getPlanRecordKey,
});

const cancelledStreamResult = feature.handleModulePlanScriptStreamEvent(
  { event: "done", data: { ok: false, status: "cancelled", error: "用户终止" } },
  { status: "running", logs: "生成日志\n" },
  "账户",
  "登录.md",
);
assert.strictEqual(cancelledStreamResult.status, "cancelled");

assert.deepStrictEqual(
  feature.getCurrentModulePlans().map((plan) => plan.filename),
  ["登录.md", "注册.md"],
);
assert.strictEqual(
  feature.getExpectedScriptFilenameForPlan("登录.md"),
  "登录.spec.ts",
);
assert.strictEqual(
  feature.findScriptForPlan("账户", "登录.md").name,
  "登录.spec.ts",
);

feature.pruneModuleSelectedPlanFiles();
assert.deepStrictEqual(Array.from(state.plans.selectedPlanFiles), ["登录.md"]);

feature.enterModulePlanBulkMode();
assert.strictEqual(state.plans.bulkSelectionMode, true);
feature.toggleModulePlanSelectAll();
assert.deepStrictEqual(
  Array.from(state.plans.selectedPlanFiles),
  ["登录.md", "注册.md"],
);
feature.cancelModulePlanBulkMode();
assert.strictEqual(state.plans.bulkSelectionMode, false);
assert.strictEqual(state.plans.selectedPlanFiles.size, 0);

feature.setPlanScriptGenerationBatch("账户", {
  status: "running",
  plan_filenames: ["登录.md"],
  items: {},
});
feature.setPlanScriptGenerationBatchItem("账户", "登录.md", {
  status: "running",
  logs: "",
});
let result = feature.handleModulePlanScriptStreamEvent(
  { event: "log", data: { message: "开始生成" } },
  { status: "running", logs: "" },
  "账户",
  "登录.md",
);
assert.strictEqual(result.logs, "开始生成\n");
result = feature.handleModulePlanScriptStreamEvent(
  { event: "done", data: { ok: true } },
  result,
  "账户",
  "登录.md",
);
assert.strictEqual(result.status, "succeeded");
assert.strictEqual(
  state.plans.scriptGenerationBatches["module:账户"].items["登录.md"].status,
  "succeeded",
);
assert.ok(persistedBatchKeys.length >= 4);
assert.ok(generationRecordUpdates.length >= 2);

feature.renderModulePlanList();
feature.renderModulePlanScriptBatchRecord();
assert.strictEqual(elements.modulePlanTableBody.children.length, 2);
assert.strictEqual(elements.modulePlanScriptBatchList.children.length, 1);

process.stdout.write("module plan generation feature VM smoke: ok\n");
