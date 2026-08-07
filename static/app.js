const SECTION = {
  REQUIREMENTS: "requirements",
  PLANS: "plans",
  SCRIPTS: "scripts",
  TEST_SUITES: "testSuites",
  AGENT: "agent",
  PROJECT_SETTINGS: "projectSettings",
  USERS: "users",
  ROLES: "roles",
};

// Feature modules receive this semantic translator explicitly.  Do not use
// source() for new UI: source() only exists as a safe bridge for old literals.
const t = (key, params = {}) => window.WaterfallI18n?.t(key, params) || key;

const MENU_ITEMS = [
  { section: SECTION.REQUIREMENTS, permission: "menu.requirements", label: "需求", title: "需求" },
  { section: SECTION.PLANS, permission: "menu.plans", label: "计划", title: "测试计划" },
  { section: SECTION.SCRIPTS, permission: "menu.scripts", label: "脚本", title: "测试脚本" },
  { section: SECTION.TEST_SUITES, permission: "menu.testSuites", label: "测试集", title: "测试集" },
  { section: SECTION.AGENT, permission: "menu.agent", label: "Agent", title: "Agent 自动测试" },
  { section: SECTION.PROJECT_SETTINGS, permission: "menu.projectSettings", label: "配置", title: "项目配置" },
  { section: SECTION.USERS, permission: "menu.users", label: "用户", title: "用户管理" },
  { section: SECTION.ROLES, permission: "menu.roles", label: "角色", title: "角色管理" },
];

const PLAN_VIEW_TAB = {
  CONTENT: "content",
  PLAN_GENERATION: "planGeneration",
  SCRIPT_GENERATION: "scriptGeneration",
  RELATED_SCRIPTS: "relatedScripts",
};

const PLAN_GENERATION_MODE = {
  MULTIPLE: "multiple",
  SINGLE: "single",
};

const COVERAGE_POLICY_START = "<<<COVERAGE_POLICY_START>>>";
const COVERAGE_POLICY_END = "<<<COVERAGE_POLICY_END>>>";
const DEFAULT_COVERAGE_PROFILE = "core";

let SCRIPT_PROMPT_FIXED_TEMPLATE = `@playwright-test-generator
请根据 specs/<模块名>/<测试计划文件名> 生成Playwright测试文件。
每个测试文件里面只能有一个测试，测试文件名字必须为中文业务测试名.spec.ts，文件名主体不能包含英文字母。
平台会在提交任务时提供候选脚本路径，请只把生成结果写入候选路径；不要直接修改正式 tests 文件。`;

let SCRIPT_PROMPT_NOTE_DEFAULT = "注意：每个Step下面尽量生成实际代码，如果实在没有代码，需要说明为什么。";

const SCRIPT_VIEW_TAB = {
  SCRIPT: "script",
  EXECUTION: "execution",
  REPAIR: "repair",
};

const TEST_SUITE_VIEW_TAB = {
  SCRIPTS: "scripts",
  EXECUTION: "execution",
};

const PROJECT_SETTINGS_VIEW_TAB = {
  BASIC: "basic",
  SETUP: "setup",
};

const REQUIREMENT_VIEW_TAB = {
  PREVIEW: "preview",
  MODULES: "modules",
  PLAN_GENERATION_BATCH: "planGenerationBatch",
};

const EXECUTION_MODE = {
  BATCH: "batch",
  SERIAL_PER_FILE: "serial_per_file",
};

let SCRIPT_RUN_PROMPT_FIXED_TEMPLATE = `@playwright-test-healer
请根据测试计划 specs/<模块名>/<模块名>.md, 运行并修复 tests/<模块名>/<测试脚本名>.spec.ts`;

let SCRIPT_RUN_PROMPT_NOTE_DEFAULT = `要求：
1. 不允许删除或注释任何 STEP。
2. 保留执行视频`;

const SCRIPT_RUN_RECORDS_STORAGE_KEY = "test-plan-viewer.scriptRunRecords.v1";
const SCRIPT_REPAIR_RECORDS_STORAGE_KEY = "test-plan-viewer.scriptRepairRecords.v1";
const MODULE_EXECUTION_RECORDS_STORAGE_KEY = "test-plan-viewer.moduleExecutionRecords.v1";
const MODULE_REPAIR_BATCHES_STORAGE_KEY = "test-plan-viewer.moduleRepairBatches.v1";
const PLAN_GENERATION_RECORDS_STORAGE_KEY = "test-plan-viewer.planGenerationRecords.v1";
const REQUIREMENT_PLAN_GENERATION_BATCHES_STORAGE_KEY =
  "test-plan-viewer.requirementPlanGenerationBatches.v1";
const SCRIPT_GENERATION_RECORDS_STORAGE_KEY = "test-plan-viewer.scriptGenerationRecords.v1";
const PLAN_SCRIPT_GENERATION_BATCHES_STORAGE_KEY = "test-plan-viewer.planScriptGenerationBatches.v1";
const TEST_SUITE_EXECUTION_RECORDS_STORAGE_KEY = "test-plan-viewer.testSuiteExecutionRecords.v1";
const VIEW_STATE_STORAGE_KEY = "test-plan-viewer.viewState.v1";
const CURRENT_PROJECT_STORAGE_KEY = "test-plan-viewer.currentProject.v1";
const CJK_NAME_PATTERN = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;
const ASCII_LETTER_PATTERN = /[A-Za-z]/;
const ASCII_LETTERS_GLOBAL_PATTERN = /[A-Za-z]+/g;
const ARTIFACT_FILENAME_UNSAFE_PATTERN = /[\\/<>\:"|?*\x00]+/g;
const SCRIPT_RUN_RECORD_LOG_LIMIT = 120000;
const PLATFORM_RECORD_SAVE_DEBOUNCE_MS = 300;
const PLATFORM_RECORD_BUCKET = {
  VIEW_STATE: "view_state",
  SCRIPT_RUN: "script_run_records",
  SCRIPT_REPAIR: "script_repair_records",
  MODULE_EXECUTION: "module_execution_records",
  MODULE_REPAIR_BATCH: "module_repair_batches",
  PLAN_GENERATION: "plan_generation_records",
  REQUIREMENT_PLAN_GENERATION_BATCH: "requirement_plan_generation_batches",
  SCRIPT_GENERATION: "script_generation_records",
  PLAN_SCRIPT_GENERATION_BATCH: "plan_script_generation_batches",
  TEST_SUITE_EXECUTION: "test_suite_execution_records",
};
const TEST_SUITE_ALL_MODULE = "__all__";

function safeJsonParse(value, fallback) {
  if (!value) {
    return fallback;
  }

  try {
    return JSON.parse(value);
  } catch (error) {
    return fallback;
  }
}

function readStorageItem(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (error) {
    return null;
  }
}

function writeStorageItem(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (error) {
    // Ignore storage quota or privacy-mode errors; the in-memory record still works for this session.
  }
}

function getStoredProjectKey() {
  return (readStorageItem(CURRENT_PROJECT_STORAGE_KEY) || "").trim();
}

function getCurrentProjectKey() {
  return state.project.currentKey || getStoredProjectKey() || "";
}

const apiClient = createApiClient({
  getProjectKey: getCurrentProjectKey,
  onUnauthorized(data) {
    window.location.href = data.redirect || "/login";
  },
});
const {
  getProjectHeaders: getProjectRequestHeaders,
  requestJson,
  readFetchError,
  getDownloadFilename,
} = apiClient;
const timerRuntime = createTimerRuntime(window);

const testSuiteResultHelpers = createTestSuiteResultHelpers({ getSuiteScriptKey });
const { finalizeTestSuiteScriptResults } = testSuiteResultHelpers;

const platformRecordStore = createPlatformRecordStore({
  fetchImpl: (...args) => fetch(...args),
  getProjectHeaders: getProjectRequestHeaders,
  safeJsonParse,
  readStorageItem,
  writeStorageItem,
  normalizeTestSuiteItem,
  getSuiteScriptKey,
  finalizeTestSuiteScriptResults,
  isPlainObject,
  executionMode: EXECUTION_MODE,
  storageKeys: {
    SCRIPT_RUN: SCRIPT_RUN_RECORDS_STORAGE_KEY,
    SCRIPT_REPAIR: SCRIPT_REPAIR_RECORDS_STORAGE_KEY,
    MODULE_EXECUTION: MODULE_EXECUTION_RECORDS_STORAGE_KEY,
    MODULE_REPAIR_BATCH: MODULE_REPAIR_BATCHES_STORAGE_KEY,
    PLAN_GENERATION: PLAN_GENERATION_RECORDS_STORAGE_KEY,
    REQUIREMENT_PLAN_GENERATION_BATCH: REQUIREMENT_PLAN_GENERATION_BATCHES_STORAGE_KEY,
    SCRIPT_GENERATION: SCRIPT_GENERATION_RECORDS_STORAGE_KEY,
    PLAN_SCRIPT_GENERATION_BATCH: PLAN_SCRIPT_GENERATION_BATCHES_STORAGE_KEY,
    TEST_SUITE_EXECUTION: TEST_SUITE_EXECUTION_RECORDS_STORAGE_KEY,
  },
  logLimit: SCRIPT_RUN_RECORD_LOG_LIMIT,
  saveDebounceMs: PLATFORM_RECORD_SAVE_DEBOUNCE_MS,
  timerHost: window,
});
const {
  queuePlatformRecordSave,
  normalizeExecutionModeValue,
  getExecutionModeLabel,
  normalizeScriptRunRecord,
  normalizeScriptRepairRecord,
  normalizeModuleExecutionRecord,
  normalizeTestSuiteExecutionRecord,
  normalizeModuleRepairBatch,
  normalizePlanScriptGenerationBatch,
  normalizeRequirementPlanGenerationBatch,
  normalizePlanGenerationRecord,
  normalizePlanScriptGenerationRecord,
  loadScriptRunRecordsFromStorage,
  loadScriptRepairRecordsFromStorage,
  loadModuleExecutionRecordsFromStorage,
  loadTestSuiteExecutionRecordsFromStorage,
  loadModuleRepairBatchesFromStorage,
  loadPlanScriptGenerationBatchesFromStorage,
  loadRequirementPlanGenerationBatchesFromStorage,
  loadPlanGenerationRecordsFromStorage,
  loadPlanScriptGenerationRecordsFromStorage,
} = platformRecordStore;

function getSuiteScriptKey(moduleName, filename) {
  if (!moduleName || !filename) {
    return "";
  }

  return `${moduleName}/${filename}`;
}

function normalizeTestSuiteItem(item) {
  if (!item || typeof item !== "object") {
    return null;
  }

  const moduleName = typeof item.module_name === "string" ? item.module_name.trim() : "";
  const filename = typeof item.filename === "string" ? item.filename.trim() : "";
  if (!moduleName || !filename) {
    return null;
  }

  return {
    item_id: Number(item.item_id || item.id) || null,
    script_asset_id: Number(item.script_asset_id) || null,
    module_name: moduleName,
    filename,
    display_name:
      typeof item.display_name === "string" && item.display_name.trim()
        ? item.display_name.trim()
        : stripSpecSuffix(filename),
    path: typeof item.path === "string" ? item.path : "",
    sort_order: Number(item.sort_order) || 0,
  };
}

function normalizeTestSuite(suite) {
  if (!suite || typeof suite !== "object") {
    return null;
  }

  const id = typeof suite.id === "string" && suite.id.trim() ? suite.id.trim() : "";
  const name = typeof suite.name === "string" && suite.name.trim() ? suite.name.trim() : "";
  if (!id || !name) {
    return null;
  }

  const seen = new Set();
  const items = (Array.isArray(suite.items) ? suite.items : [])
    .map(normalizeTestSuiteItem)
    .filter(Boolean)
    .filter((item) => {
      const key = getSuiteScriptKey(item.module_name, item.filename);
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    })
    .sort((left, right) => (left.sort_order || 0) - (right.sort_order || 0));

  return {
    id,
    suite_uid: typeof suite.suite_uid === "string" && suite.suite_uid.trim() ? suite.suite_uid.trim() : id,
    suite_id: Number(suite.suite_id) || null,
    name,
    description: typeof suite.description === "string" ? suite.description : "",
    created_at: Number(suite.created_at) || Date.now(),
    updated_at: Number(suite.updated_at) || Number(suite.created_at) || Date.now(),
    items,
  };
}

function normalizeTestSuiteExecutionArtifact(artifact) {
  if (!artifact || typeof artifact !== "object") {
    return null;
  }
  const url = typeof artifact.url === "string" ? artifact.url : "";
  const path = typeof artifact.path === "string" ? artifact.path : "";
  const relativePath = typeof artifact.relative_path === "string" ? artifact.relative_path : "";
  if (!url && !path && !relativePath) {
    return null;
  }

  return {
    artifact_id: Number(artifact.artifact_id) || null,
    artifact_type: typeof artifact.artifact_type === "string" ? artifact.artifact_type : "",
    path,
    relative_path: relativePath,
    url,
    size: Number(artifact.size) || null,
    created_at: Number(artifact.created_at) || null,
  };
}

function normalizeTestSuiteExecutionResult(result) {
  if (!result || typeof result !== "object") {
    return null;
  }

  const moduleName = typeof result.module_name === "string" ? result.module_name : "";
  const filename = typeof result.filename === "string" ? result.filename : "";
  const scriptKey =
    typeof result.script_key === "string" && result.script_key
      ? result.script_key
      : moduleName && filename
        ? getSuiteScriptKey(moduleName, filename)
        : "";

  return {
    result_id: Number(result.result_id) || null,
    run_id: typeof result.run_id === "string" ? result.run_id : "",
    order_index: Number(result.order_index) || 0,
    module_name: moduleName,
    filename,
    script_key: scriptKey,
    script_path: typeof result.script_path === "string" ? result.script_path : "",
    script_name:
      typeof result.script_name === "string" && result.script_name.trim()
        ? result.script_name.trim()
        : filename
          ? stripSpecSuffix(filename)
          : "",
    status: typeof result.status === "string" ? result.status : "unknown",
    error_message: typeof result.error_message === "string" ? result.error_message : "",
    stdout_tail: typeof result.stdout_tail === "string" ? result.stdout_tail : "",
    started_at: Number(result.started_at) || null,
    finished_at: Number(result.finished_at) || null,
    updated_at: Number(result.updated_at) || null,
    report: normalizeTestSuiteExecutionArtifact(result.report),
    video: normalizeTestSuiteExecutionArtifact(result.video),
  };
}

function normalizeTestSuiteExecutionRun(record) {
  if (!record || typeof record !== "object") {
    return null;
  }
  const runId = typeof record.run_id === "string" && record.run_id ? record.run_id : "";
  if (!runId) {
    return null;
  }

  return {
    run_id: runId,
    run_type: typeof record.run_type === "string" ? record.run_type : "",
    status: typeof record.status === "string" ? record.status : "",
    execution_mode: typeof record.execution_mode === "string" ? record.execution_mode : "",
    database_reset_mode: typeof record.database_reset_mode === "string" ? record.database_reset_mode : "",
    suite_id: typeof record.suite_id === "string" ? record.suite_id : "",
    command: typeof record.command === "string" ? record.command : "",
    git_commit_sha: typeof record.git_commit_sha === "string" ? record.git_commit_sha : "",
    summary: record.summary && typeof record.summary === "object" && !Array.isArray(record.summary) ? record.summary : {},
    total_files: Number(record.total_files) || 0,
    completed_files: Number(record.completed_files) || 0,
    error: typeof record.error === "string" ? record.error : "",
    started_at: Number(record.started_at) || null,
    finished_at: Number(record.finished_at) || null,
    created_at: Number(record.created_at) || null,
    updated_at: Number(record.updated_at) || null,
    report: normalizeTestSuiteExecutionArtifact(record.report),
    results: (Array.isArray(record.results) ? record.results : [])
      .map(normalizeTestSuiteExecutionResult)
      .filter(Boolean)
      .sort((left, right) => (left.order_index || 0) - (right.order_index || 0)),
  };
}

function normalizeTestSuiteExecutionRunList(records) {
  return (Array.isArray(records) ? records : []).map(normalizeTestSuiteExecutionRun).filter(Boolean);
}

function normalizeRequirement(requirement) {
  if (!requirement || typeof requirement !== "object") {
    return null;
  }
  const uid = typeof requirement.requirement_uid === "string" ? requirement.requirement_uid.trim() : "";
  if (!uid) {
    return null;
  }
  return {
    id: Number(requirement.id) || null,
    requirement_uid: uid,
    title:
      typeof requirement.title === "string" && requirement.title.trim()
        ? requirement.title.trim()
        : typeof requirement.filename === "string"
          ? stripMarkdownSuffix(requirement.filename)
          : uid,
    filename: typeof requirement.filename === "string" ? requirement.filename : "",
    file_path: typeof requirement.file_path === "string" ? requirement.file_path : "",
    content_sha256: typeof requirement.content_sha256 === "string" ? requirement.content_sha256 : "",
    status: typeof requirement.status === "string" ? requirement.status : "",
    source_type: typeof requirement.source_type === "string" ? requirement.source_type : "",
    created_by: typeof requirement.created_by === "string" ? requirement.created_by : "",
    created_at: Number(requirement.created_at) || null,
    updated_at: Number(requirement.updated_at) || null,
    module_count: Number(requirement.module_count) || 0,
    markdown: typeof requirement.markdown === "string" ? requirement.markdown : "",
    html: typeof requirement.html === "string" ? requirement.html : "",
  };
}

function normalizeRequirementModule(moduleItem) {
  if (!moduleItem || typeof moduleItem !== "object") {
    return null;
  }
  const uid = typeof moduleItem.module_uid === "string" ? moduleItem.module_uid.trim() : "";
  const moduleName = typeof moduleItem.module_name === "string" ? moduleItem.module_name.trim() : "";
  if (!uid || !moduleName) {
    return null;
  }
  const generatedPlan = isPlainObject(moduleItem.generated_plan) ? moduleItem.generated_plan : null;
  const generatedPlans = Array.isArray(moduleItem.generated_plans) ? moduleItem.generated_plans : generatedPlan ? [generatedPlan] : [];
  return {
    id: Number(moduleItem.id) || null,
    module_uid: uid,
    module_name: moduleName,
    plan_name:
      typeof moduleItem.plan_name === "string" && moduleItem.plan_name.trim()
        ? moduleItem.plan_name.trim()
        : moduleName,
    status: typeof moduleItem.status === "string" ? moduleItem.status : "candidate",
    confidence: Number.isFinite(Number(moduleItem.confidence)) ? Number(moduleItem.confidence) : null,
    business_goal: typeof moduleItem.business_goal === "string" ? moduleItem.business_goal : "",
    requirement_refs: Array.isArray(moduleItem.requirement_refs) ? moduleItem.requirement_refs : [],
    test_points: Array.isArray(moduleItem.test_points) ? moduleItem.test_points : [],
    matched_inventory: isPlainObject(moduleItem.matched_inventory) || Array.isArray(moduleItem.matched_inventory)
      ? moduleItem.matched_inventory
      : {},
    open_questions: Array.isArray(moduleItem.open_questions) ? moduleItem.open_questions : [],
    baseline_required: Boolean(moduleItem.baseline_required),
    write_risk: Boolean(moduleItem.write_risk),
    planner_prompt: typeof moduleItem.planner_prompt === "string" ? moduleItem.planner_prompt : "",
    source_job_id: typeof moduleItem.source_job_id === "string" ? moduleItem.source_job_id : "",
    generated_plan_asset_id: Number(moduleItem.generated_plan_asset_id) || null,
    generated_plan: generatedPlan,
    generated_plans: generatedPlans,
    created_at: Number(moduleItem.created_at) || null,
    updated_at: Number(moduleItem.updated_at) || null,
    generation_status: "",
    generation_error: "",
  };
}

function normalizeViewStateRecord(parsed) {
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return {};
  }

  const activeSection = Object.values(SECTION).includes(parsed.activeSection) ? parsed.activeSection : SECTION.PLANS;
  const planActiveTab = Object.values(PLAN_VIEW_TAB).includes(parsed.planActiveTab)
    ? parsed.planActiveTab
    : PLAN_VIEW_TAB.CONTENT;
  const activeTab = Object.values(SCRIPT_VIEW_TAB).includes(parsed.scriptActiveTab)
    ? parsed.scriptActiveTab
    : SCRIPT_VIEW_TAB.SCRIPT;
  const testSuiteActiveTab = Object.values(TEST_SUITE_VIEW_TAB).includes(parsed.testSuiteActiveTab)
    ? parsed.testSuiteActiveTab
    : TEST_SUITE_VIEW_TAB.SCRIPTS;

  return {
    activeSection,
    requirementUid: typeof parsed.requirementUid === "string" ? parsed.requirementUid : null,
    planActiveTab,
    planModule: typeof parsed.planModule === "string" ? parsed.planModule : null,
    planFile: typeof parsed.planFile === "string" ? parsed.planFile : null,
    scriptModule: typeof parsed.scriptModule === "string" ? parsed.scriptModule : null,
    scriptFile: typeof parsed.scriptFile === "string" ? parsed.scriptFile : null,
    scriptActiveTab: activeTab,
    testSuiteId: typeof parsed.testSuiteId === "string" ? parsed.testSuiteId : null,
    testSuiteModule:
      typeof parsed.testSuiteModule === "string" && parsed.testSuiteModule
        ? parsed.testSuiteModule
        : TEST_SUITE_ALL_MODULE,
    testSuiteActiveTab,
    expandedPlanModules: Array.isArray(parsed.expandedPlanModules)
      ? parsed.expandedPlanModules.filter((item) => typeof item === "string")
      : [],
    expandedModules: Array.isArray(parsed.expandedModules)
      ? parsed.expandedModules.filter((item) => typeof item === "string")
      : [],
  };
}

