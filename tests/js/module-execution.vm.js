const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");
const context = { window: {}, TextDecoder };
vm.createContext(context);
for (const filename of [
  "static/js/core/sse.js",
  "static/js/core/timers.js",
  "static/js/features/module-execution.js",
]) {
  vm.runInContext(fs.readFileSync(path.join(appDir, filename), "utf8"), context);
}

function createElement() {
  const classes = new Set();
  return {
    textContent: "",
    value: "",
    checked: false,
    disabled: false,
    indeterminate: false,
    scrollTop: 0,
    scrollHeight: 0,
    children: [],
    classList: {
      add: (...names) => names.forEach((name) => classes.add(name)),
      remove: (...names) => names.forEach((name) => classes.delete(name)),
      contains: (name) => classes.has(name),
      toggle(name, force) {
        if (force === undefined ? !classes.has(name) : force) {
          classes.add(name);
          return true;
        }
        classes.delete(name);
        return false;
      },
    },
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    append(...children) {
      this.children.push(...children);
    },
    replaceChildren(...children) {
      this.children = children;
    },
    addEventListener() {},
    setAttribute(name, value) {
      this[name] = String(value);
    },
    getAttribute(name) {
      return this[name] || null;
    },
    removeAttribute(name) {
      delete this[name];
    },
    querySelector() {
      return createElement();
    },
    load() {},
  };
}

const state = {
  activeSection: "other",
  isEditing: false,
  scripts: {
    selectedModule: "登录",
    selectedFile: null,
    selectedFiles: new Set(),
    activeTab: "script",
    bulkSelectionMode: false,
    bulkDeletingScripts: false,
    moduleExecutionRecords: {},
    moduleRepairBatches: {},
    runRecords: {},
    repairRecords: {},
    recentResults: [],
  },
  generation: { isRunning: false },
  scriptGeneration: { isRunning: false },
  scriptRecording: { isRunning: false },
  scriptExecution: { isRunning: false },
  moduleExecution: { isRunning: false },
  scriptRun: { isRunning: false },
  moduleRepair: {
    isRunning: false,
    cancelRequested: false,
    currentController: null,
    currentJobId: "",
    activeFilename: "",
    moduleName: "",
  },
  testSuiteExecution: { isRunning: false },
  requirements: {
    analysisRunning: false,
    bulkDeletingModules: false,
    planGenerationRunning: false,
  },
  project: { isExporting: false, isImporting: false },
};
const elements = {
  moduleScriptSummary: createElement(),
  moduleScriptActions: createElement(),
  moduleBulkActions: createElement(),
  moduleSelectHeader: createElement(),
  moduleSelectionCount: createElement(),
  moduleBulkToggle: createElement(),
  moduleBulkExecute: createElement(),
  moduleBulkRepair: createElement(),
  moduleBulkDelete: createElement(),
  moduleSelectAll: createElement(),
  moduleScriptTableBody: createElement(),
};

let nextTimerId = 40;
const activeTimers = new Set();
const clearedTimers = [];
const timerHost = {
  setInterval() {
    const id = ++nextTimerId;
    activeTimers.add(id);
    return id;
  },
  clearInterval(id) {
    activeTimers.delete(id);
    clearedTimers.push(id);
  },
};
const modulePersistence = { execution: [], repair: [] };
const scriptPersistence = { run: [], repair: [] };
const requests = [];
const getModuleRecordKey = (moduleName = state.scripts.selectedModule) =>
  moduleName || "";
