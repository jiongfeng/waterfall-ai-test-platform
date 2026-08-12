const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");
const context = { window: {} };
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(appDir, "static/js/features/projects.js"), "utf8"),
  context,
);

function element() {
  const classes = new Set(["hidden"]);
  return {
    value: "",
    textContent: "",
    disabled: false,
    children: [],
    classList: {
      add(value) { classes.add(value); },
      remove(value) { classes.delete(value); },
      toggle(value, force) {
        const enabled = force === undefined ? !classes.has(value) : force;
        if (enabled) classes.add(value);
        else classes.delete(value);
        return enabled;
      },
      contains(value) { return classes.has(value); },
    },
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    replaceChildren() {
      this.children = [];
    },
    setCustomValidity() {},
    reportValidity: () => true,
    focus() {},
  };
}

const state = {
  isEditing: false,
  project: {
    projects: [],
    currentKey: "",
    current: null,
    defaultKey: "",
    workspaceRoot: "",
    isExporting: false,
    isImporting: false,
    isCreating: false,
    isUpdating: false,
    isDeleting: false,
    manageView: "list",
    editingKey: "",
    deletingKey: "",
  },
  requirements: {
    items: [],
    selectedUid: null,
    current: null,
    markdown: "",
    html: "",
    modules: [],
    analysisLogs: "",
    analysisStatus: "",
    analysisError: "",
    analysisRunning: false,
    planGenerationRunning: false,
    generatingModuleUid: "",
    modulePlanLogs: {},
    activeTab: "preview",
    detailModuleUid: "",
    bulkSelectionMode: false,
    selectedModuleUids: new Set(),
    bulkDeletingModules: false,
    planGenerationBatches: {},
  },
  generation: {
    source: "plans",
    requirementUid: "",
    requirementModuleUid: "",
  },
  plans: {
    modules: [],
    expandedModules: new Set(),
    selectedModule: null,
    selectedPlanFile: null,
    currentMarkdown: "",
    currentHtml: "",
    filePath: "",
    asset: null,
    revisions: [],
    relatedScripts: [],
    generationRecords: {},
    scriptGenerationRecords: {},
    scriptGenerationBatches: {},
    bulkSelectionMode: false,
    selectedPlanFiles: new Set(),
  },
  scripts: {
    modules: [],
    expandedModules: new Set(),
    selectedModule: null,
    selectedFile: null,
    currentContent: "",
    filePath: "",
    asset: null,
    revisions: [],
    sourcePlan: null,
    recentResults: [],
    runRecords: {},
    repairRecords: {},
    moduleExecutionRecords: {},
    moduleRepairBatches: {},
    bulkSelectionMode: false,
    selectedFiles: new Set(),
  },
  testSuites: {
    suites: [],
    selectedSuiteId: null,
    selectedModule: "__all__",
    executionRecords: {},
    availableModules: [],
    addModalModule: "__all__",
    selectedScriptKeys: new Set(),
  },
  testSuiteExecution: {
    progressModalVisible: false,
    progressModalSuiteId: "",
  },
  projectSettings: {
    loaded: false,
    output: "",
    activeTab: "basic",
    setup: {},
  },
};
const elements = {
  projectSelect: element(),
  manageProjectButton: element(),
  testSuiteProgressModal: element(),
};
for (const name of [
  "projectManageModal",
  "projectManageClose",
  "projectManageBack",
  "projectManageTitle",
  "projectManageDescription",
  "projectManageFeedback",
  "projectManageListView",
  "projectManageSearch",
  "projectManageCreate",
  "projectManageImport",
  "projectManageSummary",
  "projectManageTableBody",
  "projectManageEmpty",
  "projectCreateView",
  "projectImportView",
  "projectEditView",
  "projectCreateFooter",
  "projectImportFooter",
  "projectEditFooter",
  "newProjectKey",
  "newProjectName",
  "newProjectSpecsDir",
  "newProjectTestsDir",
  "newProjectDescription",
  "projectCreateWorkspaceHint",
  "projectImportWorkspaceHint",
  "projectImportFile",
  "importProjectKey",
  "importProjectName",
  "importProjectSpecsDir",
  "importProjectTestsDir",
  "importProjectDescription",
  "projectCreateSubmit",
  "projectCreateCancel",
  "projectImportSubmit",
  "projectImportCancel",
  "projectEditSubmit",
  "projectEditCancel",
  "editProjectKey",
  "editProjectName",
  "editProjectDescription",
  "projectDeleteModal",
  "projectDeleteClose",
  "projectDeleteCancel",
  "projectDeleteSubmit",
  "projectDeleteName",
  "projectDeleteKey",
  "projectDeleteConfirmation",
]) {
  elements[name] = element();
}
elements.projectManageTableBody.innerHTML = "";
elements.projectManageTableBody.querySelector = () => null;