function loadViewStateFromStorage() {
  return normalizeViewStateRecord(safeJsonParse(readStorageItem(VIEW_STATE_STORAGE_KEY), {}));
}

const initialViewState = loadViewStateFromStorage();

const state = {
  activeSection: initialViewState.activeSection || SECTION.PLANS,
  isEditing: false,
  isSaving: false,
  project: {
    projects: [],
    currentKey: getStoredProjectKey(),
    current: null,
    defaultKey: "",
    workspaceRoot: "",
    isExporting: false,
    isImporting: false,
  },
  requirements: {
    items: [],
    selectedUid: initialViewState.requirementUid || null,
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
    activeTab: REQUIREMENT_VIEW_TAB.PREVIEW,
    detailModuleUid: "",
    bulkSelectionMode: false,
    selectedModuleUids: new Set(),
    bulkDeletingModules: false,
    planGenerationBatches: loadRequirementPlanGenerationBatchesFromStorage(),
  },
  plans: {
    modules: [],
    expandedModules: new Set(initialViewState.expandedPlanModules || []),
    selectedModule: initialViewState.planModule || null,
    selectedPlanFile: initialViewState.planFile || null,
    currentMarkdown: "",
    currentHtml: "",
    filePath: "",
    asset: null,
    revisions: [],
    relatedScripts: [],
    activeTab: initialViewState.planActiveTab || PLAN_VIEW_TAB.CONTENT,
    generationRecords: loadPlanGenerationRecordsFromStorage(),
    scriptGenerationRecords: loadPlanScriptGenerationRecordsFromStorage(),
    scriptGenerationBatches: loadPlanScriptGenerationBatchesFromStorage(),
    bulkSelectionMode: false,
    selectedPlanFiles: new Set(),
    bulkDeletingPlans: false,
  },
  generation: {
    defaultsLoaded: false,
    promptTemplate: "",
    targetPathTemplate: "",
    previousModuleName: "<模块名>",
    mode: PLAN_GENERATION_MODE.MULTIPLE,
    moduleNameMode: "select",
    jobId: null,
    pollTimer: null,
    durationTimer: null,
    isRunning: false,
    source: "plans",
    requirementUid: "",
    requirementModuleUid: "",
    coverageProfiles: [],
    defaultCoverageProfile: DEFAULT_COVERAGE_PROFILE,
    coverageProfile: DEFAULT_COVERAGE_PROFILE,
    basePrompt: "",
    defaultComposedPrompt: "",
    autoProfilePlanName: false,
  },
  scriptGeneration: {
    isRunning: false,
    durationTimer: null,
  },
  scriptExecution: {
    isRunning: false,
  },
  moduleExecution: {
    isRunning: false,
  },
  testSuiteExecution: {
    isRunning: false,
    progressModalVisible: false,
    progressModalSuiteId: "",
  },
  projectSettings: {
    loaded: false,
    isSaving: false,
    isGeneratingSeed: false,
    isTestingSeed: false,
    seedScriptPath: "tests/seed/seed.spec.ts",
    targetSystem: {
      base_url: "",
      login_url: "/login",
      username: "",
      password: "",
    },
    databaseBaseline: {
      enabled: false,
      mode: "command",
      working_directory: "",
      marker_path: "",
      backup_command: "",
      restore_command: "",
      test_command: "",
      timeout_seconds: 1800,
      database_path: "",
      baseline_path: "",
    },
    planGeneration: {
      default_coverage_profile: DEFAULT_COVERAGE_PROFILE,
    },
    coverageProfiles: [],
    output: "",
    activeTab: PROJECT_SETTINGS_VIEW_TAB.BASIC,
    setup: {
      loaded: false,
      isLoading: false,
      isSaving: false,
      isRunning: false,
      error: "",
      notice: "",
      noticeType: "",
      scripts: [],
      bindings: [],
      runs: [],
      selectedScriptUid: "",
      selectedRunUid: "",
      scriptQuery: "",
      scriptStatusFilter: "all",
      scriptModalOpen: false,
      scriptDraft: null,
      scriptDraftSourceUid: "",
      draftBinding: null,
      draftEnvironmentRows: [],
      runDetailModalOpen: false,
      runDetailScriptUid: "",
    },
  },
  executionModeDialog: {
    resolver: null,
    target: "",
  },
  scriptRecording: {
    isRunning: false,
  },
  scriptRun: {
    isRunning: false,
    durationTimer: null,
  },
  moduleRepair: {
    isRunning: false,
    cancelRequested: false,
    currentController: null,
    currentJobId: "",
    activeFilename: "",
    moduleName: "",
  },
  scripts: {
    modules: [],
    expandedModules: new Set(initialViewState.expandedModules || []),
    selectedModule: initialViewState.scriptModule || null,
    selectedFile: initialViewState.scriptFile || null,
    currentContent: "",
    filePath: "",
    asset: null,
    revisions: [],
    sourcePlan: null,
    recentResults: [],
    activeTab: initialViewState.scriptActiveTab || SCRIPT_VIEW_TAB.SCRIPT,
    runRecords: loadScriptRunRecordsFromStorage(),
    repairRecords: loadScriptRepairRecordsFromStorage(),
    moduleExecutionRecords: loadModuleExecutionRecordsFromStorage(),
    moduleRepairBatches: loadModuleRepairBatchesFromStorage(),
    bulkSelectionMode: false,
    selectedFiles: new Set(),
    bulkDeletingScripts: false,
  },
  testSuites: {
    suites: [],
    selectedSuiteId: initialViewState.testSuiteId || null,
    selectedModule: initialViewState.testSuiteModule || TEST_SUITE_ALL_MODULE,
    activeTab: initialViewState.testSuiteActiveTab || TEST_SUITE_VIEW_TAB.SCRIPTS,
    executionRecords: loadTestSuiteExecutionRecordsFromStorage(),
    executionHistory: {
      records: [],
      selectedRunId: null,
      loadedSuiteId: null,
      isLoading: false,
      error: "",
    },
    availableModules: [],
    addModalModule: TEST_SUITE_ALL_MODULE,
    renamingSuiteId: null,
    selectedScriptKeys: new Set(),
  },
  testSuiteVideoModal: {
    video: null,
    title: "",
  },
  auth: {
    user: null,
    isAdmin: false,
    permissions: new Set(),
    menus: [],
  },
  admin: {
    users: [],
    roles: [],
    permissions: [],
    userEditingId: null,
    passwordResetUserId: null,
    roleEditingId: null,
    usersLoaded: false,
    rolesLoaded: false,
    isSaving: false,
  },
  agent: {
    controller: null,
  },
};

function persistViewState() {
  const record = {
    activeSection: state.activeSection,
    requirementUid: state.requirements.selectedUid,
    planActiveTab: state.plans.activeTab,
    planModule: state.plans.selectedModule,
    planFile: state.plans.selectedPlanFile,
    expandedPlanModules: Array.from(state.plans.expandedModules),
    scriptModule: state.scripts.selectedModule,
    scriptFile: state.scripts.selectedFile,
    scriptActiveTab: state.scripts.activeTab,
    expandedModules: Array.from(state.scripts.expandedModules),
    testSuiteId: state.testSuites.selectedSuiteId,
    testSuiteModule: state.testSuites.selectedModule,
    testSuiteActiveTab: state.testSuites.activeTab,
    updated_at: Date.now(),
  };

  writeStorageItem(VIEW_STATE_STORAGE_KEY, JSON.stringify(record));
  queuePlatformRecordSave(PLATFORM_RECORD_BUCKET.VIEW_STATE, "default", record);
}

function persistTestSuiteExecutionRecords(recordKey = null) {
  platformRecordStore.persistRecordMap({
    records: state.testSuites.executionRecords,
    normalize: normalizeTestSuiteExecutionRecord,
    storageKey: TEST_SUITE_EXECUTION_RECORDS_STORAGE_KEY,
    bucket: PLATFORM_RECORD_BUCKET.TEST_SUITE_EXECUTION,
    recordKey,
  });
}

function persistScriptRunRecords(recordKey = null) {
  platformRecordStore.persistRecordMap({
    records: state.scripts.runRecords,
    normalize: normalizeScriptRunRecord,
    storageKey: SCRIPT_RUN_RECORDS_STORAGE_KEY,
    bucket: PLATFORM_RECORD_BUCKET.SCRIPT_RUN,
    recordKey,
  });
}

function persistScriptRepairRecords(recordKey = null) {
  platformRecordStore.persistRecordMap({
    records: state.scripts.repairRecords,
    normalize: normalizeScriptRepairRecord,
    storageKey: SCRIPT_REPAIR_RECORDS_STORAGE_KEY,
    bucket: PLATFORM_RECORD_BUCKET.SCRIPT_REPAIR,
    recordKey,
  });
}

function persistModuleExecutionRecords(recordKey = null) {
  platformRecordStore.persistRecordMap({
    records: state.scripts.moduleExecutionRecords,
    normalize: normalizeModuleExecutionRecord,
    storageKey: MODULE_EXECUTION_RECORDS_STORAGE_KEY,
    bucket: PLATFORM_RECORD_BUCKET.MODULE_EXECUTION,
    recordKey,
  });
}

function persistModuleRepairBatches(recordKey = null) {
  platformRecordStore.persistRecordMap({
    records: state.scripts.moduleRepairBatches,
    normalize: normalizeModuleRepairBatch,
    storageKey: MODULE_REPAIR_BATCHES_STORAGE_KEY,
    bucket: PLATFORM_RECORD_BUCKET.MODULE_REPAIR_BATCH,
    recordKey,
  });
}

function persistPlanScriptGenerationBatches(recordKey = null) {
  platformRecordStore.persistRecordMap({
    records: state.plans.scriptGenerationBatches,
    normalize: normalizePlanScriptGenerationBatch,
    storageKey: PLAN_SCRIPT_GENERATION_BATCHES_STORAGE_KEY,
    bucket: PLATFORM_RECORD_BUCKET.PLAN_SCRIPT_GENERATION_BATCH,
    recordKey,
  });
}

function persistRequirementPlanGenerationBatches(recordKey = null) {
  platformRecordStore.persistRecordMap({
    records: state.requirements.planGenerationBatches,
    normalize: normalizeRequirementPlanGenerationBatch,
    storageKey: REQUIREMENT_PLAN_GENERATION_BATCHES_STORAGE_KEY,
    bucket: PLATFORM_RECORD_BUCKET.REQUIREMENT_PLAN_GENERATION_BATCH,
    recordKey,
  });
}

function persistPlanGenerationRecords(recordKey = null) {
  platformRecordStore.persistRecordMap({
    records: state.plans.generationRecords,
    normalize: normalizePlanGenerationRecord,
    storageKey: PLAN_GENERATION_RECORDS_STORAGE_KEY,
    bucket: PLATFORM_RECORD_BUCKET.PLAN_GENERATION,
    recordKey,
  });
}

function persistPlanScriptGenerationRecords(recordKey = null) {
  platformRecordStore.persistRecordMap({
    records: state.plans.scriptGenerationRecords,
    normalize: normalizePlanScriptGenerationRecord,
    storageKey: SCRIPT_GENERATION_RECORDS_STORAGE_KEY,
    bucket: PLATFORM_RECORD_BUCKET.SCRIPT_GENERATION,
    recordKey,
  });
}

