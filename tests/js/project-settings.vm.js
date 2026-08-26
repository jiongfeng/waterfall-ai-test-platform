const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");
const documentListeners = {};
const documentObject = {
  activeElement: null,
  addEventListener(type, listener) {
    (documentListeners[type] ||= []).push(listener);
  },
  dispatch(type, event = {}) {
    const normalizedEvent = {
      key: "",
      target: null,
      defaultPrevented: false,
      preventDefault() {
        this.defaultPrevented = true;
      },
      ...event,
    };
    documentListeners[type]?.forEach((listener) => listener(normalizedEvent));
    return normalizedEvent;
  },
};
const context = { window: { document: documentObject }, document: documentObject, TextDecoder };
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
    hidden: false,
    disabled: false,
    attributes: {},
    listeners: {},
    scrollTop: 0,
    scrollHeight: 0,
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener(type, listener) {
      this.listeners[type] = listener;
    },
    dispatch(type, event = {}) {
      const normalizedEvent = {
        key: "",
        target: this,
        defaultPrevented: false,
        preventDefault() {
          this.defaultPrevented = true;
        },
        ...event,
      };
      return this.listeners[type]?.(normalizedEvent);
    },
    setAttribute(name, value) {
      this.attributes[name] = String(value);
    },
    getAttribute(name) {
      return this.attributes[name] ?? null;
    },
    focus() {
      documentObject.activeElement = this;
    },
    contains() {
      return false;
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
}

const fields = {
  "#projectTargetBaseUrl": field("https://example.com"),
  "#projectTargetLoginUrl": field("/login"),
  "#projectTargetUsername": field("qa"),
  "#projectTargetPassword": field("secret"),
  "#projectDefaultCoverageProfile": field("full"),
  "#projectSettingsOutput": field(),
};
const seedGenerateToggle = field();
const seedGenerateMenu = field();
const visitSeedButton = field();
const loginSeedButton = field();
const seedGenerateWrap = field();
const projectSettingsForm = field();
const projectSeedTest = field();
seedGenerateMenu.hidden = true;
visitSeedButton.dataset.seedMode = "visit_only";
loginSeedButton.dataset.seedMode = "login";
const seedModeButtons = [visitSeedButton, loginSeedButton];
seedGenerateMenu.querySelector = (selector) =>
  selector.includes("[data-seed-mode]")
    ? seedModeButtons.find((button) => !selector.includes(":not") || !button.disabled) || null
    : null;
seedGenerateMenu.querySelectorAll = (selector) =>
  selector.includes("[data-seed-mode]")
    ? seedModeButtons.filter((button) => !selector.includes(":not") || !button.disabled)
    : [];
seedGenerateWrap.contains = (target) =>
  [seedGenerateWrap, seedGenerateToggle, seedGenerateMenu, ...seedModeButtons].includes(target);
Object.assign(fields, {
  "#projectSettingsForm": projectSettingsForm,
  "#projectSeedGenerateToggle": seedGenerateToggle,
  "#projectSeedGenerateMenu": seedGenerateMenu,
  "#projectSeedTest": projectSeedTest,
  ".project-seed-generate-menu-wrap": seedGenerateWrap,
});
const projectSettingsPanel = field();
projectSettingsPanel.querySelector = (selector) => fields[selector] || null;
projectSettingsPanel.querySelectorAll = (selector) =>
  selector === "button"
    ? [seedGenerateToggle, ...seedModeButtons, projectSeedTest]
    : [];
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
    seedScriptPath: "tests/seed/seed.spec.ts",
    seedMode: "",
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
const seedGenerationRequests = [];
const notices = [];
let generatedSeedMode = "visit_only";
let confirmResult = true;
const confirmMessages = [];
const encoder = new TextEncoder();
const normalizeProject = (value) =>
  value?.project_key ? { ...value, key: value.project_key } : null;
const feature = context.window.createProjectSettingsFeature({
  state,
  elements,
  DEFAULT_COVERAGE_PROFILE: "core",
  PROJECT_SETTINGS_VIEW_TAB: { BASIC: "basic", SETUP: "setup" },
  fetch: async (url, options) => {
    seedGenerationRequests.push({ url, options });
    const payload = `event: done\ndata: ${JSON.stringify({
      ok: true,
      status: "succeeded",
      seed_mode: generatedSeedMode,
    })}\n\n`;
    let delivered = false;
    return {
      ok: true,
      body: {
        getReader: () => ({
          read: async () => {
            if (delivered) {
              return { done: true, value: undefined };
            }
            delivered = true;
            return { done: false, value: encoder.encode(payload) };
          },
        }),
      },
    };
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
          is_default: false,
        },
        target_system: {
          base_url: "https://target.example",
          login_url: "/signin",
          username: "runner",
          password: "masked",
        },
        database_baseline: { enabled: true },
        plan_generation: { default_coverage_profile: "full" },
        coverage_profiles: [{ key: "full", label: "完整回归" }],
        seed_script_path: "tests/seed/seed.spec.ts",
        seed_mode: "login",
      };
    }
    throw new Error(`unexpected request ${url}`);
  },
  setNotice: (message, type) => notices.push({ message, type }),
  setLoading: () => {},
  renderContent: () => {
    renderCalls += 1;
  },
  parseSseBlock: context.window.parseSseBlock,
  getProjectRequestHeaders: (headers) => headers,
  isPlainObject: (value) =>
    Boolean(value && typeof value === "object" && !Array.isArray(value)),
  escapeHtml: String,
  document: documentObject,
  confirm: (message) => {
    confirmMessages.push(message);
    return confirmResult;
  },
  t: (key, params = {}) => {
    const template = {
      "projectSettings.seedGenerationComplete": "Seed 生成完成。",
      "projectSettings.generatingSeedMode": "正在生成{mode} Seed...",
      "projectSettings.seedGenerated": "Seed 脚本已生成。",
      "projectSettings.seedModeVisitOnly": "访问目标系统（不登录）",
      "projectSettings.seedModeLogin": "带登录",
      "projectSettings.seedModeUnknown": "未知",
      "projectSettings.currentSeedMode": "当前 Seed 类型",
      "projectSettings.generateSeed": "生成 Seed",
      "projectSettings.generateVisitSeed": "生成访问 Seed（不登录）",
      "projectSettings.generateLoginSeed": "生成登录 Seed",
      "projectSettings.testSeed": "测试 Seed",
      "projectSettings.confirmSeedModeOverwrite": "从{current}切换为{next}会覆盖当前 Seed",
    }[key] || key;
    return template.replace(/\{(\w+)\}/g, (_, name) => params[name] ?? `{${name}}`);
  },
});