const stored = new Map();
const calls = { render: 0, hydrate: 0, load: 0, history: 0 };
const project = {
  project_id: 1,
  project_key: "alpha",
  name: "Alpha",
  is_default: true,
};
const feature = context.window.createProjectsFeature({
  state,
  elements,
  CURRENT_PROJECT_STORAGE_KEY: "current-project",
  PROJECT_SETTINGS_VIEW_TAB: { BASIC: "basic" },
  REQUIREMENT_VIEW_TAB: { PREVIEW: "preview" },
  TEST_SUITE_ALL_MODULE: "__all__",
  document: {
    body: { appendChild() {} },
    createElement: () => element(),
  },
  window: {
    requestAnimationFrame: (callback) => callback(),
    confirm: () => true,
    location: { href: "" },
    URL: { createObjectURL: () => "blob:test", revokeObjectURL() {} },
  },
  fetch: async () => {
    throw new Error("focused project VM paths must not fetch");
  },
  FormData: class {
    append() {}
  },
  admin: { hasProjectSettingsPermission: () => true },
  testSuites: {
    resetTestSuiteExecutionHistory: () => {
      calls.history += 1;
    },
  },
  jobs: { isAnyScriptJobRunning: () => false },
  getStoredProjectKey: () => stored.get("current-project") || "",
  writeStorageItem: (key, value) => stored.set(key, value),
  isPlainObject: (value) =>
    Boolean(value && typeof value === "object" && !Array.isArray(value)),
  getProjectRequestHeaders: (headers) => headers,
  requestJson: async (url) => {
    assert.strictEqual(url, "/api/projects");
    return {
      projects: [project],
      current_project: project,
      default_project: project,
      project_workspace_root: "/workspace",
    };
  },
  readFetchError: async () => ({}),
  getDownloadFilename: () => "project.zip",
  confirmDiscardEdit: () => true,
  hydratePlatformRecords: async () => {
    calls.hydrate += 1;
  },
  loadActiveSection: async () => {
    calls.load += 1;
  },
  renderSideList: () => {
    calls.render += 1;
  },
  renderContent: () => {
    calls.render += 1;
  },
  setNotice: () => {},
  escapeHtml: (value) => String(value ?? ""),
});

(async () => {
  assert.strictEqual(
    feature.normalizeProject({
      key: "legacy",
      name: "",
      target_system: { base_url: "https://example.com" },
    }).project_key,
    "legacy",
  );
  await feature.loadProjects();
  assert.strictEqual(state.project.currentKey, "alpha");
  assert.strictEqual(state.project.current.name, "Alpha");
  assert.strictEqual(stored.get("current-project"), "alpha");
  assert.strictEqual(elements.projectSelect.children.length, 1);

  state.project.projects.push({
    project_key: "beta",
    key: "beta",
    name: "Beta",
  });
  await feature.switchProject("beta");
  assert.strictEqual(state.project.currentKey, "beta");
  assert.strictEqual(state.plans.selectedModule, null);
  assert.strictEqual(state.requirements.activeTab, "preview");
  assert.strictEqual(state.projectSettings.activeTab, "basic");
  assert.strictEqual(calls.history, 1);
  assert.strictEqual(calls.hydrate, 1);
  assert.strictEqual(calls.load, 1);
  assert.ok(calls.render >= 2);
  process.stdout.write("projects feature VM smoke: ok\n");
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
