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
vm.runInContext(
  fs.readFileSync(path.join(appDir, "static/js/i18n/en.js"), "utf8"),
  context,
);
const english = context.window.WaterfallTranslations.en;
const translate = (key, params = {}) => Object.entries(params).reduce(
  (value, [name, replacement]) => value.replaceAll(`{${name}}`, String(replacement)),
  english[key] || key,
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
const fetchCalls = [];
let fetchImpl = async () => {
  throw new Error("focused VM paths must not fetch");
};
const windowMock = {
  WaterfallI18n: { t: translate },
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

const featureDeps = {
  state,
  elements,
  SECTION: { PLANS: "plans" },
  PLAN_VIEW_TAB: { SCRIPT_GENERATION: "script-generation" },
  SCRIPT_PROMPT_NOTE_DEFAULT: "补充说明",
  document: { createElement: () => domElement() },
  window: windowMock,
  fetch: (...args) => fetchImpl(...args),
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
  parseSseBlock: (block) => {
    let event = "message";
    const dataLines = [];
    block.split("\n").forEach((line) => {
      if (line.startsWith("event:")) {
        event = line.slice("event:".length).trim() || "message";
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice("data:".length).trimStart());
      }
    });
    return dataLines.length
      ? { event, data: JSON.parse(dataLines.join("\n")) }
      : null;
  },
  getDefaultScriptTargetPath: (moduleName) => `tests/${moduleName}`,
  getProjectRequestHeaders: (headers) => headers,
  createClientJobId: () => "generator-test",
  persistViewState() {},
  loadScriptTree: async () => {},
  renderSideList() {},
  createStatusBadge: () => domElement(),
  getGenerationStatusInfo: (record) => ({ status: record?.status || "idle" }),
  getPlanRecordKey,
};
const feature = context.window.createModulePlanGenerationFeature(featureDeps);

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

function successfulScriptResponse(scriptFilename) {
  const chunks = [
    Buffer.from(
      [
        'event: status\ndata: {"status":"running","target_path":"tests/Authentication"}\n\n',
        `event: done\ndata: ${JSON.stringify({
          ok: true,
          status: "succeeded",
          script_filename: scriptFilename,
        })}\n\n`,
      ].join(""),
    ),
  ];
  return {
    ok: true,
    body: {
      getReader() {
        return {
          async read() {
            return chunks.length
              ? { value: chunks.shift(), done: false }
              : { value: undefined, done: true };
          },
        };
      },
    },
  };
}

async function testEnglishBulkScriptGeneration() {
  const planFilenames = ["Successful Login.md", "Locked Out User.md"];
  const savedScriptFilenames = [
    "Successful Login Flow.spec.ts",
    "Locked Out User Login.spec.ts",
  ];
  state.activeSection = "plans";
  state.plans.selectedModule = "Authentication";
  state.plans.selectedPlanFile = null;
  state.plans.selectedPlanFiles = new Set(planFilenames);
  state.plans.bulkSelectionMode = true;
  state.plans.activeTab = "content";
  state.plans.modules = [
    {
      name: "Authentication",
      plans: planFilenames.map((filename) => ({
        filename,
        name: filename.replace(/\.md$/, ""),
      })),
    },
  ];
  state.scripts.modules = [{ name: "Authentication", scripts: [] }];
  state.plans.scriptGenerationBatches = {};
  state.plans.scriptGenerationRecords = {};
  fetchCalls.length = 0;
  let responseIndex = 0;
  fetchImpl = async (url, options) => {
    fetchCalls.push({ url, options, body: JSON.parse(options.body) });
    return successfulScriptResponse(savedScriptFilenames[responseIndex++]);
  };

  await feature.generateSelectedModulePlanScripts();

  assert.strictEqual(fetchCalls.length, 2);
  assert.deepStrictEqual(
    fetchCalls.map((call) => call.url),
    ["/api/script-generation-stream", "/api/script-generation-stream"],
  );
  assert.deepStrictEqual(
    fetchCalls.map((call) => call.body.plan_filename),
    planFilenames,
  );
  const batch = state.plans.scriptGenerationBatches["module:Authentication"];
  assert.strictEqual(batch.status, "succeeded");
  assert.deepStrictEqual(
    planFilenames.map((filename) => batch.items[filename].status),
    ["succeeded", "succeeded"],
  );
  assert.deepStrictEqual(
    planFilenames.map((filename) => batch.items[filename].script_filename),
    savedScriptFilenames,
  );
  savedScriptFilenames.forEach((filename) => {
    assert.match(filename, /^[A-Za-z][A-Za-z0-9 ]*\.spec\.ts$/);
  });
}

async function testEnglishScriptPreparationWorkflow() {
  const notices = [];
  const requests = [];
  const openedRuns = [];
  const result = {
    run: { run_id: "module-preparation-1", module_name: "Authentication", status: "queued" },
    snapshot: { run_id: "module-preparation-1", status: "queued", items: [] },
  };
  const preparationFeature = context.window.createModulePlanGenerationFeature({
    ...featureDeps,
    requestJson: async (url, options) => {
      requests.push({ url, body: JSON.parse(options.body) });
      return result;
    },
    setNotice: (message, type = "") => notices.push({ message, type }),
    openScriptPreparationRun: async (...args) => openedRuns.push(args),
    canOpenScriptPreparation: () => true,
  });
  state.activeSection = "plans";
  state.plans.selectedModule = "Authentication";
  state.plans.selectedPlanFile = null;
  state.plans.selectedPlanFiles = new Set(["Successful Login.md"]);
  state.plans.bulkSelectionMode = true;
  state.plans.modules = [{
    name: "Authentication",
    plans: [{ filename: "Successful Login.md", name: "Successful Login" }],
  }];

  await preparationFeature.generateSelectedModulePlanScripts();

  assert.deepStrictEqual(requests, [{
    url: "/api/script-preparation-runs",
    body: {
      module_name: "Authentication",
      plan_filenames: ["Successful Login.md"],
      client_request_id: "generator-test",
    },
  }]);
  assert.deepStrictEqual(openedRuns, [[result, "Authentication"]]);
  assert.strictEqual(
    notices[0].message,
    "Creating a script-preparation task. The platform will then generate, run, and repair failed scripts automatically.",
  );
  assert.deepStrictEqual(notices.at(-1), {
    message: "Script-preparation task created. Scripts are being generated and verified automatically.",
    type: "success",
  });

  const deniedNotices = [];
  const deniedFeature = context.window.createModulePlanGenerationFeature({
    ...featureDeps,
    setNotice: (message, type = "") => deniedNotices.push({ message, type }),
    openScriptPreparationRun: async () => {},
    canOpenScriptPreparation: () => false,
  });
  state.plans.selectedPlanFiles = new Set(["Successful Login.md"]);
  deniedFeature.renderModulePlanList();
  assert.strictEqual(elements.modulePlanBulkGenerate.title, "Requires access to the Scripts menu");
  await deniedFeature.generateSelectedModulePlanScripts();
  assert.deepStrictEqual(deniedNotices, [{
    message: "This account cannot access the Scripts menu, so it cannot start script preparation.",
    type: "error",
  }]);
}

testEnglishBulkScriptGeneration()
  .then(testEnglishScriptPreparationWorkflow)
  .then(() => {
    process.stdout.write("module plan generation English workflows: ok\n");
  })
  .catch((error) => {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 1;
  });

process.stdout.write("module plan generation feature VM smoke: ok\n");