const normalized = feature.normalizeTargetSystem({
  base_url: " https://example.com ",
  login_url: "",
  username: 42,
});
assert.strictEqual(normalized.base_url, " https://example.com ");
assert.strictEqual(normalized.login_url, "/login");
assert.strictEqual(normalized.username, "");

const payload = feature.collectProjectSettingsForm();
assert.strictEqual(payload.target_system.base_url, "https://example.com");
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
  assert.strictEqual(state.projectSettings.seedMode, "login");
  assert.strictEqual(state.projectSettings.coverageProfiles.length, 1);
  assert.ok(renderCalls >= 1);

  feature.renderProjectSettingsPanel();
  assert.match(projectSettingsPanel.innerHTML, /id="projectTargetBaseUrl"[^>]*required/);
  assert.match(projectSettingsPanel.innerHTML, /id="projectSeedGenerateToggle"/);
  assert.match(projectSettingsPanel.innerHTML, /data-seed-mode="visit_only"/);
  assert.match(projectSettingsPanel.innerHTML, /data-seed-mode="login"/);
  assert.match(projectSettingsPanel.innerHTML, /当前 Seed 类型/);
  assert.match(projectSettingsPanel.innerHTML, />带登录</);
  assert.match(projectSettingsPanel.innerHTML, /id="projectSeedTest"/);

  seedGenerateMenu.hidden = true;
  seedGenerateToggle.dispatch("click");
  assert.strictEqual(seedGenerateMenu.hidden, false);
  assert.strictEqual(seedGenerateToggle.getAttribute("aria-expanded"), "true");
  assert.strictEqual(documentObject.activeElement, visitSeedButton);
  loginSeedButton.focus();
  seedGenerateMenu.dispatch("keydown", { key: "ArrowUp" });
  assert.strictEqual(documentObject.activeElement, visitSeedButton);
  documentObject.dispatch("keydown", { key: "Escape" });
  assert.strictEqual(seedGenerateMenu.hidden, true);
  assert.strictEqual(documentObject.activeElement, seedGenerateToggle);
  seedGenerateToggle.dispatch("click");
  assert.strictEqual(seedGenerateMenu.hidden, false);
  documentObject.dispatch("pointerdown", { target: field() });
  assert.strictEqual(seedGenerateMenu.hidden, true);

  state.projectSettings.isTestingSeed = true;
  feature.renderProjectSettingsPanel();
  assert.match(
    projectSettingsPanel.innerHTML,
    /id="projectSeedGenerateToggle"[^>]*disabled/,
  );
  assert.match(
    projectSettingsPanel.innerHTML,
    /data-seed-mode="visit_only"[^>]*disabled/,
  );
  state.projectSettings.isTestingSeed = false;

  confirmResult = false;
  await visitSeedButton.dispatch("click");
  assert.strictEqual(seedGenerationRequests.length, 0);
  assert.strictEqual(confirmMessages.length, 1);

  confirmResult = true;
  generatedSeedMode = "visit_only";
  await visitSeedButton.dispatch("click");
  assert.strictEqual(seedGenerationRequests.length, 1);
  assert.strictEqual(seedGenerationRequests[0].url, "/api/project-settings/seed/generate");
  assert.deepStrictEqual(JSON.parse(seedGenerationRequests[0].options.body), {
    mode: "visit_only",
  });
  assert.strictEqual(state.projectSettings.seedMode, "visit_only");

  generatedSeedMode = "login";
  await loginSeedButton.dispatch("click");
  assert.strictEqual(seedGenerationRequests.length, 2);
  assert.deepStrictEqual(JSON.parse(seedGenerationRequests[1].options.body), {
    mode: "login",
  });
  assert.strictEqual(state.projectSettings.seedMode, "login");
  assert.strictEqual(notices.at(-1).type, "success");
  process.stdout.write("project-settings feature VM smoke: ok\n");
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
