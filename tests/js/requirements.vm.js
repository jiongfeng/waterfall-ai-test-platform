const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");
const source = fs.readFileSync(
  path.join(appDir, "static/js/features/requirements.js"),
  "utf8",
);
const context = { window: {} };
vm.createContext(context);
vm.runInContext(source, context);

const requirementModulesList = {
  innerHTML: "",
  querySelector: () => null,
  querySelectorAll: () => [],
};
const requirementModuleSummary = { textContent: "" };
const requirementModuleListSummary = { textContent: "" };
const requirementModuleSelectionCount = { textContent: "" };
const state = {
  activeSection: "other",
  isEditing: false,
  requirements: {
    items: [{ requirement_uid: "requirement-1", module_count: 2 }],
    current: {
      requirement_uid: "requirement-1",
      title: "需求一",
    },
    modules: [
      {
        module_uid: "module-1",
        module_name: "登录",
        plan_name: "登录",
        test_points: ["登录成功"],
        requirement_refs: ["REQ-1"],
        open_questions: [],
        generated_plans: [],
      },
      {
        module_uid: "module-2",
        module_name: "购物车",
        plan_name: "购物车",
        test_points: [],
        requirement_refs: [],
        open_questions: [],
        generated_plans: [],
      },
    ],
    selectedUid: "requirement-1",
    activeTab: "modules",
    planGenerationBatches: {},
    selectedModuleUids: new Set(),
    bulkSelectionMode: false,
    bulkDeletingModules: false,
    isDeleting: false,
    deletingUid: "",
    planGenerationRunning: false,
    detailModuleUid: "",
    modulePlanLogs: {},
    generatingModuleUid: "",
    analysisRunning: false,
    analysisLogs: "",
    analysisStatus: "",
    analysisError: "",
    markdown: "",
    html: "",
  },
  generation: { defaultCoverageProfile: "core" },
  plans: {
    selectedModule: null,
    selectedPlanFile: null,
    expandedModules: new Set(),
    activeTab: "content",
  },
};
const elements = {
  requirementModulesList,
  requirementModuleSummary,
  requirementModuleListSummary,
  requirementModuleActions: null,
  requirementModuleBulkActions: null,
  requirementModuleSelectionCount,
  requirementModuleBulkToggle: null,
  requirementModuleBulkCancel: null,
  requirementModuleBulkDelete: null,
  requirementModuleBulkGenerate: null,
  requirementModuleDetailModal: {
    classList: { add: () => {}, remove: () => {} },
  },
  requirementModuleDetailBody: {
    innerHTML: "",
    querySelector: () => null,
  },
  requirementFileInput: { value: "" },
  requirementDeleteButton: {
    disabled: false,
    focus() {},
  },
  requirementDeleteModal: {
    classList: {
      hidden: true,
      add() { this.hidden = true; },
      remove() { this.hidden = false; },
      contains(value) { return value === "hidden" && this.hidden; },
    },
  },
  requirementDeleteName: { textContent: "" },
  requirementDeleteConfirmation: {
    value: "",
    validationMessage: "",
    setCustomValidity(message) { this.validationMessage = message; },
    reportValidity() {},
    focus() {},
  },
  requirementDeleteSubmit: { disabled: true },
};

let requestMode = "delete";
const requests = [];
const notices = [];
let persistenceCalls = 0;
let renderCalls = 0;
const normalizeRequirementModule = (value) =>
  value?.module_uid
    ? {
        test_points: [],
        requirement_refs: [],
        matched_inventory: {},
        open_questions: [],
        write_risk: false,
        baseline_required: false,
        confidence: null,
        planner_prompt: "base prompt",
        status: "candidate",
        generated_plans: [],
        ...value,
      }
    : null;