const getScriptRunRecordKey = (
  moduleName = state.scripts.selectedModule,
  filename = state.scripts.selectedFile,
) => (moduleName && filename ? `${moduleName}/${filename}` : "");
const setScriptRunRecord = (moduleName, filename, updates) => {
  const key = getScriptRunRecordKey(moduleName, filename);
  state.scripts.runRecords[key] = {
    ...(state.scripts.runRecords[key] || {}),
    ...updates,
  };
  scriptPersistence.run.push(key);
  return state.scripts.runRecords[key];
};
const setScriptRepairRecord = (moduleName, filename, updates) => {
  const key = getScriptRunRecordKey(moduleName, filename);
  state.scripts.repairRecords[key] = {
    ...(state.scripts.repairRecords[key] || {}),
    ...updates,
  };
  scriptPersistence.repair.push(key);
  return state.scripts.repairRecords[key];
};
const scriptRepair = {
  setScriptRunRecord,
  setScriptRepairRecord,
  ensureScriptRepairRecord: () => null,
  renderScriptRunPromptFromTemplate: (moduleName, filename) =>
    `修复 ${moduleName}/${filename}`,
  renderScriptRunDuration: () => {},
  formatRepairDuration: () => "00:01",
  executeSelectedScript: async () => {},
  openScriptRepairRecord: () => {},
};

function createStreamResponse(chunks) {
  let index = 0;
  return {
    ok: true,
    body: {
      getReader() {
        return {
          async read() {
            if (index >= chunks.length) {
              return { done: true };
            }
            return {
              done: false,
              value: Buffer.from(chunks[index++], "utf8"),
            };
          },
        };
      },
    },
  };
}

let fetchMode = "unexpected";
const fetchImpl = async () => {
  if (fetchMode !== "repair") {
    throw new Error("Unexpected fetch in focused module execution VM test");
  }
  return createStreamResponse([
    'event: status\ndata: {"status":"running"}\n\nevent: log\ndata: {"message":"修复中"}\n\n',
    'event: done\ndata: {"ok":true,"status":"succeeded","returncode":0}\n\n',
  ]);
};
const document = {
  createElement: () => createElement(),
};
const feature = context.window.createModuleExecutionFeature({
  state,
  elements,
  SECTION: { SCRIPTS: "scripts" },
  SCRIPT_VIEW_TAB: {
    SCRIPT: "script",
    EXECUTION: "execution",
    REPAIR: "repair",
  },
  EXECUTION_MODE: { BATCH: "batch", SERIAL_PER_FILE: "serial_per_file" },
  SCRIPT_RUN_PROMPT_NOTE_DEFAULT: "不得删除 STEP",
  document,
  window: {
    crypto: { randomUUID: () => "job-id" },
    confirm: () => true,
    requestAnimationFrame: (callback) => callback(),
    WaterfallI18n: {
      log: (value) => value,
      t: (key, { duration }) => ({
        "duration.elapsed": `Elapsed: ${duration}`,
        "duration.total": `Duration: ${duration}`,
      })[key] || key,
    },
  },
  fetch: fetchImpl,
  TextDecoder,
  AbortController,
  timers: context.window.createTimerRuntime(timerHost),
  scriptRepair,
  getModuleRecordKey,
  normalizeModuleExecutionRecord: (value) => value,
  persistModuleExecutionRecords: (key) =>
    modulePersistence.execution.push(key),
  normalizeExecutionModeValue: (value) =>
    value === "serial_per_file" ? value : "batch",
  normalizeModuleRepairBatch: (value) => value,
  persistModuleRepairBatches: (key) => modulePersistence.repair.push(key),
  persistViewState: () => {},
  requestJson: async (url, options) => {
    requests.push({ url, options });
    return {};
  },
  encodePathPart: encodeURIComponent,
  setNotice: () => {},
  renderContent: () => {},
  getSelectedScriptModule: () => ({ scripts: [] }),
  loadScriptTree: async () => {},
  selectScript: async () => {},
  parseSseBlock: context.window.parseSseBlock,
  openExecutionModeModal: async () => "batch",
  getExecutionModeLabel: () => "批量执行",
  getProjectRequestHeaders: (headers) => headers,
  stripSpecSuffix: (value) => String(value).replace(/\.spec\.ts$/, ""),
  getScriptRunRecordKey,
  createStatusBadge: () => createElement(),
  getDbResultStatusInfo: (status) => ({ label: status }),
  getDbExecutionModeLabel: String,
  formatTimestampMs: String,
});

