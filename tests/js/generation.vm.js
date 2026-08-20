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
  "static/js/features/generation.js",
]) {
  vm.runInContext(fs.readFileSync(path.join(appDir, filename), "utf8"), context);
}

function createElement() {
  const classes = new Set();
  const element = {
    textContent: "",
    innerHTML: "",
    value: "",
    disabled: false,
    scrollTop: 0,
    scrollHeight: 0,
    focus() {},
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
  };
  Object.defineProperty(element, "className", {
    get: () => Array.from(classes).join(" "),
    set: (value) => {
      classes.clear();
      String(value || "")
        .split(/\s+/)
        .filter(Boolean)
        .forEach((name) => classes.add(name));
    },
  });
  return element;
}

const state = {
  activeSection: "other",
  isEditing: false,
  generation: {
    jobId: null,
    isRunning: false,
    pollTimer: null,
    durationTimer: null,
    coverageProfile: "core",
    coverageProfiles: [
      {
        key: "core",
        label: "核心回归",
        description: "核心路径",
        suggested_max_cases: 8,
        template_prompt: "覆盖核心路径",
      },
      {
        key: "full",
        label: "完整回归",
        description: "完整路径",
        suggested_max_cases: 20,
        template_prompt: "覆盖完整路径",
      },
    ],
    defaultCoverageProfile: "core",
    defaultComposedPrompt: "",
    basePrompt: "",
  },
  plans: {
    modules: [],
    selectedModule: "登录",
    selectedPlanFile: "登录.md",
    activeTab: "content",
    generationRecords: {},
    scriptGenerationRecords: {},
    expandedModules: new Set(),
  },
  scriptGeneration: { isRunning: false, durationTimer: null },
};
const elements = {
  planJobOutput: createElement(),
  planJobStatus: createElement(),
  planJobLogs: createElement(),
  planGenerationSubmit: createElement(),
  planRecordDuration: createElement(),
  planScriptDuration: createElement(),
  planScriptJobOutput: createElement(),
  planScriptJobStatus: createElement(),
  planScriptJobLogs: createElement(),
  planScriptGenerationSubmit: createElement(),
  planPrompt: createElement(),
  planPromptCustomized: createElement(),
  planCoverageDescription: createElement(),
};