const feature = context.window.createRequirementsFeature({
  state,
  elements,
  SECTION: { REQUIREMENTS: "requirements", PLANS: "plans" },
  REQUIREMENT_VIEW_TAB: {
    PREVIEW: "preview",
    MODULES: "modules",
    PLAN_GENERATION_BATCH: "batch",
  },
  PLAN_GENERATION_MODE: { MULTIPLE: "multiple" },
  PLAN_VIEW_TAB: { CONTENT: "content" },
  document: {
    createElement() {
      throw new Error("The focused VM paths must not require DOM creation");
    },
  },
  window: {
    confirm: () => true,
    setInterval: () => 1,
    clearInterval: () => {},
    requestAnimationFrame: (callback) => callback(),
    WaterfallI18n: {
      log: (value) => value,
      source: (value) => value,
      localizeDom: () => {},
      markDynamic: () => {},
      t: (key, params = {}) => {
        const messages = {
          "requirements.candidateMeta": "{count} candidates · {timestamp}",
          "requirements.moduleTabCount": "· {count}",
          "requirements.candidateModuleCount": "Candidate modules: {count}",
          "requirements.selectedModuleCount": "Selected: {count}",
        };
        return Object.entries(params).reduce(
          (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
          messages[key] || key,
        );
      },
    },
  },
  fetch: async () => {
    throw new Error("The focused VM paths must not issue fetch");
  },
  TextDecoder,
  FormData: class {
    append() {}
  },
  CSS: { escape: String },
  renderContent: () => {
    renderCalls += 1;
  },
  renderSideList: () => {
    renderCalls += 1;
  },
  getSearchQuery: () => "",
  escapeHtml: String,
  formatTimestampMs: String,
  isPlainObject: (value) =>
    Boolean(value && typeof value === "object" && !Array.isArray(value)),
  getPlanFilenameForProjectLanguage: (planName, moduleName, fallbackStem) =>
    `${planName || fallbackStem || moduleName}.md`,
  normalizeRequirementPlanGenerationBatch: (value) => value,
  persistRequirementPlanGenerationBatches: () => {
    persistenceCalls += 1;
  },
  normalizeRequirementModule,
  isAnyScriptJobRunning: () => false,
  requestJson: async (url, options = {}) => {
    requests.push({ url, options });
    if (requestMode === "requirement-delete") {
      if (
        url === "/api/requirements/requirement-2" &&
        options.method === "DELETE"
      ) {
        return {};
      }
      if (url === "/api/requirements") {
        return { requirements: [] };
      }
      throw new Error(`unexpected requirement delete request: ${url}`);
    }
    if (requestMode === "delete") {
      if (url.includes("/module-2")) {
        throw new Error("无权限");
      }
      return {};
    }
    if (url === "/api/requirements") {
      return {
        requirements: [{ requirement_uid: "requirement-2", title: "需求二" }],
      };
    }
    if (url === "/api/requirements/requirement-2") {
      return {
        requirement: {
          requirement_uid: "requirement-2",
          title: "需求二",
          markdown: "# 需求二",
          html: "<h1>需求二</h1>",
        },
        modules: [{ module_uid: "module-3", module_name: "支付" }],
      };
    }
    throw new Error(`unexpected request: ${url}`);
  },
  encodePathPart: encodeURIComponent,
  setNotice: (message, type) => notices.push({ message, type }),
  parseSseBlock: () => null,
  getCoverageProfile: () => ({ template_prompt: "", label: "核心回归" }),
  ensureGenerationDefaults: async () => {},
  populateCoverageSelect: () => {},
  composeCoveragePrompt: (base, coverage) => `${base}|${coverage}`,
  getProjectRequestHeaders: (headers) => headers,
  persistViewState: () => {
    persistenceCalls += 1;
  },
  formatModuleRepairDuration: () => "",
  createStatusBadge: () => ({}),
  getGenerationStatusInfo: () => ({}),
  openRequirementPlanGenerationModal: async () => {},
  loadPlanModules: async () => {},
  selectPlan: async () => {},
  confirmDiscardEdit: () => true,
  setLoading: () => {},
  normalizeRequirement: (value) => (value?.requirement_uid ? value : null),
});

feature.renderRequirementModuleCounts();
assert.strictEqual(requirementModuleSummary.textContent, "· 2");
assert.strictEqual(requirementModuleListSummary.textContent, "Candidate modules: 2");
assert.strictEqual(requirementModuleSelectionCount.textContent, "Selected: 0");
assert.strictEqual(
  feature.requirementText("requirements.candidateMeta", { count: 2, timestamp: "08/10/2026" }, "fallback"),
  "2 candidates · 08/10/2026",
);

const payload = feature.getRequirementModulePayloadFromItem({
  ...state.requirements.modules[0],
  status: "generated",
});
assert.strictEqual(payload.status, "generated");
assert.deepStrictEqual(JSON.parse(JSON.stringify(payload.test_points)), [
  "登录成功",
]);
assert.strictEqual(
  feature.getRequirementModulePlanTargetPath({
    module_name: "Login and Authentication",
    plan_name: "login-case-index",
  }),
  "specs/Login and Authentication/login-case-index.md",
  "English batch target paths must use the same project-language filename policy as the backend.",
);

feature.mergeRequirementModuleUpdate({
  module_uid: "module-1",
  module_name: "账号登录",
});
assert.strictEqual(state.requirements.modules[0].module_name, "账号登录");
assert.strictEqual(state.requirements.items[0].module_count, 2);

feature.enterRequirementModuleBulkMode();
feature.toggleRequirementModuleSelectAll();
assert.deepStrictEqual(
  Array.from(state.requirements.selectedModuleUids).sort(),
  ["module-1", "module-2"],
);

(async () => {
  await feature.deleteSelectedRequirementModules();
  assert.deepStrictEqual(
    state.requirements.modules.map((item) => item.module_uid),
    ["module-2"],
  );
  assert.ok(notices.at(-1).message.includes("失败 1 个"));

  state.activeSection = "other";
  state.requirements.modules = [
    normalizeRequirementModule({
      module_uid: "module-2",
      module_name: "购物车",
    }),
  ];
  state.requirements.items = [
    { requirement_uid: "requirement-1", module_count: 1 },
  ];
  state.requirements.selectedUid = "requirement-1";
  state.requirements.planGenerationBatches = {};

  let batchState = {
    status: "running",
    logs: "",
    module_name: "购物车",
  };
  batchState = feature.handleRequirementBatchPlanGenerationEvent(
    { event: "log", data: { message: "开始生成" } },
    batchState,
    "requirement-1",
    state.requirements.modules[0],
  );
  batchState = feature.handleRequirementBatchPlanGenerationEvent(
    {
      event: "done",
      data: {
        ok: true,
        plan_filename: "购物车.md",
        plans: [{ plan_filename: "cart-add-item.md" }],
        split: { created: [{ filename: "cart-add-item.md" }] },
        deleted_source: { plan_filename: "购物车.md" },
        requirement_module: {
          module_uid: "module-2",
          module_name: "购物车结算",
          generated_plan: { path: "specs/购物车/购物车.md" },
        },
      },
    },
    batchState,
    "requirement-1",
    state.requirements.modules[0],
  );
  assert.strictEqual(batchState.status, "succeeded");
  assert.deepStrictEqual(
    JSON.parse(JSON.stringify(batchState.plans)),
    [{ plan_filename: "cart-add-item.md" }],
  );
  assert.strictEqual(state.requirements.modules[0].module_name, "购物车结算");
  assert.strictEqual(
    state.requirements.planGenerationBatches["requirement-1"].items[
      "module-2"
    ].status,
    "succeeded",
  );

  const cancelledBatchItem = feature.handleRequirementBatchPlanGenerationEvent(
    { event: "done", data: { ok: false, status: "cancelled", error: "用户终止" } },
    { status: "running", logs: "计划日志\n" },
    "requirement-1",
    state.requirements.modules[0],
  );
  assert.strictEqual(cancelledBatchItem.status, "cancelled");

  feature.handleRequirementAnalysisEvent({
    event: "done",
    data: { ok: false, status: "cancelled", error: "用户终止" },
  });
  assert.strictEqual(state.requirements.analysisStatus, "cancelled");

  requestMode = "load";
  state.requirements.items = [];
  state.requirements.selectedUid = null;
  await feature.loadRequirements();
  assert.strictEqual(state.requirements.selectedUid, "requirement-2");
  assert.strictEqual(state.requirements.current.title, "需求二");
  assert.strictEqual(state.requirements.modules[0].module_uid, "module-3");
  assert.ok(requests.some(({ url }) => url === "/api/requirements"));
  assert.ok(persistenceCalls > 0);
  assert.ok(renderCalls > 0);

  requestMode = "requirement-delete";
  feature.openRequirementDeleteModal();
  assert.strictEqual(elements.requirementDeleteName.textContent, "需求二");
  assert.strictEqual(elements.requirementDeleteSubmit.disabled, true);
  elements.requirementDeleteConfirmation.value = "需求二";
  feature.updateRequirementDeleteSubmitState();
  assert.strictEqual(elements.requirementDeleteSubmit.disabled, false);
  await feature.submitRequirementDelete();
  const deleteRequest = requests.find(
    ({ url, options }) =>
      url === "/api/requirements/requirement-2" &&
      options.method === "DELETE",
  );
  assert.deepStrictEqual(
    JSON.parse(deleteRequest.options.body),
    { confirmation_name: "需求二" },
  );
  assert.strictEqual(state.requirements.current, null);
  assert.strictEqual(elements.requirementDeleteModal.classList.hidden, true);
  assert.ok(notices.at(-1).message.includes("已保留"));
  process.stdout.write("requirements feature VM smoke: ok\n");
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