const elements = {
  appShell: document.getElementById("appShell"),
  appTitle: document.getElementById("appTitle"),
  requirementsNav: document.getElementById("requirementsNav"),
  plansNav: document.getElementById("plansNav"),
  scriptsNav: document.getElementById("scriptsNav"),
  testSuitesNav: document.getElementById("testSuitesNav"),
  agentNav: document.getElementById("agentNav"),
  projectSettingsNav: document.getElementById("projectSettingsNav"),
  usersNav: document.getElementById("usersNav"),
  rolesNav: document.getElementById("rolesNav"),
  projectSelect: document.getElementById("projectSelect"),
  createProjectButton: document.getElementById("createProjectButton"),
  exportProjectButton: document.getElementById("exportProjectButton"),
  importProjectButton: document.getElementById("importProjectButton"),
  languageMenuControl: document.getElementById("languageMenuControl"),
  languageMenuButton: document.getElementById("languageMenuButton"),
  languageMenu: document.getElementById("languageMenu"),
  currentUserName: document.getElementById("currentUserName"),
  logoutButton: document.getElementById("logoutButton"),
  planCreateWrap: document.getElementById("planCreateWrap"),
  requirementUploadWrap: document.getElementById("requirementUploadWrap"),
  uploadRequirementButton: document.getElementById("uploadRequirementButton"),
  requirementFileInput: document.getElementById("requirementFileInput"),
  createModuleButton: document.getElementById("createModuleButton"),
  moduleList: document.getElementById("moduleList"),
  moduleSearch: document.getElementById("moduleSearch"),
  refreshButton: document.getElementById("refreshButton"),
  moduleTitle: document.getElementById("moduleTitle"),
  filePath: document.getElementById("filePath"),
  generateScriptButton: document.getElementById("generateScriptButton"),
  recordScriptButton: document.getElementById("recordScriptButton"),
  executeScriptButton: document.getElementById("executeScriptButton"),
  runScriptButton: document.getElementById("runScriptButton"),
  editSaveButton: document.getElementById("editSaveButton"),
  cancelButton: document.getElementById("cancelButton"),
  notice: document.getElementById("notice"),
  viewerArea: document.getElementById("viewerArea"),
  emptyState: document.getElementById("emptyState"),
  userAdminPanel: document.getElementById("userAdminPanel"),
  roleAdminPanel: document.getElementById("roleAdminPanel"),
  projectSettingsPanel: document.getElementById("projectSettingsPanel"),
  scriptTabs: document.getElementById("scriptTabs"),
  planTabs: document.getElementById("planTabs"),
  planContentTab: document.getElementById("planContentTab"),
  planGenerationRecordTab: document.getElementById("planGenerationRecordTab"),
  planScriptGenerationRecordTab: document.getElementById("planScriptGenerationRecordTab"),
  planRelatedScriptsTab: document.getElementById("planRelatedScriptsTab"),
  scriptContentTab: document.getElementById("scriptContentTab"),
  executionRecordTab: document.getElementById("executionRecordTab"),
  repairRecordTab: document.getElementById("repairRecordTab"),
  preview: document.getElementById("preview"),
  planGenerationRecord: document.getElementById("planGenerationRecord"),
  planGenerationRecordEmpty: document.getElementById("planGenerationRecordEmpty"),
  planGenerationRecordContent: document.getElementById("planGenerationRecordContent"),
  planRecordPrompt: document.getElementById("planRecordPrompt"),
  planRecordTargetPath: document.getElementById("planRecordTargetPath"),
  planRecordJobOutput: document.getElementById("planRecordJobOutput"),
  planRecordJobStatus: document.getElementById("planRecordJobStatus"),
  planRecordJobLogs: document.getElementById("planRecordJobLogs"),
  planRecordDuration: document.getElementById("planRecordDuration"),
  planScriptGenerationRecord: document.getElementById("planScriptGenerationRecord"),
  planScriptPromptFixed: document.getElementById("planScriptPromptFixed"),
  planScriptPromptNote: document.getElementById("planScriptPromptNote"),
  planScriptRecordTargetPath: document.getElementById("planScriptRecordTargetPath"),
  planScriptGenerationSubmit: document.getElementById("planScriptGenerationSubmit"),
  planScriptJobOutput: document.getElementById("planScriptJobOutput"),
  planScriptJobStatus: document.getElementById("planScriptJobStatus"),
  planScriptJobLogs: document.getElementById("planScriptJobLogs"),
  planScriptDuration: document.getElementById("planScriptDuration"),
  modulePlanPanel: document.getElementById("modulePlanPanel"),
  modulePlanSummary: document.getElementById("modulePlanSummary"),
  modulePlanActions: document.getElementById("modulePlanActions"),
  modulePlanBulkToggle: document.getElementById("modulePlanBulkToggle"),
  modulePlanBulkActions: document.getElementById("modulePlanBulkActions"),
  modulePlanSelectionCount: document.getElementById("modulePlanSelectionCount"),
  modulePlanBulkCancel: document.getElementById("modulePlanBulkCancel"),
  modulePlanBulkGenerate: document.getElementById("modulePlanBulkGenerate"),
  modulePlanBulkDelete: document.getElementById("modulePlanBulkDelete"),
  modulePlanSelectHeader: document.getElementById("modulePlanSelectHeader"),
  modulePlanSelectAll: document.getElementById("modulePlanSelectAll"),
  modulePlanTableBody: document.getElementById("modulePlanTableBody"),
  modulePlanScriptBatchRecord: document.getElementById("modulePlanScriptBatchRecord"),
  modulePlanScriptBatchHeader: document.getElementById("modulePlanScriptBatchHeader"),
  modulePlanScriptBatchSummary: document.getElementById("modulePlanScriptBatchSummary"),
  modulePlanScriptBatchEmpty: document.getElementById("modulePlanScriptBatchEmpty"),
  modulePlanScriptBatchList: document.getElementById("modulePlanScriptBatchList"),
  assetInfoPanel: document.getElementById("assetInfoPanel"),
  planRelatedScriptsPanel: document.getElementById("planRelatedScriptsPanel"),
  planRelatedScriptsSummary: document.getElementById("planRelatedScriptsSummary"),
  planRelatedScriptsTableBody: document.getElementById("planRelatedScriptsTableBody"),
  requirementsPanel: document.getElementById("requirementsPanel"),
  requirementHeaderActions: document.getElementById("requirementHeaderActions"),
  requirementPreviewTab: document.getElementById("requirementPreviewTab"),
  requirementModulesTab: document.getElementById("requirementModulesTab"),
  requirementPlanGenerationBatchTab: document.getElementById("requirementPlanGenerationBatchTab"),
  requirementPreviewTabPanel: document.getElementById("requirementPreviewTabPanel"),
  requirementModulesTabPanel: document.getElementById("requirementModulesTabPanel"),
  requirementPlanGenerationBatchTabPanel: document.getElementById("requirementPlanGenerationBatchTabPanel"),
  requirementMeta: document.getElementById("requirementMeta"),
  requirementDownloadLink: document.getElementById("requirementDownloadLink"),
  requirementPreview: document.getElementById("requirementPreview"),
  requirementModuleSummary: document.getElementById("requirementModuleSummary"),
  analyzeRequirementButton: document.getElementById("analyzeRequirementButton"),
  importInventoryButton: document.getElementById("importInventoryButton"),
  requirementAnalysisOutput: document.getElementById("requirementAnalysisOutput"),
  requirementAnalysisStatus: document.getElementById("requirementAnalysisStatus"),
  requirementAnalysisLogs: document.getElementById("requirementAnalysisLogs"),
  requirementModuleToolbar: document.getElementById("requirementModuleToolbar"),
  requirementModuleListSummary: document.getElementById("requirementModuleListSummary"),
  requirementModuleActions: document.getElementById("requirementModuleActions"),
  requirementModuleBulkToggle: document.getElementById("requirementModuleBulkToggle"),
  requirementModuleBulkActions: document.getElementById("requirementModuleBulkActions"),
  requirementModuleSelectionCount: document.getElementById("requirementModuleSelectionCount"),
  requirementModuleBulkCancel: document.getElementById("requirementModuleBulkCancel"),
  requirementModuleBulkDelete: document.getElementById("requirementModuleBulkDelete"),
  requirementModuleBulkGenerate: document.getElementById("requirementModuleBulkGenerate"),
  requirementModulesList: document.getElementById("requirementModulesList"),
  requirementPlanGenerationBatchRecord: document.getElementById("requirementPlanGenerationBatchRecord"),
  requirementPlanGenerationBatchHeader: document.getElementById("requirementPlanGenerationBatchHeader"),
  requirementPlanGenerationBatchSummary: document.getElementById("requirementPlanGenerationBatchSummary"),
  requirementPlanGenerationBatchEmpty: document.getElementById("requirementPlanGenerationBatchEmpty"),
  requirementPlanGenerationBatchList: document.getElementById("requirementPlanGenerationBatchList"),
  requirementModuleDetailModal: document.getElementById("requirementModuleDetailModal"),
  requirementModuleDetailTitle: document.getElementById("requirementModuleDetailTitle"),
  requirementModuleDetailSubtitle: document.getElementById("requirementModuleDetailSubtitle"),
  requirementModuleDetailClose: document.getElementById("requirementModuleDetailClose"),
  requirementModuleDetailBody: document.getElementById("requirementModuleDetailBody"),
  testSuiteListPanel: document.getElementById("testSuiteListPanel"),
  testSuiteListSummary: document.getElementById("testSuiteListSummary"),
  createTestSuiteButton: document.getElementById("createTestSuiteButton"),
  testSuiteTableBody: document.getElementById("testSuiteTableBody"),
  testSuiteDetailPanel: document.getElementById("testSuiteDetailPanel"),
  agentPanel: document.getElementById("agentPanel"),
  testSuiteModuleList: document.getElementById("testSuiteModuleList"),
  testSuiteDetailTitle: document.getElementById("testSuiteDetailTitle"),
  testSuiteDetailSummary: document.getElementById("testSuiteDetailSummary"),
  backToTestSuiteListButton: document.getElementById("backToTestSuiteListButton"),
  openAddSuiteScriptsButton: document.getElementById("openAddSuiteScriptsButton"),
  executeTestSuiteButton: document.getElementById("executeTestSuiteButton"),
  testSuiteTabs: document.getElementById("testSuiteTabs"),
  testSuiteScriptsTab: document.getElementById("testSuiteScriptsTab"),
  testSuiteExecutionTab: document.getElementById("testSuiteExecutionTab"),
  testSuiteScriptsContent: document.getElementById("testSuiteScriptsContent"),
  testSuiteScriptTableBody: document.getElementById("testSuiteScriptTableBody"),
  testSuiteExecutionRecord: document.getElementById("testSuiteExecutionRecord"),
  testSuiteExecutionHistoryList: document.getElementById("testSuiteExecutionHistoryList"),
  testSuiteExecutionResultTitle: document.getElementById("testSuiteExecutionResultTitle"),
  testSuiteExecutionResultSummary: document.getElementById("testSuiteExecutionResultSummary"),
  testSuiteExecutionResultWrap: document.getElementById("testSuiteExecutionResultWrap"),
  testSuiteExecutionResultTableBody: document.getElementById("testSuiteExecutionResultTableBody"),
  testSuiteExecutionEmpty: document.getElementById("testSuiteExecutionEmpty"),
  testSuiteExecutionLogPanel: document.getElementById("testSuiteExecutionLogPanel"),
  testSuiteExecutionLogStatus: document.getElementById("testSuiteExecutionLogStatus"),
  testSuiteExecutionLog: document.getElementById("testSuiteExecutionLog"),
  testSuiteExecutionReportLink: document.getElementById("testSuiteExecutionReportLink"),
  testSuiteProgressModal: document.getElementById("testSuiteProgressModal"),
  testSuiteProgressModalClose: document.getElementById("testSuiteProgressModalClose"),
  testSuiteProgressModalDismiss: document.getElementById("testSuiteProgressModalDismiss"),
  testSuiteProgressTitle: document.getElementById("testSuiteProgressTitle"),
  testSuiteProgressStatus: document.getElementById("testSuiteProgressStatus"),
  testSuiteProgressLog: document.getElementById("testSuiteProgressLog"),
  testSuiteProgressBar: document.getElementById("testSuiteProgressBar"),
  testSuiteProgressCompleted: document.getElementById("testSuiteProgressCompleted"),
  testSuiteProgressPassed: document.getElementById("testSuiteProgressPassed"),
  testSuiteProgressFailed: document.getElementById("testSuiteProgressFailed"),
  testSuiteVideoModal: document.getElementById("testSuiteVideoModal"),
  testSuiteVideoModalClose: document.getElementById("testSuiteVideoModalClose"),
  testSuiteVideoModalTitle: document.getElementById("testSuiteVideoModalTitle"),
  testSuiteExecutionVideo: document.getElementById("testSuiteExecutionVideo"),
  testSuiteExecutionVideoPath: document.getElementById("testSuiteExecutionVideoPath"),
  scriptPreview: document.getElementById("scriptPreview"),
  scriptCode: document.getElementById("scriptCode"),
  moduleScriptPanel: document.getElementById("moduleScriptPanel"),
  moduleScriptSummary: document.getElementById("moduleScriptSummary"),
  moduleScriptActions: document.getElementById("moduleScriptActions"),
  moduleBulkToggle: document.getElementById("moduleBulkToggle"),
  moduleBulkActions: document.getElementById("moduleBulkActions"),
  moduleSelectionCount: document.getElementById("moduleSelectionCount"),
  moduleBulkCancel: document.getElementById("moduleBulkCancel"),
  moduleBulkExecute: document.getElementById("moduleBulkExecute"),
  moduleBulkRepair: document.getElementById("moduleBulkRepair"),
  moduleBulkDelete: document.getElementById("moduleBulkDelete"),
  moduleSelectHeader: document.getElementById("moduleSelectHeader"),
  moduleSelectAll: document.getElementById("moduleSelectAll"),
  moduleScriptTableBody: document.getElementById("moduleScriptTableBody"),
  moduleExecutionRecord: document.getElementById("moduleExecutionRecord"),
  moduleExecutionEmpty: document.getElementById("moduleExecutionEmpty"),
  moduleExecutionLogPanel: document.getElementById("moduleExecutionLogPanel"),
  moduleExecutionLogStatus: document.getElementById("moduleExecutionLogStatus"),
  moduleExecutionLog: document.getElementById("moduleExecutionLog"),
  moduleExecutionReportWrap: document.getElementById("moduleExecutionReportWrap"),
  moduleExecutionReportFrame: document.getElementById("moduleExecutionReportFrame"),
  moduleExecutionReportLink: document.getElementById("moduleExecutionReportLink"),
  moduleExecutionReportPath: document.getElementById("moduleExecutionReportPath"),
  moduleRepairRecord: document.getElementById("moduleRepairRecord"),
  moduleRepairRecordHeader: document.getElementById("moduleRepairRecordHeader"),
  moduleRepairSummary: document.getElementById("moduleRepairSummary"),
  moduleRepairCancelButton: document.getElementById("moduleRepairCancelButton"),
  moduleRepairEmpty: document.getElementById("moduleRepairEmpty"),
  moduleRepairList: document.getElementById("moduleRepairList"),
  executionRecord: document.getElementById("executionRecord"),
  executionHistoryPanel: document.getElementById("executionHistoryPanel"),
  executionHistorySummary: document.getElementById("executionHistorySummary"),
  executionHistoryTableBody: document.getElementById("executionHistoryTableBody"),
  executionEmpty: document.getElementById("executionEmpty"),
  executionLogPanel: document.getElementById("executionLogPanel"),
  executionLogStatus: document.getElementById("executionLogStatus"),
  executionLog: document.getElementById("executionLog"),
  executionReportWrap: document.getElementById("executionReportWrap"),
  executionReportFrame: document.getElementById("executionReportFrame"),
  executionReportLink: document.getElementById("executionReportLink"),
  executionReportPath: document.getElementById("executionReportPath"),
  executionVideoWrap: document.getElementById("executionVideoWrap"),
  executionVideo: document.getElementById("executionVideo"),
  executionVideoPath: document.getElementById("executionVideoPath"),
  executionModeModal: document.getElementById("executionModeModal"),
  executionModeTitle: document.getElementById("executionModeTitle"),
  executionModeSummary: document.getElementById("executionModeSummary"),
  executionModeClose: document.getElementById("executionModeClose"),
  executionModeCancel: document.getElementById("executionModeCancel"),
  executionModeSubmit: document.getElementById("executionModeSubmit"),
  executionModeBatch: document.getElementById("executionModeBatch"),
  executionModeSerial: document.getElementById("executionModeSerial"),
  scriptRepairRecord: document.getElementById("scriptRepairRecord"),
  editor: document.getElementById("editor"),
  planGenerationModal: document.getElementById("planGenerationModal"),
  planGenerationClose: document.getElementById("planGenerationClose"),
  planGenerationCancel: document.getElementById("planGenerationCancel"),
  planGenerationSubmit: document.getElementById("planGenerationSubmit"),
  addModuleNameLink: document.getElementById("addModuleNameLink"),
  newModuleNameSelect: document.getElementById("newModuleNameSelect"),
  newModuleName: document.getElementById("newModuleName"),
  newPlanName: document.getElementById("newPlanName"),
  planModeMultiple: document.getElementById("planModeMultiple"),
  planModeSingle: document.getElementById("planModeSingle"),
  planCoverageProfile: document.getElementById("planCoverageProfile"),
  planCoverageDescription: document.getElementById("planCoverageDescription"),
  planPromptCustomized: document.getElementById("planPromptCustomized"),
  planPromptReset: document.getElementById("planPromptReset"),
  planPrompt: document.getElementById("planPrompt"),
  planTargetPath: document.getElementById("planTargetPath"),
  planJobOutput: document.getElementById("planJobOutput"),
  planJobStatus: document.getElementById("planJobStatus"),
  planJobLogs: document.getElementById("planJobLogs"),
  requirementBatchPlanModal: document.getElementById("requirementBatchPlanModal"),
  requirementBatchPlanClose: document.getElementById("requirementBatchPlanClose"),
  requirementBatchPlanCancel: document.getElementById("requirementBatchPlanCancel"),
  requirementBatchPlanSubmit: document.getElementById("requirementBatchPlanSubmit"),
  requirementBatchPlanSummary: document.getElementById("requirementBatchPlanSummary"),
  requirementBatchCoverageProfile: document.getElementById("requirementBatchCoverageProfile"),
  requirementBatchCoveragePrompt: document.getElementById("requirementBatchCoveragePrompt"),
  requirementBatchPromptCustomized: document.getElementById("requirementBatchPromptCustomized"),
  requirementBatchPromptReset: document.getElementById("requirementBatchPromptReset"),
  scriptGenerationModal: document.getElementById("scriptGenerationModal"),
  scriptGenerationClose: document.getElementById("scriptGenerationClose"),
  scriptGenerationCancel: document.getElementById("scriptGenerationCancel"),
  scriptGenerationSubmit: document.getElementById("scriptGenerationSubmit"),
  scriptPromptFixed: document.getElementById("scriptPromptFixed"),
  scriptPromptNote: document.getElementById("scriptPromptNote"),
  scriptJobStatus: document.getElementById("scriptJobStatus"),
  scriptJobLogs: document.getElementById("scriptJobLogs"),
  projectCreateModal: document.getElementById("projectCreateModal"),
  projectCreateClose: document.getElementById("projectCreateClose"),
  projectCreateCancel: document.getElementById("projectCreateCancel"),
  projectCreateSubmit: document.getElementById("projectCreateSubmit"),
  projectCreateWorkspaceHint: document.getElementById("projectCreateWorkspaceHint"),
  newProjectKey: document.getElementById("newProjectKey"),
  newProjectName: document.getElementById("newProjectName"),
  newProjectSpecsDir: document.getElementById("newProjectSpecsDir"),
  newProjectTestsDir: document.getElementById("newProjectTestsDir"),
  newProjectDescription: document.getElementById("newProjectDescription"),
  projectImportModal: document.getElementById("projectImportModal"),
  projectImportClose: document.getElementById("projectImportClose"),
  projectImportCancel: document.getElementById("projectImportCancel"),
  projectImportSubmit: document.getElementById("projectImportSubmit"),
  projectImportWorkspaceHint: document.getElementById("projectImportWorkspaceHint"),
  projectImportFile: document.getElementById("projectImportFile"),
  importProjectKey: document.getElementById("importProjectKey"),
  importProjectName: document.getElementById("importProjectName"),
  importProjectSpecsDir: document.getElementById("importProjectSpecsDir"),
  importProjectTestsDir: document.getElementById("importProjectTestsDir"),
  importProjectDescription: document.getElementById("importProjectDescription"),
  scriptRunSubmit: document.getElementById("scriptRunSubmit"),
  scriptRunPromptFixed: document.getElementById("scriptRunPromptFixed"),
  scriptRunPromptNote: document.getElementById("scriptRunPromptNote"),
  scriptRunJobOutput: document.getElementById("scriptRunJobOutput"),
  scriptRunJobStatus: document.getElementById("scriptRunJobStatus"),
  scriptRunJobLogs: document.getElementById("scriptRunJobLogs"),
  scriptRunDuration: document.getElementById("scriptRunDuration"),
  testSuiteCreateModal: document.getElementById("testSuiteCreateModal"),
  testSuiteCreateClose: document.getElementById("testSuiteCreateClose"),
  testSuiteCreateCancel: document.getElementById("testSuiteCreateCancel"),
  testSuiteCreateSubmit: document.getElementById("testSuiteCreateSubmit"),
  newTestSuiteName: document.getElementById("newTestSuiteName"),
  testSuiteRenameModal: document.getElementById("testSuiteRenameModal"),
  testSuiteRenameClose: document.getElementById("testSuiteRenameClose"),
  testSuiteRenameCancel: document.getElementById("testSuiteRenameCancel"),
  testSuiteRenameSubmit: document.getElementById("testSuiteRenameSubmit"),
  renameTestSuiteName: document.getElementById("renameTestSuiteName"),
  suiteScriptModal: document.getElementById("suiteScriptModal"),
  suiteScriptModalClose: document.getElementById("suiteScriptModalClose"),
  suiteScriptModalCancel: document.getElementById("suiteScriptModalCancel"),
  suiteScriptModalSubmit: document.getElementById("suiteScriptModalSubmit"),
  suiteScriptModuleList: document.getElementById("suiteScriptModuleList"),
  suiteAvailableScriptList: document.getElementById("suiteAvailableScriptList"),
  suiteScriptPickerTitle: document.getElementById("suiteScriptPickerTitle"),
  suiteScriptSelectionCount: document.getElementById("suiteScriptSelectionCount"),
};

function projectLanguage() {
  return state.project.current?.language === "zh-CN" ? "zh-CN" : "en";
}

function applyProjectLanguage() {
  const language = projectLanguage();
  window.WaterfallI18n?.setLocale(language);
  const isAdmin = Boolean(state.auth.isAdmin);
  if (language === "en") {
    SCRIPT_PROMPT_FIXED_TEMPLATE = `@playwright-test-generator
Generate a Playwright test file from specs/<module>/<test-plan-file>.
Each test file contains exactly one test and should use an English business name by default.
Write output only to the candidate path supplied by the platform; do not directly modify production tests.`;
    SCRIPT_PROMPT_NOTE_DEFAULT = "Implement real code under each STEP whenever possible; otherwise explain why.";
    SCRIPT_RUN_PROMPT_FIXED_TEMPLATE = "@playwright-test-healer\nUse specs/<module>/<module>.md to run and repair tests/<module>/<test-script>.spec.ts";
    SCRIPT_RUN_PROMPT_NOTE_DEFAULT = "Requirements:\n1. Do not delete or comment out any STEP.\n2. Preserve the execution video.";
  }
  const languageLabel = window.WaterfallI18n?.t("language") || (language === "en" ? "English" : "简体中文");
  elements.languageMenuButton.textContent = isAdmin ? `${languageLabel} ▾` : languageLabel;
  elements.languageMenuButton.disabled = !isAdmin;
  elements.languageMenuButton.classList.toggle("readonly", !isAdmin);
  elements.languageMenuButton.setAttribute("aria-expanded", "false");
  elements.languageMenu.querySelectorAll("[data-language]").forEach((item) => {
    item.setAttribute("aria-checked", String(item.dataset.language === language));
    item.textContent = `${item.dataset.language === language ? "✓ " : ""}${item.dataset.language === "en" ? "English" : "简体中文"}`;
  });
  elements.appShell.classList.remove("i18n-pending");
}

function closeLanguageMenu() {
  elements.languageMenu.classList.add("hidden");
  elements.languageMenuButton.setAttribute("aria-expanded", "false");
}

async function setProjectLanguage(language) {
  if (!state.auth.isAdmin || language === projectLanguage()) {
    closeLanguageMenu();
    return;
  }
  try {
    await requestJson("/api/project-language", {
      method: "PUT",
      headers: getProjectRequestHeaders(),
      body: JSON.stringify({ language }),
    });
    window.location.reload();
  } catch (error) {
    closeLanguageMenu();
    setNotice(error.message || window.WaterfallI18n?.t("language.changeFailed") || "Could not change language.", "error");
  }
}

const setupFeature = createSetupPreparation({
  setupState: state.projectSettings.setup,
  root: elements.projectSettingsPanel,
  getProject: () => state.project.current,
  getProjectKey: () => state.project.currentKey,
  getTestSuites: () => state.testSuites.suites,
  getScriptModules: () => state.scripts.modules,
  isActive: () => state.activeSection === SECTION.PROJECT_SETTINGS,
  requestJson,
  encodePathPart,
  isPlainObject,
  escapeHtml,
  stripSpecSuffix,
  renderHost: (...args) => renderProjectSettingsPanel(...args),
});

function encodePathPart(value) {
  return encodeURIComponent(value);
}

function stripSpecSuffix(filename) {
  return filename.endsWith(".spec.ts") ? filename.slice(0, -".spec.ts".length) : filename;
}

function stripMarkdownSuffix(filename) {
  return filename.endsWith(".md") ? filename.slice(0, -".md".length) : filename;
}

function stripArtifactSuffix(value, suffix) {
  const text = String(value || "").trim();
  return text.toLowerCase().endsWith(suffix.toLowerCase()) ? text.slice(0, -suffix.length) : text;
}

function hasChineseText(value) {
  return CJK_NAME_PATTERN.test(String(value || ""));
}

function hasAsciiLetters(value) {
  return ASCII_LETTER_PATTERN.test(String(value || ""));
}

function isChineseArtifactStem(value) {
  const stem = String(value || "").trim();
  return Boolean(stem && hasChineseText(stem) && !hasAsciiLetters(stem));
}

function stableNumericSuffix(value) {
  let total = 0;
  for (const char of String(value || "")) {
    total = (total * 131 + char.codePointAt(0)) % 1000000;
  }
  return String(total).padStart(6, "0");
}

function sanitizeChineseArtifactStem(value, fallback = "测试用例", uniqueKey = "") {
  let stem = stripArtifactSuffix(stripArtifactSuffix(value, ".spec.ts"), ".md");
  stem = stem
    .replace(ARTIFACT_FILENAME_UNSAFE_PATTERN, "-")
    .replace(ASCII_LETTERS_GLOBAL_PATTERN, "")
    .replace(/[\s._-]+/g, "-")
    .replace(/^[\s.\-_。-]+|[\s.\-_。-]+$/g, "");
  if (!isChineseArtifactStem(stem)) {
    let fallbackStem = stripArtifactSuffix(stripArtifactSuffix(fallback, ".spec.ts"), ".md");
    fallbackStem = fallbackStem
      .replace(ARTIFACT_FILENAME_UNSAFE_PATTERN, "-")
      .replace(ASCII_LETTERS_GLOBAL_PATTERN, "")
      .replace(/[\s._-]+/g, "-")
      .replace(/^[\s.\-_。-]+|[\s.\-_。-]+$/g, "");
    stem = isChineseArtifactStem(fallbackStem) ? fallbackStem : "测试用例";
  }
  return uniqueKey ? `${stem}-${stableNumericSuffix(uniqueKey)}` : stem;
}

function getDefaultPlanFilename(moduleName) {
  return moduleName ? `${moduleName}.md` : "";
}

function getPlanFilenameFromName(planName, moduleName) {
  const value = (planName || moduleName || "").trim();
  if (!value) {
    return "";
  }
  return value.endsWith(".md") ? value : `${value}.md`;
}