assert.deepStrictEqual(
  JSON.parse(JSON.stringify(feature.getExecutionStatusInfo({ status: "running" }))),
  { label: "执行中", className: "running" },
);
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(feature.getRepairStatusInfo({ status: "cancelled" }))),
  { label: "已取消", className: "cancelled" },
);
assert.strictEqual(
  feature.formatModuleRepairDuration({ status: "running", started_at: Date.now() - 1000 }),
  "Elapsed: 00:01",
);

let executionResult = { status: "running", logs: "", script_results: {} };
executionResult = feature.handleModuleExecutionStreamEvent(
  {
    event: "status",
    data: {
      status: "running",
      execution_mode: "serial_per_file",
      script_results: { "登录成功.spec.ts": "passed" },
    },
  },
  executionResult,
  "登录",
);
executionResult = feature.handleModuleExecutionStreamEvent(
  { event: "log", data: { message: "批量执行" } },
  executionResult,
  "登录",
);
executionResult = feature.handleModuleExecutionStreamEvent(
  {
    event: "done",
    data: {
      status: "succeeded",
      report: { url: "/report" },
      returncode: 0,
    },
  },
  executionResult,
  "登录",
);
assert.strictEqual(executionResult.status, "succeeded");
assert.strictEqual(executionResult.logs, "批量执行\n");
assert.strictEqual(
  state.scripts.runRecords["登录/登录成功.spec.ts"].status,
  "passed",
);
assert.ok(modulePersistence.execution.length >= 3);

let repairResult = { status: "running", logs: "" };
repairResult = feature.handleModuleRepairStreamEvent(
  { event: "log", data: { message: "逐条修复" } },
  repairResult,
  "登录",
  "登录成功.spec.ts",
);
repairResult = feature.handleModuleRepairStreamEvent(
  { event: "done", data: { ok: true, returncode: 0 } },
  repairResult,
  "登录",
  "登录成功.spec.ts",
);
assert.strictEqual(repairResult.status, "succeeded");
assert.strictEqual(
  state.scripts.moduleRepairBatches["登录"].items["登录成功.spec.ts"].status,
  "succeeded",
);

state.scripts.moduleRepairBatches["登录"] = {
  status: "running",
  module_name: "登录",
  filenames: ["登录成功.spec.ts", "登录失败.spec.ts"],
  items: {
    "登录成功.spec.ts": { status: "running", logs: "正在运行\n" },
    "登录失败.spec.ts": { status: "queued", logs: "" },
  },
};
let aborted = false;
state.moduleRepair.isRunning = true;
state.moduleRepair.moduleName = "登录";
state.moduleRepair.currentJobId = "server-job";
state.moduleRepair.currentController = {
  abort() {
    aborted = true;
  },
};
feature.cancelModuleRepairBatch();
assert.strictEqual(aborted, true);
assert.strictEqual(
  state.scripts.moduleRepairBatches["登录"].status,
  "cancelled",
);
assert.strictEqual(
  state.scripts.moduleRepairBatches["登录"].items["登录失败.spec.ts"].status,
  "cancelled",
);
assert.strictEqual(requests.at(-1).url, "/api/script-run-cancel");

(async () => {
  state.moduleRepair.isRunning = false;
  state.moduleRepair.cancelRequested = false;
  state.moduleRepair.currentController = null;
  state.moduleRepair.currentJobId = "";
  state.moduleRepair.moduleName = "";
  state.scripts.selectedFiles = new Set(["登录成功.spec.ts"]);
  fetchMode = "repair";
  await feature.repairSelectedModuleScripts();
  assert.strictEqual(
    state.scripts.moduleRepairBatches["登录"].status,
    "succeeded",
  );
  assert.strictEqual(
    state.scripts.moduleRepairBatches["登录"].items["登录成功.spec.ts"].status,
    "succeeded",
  );
  assert.strictEqual(state.moduleRepair.isRunning, false);
  assert.strictEqual(activeTimers.size, 0);
  assert.ok(clearedTimers.length >= 1);
  assert.ok(modulePersistence.repair.length >= 8);
  assert.ok(scriptPersistence.repair.length >= 4);
  process.stdout.write("module-execution feature VM smoke: ok\n");
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
