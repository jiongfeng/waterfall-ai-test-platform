const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");
const context = { window: {}, TextDecoder };
vm.createContext(context);
for (const filename of [
  "static/js/core/sse.js",
  "static/js/features/project-settings.js",
]) {
  vm.runInContext(fs.readFileSync(path.join(appDir, filename), "utf8"), context);
}

function field(value = "") {
  return {
    value,
    textContent: "",
    innerHTML: "",
    className: "",
    scrollTop: 0,
    scrollHeight: 0,
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {},
  };
}

const fields = {
  "#projectTargetBaseUrl": field("https://example.com"),
  "#projectTargetLoginUrl": field("/login"),
  "#projectTargetUsernameEnv": field("TARGET_DEMO_USERNAME"),
  "#projectTargetPasswordEnv": field("TARGET_DEMO_PASSWORD"),
  "#projectDefaultCoverageProfile": field("full"),
  "#projectSettingsOutput": field(),
};
const projectSettingsPanel = field();
projectSettingsPanel.querySelector = (selector) => fields[selector] || null;
projectSettingsPanel.querySelectorAll = () => [];
const state = {
  project: {
    currentKey: "alpha",
    current: { project_key: "alpha", name: "Alpha" },
  },
  generation: { coverageProfiles: [] },
  projectSettings: {
    loaded: false,
    isSaving: false,
    isGeneratingSeed: false,
    isTestingSeed: false,
    output: "",
    activeTab: "basic",
    setup: { loaded: false },
    targetSystem: {},
    databaseBaseline: {},
    planGeneration: {},
    coverageProfiles: [],
  },
};
const elements = { projectSettingsPanel };
let renderCalls = 0;
const requests = [];
const normalizeProject = (value) =>
  value?.project_key ? { ...value, key: value.project_key } : null;
const feature = context.window.createProjectSettingsFeature({
  state,
  elements,
  DEFAULT_COVERAGE_PROFILE: "core",
  PROJECT_SETTINGS_VIEW_TAB: { BASIC: "basic", SETUP: "setup" },
  fetch: async () => {
    throw new Error("focused settings VM paths must not fetch");
  },
  TextDecoder,
  setupFeature: {
    load: async () => {},
    renderMarkup: () => "<div>setup</div>",
    bindEvents: () => {},
  },
  projects: {
    normalizeProject,
    loadProjects: async () => {},
  },
  jobs: { isAnyScriptJobRunning: () => false },
  requestJson: async (url) => {
    requests.push(url);
    if (url === "/api/project-settings") {
      return {
        project: {
          project_key: "alpha",
          name: "Alpha",
        },
        target_system: {
          base_url: "https://target.example",
          login_url: "/signin",
          username_env: "TARGET_USERNAME",
          password_env: "TARGET_PASSWORD",
        },
        database_baseline: { enabled: true },
        plan_generation: { default_coverage_profile: "full" },
        coverage_profiles: [{ key: "full", label: "完整回归" }],
        seed_script_path: "tests/seed/seed.spec.ts",
      };
    }
    throw new Error(`unexpected request ${url}`);
  },
  setNotice: () => {},
  setLoading: () => {},
  renderContent: () => {
    renderCalls += 1;
  },
  parseSseBlock: context.window.parseSseBlock,
  getProjectRequestHeaders: (headers) => headers,
  isPlainObject: (value) =>
    Boolean(value && typeof value === "object" && !Array.isArray(value)),
  escapeHtml: String,
});

const normalized = feature.normalizeTargetSystem({
  base_url: " https://example.com ",
  login_url: "",
  username_env: 42,
});
assert.strictEqual(normalized.base_url, " https://example.com ");
assert.strictEqual(normalized.login_url, "/login");
assert.strictEqual(normalized.username_env, "TARGET_SYSTEM_USERNAME");

const payload = feature.collectProjectSettingsForm();
assert.strictEqual(payload.target_system.base_url, "https://example.com");
assert.strictEqual(payload.target_system.username_env, "TARGET_DEMO_USERNAME");
assert.strictEqual(payload.target_system.password_env, "TARGET_DEMO_PASSWORD");
assert.strictEqual(payload.plan_generation.default_coverage_profile, "full");

let streamResult = { status: "running", logs: "" };
streamResult = feature.handleProjectSettingsStreamEvent(
  { event: "log", data: { message: "开始生成 Seed" } },
  streamResult,
);
streamResult = feature.handleProjectSettingsStreamEvent(
  { event: "delta", data: { text: "增量日志" } },
  streamResult,
);
streamResult = feature.handleProjectSettingsStreamEvent(
  { event: "done", data: { ok: true, status: "succeeded" } },
  streamResult,
);
assert.strictEqual(streamResult.status, "succeeded");
assert.strictEqual(
  state.projectSettings.output,
  "开始生成 Seed\n增量日志\nSeed 生成完成。\n",
);

(async () => {
  await feature.loadProjectSettings();
  assert.deepStrictEqual(requests, ["/api/project-settings"]);
  assert.strictEqual(state.projectSettings.loaded, true);
  assert.strictEqual(
    state.projectSettings.targetSystem.base_url,
    "https://target.example",
  );
  assert.strictEqual(
    state.projectSettings.planGeneration.default_coverage_profile,
    "full",
  );
  assert.strictEqual(state.projectSettings.coverageProfiles.length, 1);
  assert.ok(renderCalls >= 1);
  process.stdout.write("project-settings feature VM smoke: ok\n");
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