function getChinesePlanFilenameFromName(planName, moduleName, fallbackStem = "") {
  const candidate = getPlanFilenameFromName(planName, moduleName);
  const stem = stripMarkdownSuffix(candidate);
  if (isChineseArtifactStem(stem)) {
    return candidate;
  }
  const fallbackSource = fallbackStem || moduleName;
  const fallback = hasChineseText(fallbackSource) ? fallbackSource : "测试计划";
  const uniqueKey = hasChineseText(fallbackSource) ? "" : `${moduleName || ""}/${planName || ""}`;
  return `${sanitizeChineseArtifactStem(candidate, fallback, uniqueKey)}.md`;
}

function getGeneratedScriptFilenameFromPlan(planFilename) {
  const stem = stripMarkdownSuffix(planFilename || "");
  if (isChineseArtifactStem(stem)) {
    return `${stem}.spec.ts`;
  }
  return `${sanitizeChineseArtifactStem(stem, "测试脚本", planFilename)}.spec.ts`;
}

function isPlanIndexFilename(filename) {
  const value = typeof filename === "string" ? filename.trim() : "";
  if (!value) {
    return false;
  }
  const pathName = value.split(/[\\/]/).pop();
  const stem = stripMarkdownSuffix(pathName);
  return pathName.startsWith("_") || stem === "用例索引" || stem === "case-index" || stem.endsWith("-用例索引");
}

function getPlanGenerationMode() {
  return elements.planModeSingle?.checked ? PLAN_GENERATION_MODE.SINGLE : PLAN_GENERATION_MODE.MULTIPLE;
}

function getSelectedExecutionMode() {
  return elements.executionModeSerial?.checked ? EXECUTION_MODE.SERIAL_PER_FILE : EXECUTION_MODE.BATCH;
}

function closeExecutionModeModal(result = null) {
  elements.executionModeModal.classList.add("hidden");
  const resolver = state.executionModeDialog.resolver;
  state.executionModeDialog.resolver = null;
  state.executionModeDialog.target = "";
  if (resolver) {
    resolver(result);
  }
}

function openExecutionModeModal({ title, summary, target }) {
  if (state.executionModeDialog.resolver) {
    closeExecutionModeModal(null);
  }

  elements.executionModeTitle.textContent = title || "选择批量执行模式";
  elements.executionModeSummary.textContent = summary || "";
  elements.executionModeBatch.checked = true;
  elements.executionModeSerial.checked = false;
  elements.executionModeModal.classList.remove("hidden");
  state.executionModeDialog.target = target || "";

  return new Promise((resolve) => {
    state.executionModeDialog.resolver = resolve;
    elements.executionModeBatch.focus();
  });
}

function getPlanGenerationPlanFilename(moduleName) {
  const mode = getPlanGenerationMode();
  const planName = elements.newPlanName.value.trim();
  if (mode === PLAN_GENERATION_MODE.MULTIPLE) {
    const indexName = planName || (moduleName ? `${moduleName}-用例索引` : "<测试计划名>");
    const fallbackStem = moduleName ? `${moduleName}-用例索引` : "用例索引";
    return getChinesePlanFilenameFromName(indexName, moduleName, fallbackStem);
  }
  return getChinesePlanFilenameFromName(planName || moduleName, moduleName, moduleName || "测试计划");
}

function getSelectedPlan() {
  const moduleItem = state.plans.modules.find((item) => item.name === state.plans.selectedModule);
  return moduleItem?.plans.find((plan) => plan.filename === state.plans.selectedPlanFile) || null;
}

function getSelectedPlanModule(moduleName = state.plans.selectedModule) {
  return state.plans.modules.find((item) => item.name === moduleName) || null;
}

function getSelectedScriptModule(moduleName = state.scripts.selectedModule) {
  return state.scripts.modules.find((item) => item.name === moduleName) || null;
}

function getPlanModuleNames() {
  return state.plans.modules.map((moduleItem) => moduleItem.name).filter(Boolean);
}

function getPlanGenerationModuleName() {
  if (state.generation.moduleNameMode === "input") {
    return elements.newModuleName.value.trim();
  }

  return elements.newModuleNameSelect.value.trim();
}

function renderPlanGenerationModuleOptions(selectedModuleName = "") {
  const moduleNames = getPlanModuleNames();
  elements.newModuleNameSelect.replaceChildren();

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = moduleNames.length ? "请选择模块" : "暂无已有模块";
  elements.newModuleNameSelect.appendChild(placeholder);

  moduleNames.forEach((moduleName) => {
    const option = document.createElement("option");
    option.value = moduleName;
    option.textContent = moduleName;
    elements.newModuleNameSelect.appendChild(option);
  });

  elements.newModuleNameSelect.value = moduleNames.includes(selectedModuleName) ? selectedModuleName : "";
}

function setPlanGenerationModuleMode(mode, { focus = false } = {}) {
  const hasModuleOptions = getPlanModuleNames().length > 0;
  const nextMode = mode === "input" || !hasModuleOptions ? "input" : "select";
  const isInputMode = nextMode === "input";

  state.generation.moduleNameMode = nextMode;
  elements.newModuleNameSelect.classList.toggle("hidden", isInputMode);
  elements.newModuleName.classList.toggle("hidden", !isInputMode);
  elements.addModuleNameLink.classList.toggle("hidden", !hasModuleOptions);
  elements.addModuleNameLink.textContent = isInputMode ? "选择已有模块" : "新增模块名";

  if (focus) {
    window.requestAnimationFrame(() => {
      (isInputMode ? elements.newModuleName : elements.newModuleNameSelect).focus();
    });
  }
}

function resetPlanGenerationSource() {
  state.generation.source = "plans";
  state.generation.defaultsLoaded = false;
  state.generation.coverageProfiles = [];
  state.generation.defaultCoverageProfile = DEFAULT_COVERAGE_PROFILE;
  state.generation.coverageProfile = DEFAULT_COVERAGE_PROFILE;
  state.generation.basePrompt = "";
  state.generation.defaultComposedPrompt = "";
  state.generation.requirementUid = "";
  state.generation.requirementModuleUid = "";
}

function isRequirementPlanGeneration() {
  return (
    state.generation.source === "requirement" &&
    Boolean(state.generation.requirementUid && state.generation.requirementModuleUid)
  );
}

function setPlanGenerationModuleControlsLocked(locked) {
  elements.newModuleName.disabled = locked;
  elements.newModuleNameSelect.disabled = locked;
  elements.addModuleNameLink.disabled = locked;
  if (locked) {
    elements.addModuleNameLink.classList.add("hidden");
  }
}

function togglePlanGenerationModuleMode() {
  if (state.generation.moduleNameMode === "input") {
    const typedModuleName = elements.newModuleName.value.trim();
    if (getPlanModuleNames().includes(typedModuleName)) {
      elements.newModuleNameSelect.value = typedModuleName;
    }
    setPlanGenerationModuleMode("select", { focus: true });
  } else {
    elements.newModuleName.value = elements.newModuleNameSelect.value.trim();
    setPlanGenerationModuleMode("input", { focus: true });
  }

  updatePromptForModuleName();
}

function setupPlanGenerationModuleField() {
  setPlanGenerationModuleControlsLocked(false);
  const selectedModuleName = getPlanModuleNames().includes(state.plans.selectedModule) ? state.plans.selectedModule : "";
  renderPlanGenerationModuleOptions(selectedModuleName);
  elements.newModuleName.value = selectedModuleName;
  setPlanGenerationModuleMode(getPlanModuleNames().length ? "select" : "input");
  return getPlanGenerationModuleName();
}

function normalizePlanModule(moduleItem) {
  const moduleName = moduleItem?.name || "";
  const rawPlans = Array.isArray(moduleItem?.plans)
    ? moduleItem.plans
    : moduleName
      ? [
          {
            name: moduleName,
            filename: getDefaultPlanFilename(moduleName),
            path: moduleItem?.path || "",
            is_default: true,
          },
        ]
      : [];
  const plans = rawPlans
    .map((plan) => {
      const filename = typeof plan.filename === "string" ? plan.filename : getPlanFilenameFromName(plan.name, moduleName);
      return {
        name: typeof plan.name === "string" && plan.name ? plan.name : stripMarkdownSuffix(filename),
        filename,
        path: typeof plan.path === "string" ? plan.path : "",
        is_default: Boolean(plan.is_default) || filename === getDefaultPlanFilename(moduleName),
        is_index: Boolean(plan.is_index) || isPlanIndexFilename(filename),
      };
    })
    .filter((plan) => plan.filename)
    .sort((left, right) => {
      if (left.is_default !== right.is_default) {
        return left.is_default ? -1 : 1;
      }
      return left.filename.localeCompare(right.filename);
    });

  return {
    name: moduleName,
    path: typeof moduleItem?.path === "string" ? moduleItem.path : "",
    plans,
  };
}

function normalizeAsset(asset) {
  if (!asset || typeof asset !== "object") {
    return null;
  }
  return {
    asset_id: Number(asset.asset_id) || null,
    asset_type: typeof asset.asset_type === "string" ? asset.asset_type : "",
    module_name: typeof asset.module_name === "string" ? asset.module_name : "",
    title: typeof asset.title === "string" ? asset.title : "",
    current_path: typeof asset.current_path === "string" ? asset.current_path : "",
    current_revision_id: Number(asset.current_revision_id) || null,
    from_plan_asset_id: Number(asset.from_plan_asset_id) || null,
    source_job_id: typeof asset.source_job_id === "string" ? asset.source_job_id : "",
    status: typeof asset.status === "string" ? asset.status : "",
    created_at: Number(asset.created_at) || null,
    updated_at: Number(asset.updated_at) || null,
    last_status: typeof asset.last_status === "string" ? asset.last_status : "",
    last_run_at: Number(asset.last_run_at) || null,
  };
}

function normalizeRevision(revision) {
  if (!revision || typeof revision !== "object") {
    return null;
  }
  return {
    revision_id: Number(revision.revision_id) || null,
    asset_id: Number(revision.asset_id) || null,
    version_no: Number(revision.version_no) || 0,
    file_path: typeof revision.file_path === "string" ? revision.file_path : "",
    git_commit_sha: typeof revision.git_commit_sha === "string" ? revision.git_commit_sha : "",
    content_sha256: typeof revision.content_sha256 === "string" ? revision.content_sha256 : "",
    change_source: typeof revision.change_source === "string" ? revision.change_source : "",
    source_job_id: typeof revision.source_job_id === "string" ? revision.source_job_id : "",
    author: typeof revision.author === "string" ? revision.author : "",
    message: typeof revision.message === "string" ? revision.message : "",
    created_at: Number(revision.created_at) || null,
  };
}

function normalizeRunResult(result) {
  if (!result || typeof result !== "object") {
    return null;
  }
  return {
    result_id: Number(result.result_id) || null,
    run_id: typeof result.run_id === "string" ? result.run_id : "",
    run_type: typeof result.run_type === "string" ? result.run_type : "",
    execution_mode: typeof result.execution_mode === "string" ? result.execution_mode : "",
    database_reset_mode: typeof result.database_reset_mode === "string" ? result.database_reset_mode : "",
    script_revision_id: Number(result.script_revision_id) || null,
    plan_revision_id: Number(result.plan_revision_id) || null,
    status: typeof result.status === "string" ? result.status : "",
    error_message: typeof result.error_message === "string" ? result.error_message : "",
    stdout_tail: typeof result.stdout_tail === "string" ? result.stdout_tail : "",
    started_at: Number(result.started_at) || null,
    finished_at: Number(result.finished_at) || null,
    updated_at: Number(result.updated_at) || null,
  };
}

function normalizeAssetList(items) {
  return (Array.isArray(items) ? items : []).map(normalizeAsset).filter(Boolean);
}

function normalizeRevisionList(items) {
  return (Array.isArray(items) ? items : []).map(normalizeRevision).filter(Boolean);
}

function normalizeRunResultList(items) {
  return (Array.isArray(items) ? items : []).map(normalizeRunResult).filter(Boolean);
}

