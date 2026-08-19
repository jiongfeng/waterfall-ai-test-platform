const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const {
  createScriptPreparationHarness,
} = require("./script-preparation-test-harness");

const appDir = path.resolve(__dirname, "../..");
const harness = createScriptPreparationHarness();
let sharedOptions = null;
let workbenchState = {
  active: false,
  runId: "",
  status: "queued",
};
const workbenchCalls = {
  activate: [],
  applyEvent: [],
  deactivate: 0,
  destroy: 0,
  refresh: [],
  setRun: [],
};

let nextTimeoutId = 0;
const timeouts = new Map();
function flushTimeouts() {
  const queued = Array.from(timeouts.entries());
  timeouts.clear();
  queued.forEach(([, callback]) => callback());
}

const workbench = {
  async activate(runId) {
    workbenchCalls.activate.push(runId);
    workbenchState = { ...workbenchState, active: true, runId };
    return null;
  },
  applyEvent(event) {
    workbenchCalls.applyEvent.push(event);
    return true;
  },
  deactivate() {
    workbenchCalls.deactivate += 1;
    workbenchState = { ...workbenchState, active: false };
  },
  destroy() {
    workbenchCalls.destroy += 1;
    workbenchState = { ...workbenchState, active: false };
  },
  getState() {
    return { ...workbenchState };
  },
  async refresh(options) {
    workbenchCalls.refresh.push(options);
    return null;
  },
  setRun(runId) {
    workbenchCalls.setRun.push(runId);
    workbenchState = { ...workbenchState, runId };
    return true;
  },
};

const windowRef = {
  setTimeout(callback) {
    nextTimeoutId += 1;
    timeouts.set(nextTimeoutId, callback);
    return nextTimeoutId;
  },
  clearTimeout(timeoutId) {
    timeouts.delete(timeoutId);
  },
  createScriptPreparationFeature(root, options) {
    assert.strictEqual(root, harness.root);
    sharedOptions = options;
    return workbench;
  },
};

const context = {
  window: windowRef,
  document: harness.documentRef,
  console,
};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(
    path.join(appDir, "static/js/features/module-script-preparation.js"),
    "utf8",
  ),
  context,
);

assert.strictEqual(
  typeof context.window.createModuleScriptPreparationFeature,
  "function",
);
assert.deepStrictEqual(
  { ...context.window.normalizeModuleScriptPreparationRuns({ "模块 A": "run-a", "": "invalid" }, "模块 B", "run-b") },
  { "模块 A": "run-a", "模块 B": "run-b" },
  "persisted mappings and the legacy current pair must hydrate together",
);

const SECTION = { PLANS: "plans", SCRIPTS: "scripts" };
const SCRIPT_VIEW_TAB = {
  SCRIPT: "script",
  PREPARATION: "preparation",
};
const state = {
  activeSection: SECTION.PLANS,
  scripts: {
    modules: [],
    expandedModules: new Set(),
    selectedModule: null,
    selectedFile: null,
    activeTab: SCRIPT_VIEW_TAB.SCRIPT,
    preparationRunId: "",
    preparationModule: "",
    bulkSelectionMode: true,
    selectedFiles: new Set(["old.spec.ts"]),
  },
};
let persistCount = 0;
let sideListRenderCount = 0;
let contentRenderCount = 0;
let treeRefreshCount = 0;
const treeRefreshViews = [];

const feature = context.window.createModuleScriptPreparationFeature({
  root: harness.root,
  state,
  SECTION,
  SCRIPT_VIEW_TAB,
  requestJson: async () => ({}),
  encodePathPart: encodeURIComponent,
  persistViewState() {
    persistCount += 1;
  },
  renderSideList() {
    sideListRenderCount += 1;
  },
  renderContent() {
    contentRenderCount += 1;
  },
  async refreshScriptTree() {
    treeRefreshCount += 1;
    treeRefreshViews.push({
      activeSection: state.activeSection,
      activeTab: state.scripts.activeTab,
      selectedModule: state.scripts.selectedModule,
    });
  },
  window: windowRef,
  document: harness.documentRef,
});

