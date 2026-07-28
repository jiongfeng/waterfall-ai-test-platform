const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");
const source = fs.readFileSync(
  path.join(appDir, "static/js/features/test-suites.js"),
  "utf8",
);
const context = { window: {} };
vm.createContext(context);
vm.runInContext(source, context);

const getSuiteScriptKey = (moduleName, filename) =>
  moduleName && filename ? `${moduleName}/${filename}` : "";
const resultHelpers = context.window.createTestSuiteResultHelpers({
  getSuiteScriptKey,
});

assert.deepStrictEqual(
  JSON.parse(
    JSON.stringify(
      resultHelpers.finalizeTestSuiteScriptResults(
        [
          { module_name: "module", filename: "passed.spec.ts" },
          { module_name: "module", filename: "running.spec.ts" },
          { module_name: "module", filename: "missing.spec.ts" },
        ],
        {
          "module/passed.spec.ts": "passed",
          "module/running.spec.ts": "running",
        },
        "interrupted",
      ),
    ),
  ),
  {
    "module/passed.spec.ts": "passed",
    "module/running.spec.ts": "interrupted",
    "module/missing.spec.ts": "interrupted",
  },
);

const suite = {
  id: "suite-1",
  name: "核心流程",
  updated_at: 1,
  items: [
    { item_id: 1, module_name: "购物车", filename: "加入商品.spec.ts" },
    { item_id: 2, module_name: "登录", filename: "登录成功.spec.ts" },
  ],
};
const state = {
  activeSection: "other",
  testSuites: {
    suites: [suite],
    selectedSuiteId: suite.id,
    selectedModule: "__all__",
    activeTab: "scripts",
    executionRecords: {},
    executionHistory: {
      records: [],
      selectedRunId: null,
      loadedSuiteId: null,
      isLoading: false,
      error: "",
    },
    availableModules: [],
    addModalModule: "__all__",
    selectedScriptKeys: new Set(),
    renamingSuiteId: null,
  },
  testSuiteExecution: {
    isRunning: false,
    progressModalVisible: false,
    progressModalSuiteId: "",
  },
  testSuiteVideoModal: { video: null, title: "" },
};

let persistenceCalls = 0;
let renderCalls = 0;
let fetchCalls = 0;
const requests = [];
const feature = context.window.createTestSuitesFeature({
  state,
  elements: {},
  SECTION: { TEST_SUITES: "testSuites" },
  TEST_SUITE_VIEW_TAB: { SCRIPTS: "scripts", EXECUTION: "execution" },
  TEST_SUITE_ALL_MODULE: "__all__",
  EXECUTION_MODE: { BATCH: "batch", SERIAL_PER_FILE: "serial_per_file" },
  document: {
    createElement() {
      throw new Error("The focused VM paths must not require DOM rendering");
    },
  },
  window: { confirm: () => true, requestAnimationFrame: (callback) => callback() },
  fetch: async () => {
    fetchCalls += 1;
    throw new Error("A cancelled execution must not issue fetch");
  },
  TextDecoder,
  resultHelpers,
  getSuiteScriptKey,
  stripSpecSuffix: (value) => String(value || "").replace(/\.spec\.ts$/, ""),
  normalizeTestSuiteExecutionArtifact: (value) => value,
  normalizeTestSuiteExecutionRunList: (value) => value,
  formatTimestampMs: String,
  getDbExecutionModeLabel: String,
  getDbResultStatusInfo: (status) => ({ label: status, className: status }),
  isAnyScriptJobRunning: () => false,
  persistViewState: () => {
    persistenceCalls += 1;
  },
  persistTestSuiteExecutionRecords: () => {
    persistenceCalls += 1;
  },
  renderContent: () => {
    renderCalls += 1;
  },
  renderSideList: () => {
    renderCalls += 1;
  },
  setNotice: () => {},
  setLoading: () => {},
  requestJson: async (url, options) => {
    requests.push({ url, options });
    return {
      suite: {
        ...suite,
        items: [suite.items[1], suite.items[0]],
        updated_at: 2,
      },
    };
  },
  encodePathPart: encodeURIComponent,
  normalizeTestSuite: (value) => value,
  normalizeTestSuiteExecutionRecord: (value) => value,
  normalizeExecutionModeValue: (value) =>
    value === "serial_per_file" ? value : "batch",
  parseSseBlock: () => null,
  openExecutionModeModal: async () => null,
  getExecutionModeLabel: String,
  getProjectRequestHeaders: (headers) => headers,
});

assert.deepStrictEqual(
  JSON.parse(JSON.stringify(feature.getSuiteModuleOptions(suite))),
  [
    { name: "__all__", label: "全部", count: 2 },
    { name: "登录", label: "登录", count: 1 },
    { name: "购物车", label: "购物车", count: 1 },
  ],
);

let streamState = {
  status: "running",
  execution_mode: "batch",
  script_results: { "登录/登录成功.spec.ts": "passed" },
  logs: "existing\n",
};
streamState = feature.handleTestSuiteExecutionStreamEvent(
  {
    event: "status",
    data: {
      status: "running",
      script_results: { "购物车/加入商品.spec.ts": "failed" },
      completed_files: 1,
    },
  },
  streamState,
  suite,
);
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(streamState.script_results)),
  {
    "登录/登录成功.spec.ts": "passed",
    "购物车/加入商品.spec.ts": "failed",
  },
);
assert.strictEqual(state.testSuites.executionRecords[suite.id].completed_files, 1);

streamState = feature.handleTestSuiteExecutionStreamEvent(
  {
    event: "done",
    data: {
      status: "succeeded",
      script_results: { "购物车/加入商品.spec.ts": "passed" },
      completed_files: 2,
    },
  },
  streamState,
  suite,
);
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(streamState.script_results)),
  {
    "登录/登录成功.spec.ts": "passed",
    "购物车/加入商品.spec.ts": "passed",
  },
);

(async () => {
  await feature.moveTestSuiteItem(suite.id, 1, 1);
  assert.deepStrictEqual(JSON.parse(requests.at(-1).options.body).item_ids, [2, 1]);

  feature.selectTestSuite(suite.id);
  assert.strictEqual(state.testSuites.selectedSuiteId, suite.id);
  assert.strictEqual(state.testSuites.selectedModule, "__all__");

  await feature.executeSelectedTestSuite();
  assert.strictEqual(fetchCalls, 0);
  assert.strictEqual(state.testSuiteExecution.isRunning, false);
  assert.ok(persistenceCalls > 0);
  assert.ok(renderCalls > 0);
  process.stdout.write("test-suite feature VM smoke: ok\n");
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