function formatTimestampMs(timestamp) {
  const value = Number(timestamp);
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat(window.WaterfallI18n?.getLocale?.() || "en", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function getDbResultStatusInfo(status) {
  if (status === "passed" || status === "succeeded") {
    return { label: "通过", className: "success" };
  }
  if (status === "failed") {
    return { label: "失败", className: "error" };
  }
  if (status === "timed_out") {
    return { label: "超时", className: "error" };
  }
  if (status === "interrupted") {
    return { label: "中断", className: "" };
  }
  if (status === "skipped") {
    return { label: "跳过", className: "" };
  }
  if (status === "running") {
    return { label: "执行中", className: "running" };
  }
  return { label: !status || status === "unknown" ? "未知" : status, className: "" };
}

function getDbExecutionModeLabel(value) {
  if (value === "serial_per_file") {
    return "按文件串行执行";
  }
  if (value === "batch_once") {
    return "当前批量执行";
  }
  return getExecutionModeLabel(value);
}

function getScriptRunRecordKey(moduleName = state.scripts.selectedModule, filename = state.scripts.selectedFile) {
  if (!moduleName || !filename) {
    return "";
  }

  return `${moduleName}/${filename}`;
}

function getModuleRecordKey(moduleName = state.scripts.selectedModule) {
  return moduleName || "";
}

function getPlanModuleRecordKey(moduleName = state.plans.selectedModule) {
  return moduleName || "";
}

function getPlanRecordKey(moduleName = state.plans.selectedModule, planFilename = state.plans.selectedPlanFile) {
  if (!moduleName) {
    return "";
  }

  const filename = planFilename || getDefaultPlanFilename(moduleName);
  return filename ? `${moduleName}/${filename}` : "";
}

function getDefaultScriptTargetPath(moduleName = state.plans.selectedModule) {
  return moduleName ? `tests/${moduleName}/` : "";
}

function replaceAllText(value, search, replacement) {
  if (!search) {
    return value;
  }

  return value.split(search).join(replacement);
}

function setNotice(message, type = "") {
  elements.notice.textContent = message;
  elements.notice.className = `notice ${type}`.trim();
  elements.notice.classList.toggle("hidden", !message);
}

function hasSelection() {
  if (isAdminSection() || isProjectSettingsSection() || isAgentSection()) {
    return true;
  }

  if (state.activeSection === SECTION.REQUIREMENTS) {
    return Boolean(state.requirements.current);
  }

  if (state.activeSection === SECTION.PLANS) {
    return Boolean(state.plans.selectedModule);
  }

  if (state.activeSection === SECTION.TEST_SUITES) {
    return true;
  }

  return Boolean(state.scripts.selectedModule);
}

function isEditableView() {
  if (isAdminSection() || isProjectSettingsSection() || isAgentSection()) {
    return false;
  }

  if (state.activeSection === SECTION.PLANS) {
    return Boolean(state.plans.selectedPlanFile) && state.plans.activeTab === PLAN_VIEW_TAB.CONTENT;
  }

  if (state.activeSection === SECTION.REQUIREMENTS) {
    return false;
  }

  if (state.activeSection === SECTION.TEST_SUITES) {
    return false;
  }

  return Boolean(state.scripts.selectedFile) && state.scripts.activeTab === SCRIPT_VIEW_TAB.SCRIPT;
}

function canEditSelection() {
  return hasSelection() && isEditableView();
}

function confirmDiscardEdit() {
  if (!state.isEditing) {
    return true;
  }

  return window.confirm("当前内容尚未保存，切换后会丢失本次编辑。是否继续？");
}

function setLoading(isLoading) {
  elements.editSaveButton.disabled = isLoading || !canEditSelection();
  elements.generateScriptButton.disabled =
    isLoading ||
    state.activeSection !== SECTION.PLANS ||
    !state.plans.selectedModule ||
    !state.plans.selectedPlanFile ||
    state.isEditing ||
    state.generation.isRunning ||
    state.scriptGeneration.isRunning;
  elements.runScriptButton.disabled =
    isLoading ||
    state.activeSection !== SECTION.SCRIPTS ||
    !state.scripts.selectedModule ||
    !state.scripts.selectedFile ||
    state.isEditing ||
    state.scriptRecording.isRunning ||
    state.scriptExecution.isRunning ||
    state.moduleExecution.isRunning ||
    state.moduleRepair.isRunning ||
    state.scriptRun.isRunning ||
    state.testSuiteExecution.isRunning;
  elements.recordScriptButton.disabled =
    isLoading ||
    state.activeSection !== SECTION.SCRIPTS ||
    !state.scripts.selectedModule ||
    !state.scripts.selectedFile ||
    state.isEditing ||
    state.scriptRecording.isRunning ||
    state.scriptExecution.isRunning ||
    state.moduleExecution.isRunning ||
    state.moduleRepair.isRunning ||
    state.scriptRun.isRunning ||
    state.testSuiteExecution.isRunning;
  elements.executeScriptButton.disabled =
    isLoading ||
    state.activeSection !== SECTION.SCRIPTS ||
    !state.scripts.selectedModule ||
    !state.scripts.selectedFile ||
    state.isEditing ||
    state.scriptRecording.isRunning ||
    state.scriptExecution.isRunning ||
    state.moduleExecution.isRunning ||
    state.moduleRepair.isRunning ||
    state.scriptRun.isRunning ||
    state.testSuiteExecution.isRunning;
  elements.refreshButton.disabled = isLoading;
}

function getSearchQuery() {
  return elements.moduleSearch.value.trim().toLowerCase();
}

const adminFeature = createAdminFeature({
  state,
  elements,
  SECTION,
  MENU_ITEMS,
  window,
  projects: {
    renderProjectSelect: (...args) => renderProjectSelect(...args),
  },
  requestJson,
  setNotice,
  setLoading,
  renderContent,
  escapeHtml,
});
const {
  getMenuItem,
  hasMenu,
  hasProjectSettingsPermission,
  getFirstAllowedSection,
  isAdminSection,
  isProjectSettingsSection,
  isAgentSection,
  ensureAllowedActiveSection,
  loadAuthContext,
  renderNavigation,
  loadAdminRoles,
  loadAdminUsers,
  renderUserAdminPanel,
  renderRoleAdminPanel,
} = adminFeature;

const projectsFeature = createProjectsFeature({
  state,
  elements,
  CURRENT_PROJECT_STORAGE_KEY,
  PROJECT_SETTINGS_VIEW_TAB,
  REQUIREMENT_VIEW_TAB,
  TEST_SUITE_ALL_MODULE,
  document,
  window,
  fetch: (...args) => fetch(...args),
  FormData,
  admin: adminFeature,
  testSuites: {
    resetTestSuiteExecutionHistory: (...args) => resetTestSuiteExecutionHistory(...args),
  },
  jobs: {
    isAnyScriptJobRunning: (...args) => isAnyScriptJobRunning(...args),
  },
  getStoredProjectKey,
  writeStorageItem,
  isPlainObject,
  getProjectRequestHeaders,
  requestJson,
  readFetchError,
  getDownloadFilename,
  confirmDiscardEdit,
  hydratePlatformRecords,
  loadActiveSection,
  renderSideList,
  renderContent,
  setNotice,
});
const {
  normalizeProject,
  resetProjectScopedState,
  renderProjectSelect,
  loadProjects,
  openProjectCreateModal,
  closeProjectCreateModal,
  submitProjectCreate,
  openProjectImportModal,
  closeProjectImportModal,
  exportCurrentProject,
  submitProjectImport,
  switchProject,
} = projectsFeature;


function isPlainObject(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function applyViewStateRecord(record) {
  if (!isPlainObject(record) || !Object.keys(record).length) {
    return;
  }

  const next = normalizeViewStateRecord(record);
  state.activeSection = next.activeSection || state.activeSection;
  state.requirements.selectedUid = next.requirementUid || null;
  state.plans.activeTab = next.planActiveTab || state.plans.activeTab;
  state.plans.selectedModule = next.planModule || null;
  state.plans.selectedPlanFile = next.planFile || null;
  state.plans.expandedModules = new Set(next.expandedPlanModules || []);
  state.scripts.selectedModule = next.scriptModule || null;
  state.scripts.selectedFile = next.scriptFile || null;
  state.scripts.activeTab = next.scriptActiveTab || state.scripts.activeTab;
  state.scripts.expandedModules = new Set(next.expandedModules || []);
  state.testSuites.selectedSuiteId = next.testSuiteId || null;
  state.testSuites.selectedModule = next.testSuiteModule || TEST_SUITE_ALL_MODULE;
  state.testSuites.activeTab = next.testSuiteActiveTab || state.testSuites.activeTab;
  resetTestSuiteExecutionHistory();
}

async function hydratePlatformRecords() {
  await platformRecordStore.hydrate({
    requestJson,
    descriptors: [
      {
        remoteKey: PLATFORM_RECORD_BUCKET.VIEW_STATE,
        storageKey: VIEW_STATE_STORAGE_KEY,
        apply: applyViewStateRecord,
      },
      {
        remoteKey: PLATFORM_RECORD_BUCKET.SCRIPT_RUN,
        storageKey: SCRIPT_RUN_RECORDS_STORAGE_KEY,
        apply: () => {
          state.scripts.runRecords = loadScriptRunRecordsFromStorage();
        },
      },
      {
        remoteKey: PLATFORM_RECORD_BUCKET.SCRIPT_REPAIR,
        storageKey: SCRIPT_REPAIR_RECORDS_STORAGE_KEY,
        apply: () => {
          state.scripts.repairRecords = loadScriptRepairRecordsFromStorage();
        },
      },
      {
        remoteKey: PLATFORM_RECORD_BUCKET.MODULE_EXECUTION,
        storageKey: MODULE_EXECUTION_RECORDS_STORAGE_KEY,
        apply: () => {
          state.scripts.moduleExecutionRecords = loadModuleExecutionRecordsFromStorage();
        },
      },
      {
        remoteKey: PLATFORM_RECORD_BUCKET.MODULE_REPAIR_BATCH,
        storageKey: MODULE_REPAIR_BATCHES_STORAGE_KEY,
        apply: () => {
          state.scripts.moduleRepairBatches = loadModuleRepairBatchesFromStorage();
        },
      },
      {
        remoteKey: PLATFORM_RECORD_BUCKET.PLAN_GENERATION,
        storageKey: PLAN_GENERATION_RECORDS_STORAGE_KEY,
        apply: () => {
          state.plans.generationRecords = loadPlanGenerationRecordsFromStorage();
        },
      },
      {
        remoteKey: PLATFORM_RECORD_BUCKET.REQUIREMENT_PLAN_GENERATION_BATCH,
        storageKey: REQUIREMENT_PLAN_GENERATION_BATCHES_STORAGE_KEY,
        apply: () => {
          state.requirements.planGenerationBatches = loadRequirementPlanGenerationBatchesFromStorage();
        },
      },
      {
        remoteKey: PLATFORM_RECORD_BUCKET.SCRIPT_GENERATION,
        storageKey: SCRIPT_GENERATION_RECORDS_STORAGE_KEY,
        apply: () => {
          state.plans.scriptGenerationRecords = loadPlanScriptGenerationRecordsFromStorage();
        },
      },
      {
        remoteKey: PLATFORM_RECORD_BUCKET.PLAN_SCRIPT_GENERATION_BATCH,
        storageKey: PLAN_SCRIPT_GENERATION_BATCHES_STORAGE_KEY,
        apply: () => {
          state.plans.scriptGenerationBatches = loadPlanScriptGenerationBatchesFromStorage();
        },
      },
      {
        remoteKey: PLATFORM_RECORD_BUCKET.TEST_SUITE_EXECUTION,
        storageKey: TEST_SUITE_EXECUTION_RECORDS_STORAGE_KEY,
        apply: () => {
          state.testSuites.executionRecords = loadTestSuiteExecutionRecordsFromStorage();
        },
      },
    ],
  });
}


const generationFeature = createGenerationFeature({
  state,
  elements,
  SECTION,
  PLAN_VIEW_TAB,
  PLAN_GENERATION_MODE,
  COVERAGE_POLICY_START,
  COVERAGE_POLICY_END,
  DEFAULT_COVERAGE_PROFILE,
  SCRIPT_PROMPT_FIXED_TEMPLATE,
  SCRIPT_PROMPT_NOTE_DEFAULT,
  window,
  fetch: (...args) => fetch(...args),
  TextDecoder,
  timers: timerRuntime,
  formatDuration: formatElapsedDuration,
  requirements: {
    getRequirementModuleByUid: (...args) => getRequirementModuleByUid(...args),
    saveRequirementModule: (...args) => saveRequirementModule(...args),
    closeRequirementModuleDetail: (...args) => closeRequirementModuleDetail(...args),
    mergeRequirementModuleUpdate: (...args) => mergeRequirementModuleUpdate(...args),
  },
  getPlanGenerationMode,
  getPlanGenerationPlanFilename,
  getPlanGenerationModuleName,
  getDefaultPlanFilename,
  getDefaultScriptTargetPath,
  getPlanRecordKey,
  renderPlanGenerationModuleOptions,
  setPlanGenerationModuleMode,
  resetPlanGenerationSource,
  isRequirementPlanGeneration,
  setPlanGenerationModuleControlsLocked,
  setupPlanGenerationModuleField,
  normalizePlanGenerationRecord,
  persistPlanGenerationRecords,
  normalizePlanScriptGenerationRecord,
  persistPlanScriptGenerationRecords,
  replaceAllText,
  requestJson,
  encodePathPart,
  getProjectRequestHeaders,
  parseSseBlock,
  setNotice,
  persistViewState,
  renderContent,
  renderSideList,
  renderPlanGenerationRecord,
  loadPlanModules,
  selectPlan,
  selectPlanModule,
  confirmDiscardEdit,
  escapeHtml,
});
const {
  renderGenerationDuration,
  ensurePlanScriptGenerationRecord,
  setPlanScriptGenerationRecord,
  updatePlanScriptGenerationPromptFromInputs,
  getCoverageProfile,
  composeCoveragePrompt,
  populateCoverageSelect,
  renderPlanCoverageState,
  resetPlanPromptForCoverage,
  changePlanCoverageProfile,
  updatePromptForModuleName,
  updateTargetForPlanName,
  updatePlanGenerationMode,
  ensureGenerationDefaults,
  openPlanGenerationModal,
  openRequirementPlanGenerationModal,
  closePlanGenerationModal,
  submitPlanGeneration,
  renderScriptPromptFromTemplate,
  openScriptGenerationModal,
  closeScriptGenerationModal,
  submitScriptGeneration,
} = generationFeature;

const scriptRepairFeature = createScriptRepairFeature({
  state,
  elements,
  SECTION,
  SCRIPT_VIEW_TAB,
  SCRIPT_RUN_PROMPT_FIXED_TEMPLATE,
  SCRIPT_RUN_PROMPT_NOTE_DEFAULT,
  fetch: (...args) => fetch(...args),
  TextDecoder,
  timers: timerRuntime,
  formatDuration: formatElapsedDuration,
  replaceAllText,
  stripSpecSuffix,
  getScriptRunRecordKey,
  normalizeScriptRepairRecord,
  persistScriptRepairRecords,
  persistScriptRunRecords,
  parseSseBlock,
  renderExecutionRecord: (...args) => renderExecutionRecord(...args),
  persistViewState,
  renderContent,
  setNotice,
  getProjectRequestHeaders,
  refreshScriptMetadata,
  confirmDiscardEdit,
});
const {
  formatRepairDuration,
  renderScriptRunDuration,
  ensureScriptRepairRecord,
  setScriptRepairRecord,
  setScriptRunRecord,
  renderScriptRunPromptFromTemplate,
  executeSelectedScript,
  openScriptRepairRecord,
  submitScriptRun,
  updateScriptRepairPromptFromInputs,
} = scriptRepairFeature;

const moduleExecutionFeature = createModuleExecutionFeature({
  state,
  elements,
  SECTION,
  SCRIPT_VIEW_TAB,
  EXECUTION_MODE,
  SCRIPT_RUN_PROMPT_NOTE_DEFAULT,
  document,
  window,
  fetch: (...args) => fetch(...args),
  TextDecoder,
  AbortController,
  timers: timerRuntime,
  scriptRepair: scriptRepairFeature,
  getModuleRecordKey,
  normalizeModuleExecutionRecord,
  persistModuleExecutionRecords,
  normalizeExecutionModeValue,
  normalizeModuleRepairBatch,
  persistModuleRepairBatches,
  persistViewState,
  requestJson,
  encodePathPart,
  setNotice,
  renderContent,
  getSelectedScriptModule,
  loadScriptTree,
  selectScript,
  parseSseBlock,
  openExecutionModeModal,
  getExecutionModeLabel,
  getProjectRequestHeaders,
  stripSpecSuffix,
  getScriptRunRecordKey,
  createStatusBadge,
  getDbResultStatusInfo,
  getDbExecutionModeLabel,
  formatTimestampMs,
});
const {
  enterModuleBulkMode,
  cancelModuleBulkMode,
  cancelModuleRepairBatch,
  toggleModuleSelectAll,
  deleteSelectedModuleScripts,
  executeSelectedModuleScripts,
  repairSelectedModuleScripts,
  renderExecutionHistory,
  isAnyScriptJobRunning,
  renderModuleScriptList,
  renderModuleExecutionRecord,
  formatModuleRepairDuration,
  renderModuleRepairRecord,
  renderExecutionRecord,
  renderScriptRepairRecord,
} = moduleExecutionFeature;


async function recordSelectedScript() {
  const moduleName = state.scripts.selectedModule;
  const filename = state.scripts.selectedFile;

  if (
    state.activeSection !== SECTION.SCRIPTS ||
    !moduleName ||
    !filename ||
    state.isEditing ||
    state.scriptRecording.isRunning ||
    state.scriptExecution.isRunning ||
    state.moduleExecution.isRunning ||
    state.moduleRepair.isRunning ||
    state.scriptRun.isRunning ||
    state.testSuiteExecution.isRunning
  ) {
    return;
  }

  state.scriptRecording.isRunning = true;
  state.scripts.activeTab = SCRIPT_VIEW_TAB.SCRIPT;
  persistViewState();
  renderContent();
  setNotice("正在启动 Playwright codegen。录制完成后请关闭 codegen 窗口，脚本会自动刷新。");

  try {
    const result = await requestJson("/api/script-recordings", {
      method: "POST",
      body: JSON.stringify({
        module_name: moduleName,
        filename,
      }),
    });

    if (result.content !== undefined) {
      state.scripts.currentContent = result.content || "";
      state.scripts.filePath = result.path || state.scripts.filePath;
      state.scripts.asset = normalizeAsset(result.asset) || state.scripts.asset;
      state.scripts.revisions = normalizeRevisionList(result.revisions);
    }

    renderContent();

    if (result.status === "succeeded") {
      setNotice("脚本录制完成，当前脚本已刷新。", "success");
      return;
    }

    setNotice(result.error || "脚本录制失败。", "error");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    state.scriptRecording.isRunning = false;
    renderContent();
  }
}




function switchPlanViewTab(nextTab) {
  if (
    state.activeSection !== SECTION.PLANS ||
    !state.plans.selectedModule ||
    state.plans.activeTab === nextTab
  ) {
    return;
  }

  if (state.isEditing && !confirmDiscardEdit()) {
    return;
  }

  state.isEditing = false;
  state.plans.activeTab = nextTab;
  if (nextTab === PLAN_VIEW_TAB.SCRIPT_GENERATION && state.plans.selectedPlanFile) {
    ensurePlanScriptGenerationRecord();
  }
  persistViewState();
  renderContent();
}

function switchScriptViewTab(nextTab) {
  if (state.scripts.activeTab === nextTab) {
    return;
  }

  if (state.isEditing && !confirmDiscardEdit()) {
    return;
  }

  state.isEditing = false;
  state.scripts.activeTab = nextTab;
  persistViewState();
  renderContent();
}

function filteredPlanModules() {
  const query = getSearchQuery();
  if (!query) {
    return state.plans.modules;
  }

  return state.plans.modules
    .map((moduleItem) => {
      const moduleMatches = moduleItem.name.toLowerCase().includes(query);
      const plans = moduleMatches
        ? moduleItem.plans
        : moduleItem.plans.filter((plan) => {
            const planName = (plan.name || stripMarkdownSuffix(plan.filename)).toLowerCase();
            const filename = plan.filename.toLowerCase();
            return planName.includes(query) || filename.includes(query);
          });

      return { ...moduleItem, plans };
    })
    .filter((moduleItem) => moduleItem.plans.length > 0);
}

function filteredScriptModules() {
  const query = getSearchQuery();
  if (!query) {
    return state.scripts.modules;
  }

  return state.scripts.modules
    .map((moduleItem) => {
      const moduleMatches = moduleItem.name.toLowerCase().includes(query);
      const scripts = moduleMatches
        ? moduleItem.scripts
        : moduleItem.scripts.filter((script) => {
            const scriptName = script.name.toLowerCase();
            const displayName = (script.display_name || stripSpecSuffix(script.name)).toLowerCase();
            return scriptName.includes(query) || displayName.includes(query);
          });

      return { ...moduleItem, scripts };
    })
    .filter((moduleItem) => moduleItem.scripts.length > 0);
}

function renderSideList() {
  renderNavigation();
  elements.moduleList.innerHTML = "";

  if (state.activeSection === SECTION.REQUIREMENTS) {
    renderRequirementList();
    return;
  }

  if (state.activeSection === SECTION.PLANS) {
    renderPlanList();
    return;
  }

  if (state.activeSection === SECTION.TEST_SUITES) {
    return;
  }

  if (isAdminSection() || isProjectSettingsSection() || isAgentSection()) {
    return;
  }

  renderScriptTree();
}

function renderPlanList() {
  const modules = filteredPlanModules();
  const query = getSearchQuery();

  if (!modules.length) {
    const empty = document.createElement("div");
    empty.className = "module-item";
    empty.textContent = state.plans.modules.length ? "没有匹配的计划" : "未找到测试计划";
    elements.moduleList.appendChild(empty);
    return;
  }

  modules.forEach((moduleItem) => {
    const group = document.createElement("div");
    group.className = "tree-group";

    const moduleButton = document.createElement("button");
    moduleButton.type = "button";
    moduleButton.className = "tree-module-button";
    moduleButton.title = moduleItem.path || moduleItem.name;

    const isExpanded = query || state.plans.expandedModules.has(moduleItem.name);
    const isSelectedModule = moduleItem.name === state.plans.selectedModule && !state.plans.selectedPlanFile;
    moduleButton.classList.toggle("active", isSelectedModule);
    moduleButton.innerHTML = `
      <span class="tree-caret" aria-hidden="true">${isExpanded ? "▾" : "▸"}</span>
      <span class="tree-module-name"></span>
      <span class="tree-count">${moduleItem.plans.length}</span>
    `;
    moduleButton.querySelector(".tree-module-name").textContent = moduleItem.name;
    moduleButton.addEventListener("click", () => {
      if (moduleItem.name === state.plans.selectedModule && !state.plans.selectedPlanFile) {
        togglePlanModule(moduleItem.name);
        return;
      }
      selectPlanModule(moduleItem.name);
    });

    group.appendChild(moduleButton);

    if (isExpanded) {
      const planList = document.createElement("div");
      planList.className = "tree-plan-list";

      moduleItem.plans.forEach((plan) => {
        const planButton = document.createElement("button");
        planButton.type = "button";
        planButton.className = "tree-plan-button";
        planButton.textContent = plan.name || stripMarkdownSuffix(plan.filename);
        planButton.title = plan.path || plan.filename;
        planButton.classList.toggle(
          "active",
          moduleItem.name === state.plans.selectedModule && plan.filename === state.plans.selectedPlanFile,
        );
        planButton.addEventListener("click", () => selectPlan(moduleItem.name, plan.filename));
        planList.appendChild(planButton);
      });

      group.appendChild(planList);
    }

    elements.moduleList.appendChild(group);
  });
}

function renderScriptTree() {
  const modules = filteredScriptModules();
  const query = getSearchQuery();

  if (!modules.length) {
    const empty = document.createElement("div");
    empty.className = "module-item";
    empty.textContent = state.scripts.modules.length ? "没有匹配的脚本" : "未找到测试脚本";
    elements.moduleList.appendChild(empty);
    return;
  }

  modules.forEach((moduleItem) => {
    const group = document.createElement("div");
    group.className = "tree-group";

    const moduleButton = document.createElement("button");
    moduleButton.type = "button";
    moduleButton.className = "tree-module-button";
    moduleButton.title = moduleItem.path || moduleItem.name;

    const isExpanded = query || state.scripts.expandedModules.has(moduleItem.name);
    const isSelectedModule = moduleItem.name === state.scripts.selectedModule && !state.scripts.selectedFile;
    moduleButton.classList.toggle("active", isSelectedModule);
    moduleButton.innerHTML = `
      <span class="tree-caret" aria-hidden="true">${isExpanded ? "▾" : "▸"}</span>
      <span class="tree-module-name"></span>
      <span class="tree-count">${moduleItem.scripts.length}</span>
    `;
    moduleButton.querySelector(".tree-module-name").textContent = moduleItem.name;
    moduleButton.addEventListener("click", () => toggleScriptModule(moduleItem.name));

    group.appendChild(moduleButton);

    if (isExpanded) {
      const scriptList = document.createElement("div");
      scriptList.className = "tree-script-list";

      moduleItem.scripts.forEach((script) => {
        const scriptButton = document.createElement("button");
        scriptButton.type = "button";
        scriptButton.className = "tree-script-button";
        scriptButton.textContent = script.display_name || stripSpecSuffix(script.name);
        scriptButton.title = script.path || script.name;
        scriptButton.classList.toggle(
          "active",
          moduleItem.name === state.scripts.selectedModule && script.name === state.scripts.selectedFile,
        );
        scriptButton.addEventListener("click", () => selectScript(moduleItem.name, script.name));
        scriptList.appendChild(scriptButton);
      });

      group.appendChild(scriptList);
    }

    elements.moduleList.appendChild(group);
  });
}

function getCurrentAssetContext() {
  if (state.activeSection === SECTION.PLANS && state.plans.selectedPlanFile) {
    return {
      asset: state.plans.asset,
      revisions: state.plans.revisions,
      kind: "plan",
    };
  }
  if (state.activeSection === SECTION.SCRIPTS && state.scripts.selectedFile) {
    return {
      asset: state.scripts.asset,
      revisions: state.scripts.revisions,
      kind: "script",
    };
  }
  return { asset: null, revisions: [], kind: "" };
}

function renderAssetInfoPanel() {
  const { asset, revisions, kind } = getCurrentAssetContext();
  const shouldShow =
    Boolean(asset) &&
    !state.isEditing &&
    ((kind === "plan" && state.plans.activeTab === PLAN_VIEW_TAB.CONTENT) ||
      (kind === "script" && state.scripts.activeTab === SCRIPT_VIEW_TAB.SCRIPT));

  elements.assetInfoPanel.classList.toggle("hidden", !shouldShow);
  elements.assetInfoPanel.replaceChildren();
  if (!shouldShow) {
    return;
  }

  const header = document.createElement("div");
  header.className = "asset-info-header";
  const title = document.createElement("div");
  title.innerHTML = `<strong>${kind === "plan" ? "测试计划资产" : "测试脚本资产"}</strong>`;
  const meta = document.createElement("span");
  const currentRevision = revisions.find((revision) => revision.revision_id === asset.current_revision_id);
  meta.textContent = currentRevision
    ? `当前版本 v${currentRevision.version_no} · ${currentRevision.git_commit_sha.slice(0, 8)}`
    : "当前版本尚未建立";
  header.append(title, meta);
  elements.assetInfoPanel.appendChild(header);

  if (kind === "script") {
    const source = document.createElement("div");
    source.className = "asset-source-row";
    const sourcePlan = state.scripts.sourcePlan;
    if (sourcePlan) {
      const openButton = document.createElement("button");
      openButton.type = "button";
      openButton.className = "inline-link-button";
      openButton.textContent = sourcePlan.title || "打开来源计划";
      openButton.addEventListener("click", () => {
        const planFilename = (sourcePlan.current_path || "").split(/[\\/]/).pop() || `${sourcePlan.title}.md`;
        state.activeSection = SECTION.PLANS;
        selectPlan(sourcePlan.module_name, planFilename);
      });
      source.append("来源计划：", openButton);
    } else {
      source.textContent = "来源计划：未关联";
    }
    elements.assetInfoPanel.appendChild(source);
  }

  const list = document.createElement("div");
  list.className = "asset-revision-list";
  if (!revisions.length) {
    list.textContent = "暂无版本历史。";
    elements.assetInfoPanel.appendChild(list);
    return;
  }

  revisions.slice(0, 6).forEach((revision) => {
    const item = document.createElement("div");
    item.className = "asset-revision-item";
    const main = document.createElement("span");
    main.textContent = `v${revision.version_no} · ${revision.change_source || "-"} · ${formatTimestampMs(revision.created_at)}`;
    const commit = document.createElement("code");
    commit.textContent = revision.git_commit_sha ? revision.git_commit_sha.slice(0, 8) : "-";
    const restoreButton = document.createElement("button");
    restoreButton.type = "button";
    restoreButton.className = "secondary-button";
    restoreButton.textContent = "恢复";
    restoreButton.disabled = revision.revision_id === asset.current_revision_id || isAnyScriptJobRunning();
    restoreButton.addEventListener("click", () => restoreAssetRevision(asset.asset_id, revision.revision_id));
    item.append(main, commit, restoreButton);
    list.appendChild(item);
  });
  elements.assetInfoPanel.appendChild(list);
}

async function restoreAssetRevision(assetId, revisionId) {
  if (!assetId || !revisionId || isAnyScriptJobRunning()) {
    return;
  }
  const confirmed = window.confirm("恢复此版本会覆盖当前文件，并创建一个新的当前版本。是否继续？");
  if (!confirmed) {
    return;
  }
  try {
    await requestJson(`/api/assets/${assetId}/revisions/${revisionId}/restore`, { method: "POST" });
    setNotice("版本已恢复。", "success");
    if (state.activeSection === SECTION.PLANS && state.plans.selectedModule && state.plans.selectedPlanFile) {
      await selectPlan(state.plans.selectedModule, state.plans.selectedPlanFile, true);
    } else if (state.activeSection === SECTION.SCRIPTS && state.scripts.selectedModule && state.scripts.selectedFile) {
      await selectScript(state.scripts.selectedModule, state.scripts.selectedFile, true);
    }
  } catch (error) {
    setNotice(error.message, "error");
  }
}

function renderPlanRelatedScripts() {
  const scripts = state.plans.relatedScripts || [];
  elements.planRelatedScriptsSummary.textContent = `共 ${scripts.length} 条相关脚本`;
  elements.planRelatedScriptsTableBody.replaceChildren();

  if (!scripts.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.textContent = "暂无相关脚本。通过当前计划生成脚本后会自动建立关联。";
    row.appendChild(cell);
    elements.planRelatedScriptsTableBody.appendChild(row);
    return;
  }

  scripts.forEach((script) => {
    const row = document.createElement("tr");

    const nameCell = document.createElement("td");
    const nameButton = document.createElement("button");
    nameButton.type = "button";
    nameButton.className = "module-script-name-button";
    nameButton.textContent = script.title || stripSpecSuffix((script.current_path || "").split(/[\\/]/).pop() || "");
    nameButton.addEventListener("click", () => {
      const filename = (script.current_path || "").split(/[\\/]/).pop();
      if (filename) {
        state.activeSection = SECTION.SCRIPTS;
        selectScript(script.module_name, filename);
      }
    });
    nameCell.appendChild(nameButton);
    row.appendChild(nameCell);

    const statusCell = document.createElement("td");
    statusCell.appendChild(createStatusBadge(getDbResultStatusInfo(script.last_status)));
    row.appendChild(statusCell);

    const pathCell = document.createElement("td");
    pathCell.textContent = script.current_path || "";
    pathCell.title = script.current_path || "";
    row.appendChild(pathCell);

    const jobCell = document.createElement("td");
    jobCell.textContent = script.source_job_id || "-";
    row.appendChild(jobCell);

    const actionsCell = document.createElement("td");
    const actions = document.createElement("div");
    actions.className = "module-row-actions";
    const openButton = document.createElement("button");
    openButton.type = "button";
    openButton.className = "secondary-button";
    openButton.textContent = "打开";
    openButton.addEventListener("click", () => {
      const filename = (script.current_path || "").split(/[\\/]/).pop();
      if (filename) {
        state.activeSection = SECTION.SCRIPTS;
        selectScript(script.module_name, filename);
      }
    });
    actions.appendChild(openButton);
    actionsCell.appendChild(actions);
    row.appendChild(actionsCell);

    elements.planRelatedScriptsTableBody.appendChild(row);
  });
}


function renderPlanTabs() {
  const showTabs = state.activeSection === SECTION.PLANS && hasSelection();
  elements.planTabs.classList.toggle("hidden", !showTabs);
  elements.planContentTab.textContent = state.plans.selectedPlanFile ? "测试计划" : "计划";

  const isContentTab = state.plans.activeTab === PLAN_VIEW_TAB.CONTENT;
  const isPlanGenerationTab = state.plans.activeTab === PLAN_VIEW_TAB.PLAN_GENERATION;
  const isScriptGenerationTab = state.plans.activeTab === PLAN_VIEW_TAB.SCRIPT_GENERATION;
  const isRelatedScriptsTab = state.plans.activeTab === PLAN_VIEW_TAB.RELATED_SCRIPTS;
  elements.planContentTab.classList.toggle("active", isContentTab);
  elements.planContentTab.setAttribute("aria-selected", String(isContentTab));
  elements.planGenerationRecordTab.classList.toggle("active", isPlanGenerationTab);
  elements.planGenerationRecordTab.setAttribute("aria-selected", String(isPlanGenerationTab));
  elements.planScriptGenerationRecordTab.classList.toggle("active", isScriptGenerationTab);
  elements.planScriptGenerationRecordTab.setAttribute("aria-selected", String(isScriptGenerationTab));
  elements.planRelatedScriptsTab.classList.toggle("active", isRelatedScriptsTab);
  elements.planRelatedScriptsTab.setAttribute("aria-selected", String(isRelatedScriptsTab));
  return showTabs;
}

function renderScriptTabs() {
  const showTabs = state.activeSection === SECTION.SCRIPTS && hasSelection();
  elements.scriptTabs.classList.toggle("hidden", !showTabs);

  const isScriptTab = state.scripts.activeTab === SCRIPT_VIEW_TAB.SCRIPT;
  const isExecutionTab = state.scripts.activeTab === SCRIPT_VIEW_TAB.EXECUTION;
  const isRepairTab = state.scripts.activeTab === SCRIPT_VIEW_TAB.REPAIR;
  elements.scriptContentTab.classList.toggle("active", isScriptTab);
  elements.scriptContentTab.setAttribute("aria-selected", String(isScriptTab));
  elements.executionRecordTab.classList.toggle("active", isExecutionTab);
  elements.executionRecordTab.setAttribute("aria-selected", String(isExecutionTab));
  elements.repairRecordTab.classList.toggle("active", isRepairTab);
  elements.repairRecordTab.setAttribute("aria-selected", String(isRepairTab));
  return showTabs;
}

function getCurrentPlanGenerationRecord() {
  if (!state.plans.selectedModule) {
    return null;
  }
  if (state.plans.selectedPlanFile) {
    return state.plans.generationRecords[getPlanRecordKey()] || null;
  }
  return Object.values(state.plans.generationRecords)
    .filter((record) => record?.module_name === state.plans.selectedModule)
    .sort((left, right) => (right.updated_at || 0) - (left.updated_at || 0))[0] || null;
}

function renderPlanGenerationRecord() {
  const record = getCurrentPlanGenerationRecord();
  const hasRecord = Boolean(record);

  elements.planGenerationRecordEmpty.classList.toggle("hidden", hasRecord);
  elements.planGenerationRecordContent.classList.toggle("hidden", !hasRecord);

  if (!hasRecord) {
    elements.planRecordPrompt.value = "";
    elements.planRecordTargetPath.textContent = "";
    elements.planRecordJobLogs.textContent = "";
    elements.planRecordJobOutput.classList.add("hidden");
    renderGenerationDuration(elements.planRecordDuration, null, "生成进行时间", "生成耗时");
    return;
  }

  elements.planRecordPrompt.value = record.prompt || "";
  elements.planRecordTargetPath.textContent = record.target_path || "";
  elements.planRecordJobLogs.textContent = record.logs || "";
  elements.planRecordJobLogs.scrollTop = elements.planRecordJobLogs.scrollHeight;
  elements.planRecordJobOutput.classList.toggle("hidden", !record.logs && record.status === "idle");
  elements.planRecordJobStatus.className = "job-status";
  const coverageMeta = record.coverage_profile
    ? `模板来源：${getCoverageProfile(record.coverage_profile)?.label || "核心回归"}${record.prompt_customized ? " · 已自定义" : ""}`
    : "";
  renderGenerationDuration(elements.planRecordDuration, record, "生成进行时间", "生成耗时");

  if (record.status === "succeeded") {
    elements.planRecordJobStatus.textContent = coverageMeta ? `任务成功 · ${coverageMeta}` : "任务成功";
    elements.planRecordJobStatus.classList.add("success");
    return;
  }

  if (record.status === "failed") {
    elements.planRecordJobStatus.textContent = `任务失败${coverageMeta ? ` · ${coverageMeta}` : ""}${record.error ? `：${record.error}` : ""}`;
    elements.planRecordJobStatus.classList.add("error");
    return;
  }

  if (record.status === "running") {
    elements.planRecordJobStatus.textContent = `任务进行中${coverageMeta ? ` · ${coverageMeta}` : ""}，正在接收实时输出`;
    return;
  }

  elements.planRecordJobStatus.textContent = "任务进行中";
}

function renderPlanScriptGenerationRecord() {
  const record = ensurePlanScriptGenerationRecord();
  if (!record) {
    elements.planScriptPromptFixed.value = "";
    elements.planScriptPromptNote.value = "";
    elements.planScriptRecordTargetPath.textContent = "";
    elements.planScriptJobLogs.textContent = "";
    elements.planScriptJobOutput.classList.add("hidden");
    renderGenerationDuration(elements.planScriptDuration, null, "生成进行时间", "生成耗时");
    elements.planScriptGenerationSubmit.disabled = true;
    elements.planScriptGenerationSubmit.textContent = "确认生成";
    return;
  }

  elements.planScriptPromptFixed.value = record.prompt_fixed;
  elements.planScriptPromptNote.value = record.prompt_note;
  elements.planScriptRecordTargetPath.textContent = record.target_path || getDefaultScriptTargetPath(record.module_name);
  elements.planScriptJobLogs.textContent = record.logs || "";
  elements.planScriptJobLogs.scrollTop = elements.planScriptJobLogs.scrollHeight;
  elements.planScriptJobOutput.classList.toggle("hidden", !record.logs && record.status === "idle");
  elements.planScriptJobStatus.className = "job-status";
  renderGenerationDuration(elements.planScriptDuration, record, "生成进行时间", "生成耗时");

  if (record.status === "succeeded") {
    elements.planScriptJobStatus.textContent = "任务成功";
    elements.planScriptJobStatus.classList.add("success");
    elements.planScriptGenerationSubmit.disabled = state.scriptGeneration.isRunning;
    elements.planScriptGenerationSubmit.textContent = "重新生成";
    return;
  }

  if (record.status === "failed") {
    elements.planScriptJobStatus.textContent = `任务失败${record.error ? `：${record.error}` : ""}`;
    elements.planScriptJobStatus.classList.add("error");
    elements.planScriptGenerationSubmit.disabled = state.scriptGeneration.isRunning;
    elements.planScriptGenerationSubmit.textContent = "重试";
    return;
  }

  if (record.status === "running" || state.scriptGeneration.isRunning) {
    elements.planScriptJobStatus.textContent = "任务进行中，正在接收实时输出";
    elements.planScriptGenerationSubmit.disabled = true;
    elements.planScriptGenerationSubmit.textContent = "生成中";
    return;
  }

  elements.planScriptJobStatus.textContent = "任务进行中";
  elements.planScriptGenerationSubmit.disabled = state.scriptGeneration.isRunning;
  elements.planScriptGenerationSubmit.textContent = "确认生成";
}


function getGenerationStatusInfo(recordOrItem) {
  if (recordOrItem?.status === "queued") {
    return { label: "排队", className: "queued" };
  }
  if (recordOrItem?.status === "running") {
    return { label: "生成中", className: "running" };
  }
  if (recordOrItem?.status === "succeeded") {
    return { label: "成功", className: "success" };
  }
  if (recordOrItem?.status === "failed") {
    return { label: "失败", className: "error" };
  }
  return { label: "未生成", className: "" };
}

function createStatusBadge(info) {
  const badge = document.createElement("span");
  badge.className = `status-badge ${info.className || ""}`.trim();
  badge.textContent = info.label;
  return badge;
}

const testSuitesFeature = createTestSuitesFeature({
  state,
  elements,
  SECTION,
  TEST_SUITE_VIEW_TAB,
  TEST_SUITE_ALL_MODULE,
  EXECUTION_MODE,
  document,
  window,
  fetch: (...args) => fetch(...args),
  TextDecoder,
  resultHelpers: testSuiteResultHelpers,
  getSuiteScriptKey,
  stripSpecSuffix,
  normalizeTestSuiteExecutionArtifact,
  normalizeTestSuiteExecutionRunList,
  formatTimestampMs,
  getDbExecutionModeLabel,
  getDbResultStatusInfo,
  isAnyScriptJobRunning,
  persistViewState,
  persistTestSuiteExecutionRecords,
  renderContent,
  renderSideList,
  setNotice,
  setLoading,
  requestJson,
  encodePathPart,
  normalizeTestSuite,
  normalizeTestSuiteExecutionRecord,
  normalizeExecutionModeValue,
  parseSseBlock,
  openExecutionModeModal,
  getExecutionModeLabel,
  getProjectRequestHeaders,
});
const {
  getSelectedTestSuite,
  resetTestSuiteExecutionHistory,
  renderTestSuiteList,
  renderExecutionResultPanel,
  closeTestSuiteProgressModal,
  closeTestSuiteExecutionVideo,
  renderTestSuiteDetail,
  openTestSuiteCreateModal,
  closeTestSuiteCreateModal,
  closeTestSuiteRenameModal,
  submitTestSuiteCreate,
  loadTestSuiteExecutionRecords,
  submitTestSuiteRename,
  switchTestSuiteViewTab,
  backToTestSuiteList,
  openSuiteScriptModal,
  closeSuiteScriptModal,
  submitSuiteScripts,
  executeSelectedTestSuite,
  loadTestSuites,
} = testSuitesFeature;


const modulePlanGenerationFeature = createModulePlanGenerationFeature({
  state,
  elements,
  SECTION,
  PLAN_VIEW_TAB,
  SCRIPT_PROMPT_NOTE_DEFAULT,
  document,
  window,
  fetch: (...args) => fetch(...args),
  TextDecoder,
  generation: generationFeature,
  moduleExecution: moduleExecutionFeature,
  getSelectedPlanModule,
  getGeneratedScriptFilenameFromPlan,
  getSelectedScriptModule,
  requestJson,
  encodePathPart,
  stripMarkdownSuffix,
  loadPlanModules,
  setNotice,
  renderContent,
  selectPlan,
  getPlanModuleRecordKey,
  normalizePlanScriptGenerationBatch,
  persistPlanScriptGenerationBatches,
  parseSseBlock,
  getDefaultScriptTargetPath,
  getProjectRequestHeaders,
  persistViewState,
  loadScriptTree,
  renderSideList,
  createStatusBadge,
  getGenerationStatusInfo,
  getPlanRecordKey,
});
const {
  getCurrentModulePlans,
  getExpectedScriptFilenameForPlan,
  findScriptForPlan,
  pruneModuleSelectedPlanFiles,
  isModulePlanActionBusy,
  enterModulePlanBulkMode,
  cancelModulePlanBulkMode,
  toggleModulePlanSelectAll,
  deleteSelectedModulePlans,
  generatePlanScriptFromModule,
  setPlanScriptGenerationBatch,
  setPlanScriptGenerationBatchItem,
  appendPlanScriptBatchLog,
  readModulePlanScriptGenerationStream,
  handleModulePlanScriptStreamEvent,
  generateSelectedModulePlanScripts,
  renderModulePlanList,
  renderModulePlanScriptBatchRecord,
} = modulePlanGenerationFeature;


function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}


const projectSettingsFeature = createProjectSettingsFeature({
  state,
  elements,
  DEFAULT_COVERAGE_PROFILE,
  PROJECT_SETTINGS_VIEW_TAB,
  fetch: (...args) => fetch(...args),
  TextDecoder,
  setupFeature,
  projects: projectsFeature,
  jobs: moduleExecutionFeature,
  requestJson,
  setNotice,
  setLoading,
  renderContent,
  parseSseBlock,
  getProjectRequestHeaders,
  isPlainObject,
  escapeHtml,
  t,
});
const {
  loadProjectSettings,
  renderProjectSettingsPanel,
} = projectSettingsFeature;

const requirementsFeature = createRequirementsFeature({
  state,
  elements,
  SECTION,
  REQUIREMENT_VIEW_TAB,
  PLAN_GENERATION_MODE,
  PLAN_VIEW_TAB,
  document,
  window,
  fetch: (...args) => fetch(...args),
  TextDecoder,
  FormData,
  CSS,
  renderContent,
  renderSideList,
  getSearchQuery,
  escapeHtml,
  formatTimestampMs,
  isPlainObject,
  getChinesePlanFilenameFromName,
  normalizeRequirementPlanGenerationBatch,
  persistRequirementPlanGenerationBatches,
  normalizeRequirementModule,
  isAnyScriptJobRunning,
  requestJson,
  encodePathPart,
  setNotice,
  parseSseBlock,
  getCoverageProfile,
  ensureGenerationDefaults,
  populateCoverageSelect,
  composeCoveragePrompt,
  getProjectRequestHeaders,
  persistViewState,
  formatModuleRepairDuration,
  createStatusBadge,
  getGenerationStatusInfo,
  openRequirementPlanGenerationModal,
  loadPlanModules,
  selectPlan,
  confirmDiscardEdit,
  setLoading,
  normalizeRequirement,
});
const {
  switchRequirementViewTab,
  renderRequirementList,
  enterRequirementModuleBulkMode,
  cancelRequirementModuleBulkMode,
  deleteSelectedRequirementModules,
  renderRequirementBatchPromptState,
  openRequirementBatchPlanModal,
  closeRequirementBatchPlanModal,
  changeRequirementBatchCoverageProfile,
  resetRequirementBatchCoveragePrompt,
  generateSelectedRequirementModulePlans,
  renderRequirementsPanel,
  getRequirementModuleByUid,
  mergeRequirementModuleUpdate,
  closeRequirementModuleDetail,
  saveRequirementModule,
  analyzeSelectedRequirement,
  importInventoryFromDefaultDoc,
  loadRequirements,
  uploadRequirementFile,
} = requirementsFeature;

function renderContent() {
  ensureAllowedActiveSection();
  const selected = hasSelection();
  const isRequirementsSection = state.activeSection === SECTION.REQUIREMENTS;
  const isTestSuiteSection = state.activeSection === SECTION.TEST_SUITES;
  const isAgentSectionActive = state.activeSection === SECTION.AGENT;
  const adminSection = isAdminSection();
  const projectSettingsSection = isProjectSettingsSection();
  elements.requirementHeaderActions.classList.toggle("hidden", !isRequirementsSection);
  if (!isRequirementsSection && !elements.requirementModuleDetailModal.classList.contains("hidden")) {
    closeRequirementModuleDetail();
  }
  elements.editSaveButton.classList.toggle(
    "hidden",
    isRequirementsSection || isTestSuiteSection || isAgentSectionActive || adminSection || projectSettingsSection,
  );
  elements.editSaveButton.disabled =
    !canEditSelection() ||
    state.isSaving ||
    state.scriptRecording.isRunning ||
    state.moduleExecution.isRunning ||
    state.moduleRepair.isRunning ||
    state.testSuiteExecution.isRunning;
  elements.editSaveButton.textContent = state.isEditing ? "保存" : "编辑";
  elements.cancelButton.classList.toggle("hidden", !state.isEditing || isRequirementsSection || isTestSuiteSection || isAgentSectionActive);
  renderProjectSelect();
  const showGenerateScript =
    state.activeSection === SECTION.PLANS && Boolean(state.plans.selectedModule && state.plans.selectedPlanFile);
  const showScriptActions =
    state.activeSection === SECTION.SCRIPTS && Boolean(state.scripts.selectedModule && state.scripts.selectedFile);
  elements.generateScriptButton.classList.toggle("hidden", !showGenerateScript);
  elements.generateScriptButton.disabled =
    !showGenerateScript ||
    state.isSaving ||
    state.isEditing ||
    state.generation.isRunning ||
    state.scriptGeneration.isRunning;
  elements.recordScriptButton.classList.toggle("hidden", !showScriptActions);
  elements.recordScriptButton.disabled =
    !showScriptActions ||
    state.isSaving ||
    state.isEditing ||
    state.scriptRecording.isRunning ||
    state.scriptExecution.isRunning ||
    state.moduleExecution.isRunning ||
    state.moduleRepair.isRunning ||
    state.scriptRun.isRunning ||
    state.testSuiteExecution.isRunning;
  elements.recordScriptButton.textContent = state.scriptRecording.isRunning ? "录制中" : "录制脚本";
  elements.executeScriptButton.classList.toggle("hidden", !showScriptActions);
  elements.executeScriptButton.disabled =
    !showScriptActions ||
    state.isSaving ||
    state.isEditing ||
    state.scriptRecording.isRunning ||
    state.scriptExecution.isRunning ||
    state.moduleExecution.isRunning ||
    state.moduleRepair.isRunning ||
    state.scriptRun.isRunning ||
    state.testSuiteExecution.isRunning;
  elements.executeScriptButton.textContent = state.scriptExecution.isRunning ? "执行中" : "执行脚本";
  elements.runScriptButton.classList.toggle("hidden", !showScriptActions);
  elements.runScriptButton.disabled =
    !showScriptActions ||
    state.isSaving ||
    state.isEditing ||
    state.scriptRecording.isRunning ||
    state.scriptExecution.isRunning ||
    state.moduleExecution.isRunning ||
    state.moduleRepair.isRunning ||
    state.scriptRun.isRunning ||
    state.testSuiteExecution.isRunning;
  elements.runScriptButton.textContent = state.scriptRun.isRunning ? "修复中" : "修复脚本";

  elements.preview.classList.add("hidden");
  elements.planGenerationRecord.classList.add("hidden");
  elements.planScriptGenerationRecord.classList.add("hidden");
  elements.modulePlanPanel.classList.add("hidden");
  elements.modulePlanScriptBatchRecord.classList.add("hidden");
  elements.assetInfoPanel.classList.add("hidden");
  elements.planRelatedScriptsPanel.classList.add("hidden");
  elements.requirementsPanel.classList.add("hidden");
  elements.scriptPreview.classList.add("hidden");
  elements.testSuiteListPanel.classList.add("hidden");
  elements.testSuiteDetailPanel.classList.add("hidden");
  elements.agentPanel.classList.add("hidden");
  elements.moduleScriptPanel.classList.add("hidden");
  elements.moduleExecutionRecord.classList.add("hidden");
  elements.moduleRepairRecord.classList.add("hidden");
  elements.executionRecord.classList.add("hidden");
  elements.executionHistoryPanel.classList.add("hidden");
  elements.scriptRepairRecord.classList.add("hidden");
  elements.userAdminPanel.classList.add("hidden");
  elements.roleAdminPanel.classList.add("hidden");
  elements.projectSettingsPanel.classList.add("hidden");
  elements.editor.classList.add("hidden");
  elements.editor.classList.toggle("code-editor", state.activeSection === SECTION.SCRIPTS);
  elements.editor.wrap = state.activeSection === SECTION.SCRIPTS ? "off" : "soft";
  const showPlanTabs = renderPlanTabs();
  const showScriptTabs = renderScriptTabs();
  elements.viewerArea.classList.toggle("with-tabs", showPlanTabs || showScriptTabs);

  if (!getFirstAllowedSection()) {
    elements.moduleTitle.textContent = "暂无可用菜单";
    elements.filePath.textContent = "请联系管理员配置角色权限";
    elements.emptyState.classList.remove("hidden");
    elements.emptyState.querySelector("h3").textContent = "暂无可访问功能";
    elements.emptyState.querySelector("p").textContent = "当前账号没有分配菜单权限。";
    return;
  }

  if (adminSection) {
    elements.emptyState.classList.add("hidden");
    if (state.activeSection === SECTION.USERS) {
      elements.moduleTitle.textContent = "用户管理";
      elements.filePath.textContent = "账号、状态和角色";
      renderUserAdminPanel();
      elements.userAdminPanel.classList.remove("hidden");
    } else {
      elements.moduleTitle.textContent = "角色管理";
      elements.filePath.textContent = "角色和菜单权限";
      renderRoleAdminPanel();
      elements.roleAdminPanel.classList.remove("hidden");
    }
    return;
  }

  if (projectSettingsSection) {
    elements.emptyState.classList.add("hidden");
    elements.moduleTitle.textContent = "项目配置";
    elements.filePath.textContent = state.project.current?.playwright_project_root || "";
    renderProjectSettingsPanel();
    elements.projectSettingsPanel.classList.remove("hidden");
    return;
  }

  if (isAgentSectionActive) {
    elements.emptyState.classList.add("hidden");
    elements.moduleTitle.textContent = "Agent 自动测试";
    elements.filePath.textContent = state.project.current?.name || state.project.currentKey || "";
    elements.agentPanel.classList.remove("hidden");
    return;
  }

  if (isRequirementsSection) {
    if (!state.requirements.current) {
      elements.moduleTitle.textContent = "需求";
      elements.filePath.textContent = "未选择需求";
      elements.analyzeRequirementButton.disabled = true;
      elements.analyzeRequirementButton.textContent = "解析需求";
      elements.importInventoryButton.disabled = state.requirements.analysisRunning || state.requirements.planGenerationRunning;
      elements.emptyState.classList.remove("hidden");
      elements.emptyState.querySelector("h3").textContent = "暂无需求";
      elements.emptyState.querySelector("p").textContent = "上传 Markdown 需求文件后，可以解析模块候选并生成测试计划。";
      return;
    }
    elements.emptyState.classList.add("hidden");
    elements.moduleTitle.textContent = state.requirements.current.title || "需求";
    elements.filePath.textContent = state.requirements.current.file_path || "";
    renderRequirementsPanel();
    elements.requirementsPanel.classList.remove("hidden");
    return;
  }

  if (isTestSuiteSection) {
    const suite = getSelectedTestSuite();
    if (state.testSuites.selectedSuiteId && !suite) {
      state.testSuites.selectedSuiteId = null;
      state.testSuites.selectedModule = TEST_SUITE_ALL_MODULE;
      persistViewState();
    }

    elements.emptyState.classList.add("hidden");
    if (suite) {
      elements.moduleTitle.textContent = suite.name;
      elements.filePath.textContent = `测试集详情 / 脚本数量：${suite.items.length}`;
      renderTestSuiteDetail();
      elements.testSuiteDetailPanel.classList.remove("hidden");
    } else {
      elements.moduleTitle.textContent = "测试集";
      elements.filePath.textContent = "测试集列表";
      renderTestSuiteList();
      elements.testSuiteListPanel.classList.remove("hidden");
    }
    return;
  }

  if (!selected) {
    const isPlan = state.activeSection === SECTION.PLANS;
    elements.moduleTitle.textContent = isPlan ? "请选择模块" : "请选择测试用例";
    elements.filePath.textContent = "未选择文件";
    elements.emptyState.classList.remove("hidden");
    elements.emptyState.querySelector("h3").textContent = isPlan ? "暂无测试计划" : "暂无测试脚本";
    elements.emptyState.querySelector("p").textContent = isPlan
      ? "左侧会显示 specs/<模块名>/*.md。"
      : "左侧会显示 tests/<模块名>/*.spec.ts。";
    elements.preview.innerHTML = "";
    elements.scriptCode.textContent = "";
    renderModuleScriptList();
    renderModuleExecutionRecord();
    renderModuleRepairRecord();
    renderExecutionRecord();
    renderScriptRepairRecord();
    elements.editor.value = "";
    return;
  }

  elements.emptyState.classList.add("hidden");

  if (state.activeSection === SECTION.PLANS) {
    if (!state.plans.selectedPlanFile) {
      const moduleItem = getSelectedPlanModule();
      elements.moduleTitle.textContent = state.plans.selectedModule || "请选择模块";
      elements.filePath.textContent = moduleItem?.path || "";
      elements.preview.innerHTML = "";
      elements.editor.value = "";
      renderModulePlanList();
      renderModulePlanScriptBatchRecord();
      if (state.plans.activeTab === PLAN_VIEW_TAB.PLAN_GENERATION) {
        renderPlanGenerationRecord();
        elements.planGenerationRecord.classList.remove("hidden");
      } else if (state.plans.activeTab === PLAN_VIEW_TAB.SCRIPT_GENERATION) {
        elements.modulePlanScriptBatchRecord.classList.remove("hidden");
      } else {
        elements.modulePlanPanel.classList.remove("hidden");
      }
    } else {
      elements.moduleTitle.textContent = `${state.plans.selectedModule} / ${stripMarkdownSuffix(
        state.plans.selectedPlanFile,
      )}`;
      elements.filePath.textContent = state.plans.filePath || "";
      elements.preview.innerHTML = state.plans.currentHtml;
      elements.editor.value = state.plans.currentMarkdown;
      renderAssetInfoPanel();
      renderPlanGenerationRecord();
      renderPlanScriptGenerationRecord();
      renderPlanRelatedScripts();
      if (state.plans.activeTab === PLAN_VIEW_TAB.PLAN_GENERATION) {
        elements.planGenerationRecord.classList.remove("hidden");
      } else if (state.plans.activeTab === PLAN_VIEW_TAB.SCRIPT_GENERATION) {
        elements.planScriptGenerationRecord.classList.remove("hidden");
      } else if (state.plans.activeTab === PLAN_VIEW_TAB.RELATED_SCRIPTS) {
        elements.planRelatedScriptsPanel.classList.remove("hidden");
      } else {
        elements.preview.classList.toggle("hidden", state.isEditing);
      }
    }
  } else {
    const moduleItem = getSelectedScriptModule();
    if (!state.scripts.selectedFile) {
      elements.moduleTitle.textContent = state.scripts.selectedModule || "请选择模块";
      elements.filePath.textContent = moduleItem?.path || "";
      elements.scriptCode.textContent = "";
      elements.editor.value = "";
      renderModuleScriptList();
      renderModuleExecutionRecord();
      renderModuleRepairRecord();
      if (state.scripts.activeTab === SCRIPT_VIEW_TAB.EXECUTION) {
        elements.moduleExecutionRecord.classList.remove("hidden");
      } else if (state.scripts.activeTab === SCRIPT_VIEW_TAB.REPAIR) {
        elements.moduleRepairRecord.classList.remove("hidden");
      } else {
        elements.moduleScriptPanel.classList.remove("hidden");
      }
    } else {
      elements.moduleTitle.textContent = stripSpecSuffix(state.scripts.selectedFile);
      elements.filePath.textContent = state.scripts.filePath || "";
      elements.scriptCode.textContent = state.scripts.currentContent;
      elements.editor.value = state.scripts.currentContent;
      renderAssetInfoPanel();
      renderExecutionRecord();
      renderScriptRepairRecord();
      if (state.scripts.activeTab === SCRIPT_VIEW_TAB.EXECUTION) {
        renderExecutionHistory();
        elements.executionRecord.classList.remove("hidden");
      } else if (state.scripts.activeTab === SCRIPT_VIEW_TAB.REPAIR) {
        elements.scriptRepairRecord.classList.remove("hidden");
      } else {
        elements.scriptPreview.classList.toggle("hidden", state.isEditing);
      }
    }
  }

  elements.editor.classList.toggle(
    "hidden",
    !state.isEditing ||
      state.activeSection === SECTION.PLANS && state.plans.activeTab !== PLAN_VIEW_TAB.CONTENT ||
      state.activeSection === SECTION.SCRIPTS && state.scripts.activeTab !== SCRIPT_VIEW_TAB.SCRIPT,
  );

  if (state.isEditing) {
    elements.editor.focus();
  }
}

async function switchSection(nextSection) {
  if (!hasMenu(nextSection)) {
    setNotice("当前账号没有访问该菜单的权限。", "error");
    return;
  }

  if (nextSection === state.activeSection) {
    return;
  }

  if (!confirmDiscardEdit()) {
    return;
  }

  if (isAgentSection() && nextSection !== SECTION.AGENT) {
    deactivateAgentSection();
  }

  state.isEditing = false;
  state.activeSection = nextSection;
  elements.moduleSearch.value = "";
  setNotice("");
  persistViewState();
  renderSideList();
  renderContent();
  await loadActiveSection();
}

async function loadActiveSection() {
  if (state.activeSection === SECTION.USERS) {
    await loadAdminUsers();
    return;
  }

  if (state.activeSection === SECTION.ROLES) {
    await loadAdminRoles();
    return;
  }

  if (state.activeSection === SECTION.PROJECT_SETTINGS) {
    await loadProjectSettings();
    return;
  }

  if (state.activeSection === SECTION.REQUIREMENTS) {
    await loadRequirements();
    return;
  }

  if (state.activeSection === SECTION.PLANS) {
    await loadPlanModules();
    return;
  }

  if (state.activeSection === SECTION.TEST_SUITES) {
    await loadTestSuites();
    return;
  }

  if (state.activeSection === SECTION.AGENT) {
    await activateAgentSection();
    return;
  }

  await loadScriptTree();
}

async function refreshActiveSection() {
  if (!confirmDiscardEdit()) {
    return;
  }

  state.isEditing = false;
  await loadActiveSection();
}

async function loadPlanModules() {
  setNotice("");
  setLoading(true);

  try {
    const data = await requestJson("/api/modules");
    state.plans.modules = (data.modules || []).map(normalizePlanModule).filter((moduleItem) => moduleItem.plans.length);

    if (!state.plans.modules.length) {
      state.plans.selectedModule = null;
      state.plans.selectedPlanFile = null;
      state.plans.currentMarkdown = "";
      state.plans.currentHtml = "";
      state.plans.filePath = "";
      state.plans.asset = null;
      state.plans.revisions = [];
      state.plans.relatedScripts = [];
      renderSideList();
      renderContent();
      setNotice("没有找到符合 specs/<模块名>/*.md 规则的测试计划。");
      return;
    }

    const selectedModule = state.plans.modules.find((item) => item.name === state.plans.selectedModule);
    const selectedPlan = selectedModule?.plans.find((item) => item.filename === state.plans.selectedPlanFile);
    const nextModule = selectedModule || state.plans.modules[0];
    renderSideList();
    if (selectedPlan) {
      await selectPlan(nextModule.name, selectedPlan.filename, true);
    } else {
      await selectPlanModule(nextModule.name, true);
    }
  } catch (error) {
    state.plans.modules = [];
    state.plans.selectedModule = null;
    state.plans.selectedPlanFile = null;
    state.plans.currentMarkdown = "";
    state.plans.currentHtml = "";
    state.plans.filePath = "";
    state.plans.asset = null;
    state.plans.revisions = [];
    state.plans.relatedScripts = [];
    renderSideList();
    renderContent();
    setNotice(error.message, "error");
  } finally {
    setLoading(false);
  }
}

function togglePlanModule(moduleName) {
  if (state.plans.expandedModules.has(moduleName)) {
    state.plans.expandedModules.delete(moduleName);
  } else {
    state.plans.expandedModules.add(moduleName);
  }
  persistViewState();
  renderSideList();
}

async function selectPlanModule(moduleName, skipConfirm = false) {
  const sameSelection = moduleName === state.plans.selectedModule && !state.plans.selectedPlanFile;
  if (!skipConfirm && !sameSelection && !confirmDiscardEdit()) {
    return;
  }

  const moduleItem = getSelectedPlanModule(moduleName) || state.plans.modules.find((item) => item.name === moduleName);
  state.activeSection = SECTION.PLANS;
  state.plans.selectedModule = moduleName;
  state.plans.selectedPlanFile = null;
  state.plans.currentMarkdown = "";
  state.plans.currentHtml = "";
  state.plans.filePath = moduleItem?.path || "";
  state.plans.asset = null;
  state.plans.revisions = [];
  state.plans.relatedScripts = [];
  state.plans.expandedModules.add(moduleName);
  state.plans.selectedPlanFiles.clear();
  state.plans.bulkSelectionMode = false;
  state.plans.activeTab = PLAN_VIEW_TAB.CONTENT;
  state.isEditing = false;
  persistViewState();
  setNotice("");
  renderSideList();
  renderContent();
}

async function selectPlan(moduleName, planFilename, skipConfirm = false) {
  const sameSelection = moduleName === state.plans.selectedModule && planFilename === state.plans.selectedPlanFile;
  if (!skipConfirm && !sameSelection && !confirmDiscardEdit()) {
    return;
  }

  state.activeSection = SECTION.PLANS;
  state.plans.selectedModule = moduleName;
  state.plans.selectedPlanFile = planFilename || getDefaultPlanFilename(moduleName);
  state.plans.expandedModules.add(moduleName);
  state.plans.selectedPlanFiles.clear();
  state.plans.bulkSelectionMode = false;
  state.isEditing = false;
  persistViewState();
  setNotice("");
  setLoading(true);
  renderSideList();

  try {
    const data = await requestJson(
      `/api/plans/${encodePathPart(moduleName)}/${encodePathPart(state.plans.selectedPlanFile)}`,
    );
    state.plans.currentMarkdown = data.markdown || "";
    state.plans.currentHtml = data.html || "";
    state.plans.filePath = data.path || "";
    state.plans.selectedPlanFile = data.plan_filename || state.plans.selectedPlanFile;
    state.plans.asset = normalizeAsset(data.asset);
    state.plans.revisions = normalizeRevisionList(data.revisions);
    state.plans.relatedScripts = normalizeAssetList(data.related_scripts);
    persistViewState();
    renderContent();
  } catch (error) {
    state.plans.currentMarkdown = "";
    state.plans.currentHtml = "";
    state.plans.filePath = "读取失败";
    state.plans.asset = null;
    state.plans.revisions = [];
    state.plans.relatedScripts = [];
    renderContent();
    setNotice(error.message, "error");
  } finally {
    setLoading(false);
  }
}

async function loadScriptTree() {
  setNotice("");
  setLoading(true);

  try {
    const data = await requestJson("/api/test-scripts");
    state.scripts.modules = data.modules || [];

    const selectedModule = state.scripts.modules.find((item) => item.name === state.scripts.selectedModule);
    const selectedScript = selectedModule?.scripts.find((item) => item.name === state.scripts.selectedFile);

    if (!state.scripts.modules.length) {
      state.scripts.selectedModule = null;
      state.scripts.selectedFile = null;
      state.scripts.currentContent = "";
      state.scripts.filePath = "";
      state.scripts.asset = null;
      state.scripts.revisions = [];
      state.scripts.sourcePlan = null;
      state.scripts.recentResults = [];
      renderSideList();
      renderContent();
      setNotice("没有找到符合 tests/<模块名>/*.spec.ts 规则的测试脚本。");
      return;
    }

    const nextModule = selectedModule || state.scripts.modules[0];
    const nextScript = selectedScript || null;
    state.scripts.expandedModules.add(nextModule.name);
    renderSideList();
    if (nextScript) {
      await selectScript(nextModule.name, nextScript.name, true);
    } else {
      selectScriptModule(nextModule.name, true);
    }
  } catch (error) {
    state.scripts.modules = [];
    state.scripts.selectedModule = null;
    state.scripts.selectedFile = null;
    state.scripts.currentContent = "";
    state.scripts.filePath = "";
    state.scripts.asset = null;
    state.scripts.revisions = [];
    state.scripts.sourcePlan = null;
    state.scripts.recentResults = [];
    renderSideList();
    renderContent();
    setNotice(error.message, "error");
  } finally {
    setLoading(false);
  }
}

function toggleScriptModule(moduleName) {
  if (state.isEditing && !confirmDiscardEdit()) {
    return;
  }

  if (state.scripts.expandedModules.has(moduleName)) {
    state.scripts.expandedModules.delete(moduleName);
  } else {
    state.scripts.expandedModules.add(moduleName);
  }

  selectScriptModule(moduleName, true, { preserveTab: state.scripts.selectedModule === moduleName });
}

function selectScriptModule(moduleName, skipConfirm = false, { preserveTab = false } = {}) {
  const sameSelection = moduleName === state.scripts.selectedModule && !state.scripts.selectedFile;
  if (!skipConfirm && !sameSelection && !confirmDiscardEdit()) {
    return;
  }

  state.activeSection = SECTION.SCRIPTS;
  state.scripts.selectedModule = moduleName;
  state.scripts.selectedFile = null;
  state.scripts.currentContent = "";
  state.scripts.filePath = "";
  state.scripts.asset = null;
  state.scripts.revisions = [];
  state.scripts.sourcePlan = null;
  state.scripts.recentResults = [];
  state.scripts.selectedFiles.clear();
  state.scripts.bulkSelectionMode = false;
  state.scripts.activeTab = preserveTab ? state.scripts.activeTab : SCRIPT_VIEW_TAB.SCRIPT;
  state.isEditing = false;
  persistViewState();
  setNotice("");
  renderSideList();
  renderContent();
}

async function selectScript(moduleName, filename, skipConfirm = false) {
  const sameSelection = moduleName === state.scripts.selectedModule && filename === state.scripts.selectedFile;
  if (!skipConfirm && !sameSelection && !confirmDiscardEdit()) {
    return;
  }

  state.activeSection = SECTION.SCRIPTS;
  state.scripts.selectedModule = moduleName;
  state.scripts.selectedFile = filename;
  state.scripts.expandedModules.add(moduleName);
  state.scripts.bulkSelectionMode = false;
  state.scripts.selectedFiles.clear();
  state.scripts.activeTab = sameSelection ? state.scripts.activeTab : SCRIPT_VIEW_TAB.SCRIPT;
  state.isEditing = false;
  persistViewState();
  setNotice("");
  setLoading(true);
  renderSideList();

  try {
    const data = await requestJson(
      `/api/test-scripts/${encodePathPart(moduleName)}/${encodePathPart(filename)}`,
    );
    state.scripts.currentContent = data.content || "";
    state.scripts.filePath = data.path || "";
    state.scripts.asset = normalizeAsset(data.asset);
    state.scripts.revisions = normalizeRevisionList(data.revisions);
    state.scripts.sourcePlan = normalizeAsset(data.source_plan);
    state.scripts.recentResults = normalizeRunResultList(data.recent_results);
    renderContent();
  } catch (error) {
    state.scripts.currentContent = "";
    state.scripts.filePath = "读取失败";
    state.scripts.asset = null;
    state.scripts.revisions = [];
    state.scripts.sourcePlan = null;
    state.scripts.recentResults = [];
    renderContent();
    setNotice(error.message, "error");
  } finally {
    setLoading(false);
  }
}

async function refreshScriptMetadata(moduleName = state.scripts.selectedModule, filename = state.scripts.selectedFile) {
  if (!moduleName || !filename) {
    return;
  }
  const data = await requestJson(`/api/test-scripts/${encodePathPart(moduleName)}/${encodePathPart(filename)}`);
  if (state.scripts.selectedModule === moduleName && state.scripts.selectedFile === filename) {
    state.scripts.currentContent = data.content || "";
    state.scripts.filePath = data.path || "";
    state.scripts.asset = normalizeAsset(data.asset);
    state.scripts.revisions = normalizeRevisionList(data.revisions);
    state.scripts.sourcePlan = normalizeAsset(data.source_plan);
    state.scripts.recentResults = normalizeRunResultList(data.recent_results);
  }
}

async function saveCurrentItem() {
  if (!canEditSelection()) {
    return;
  }

  state.isSaving = true;
  elements.editSaveButton.textContent = "保存中";
  elements.editSaveButton.disabled = true;
  elements.cancelButton.disabled = true;
  setNotice("");

  try {
    if (state.activeSection === SECTION.PLANS) {
      const nextMarkdown = elements.editor.value;
      const data = await requestJson(
        `/api/plans/${encodePathPart(state.plans.selectedModule)}/${encodePathPart(state.plans.selectedPlanFile)}`,
        {
          method: "PUT",
          body: JSON.stringify({ markdown: nextMarkdown }),
        },
      );
      state.plans.currentMarkdown = data.markdown || "";
      state.plans.currentHtml = data.html || "";
      state.plans.filePath = data.path || "";
      state.plans.selectedPlanFile = data.plan_filename || state.plans.selectedPlanFile;
      state.plans.asset = normalizeAsset(data.asset);
      state.plans.revisions = normalizeRevisionList(data.revisions);
      state.plans.relatedScripts = normalizeAssetList(data.related_scripts);
      persistViewState();
    } else {
      const nextContent = elements.editor.value;
      const data = await requestJson(
        `/api/test-scripts/${encodePathPart(state.scripts.selectedModule)}/${encodePathPart(
          state.scripts.selectedFile,
        )}`,
        {
          method: "PUT",
          body: JSON.stringify({ content: nextContent }),
        },
      );
      state.scripts.currentContent = data.content || "";
      state.scripts.filePath = data.path || "";
      state.scripts.asset = normalizeAsset(data.asset);
      state.scripts.revisions = normalizeRevisionList(data.revisions);
      state.scripts.sourcePlan = normalizeAsset(data.source_plan);
      state.scripts.recentResults = normalizeRunResultList(data.recent_results);
    }

    state.isEditing = false;
    renderContent();
    setNotice("保存成功。", "success");
  } catch (error) {
    setNotice(error.message, "error");
    elements.editor.focus();
  } finally {
    state.isSaving = false;
    elements.cancelButton.disabled = false;
    renderContent();
  }
}

function toggleEditSave() {
  if (!canEditSelection()) {
    return;
  }

  if (!state.isEditing) {
    state.isEditing = true;
    renderContent();
    return;
  }

  saveCurrentItem();
}

function cancelEdit() {
  state.isEditing = false;
  renderContent();
  setNotice("");
}

function ensureAgentController() {
  if (!state.agent.controller) {
    state.agent.controller = createAgentAutoTest(elements.agentPanel, {
      projectKey: state.project.currentKey,
      apiClient,
      renderExecutionResultPanel,
      parseSseBlock,
    });
  }
  return state.agent.controller;
}

async function activateAgentSection() {
  const controller = ensureAgentController();
  await controller.activate(state.project.currentKey);
}

function deactivateAgentSection() {
  state.agent.controller?.deactivate();
}

elements.requirementsNav.addEventListener("click", () => switchSection(SECTION.REQUIREMENTS));
elements.plansNav.addEventListener("click", () => switchSection(SECTION.PLANS));
elements.scriptsNav.addEventListener("click", () => switchSection(SECTION.SCRIPTS));
elements.testSuitesNav.addEventListener("click", () => switchSection(SECTION.TEST_SUITES));
elements.agentNav.addEventListener("click", () => switchSection(SECTION.AGENT));
elements.projectSettingsNav.addEventListener("click", () => switchSection(SECTION.PROJECT_SETTINGS));
elements.usersNav.addEventListener("click", () => switchSection(SECTION.USERS));
elements.rolesNav.addEventListener("click", () => switchSection(SECTION.ROLES));
elements.projectSelect.addEventListener("change", () => switchProject(elements.projectSelect.value));
elements.createProjectButton.addEventListener("click", openProjectCreateModal);
elements.exportProjectButton.addEventListener("click", exportCurrentProject);
elements.importProjectButton.addEventListener("click", openProjectImportModal);
elements.languageMenuButton.addEventListener("click", () => {
  if (!state.auth.isAdmin) return;
  const opening = elements.languageMenu.classList.contains("hidden");
  elements.languageMenu.classList.toggle("hidden", !opening);
  elements.languageMenuButton.setAttribute("aria-expanded", String(opening));
  if (opening) elements.languageMenu.querySelector('[aria-checked="true"]')?.focus();
});
elements.languageMenu.addEventListener("click", (event) => {
  const item = event.target.closest("[data-language]");
  if (item) setProjectLanguage(item.dataset.language);
});
elements.languageMenu.addEventListener("keydown", (event) => {
  const choices = [...elements.languageMenu.querySelectorAll("[data-language]")];
  const index = choices.indexOf(document.activeElement);
  if (index < 0) return;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    choices[(index + (event.key === "ArrowDown" ? 1 : choices.length - 1)) % choices.length].focus();
  }
});
document.addEventListener("click", (event) => {
  if (!elements.languageMenuControl.contains(event.target)) closeLanguageMenu();
});
elements.logoutButton.addEventListener("click", async () => {
  try {
    await requestJson("/api/auth/logout", { method: "POST" });
  } finally {
    window.location.href = "/login";
  }
});
elements.createModuleButton.addEventListener("click", openPlanGenerationModal);
elements.uploadRequirementButton.addEventListener("click", () => {
  elements.requirementFileInput.value = "";
  elements.requirementFileInput.click();
});
elements.requirementFileInput.addEventListener("change", () => uploadRequirementFile(elements.requirementFileInput.files?.[0]));
elements.analyzeRequirementButton.addEventListener("click", analyzeSelectedRequirement);
elements.importInventoryButton.addEventListener("click", importInventoryFromDefaultDoc);
elements.requirementPreviewTab.addEventListener("click", () => switchRequirementViewTab(REQUIREMENT_VIEW_TAB.PREVIEW));
elements.requirementModulesTab.addEventListener("click", () => switchRequirementViewTab(REQUIREMENT_VIEW_TAB.MODULES));
elements.requirementPlanGenerationBatchTab?.addEventListener("click", () =>
  switchRequirementViewTab(REQUIREMENT_VIEW_TAB.PLAN_GENERATION_BATCH),
);
elements.requirementModuleBulkToggle?.addEventListener("click", enterRequirementModuleBulkMode);
elements.requirementModuleBulkCancel?.addEventListener("click", cancelRequirementModuleBulkMode);
elements.requirementModuleBulkDelete?.addEventListener("click", deleteSelectedRequirementModules);
elements.requirementModuleBulkGenerate?.addEventListener("click", openRequirementBatchPlanModal);
elements.requirementModuleDetailClose.addEventListener("click", closeRequirementModuleDetail);
elements.createTestSuiteButton.addEventListener("click", openTestSuiteCreateModal);
elements.backToTestSuiteListButton.addEventListener("click", backToTestSuiteList);
elements.openAddSuiteScriptsButton.addEventListener("click", openSuiteScriptModal);
elements.executeTestSuiteButton.addEventListener("click", executeSelectedTestSuite);
elements.generateScriptButton.addEventListener("click", openScriptGenerationModal);
elements.recordScriptButton.addEventListener("click", recordSelectedScript);
elements.executeScriptButton.addEventListener("click", executeSelectedScript);
elements.runScriptButton.addEventListener("click", openScriptRepairRecord);
elements.planGenerationClose.addEventListener("click", closePlanGenerationModal);
elements.planGenerationCancel.addEventListener("click", closePlanGenerationModal);
elements.planGenerationSubmit.addEventListener("click", submitPlanGeneration);
elements.planCoverageProfile.addEventListener("change", changePlanCoverageProfile);
elements.planPrompt.addEventListener("input", renderPlanCoverageState);
elements.planPromptReset.addEventListener("click", () => resetPlanPromptForCoverage(false));
elements.requirementBatchPlanClose.addEventListener("click", closeRequirementBatchPlanModal);
elements.requirementBatchPlanCancel.addEventListener("click", closeRequirementBatchPlanModal);
elements.requirementBatchPlanSubmit.addEventListener("click", generateSelectedRequirementModulePlans);
elements.requirementBatchCoverageProfile.addEventListener("change", changeRequirementBatchCoverageProfile);
elements.requirementBatchCoveragePrompt.addEventListener("input", renderRequirementBatchPromptState);
elements.requirementBatchPromptReset.addEventListener("click", resetRequirementBatchCoveragePrompt);
elements.scriptGenerationClose.addEventListener("click", closeScriptGenerationModal);
elements.scriptGenerationCancel.addEventListener("click", closeScriptGenerationModal);
elements.scriptGenerationSubmit.addEventListener("click", submitScriptGeneration);
elements.projectCreateClose.addEventListener("click", closeProjectCreateModal);
elements.projectCreateCancel.addEventListener("click", closeProjectCreateModal);
elements.projectCreateSubmit.addEventListener("click", submitProjectCreate);
elements.projectImportClose.addEventListener("click", closeProjectImportModal);
elements.projectImportCancel.addEventListener("click", closeProjectImportModal);
elements.projectImportSubmit.addEventListener("click", submitProjectImport);
[
  elements.newProjectKey,
  elements.newProjectName,
  elements.newProjectSpecsDir,
  elements.newProjectTestsDir,
  elements.newProjectDescription,
].forEach((input) => input.addEventListener("input", () => input.setCustomValidity("")));
[
  elements.projectImportFile,
  elements.importProjectKey,
  elements.importProjectName,
  elements.importProjectSpecsDir,
  elements.importProjectTestsDir,
  elements.importProjectDescription,
].forEach((input) => input.addEventListener("input", () => input.setCustomValidity("")));
elements.newProjectDescription.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    submitProjectCreate();
  }
});
elements.importProjectDescription.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    submitProjectImport();
  }
});
elements.planScriptGenerationSubmit.addEventListener("click", submitScriptGeneration);
elements.planScriptPromptFixed.addEventListener("input", updatePlanScriptGenerationPromptFromInputs);
elements.planScriptPromptNote.addEventListener("input", updatePlanScriptGenerationPromptFromInputs);
elements.scriptRunSubmit.addEventListener("click", submitScriptRun);
elements.scriptRunPromptFixed.addEventListener("input", updateScriptRepairPromptFromInputs);
elements.scriptRunPromptNote.addEventListener("input", updateScriptRepairPromptFromInputs);
elements.testSuiteCreateClose.addEventListener("click", closeTestSuiteCreateModal);
elements.testSuiteCreateCancel.addEventListener("click", closeTestSuiteCreateModal);
elements.testSuiteCreateSubmit.addEventListener("click", submitTestSuiteCreate);
elements.newTestSuiteName.addEventListener("input", () => elements.newTestSuiteName.setCustomValidity(""));
elements.newTestSuiteName.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    submitTestSuiteCreate();
  }
});
elements.testSuiteRenameClose.addEventListener("click", closeTestSuiteRenameModal);
elements.testSuiteRenameCancel.addEventListener("click", closeTestSuiteRenameModal);
elements.testSuiteRenameSubmit.addEventListener("click", submitTestSuiteRename);
elements.renameTestSuiteName.addEventListener("input", () => elements.renameTestSuiteName.setCustomValidity(""));
elements.renameTestSuiteName.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    submitTestSuiteRename();
  }
});
elements.suiteScriptModalClose.addEventListener("click", closeSuiteScriptModal);
elements.suiteScriptModalCancel.addEventListener("click", closeSuiteScriptModal);
elements.suiteScriptModalSubmit.addEventListener("click", submitSuiteScripts);
elements.executionModeClose.addEventListener("click", () => closeExecutionModeModal(null));
elements.executionModeCancel.addEventListener("click", () => closeExecutionModeModal(null));
elements.executionModeSubmit.addEventListener("click", () => closeExecutionModeModal(getSelectedExecutionMode()));
elements.testSuiteScriptsTab.addEventListener("click", () => switchTestSuiteViewTab(TEST_SUITE_VIEW_TAB.SCRIPTS));
elements.testSuiteExecutionTab.addEventListener("click", () => switchTestSuiteViewTab(TEST_SUITE_VIEW_TAB.EXECUTION));
elements.testSuiteProgressModalClose.addEventListener("click", closeTestSuiteProgressModal);
elements.testSuiteProgressModalDismiss.addEventListener("click", closeTestSuiteProgressModal);
elements.testSuiteProgressModal.addEventListener("click", (event) => {
  if (event.target === elements.testSuiteProgressModal) {
    closeTestSuiteProgressModal();
  }
});
elements.testSuiteVideoModalClose.addEventListener("click", closeTestSuiteExecutionVideo);
elements.testSuiteVideoModal.addEventListener("click", (event) => {
  if (event.target === elements.testSuiteVideoModal) {
    closeTestSuiteExecutionVideo();
  }
});
elements.planContentTab.addEventListener("click", () => switchPlanViewTab(PLAN_VIEW_TAB.CONTENT));
elements.planGenerationRecordTab.addEventListener("click", () => switchPlanViewTab(PLAN_VIEW_TAB.PLAN_GENERATION));
elements.planScriptGenerationRecordTab.addEventListener("click", () => switchPlanViewTab(PLAN_VIEW_TAB.SCRIPT_GENERATION));
elements.planRelatedScriptsTab.addEventListener("click", () => switchPlanViewTab(PLAN_VIEW_TAB.RELATED_SCRIPTS));
elements.scriptContentTab.addEventListener("click", () => switchScriptViewTab(SCRIPT_VIEW_TAB.SCRIPT));
elements.executionRecordTab.addEventListener("click", () => switchScriptViewTab(SCRIPT_VIEW_TAB.EXECUTION));
elements.repairRecordTab.addEventListener("click", () => switchScriptViewTab(SCRIPT_VIEW_TAB.REPAIR));
elements.moduleBulkToggle.addEventListener("click", enterModuleBulkMode);
elements.moduleBulkCancel.addEventListener("click", cancelModuleBulkMode);
elements.moduleBulkExecute.addEventListener("click", executeSelectedModuleScripts);
elements.moduleBulkRepair.addEventListener("click", repairSelectedModuleScripts);
elements.moduleBulkDelete.addEventListener("click", deleteSelectedModuleScripts);
elements.moduleRepairCancelButton.addEventListener("click", cancelModuleRepairBatch);
elements.moduleSelectAll.addEventListener("change", toggleModuleSelectAll);
elements.modulePlanBulkToggle.addEventListener("click", enterModulePlanBulkMode);
elements.modulePlanBulkCancel.addEventListener("click", cancelModulePlanBulkMode);
elements.modulePlanBulkGenerate.addEventListener("click", generateSelectedModulePlanScripts);
elements.modulePlanBulkDelete.addEventListener("click", deleteSelectedModulePlans);
elements.modulePlanSelectAll.addEventListener("change", toggleModulePlanSelectAll);
elements.addModuleNameLink.addEventListener("click", togglePlanGenerationModuleMode);
elements.newModuleNameSelect.addEventListener("change", updatePromptForModuleName);
elements.newModuleName.addEventListener("input", updatePromptForModuleName);
elements.newPlanName.addEventListener("input", () => {
  state.generation.autoProfilePlanName = false;
  updateTargetForPlanName();
});
elements.planModeMultiple.addEventListener("change", updatePlanGenerationMode);
elements.planModeSingle.addEventListener("change", updatePlanGenerationMode);
elements.refreshButton.addEventListener("click", refreshActiveSection);
elements.moduleSearch.addEventListener("input", renderSideList);
elements.editSaveButton.addEventListener("click", toggleEditSave);
elements.cancelButton.addEventListener("click", cancelEdit);

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !elements.languageMenu.classList.contains("hidden")) {
    closeLanguageMenu();
    elements.languageMenuButton.focus();
    return;
  }
  if ((event.key === "Enter" || event.key === " ") && document.activeElement?.matches("#languageMenu [data-language]")) {
    event.preventDefault();
    setProjectLanguage(document.activeElement.dataset.language);
    return;
  }
  if (event.key === "Escape" && !elements.requirementBatchPlanModal.classList.contains("hidden")) {
    closeRequirementBatchPlanModal();
    return;
  }
  if (event.key === "Escape" && !elements.requirementModuleDetailModal.classList.contains("hidden")) {
    closeRequirementModuleDetail();
    return;
  }

  if (event.key === "Escape" && !elements.planGenerationModal.classList.contains("hidden")) {
    closePlanGenerationModal();
    return;
  }

  if (event.key === "Escape" && !elements.scriptGenerationModal.classList.contains("hidden")) {
    closeScriptGenerationModal();
    return;
  }

  if (event.key === "Escape" && !elements.testSuiteCreateModal.classList.contains("hidden")) {
    closeTestSuiteCreateModal();
    return;
  }

  if (event.key === "Escape" && !elements.testSuiteRenameModal.classList.contains("hidden")) {
    closeTestSuiteRenameModal();
    return;
  }

  if (event.key === "Escape" && !elements.suiteScriptModal.classList.contains("hidden")) {
    closeSuiteScriptModal();
    return;
  }

  if (event.key === "Escape" && !elements.executionModeModal.classList.contains("hidden")) {
    closeExecutionModeModal(null);
    return;
  }

  if (event.key === "Escape" && !elements.testSuiteProgressModal.classList.contains("hidden")) {
    closeTestSuiteProgressModal();
    return;
  }

  if (event.key === "Escape" && !elements.testSuiteVideoModal.classList.contains("hidden")) {
    closeTestSuiteExecutionVideo();
    return;
  }

  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    if (state.isEditing) {
      event.preventDefault();
      saveCurrentItem();
    }
  }
});

async function bootstrap() {
  await loadAuthContext();
  await loadProjects();
  applyProjectLanguage();
  resetProjectScopedState();
  renderSideList();
  renderContent();
  await hydratePlatformRecords();
  ensureAllowedActiveSection();
  renderSideList();
  renderContent();
  await loadActiveSection();
}

bootstrap();