(async () => {
  assert.ok(sharedOptions, "the module adapter must mount the shared workbench");
  assert.strictEqual(
    sharedOptions.api.snapshotUrl("run / 1"),
    "/api/script-preparation-runs/run%20%2F%201",
  );
  assert.strictEqual(
    sharedOptions.api.itemUrl("run / 1", "item / 1"),
    "/api/script-preparation-runs/run%20%2F%201/items/item%20%2F%201",
  );
  assert.strictEqual(
    sharedOptions.api.batchActionsUrl("run / 1"),
    "/api/script-preparation-runs/run%20%2F%201/items/batch-actions",
    "the production batch endpoint is nested under /items",
  );
  assert.strictEqual(
    sharedOptions.pollIntervalMs,
    3000,
    "the module adapter delegates polling to the shared workbench",
  );
  assert.strictEqual(sharedOptions.revealOnActivate, false, "background refresh must never reveal the hidden host");

  const runId = await feature.openRun(
    {
      run_id: "module-run-1",
      status: "running",
      snapshot: {
        run_id: "module-run-1",
        status: "running",
        items: [],
      },
    },
    "尚无脚本的模块",
  );

  assert.strictEqual(runId, "module-run-1");
  assert.strictEqual(state.activeSection, SECTION.SCRIPTS);
  assert.strictEqual(state.scripts.activeTab, SCRIPT_VIEW_TAB.PREPARATION);
  assert.strictEqual(state.scripts.preparationRunId, "module-run-1");
  assert.strictEqual(state.scripts.preparationModule, "尚无脚本的模块");
  assert.strictEqual(state.scripts.preparationRuns["尚无脚本的模块"], "module-run-1");
  assert.strictEqual(state.scripts.selectedModule, "尚无脚本的模块");
  assert.strictEqual(state.scripts.selectedFile, null);
  assert.strictEqual(state.scripts.modules.length, 0, "opening preparation must not depend on a generated script tree");
  assert.deepStrictEqual(Array.from(state.scripts.selectedFiles), []);
  assert.ok(state.scripts.expandedModules.has("尚无脚本的模块"));
  assert.strictEqual(persistCount, 1);
  assert.strictEqual(sideListRenderCount, 1);
  assert.strictEqual(contentRenderCount, 1);
  assert.deepStrictEqual(workbenchCalls.setRun, ["module-run-1"]);
  assert.strictEqual(workbenchCalls.applyEvent.length, 1);
  assert.deepStrictEqual(workbenchCalls.activate, ["module-run-1"]);
  assert.strictEqual(harness.root.classList.contains("hidden"), false);

  sharedOptions.onStateChange({
    status: "running",
    active: true,
    items: [{ item_id: "module-item", current_revision_id: "revision-1", filename: "module-item.spec.ts" }],
  });
  flushTimeouts();
  assert.strictEqual(treeRefreshCount, 1, "a visible revision update refreshes the script tree");
  assert.deepStrictEqual(treeRefreshViews[0], {
    activeSection: SECTION.SCRIPTS,
    activeTab: SCRIPT_VIEW_TAB.PREPARATION,
    selectedModule: "尚无脚本的模块",
  });

  feature.render();
  feature.render();
  assert.deepStrictEqual(
    workbenchCalls.activate,
    ["module-run-1"],
    "re-rendering an active workbench must not reactivate or create another poll owner",
  );

  sharedOptions.onStateChange({ status: "succeeded", active: true });
  state.scripts.activeTab = SCRIPT_VIEW_TAB.SCRIPT;
  state.activeSection = SECTION.PLANS;
  feature.render();
  assert.ok(workbenchCalls.deactivate >= 1, "a hidden terminal workbench must deactivate");
  const activationCountWhileHidden = workbenchCalls.activate.length;
  await feature.refresh();
  assert.strictEqual(
    workbenchCalls.activate.length,
    activationCountWhileHidden,
    "a hidden refresh must not activate polling or reveal the shared workbench",
  );
  sharedOptions.onStateChange({
    status: "running",
    active: false,
    items: [{ item_id: "module-item", current_revision_id: "revision-2", filename: "module-item.spec.ts" }],
  });
  flushTimeouts();
  assert.strictEqual(treeRefreshCount, 1, "hidden workbenches pause tree refresh instead of navigating the user");
  assert.strictEqual(state.activeSection, SECTION.PLANS);
  assert.strictEqual(state.scripts.activeTab, SCRIPT_VIEW_TAB.SCRIPT);
  assert.strictEqual(state.scripts.selectedModule, "尚无脚本的模块");
  state.activeSection = SECTION.SCRIPTS;
  state.scripts.activeTab = SCRIPT_VIEW_TAB.PREPARATION;
  feature.render();
  sharedOptions.onStateChange({
    status: "running",
    active: true,
    items: [{ item_id: "module-item", current_revision_id: "revision-2", filename: "module-item.spec.ts" }],
  });
  flushTimeouts();
  assert.strictEqual(treeRefreshCount, 2, "returning to the workbench refreshes a revision first observed while hidden");

  await feature.openRun({ run_id: "module-run-2", status: "running" }, "另一个模块");
  assert.deepStrictEqual(
    { ...state.scripts.preparationRuns },
    { "尚无脚本的模块": "module-run-1", "另一个模块": "module-run-2" },
    "opening module B must preserve module A's run",
  );
  state.scripts.selectedModule = "尚无脚本的模块";
  feature.render();
  assert.strictEqual(state.scripts.preparationRunId, "module-run-1");
  assert.strictEqual(state.scripts.preparationModule, "尚无脚本的模块");
  assert.strictEqual(workbenchState.runId, "module-run-1", "returning to module A restores A's workbench run");
  state.scripts.selectedModule = "另一个模块";
  feature.render();
  assert.strictEqual(workbenchState.runId, "module-run-2", "switching back to module B restores B's run");
  feature.reset();
  assert.strictEqual(state.scripts.preparationRunId, "");
  assert.strictEqual(state.scripts.preparationModule, "");
  assert.deepStrictEqual({ ...state.scripts.preparationRuns }, {});
  assert.strictEqual(workbenchState.active, false, "reset must deactivate the shared poll owner");

  await feature.openRun({ run_id: "module-run-3", status: "running" }, "第三个模块");
  feature.destroy();
  assert.strictEqual(workbenchCalls.destroy, 1);
  console.log("module script preparation adapter VM smoke: ok");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