let nextTimerId = 10;
const activeTimers = new Map();
const clearedTimers = [];
const timerHost = {
  setInterval(callback, delay) {
    const id = ++nextTimerId;
    activeTimers.set(id, { callback, delay });
    return id;
  },
  clearInterval(id) {
    clearedTimers.push(id);
    activeTimers.delete(id);
  },
};
const persistence = { plan: [], script: [] };
const splitRequests = [];
const selectedPlans = [];
const selectedModules = [];
let loadPlanModulesCalls = 0;
const getPlanRecordKey = (
  moduleName = state.plans.selectedModule,
  planFilename = state.plans.selectedPlanFile,
) => (moduleName && planFilename ? `${moduleName}/${planFilename}` : "");
const normalizeRecord = (value) => value;
const feature = context.window.createGenerationFeature({
  state,
  elements,
  SECTION: { PLANS: "plans" },
  PLAN_VIEW_TAB: {
    CONTENT: "content",
    PLAN_GENERATION: "planGeneration",
    SCRIPT_GENERATION: "scriptGeneration",
  },
  PLAN_GENERATION_MODE: { MULTIPLE: "multiple", SINGLE: "single" },
  COVERAGE_POLICY_START: "<<<COVERAGE_POLICY_START>>>",
  COVERAGE_POLICY_END: "<<<COVERAGE_POLICY_END>>>",
  DEFAULT_COVERAGE_PROFILE: "core",
  getScriptPromptFixedTemplate: () =>
    "Generate specs/<module>/<test-plan-file> to a candidate script",
  getScriptPromptNoteDefault: () => "Additional guidance",
  window: {
    confirm: () => true,
    WaterfallI18n: {
      log: (value) => value,
      t: (key, params = {}) => {
        const messages = {
          "generation.elapsed": "Generation elapsed: {duration}",
          "generation.duration": "Generation time: {duration}",
        };
        return Object.entries(params).reduce(
          (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
          messages[key] || key,
        );
      },
    },
  },
  fetch: async () => {
    throw new Error("Focused generation VM paths must not issue fetch");
  },
  TextDecoder,
  timers: context.window.createTimerRuntime(timerHost),
  formatDuration: context.window.formatElapsedDuration,
  requirements: {
    getRequirementModuleByUid: () => null,
    saveRequirementModule: async () => null,
    closeRequirementModuleDetail: () => {},
    mergeRequirementModuleUpdate: () => {},
  },
  getPlanGenerationMode: () => "multiple",
  getPlanGenerationPlanFilename: (moduleName) => `${moduleName}.md`,
  getPlanGenerationModuleName: () => state.plans.selectedModule,
  getDefaultPlanFilename: (moduleName) => `${moduleName}.md`,
  getDefaultScriptTargetPath: (moduleName) => `tests/${moduleName}`,
  getPlanRecordKey,
  renderPlanGenerationModuleOptions: () => {},
  setPlanGenerationModuleMode: () => {},
  resetPlanGenerationSource: () => {},
  isRequirementPlanGeneration: () => false,
  setPlanGenerationModuleControlsLocked: () => {},
  setupPlanGenerationModuleField: () => state.plans.selectedModule,
  normalizePlanGenerationRecord: normalizeRecord,
  persistPlanGenerationRecords: (key) => persistence.plan.push(key),
  normalizePlanScriptGenerationRecord: normalizeRecord,
  persistPlanScriptGenerationRecords: (key) => persistence.script.push(key),
  replaceAllText: (value, search, replacement) =>
    String(value).split(search).join(replacement),
  requestJson: async (url) => {
    splitRequests.push(url);
    return { created: [{ filename: "legacy-split.md" }], reused: [] };
  },
  encodePathPart: encodeURIComponent,
  getProjectRequestHeaders: (headers) => headers,
  parseSseBlock: context.window.parseSseBlock,
  setNotice: () => {},
  persistViewState: () => {},
  renderContent: () => {},
  renderSideList: () => {},
  renderPlanGenerationRecord: () => {},
  loadPlanModules: async () => {
    loadPlanModulesCalls += 1;
  },
  selectPlan: async (...args) => selectedPlans.push(args),
  selectPlanModule: async (...args) => selectedModules.push(args),
  confirmDiscardEdit: () => true,
  escapeHtml: String,
});

const composed = feature.composeCoveragePrompt("基础提示", "覆盖核心路径");
assert.ok(composed.includes("<<<COVERAGE_POLICY_START>>>"));
assert.strictEqual(feature.extractCoveragePolicy(composed), "覆盖核心路径");
const replaced = feature.replaceCoveragePolicy(composed, "覆盖完整路径");
assert.strictEqual(feature.extractCoveragePolicy(replaced), "覆盖完整路径");
assert.strictEqual((replaced.match(/COVERAGE_POLICY_START/g) || []).length, 1);
assert.strictEqual(
  feature.replaceCoveragePolicy("仅基础提示", ""),
  "仅基础提示",
);

const planKey = getPlanRecordKey();
feature.setPlanGenerationRecord("登录", "登录.md", {
  status: "running",
  started_at: Date.now() - 2000,
  logs: "",
});
assert.deepStrictEqual(persistence.plan, [planKey]);
feature.startPlanGenerationDurationTimer();
const planTimerId = state.generation.durationTimer;
assert.ok(activeTimers.has(planTimerId));
assert.ok(elements.planRecordDuration.textContent.startsWith("Generation elapsed: "));
feature.stopPlanGenerationDurationTimer();
assert.strictEqual(state.generation.durationTimer, null);
assert.ok(clearedTimers.includes(planTimerId));

feature.ensurePlanScriptGenerationRecord("登录", "登录.md");
assert.deepStrictEqual(persistence.script, [planKey]);
assert.ok(
  feature.renderScriptPromptFromTemplate("login", "login.md").includes(
    "specs/login/login.md",
  ),
);
feature.setPlanScriptGenerationRecord("登录", "登录.md", {
  status: "running",
  started_at: Date.now() - 1000,
  logs: "",
});
feature.startPlanScriptGenerationDurationTimer();
const scriptTimerId = state.scriptGeneration.durationTimer;
feature.stopPlanScriptGenerationDurationTimer();
assert.ok(clearedTimers.includes(scriptTimerId));

let planResult = { status: "running", logs: "" };
planResult = feature.handlePlanStreamEvent(
  {
    event: "status",
    data: { status: "running", target_path: "specs/登录/登录.md" },
  },
  planResult,
  "登录",
  "登录.md",
);
planResult = feature.handlePlanStreamEvent(
  { event: "log", data: { message: "开始生成" } },
  planResult,
  "登录",
  "登录.md",
);
planResult = feature.handlePlanStreamEvent(
  { event: "delta", data: { text: "增量内容" } },
  planResult,
  "登录",
  "登录.md",
);
planResult = feature.handlePlanStreamEvent(
  {
    event: "status",
    data: {
      status: "succeeded",
      plans: [{ plan_filename: "login-happy-path.md" }],
      split: { created: [{ filename: "login-happy-path.md" }] },
      deleted_source: { plan_filename: "login-case-index.md" },
    },
  },
  planResult,
  "登录",
  "登录.md",
);
planResult = feature.handlePlanStreamEvent(
  { event: "done", data: { ok: true, plan_filename: "登录.md" } },
  planResult,
  "登录",
  "登录.md",
);
assert.strictEqual(planResult.status, "succeeded");
assert.strictEqual(planResult.logs, "开始生成\n增量内容");
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(planResult.plans)),
  [{ plan_filename: "login-happy-path.md" }],
  "The terminal event must retain split files from the succeeded status payload.",
);
assert.strictEqual(state.plans.generationRecords[planKey].status, "succeeded");

const failedPlan = feature.handlePlanStreamEvent(
  { event: "done", data: { ok: false, error: "生成失败" } },
  { status: "running", logs: "已有日志\n" },
  "登录",
  "登录.md",
);
assert.strictEqual(failedPlan.status, "failed");
assert.ok(elements.planJobStatus.textContent.includes("生成失败"));

const cancelledPlan = feature.handlePlanStreamEvent(
  { event: "done", data: { ok: false, status: "cancelled", error: "用户终止" } },
  { status: "running", logs: "已有日志\n" },
  "登录",
  "登录.md",
);
assert.strictEqual(cancelledPlan.status, "cancelled");
assert.strictEqual(elements.planJobStatus.textContent, "任务已取消");

let scriptResult = { status: "running", logs: "" };
scriptResult = feature.handleScriptStreamEvent(
  { event: "log", data: { message: "脚本开始" } },
  scriptResult,
  "登录",
  "登录.md",
);
scriptResult = feature.handleScriptStreamEvent(
  { event: "done", data: { ok: true } },
  scriptResult,
  "登录",
  "登录.md",
);
assert.strictEqual(scriptResult.status, "succeeded");
assert.strictEqual(
  state.plans.scriptGenerationRecords[planKey].status,
  "succeeded",
);

const cancelledScript = feature.handleScriptStreamEvent(
  { event: "done", data: { ok: false, status: "cancelled", error: "用户终止" } },
  { status: "running", logs: "脚本日志\n" },
  "登录",
  "登录.md",
);
assert.strictEqual(cancelledScript.status, "cancelled");
assert.strictEqual(elements.planScriptJobStatus.textContent, "任务已取消");

function streamResponse(chunks) {
  let index = 0;
  return {
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

(async () => {
  await feature.openPlanGenerationResult(
    {
      plans: [
        { plan_filename: "login-happy-path.md" },
        { plan_filename: "login-invalid-password.md" },
      ],
    },
    "Login",
    "login-case-index.md",
    "multiple",
  );
  assert.strictEqual(splitRequests.length, 0, "A finalized stream payload must not be split twice.");
  assert.deepStrictEqual(selectedPlans.at(-1), ["Login", "login-happy-path.md", true]);

  await feature.openPlanGenerationResult(
    { split: { reused: [{ filename: "historical-login.md" }] } },
    "Login",
    "login-case-index.md",
    "multiple",
  );
  assert.strictEqual(splitRequests.length, 0, "Historical nested split payloads remain compatible.");
  assert.deepStrictEqual(selectedPlans.at(-1), ["Login", "historical-login.md", true]);

  state.plans.modules = [{ name: "Login", plans: [{ filename: "server-split.md" }] }];
  await feature.openPlanGenerationResult({}, "Login", "missing-index.md", "multiple");
  assert.strictEqual(splitRequests.length, 0, "A missing finalized source is not posted for a second split.");
  assert.deepStrictEqual(selectedPlans.at(-1), ["Login", "server-split.md", true]);

  state.plans.modules = [{ name: "Login", plans: [{ filename: "legacy-index.md" }] }];
  await feature.openPlanGenerationResult({}, "Login", "legacy-index.md", "multiple");
  assert.strictEqual(splitRequests.length, 1, "A legacy unsplit response uses the split endpoint once.");
  assert.ok(splitRequests[0].endsWith("/legacy-index.md/split-cases"));
  assert.deepStrictEqual(selectedPlans.at(-1), ["Login", "legacy-split.md", true]);
  assert.strictEqual(selectedModules.length, 0);
  assert.strictEqual(loadPlanModulesCalls, 5);

  const streamed = await feature.readPlanGenerationStream(
    streamResponse([
      'event: log\ndata: {"message":"分',
      '片日志"}\n\nevent: done\ndata: {"ok":true,"plan_filename":"登录.md"}\n\n',
    ]),
    "登录",
    "登录.md",
  );
  assert.strictEqual(streamed.status, "succeeded");
  assert.strictEqual(streamed.logs, "分片日志\n");
  assert.ok(persistence.plan.length >= 5);
  assert.ok(persistence.script.length >= 3);
  process.stdout.write("generation feature VM smoke: ok\n");
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
