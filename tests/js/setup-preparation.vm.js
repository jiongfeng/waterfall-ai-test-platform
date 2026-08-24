const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");
const context = { window: {} };
vm.createContext(context);
for (const language of ["zh-CN", "en"]) {
  vm.runInContext(
    fs.readFileSync(path.join(appDir, `static/js/i18n/${language}.js`), "utf8"),
    context,
  );
}

let locale = "en";
context.window.WaterfallI18n = {
  t(key, params = {}) {
    const dictionary = context.window.WaterfallTranslations[locale];
    return Object.entries(params).reduce(
      (value, [name, replacement]) => value.replaceAll(`{${name}}`, String(replacement)),
      dictionary[key] || key,
    );
  },
};
context.window.requestAnimationFrame = (callback) => callback();
context.window.confirm = () => true;
vm.runInContext(
  fs.readFileSync(path.join(appDir, "static/js/features/setup-preparation.js"), "utf8"),
  context,
  { filename: "setup-preparation.js" },
);

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#39;");
const setupState = {
  loaded: true,
  isLoading: false,
  isSaving: false,
  isRunning: false,
  error: "",
  notice: "",
  noticeType: "",
  scripts: [
    {
      uid: "setup-1",
      name: "Restore database",
      description: "Restore the baseline before a test",
      script_content: "#!/usr/bin/env bash\ntrue\n",
      working_directory: "",
      environment_overrides: {},
      timeout_seconds: 300,
      concurrency_key: "database",
      enabled: true,
      bindings: [],
      latest_run: null,
    },
  ],
  bindings: [
    {
      uid: "binding-1",
      script_uid: "setup-1",
      scope_type: "project",
      scope_key: "demo",
      scope_label: "Demo",
      enabled: true,
    },
  ],
  runs: [
    {
      uid: "run-1",
      script_uid: "setup-1",
      target_type: "project",
      target_key: "demo",
      status: "succeeded",
      exit_code: 0,
      output_summary: "ready",
      error: "",
      started_at: "2026-08-21T10:00:00Z",
      finished_at: "2026-08-21T10:00:01Z",
      duration_ms: 1000,
    },
  ],
  selectedScriptUid: "setup-1",
  selectedRunUid: "run-1",
  runDetailScriptUid: "",
  scriptModalOpen: false,
  runDetailModalOpen: false,
  scriptDraftSourceUid: "",
  scriptDraft: null,
  draftBinding: null,
  draftEnvironmentRows: [],
  scriptQuery: "",
  scriptStatusFilter: "all",
};
const root = {
  querySelector() { return null; },
  querySelectorAll() { return []; },
};
const feature = context.window.createSetupPreparation({
  setupState,
  root,
  getProject: () => ({ project_key: "demo", name: "Demo" }),
  getProjectKey: () => "demo",
  getTestSuites: () => [],
  getScriptModules: () => [],
  isActive: () => true,
  requestJson: async () => ({}),
  encodePathPart: encodeURIComponent,
  isPlainObject: (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value),
  escapeHtml,
  stripSpecSuffix: (value) => value.replace(/\.spec\.ts$/, ""),
  renderHost() {},
});

function assertEnglishMarkup(markup, expected) {
  assert.doesNotMatch(markup, /[\u3400-\u9fff]/, markup);
  for (const value of expected) assert.ok(markup.includes(value), `Missing English UI copy: ${value}`);
}

assertEnglishMarkup(feature.renderMarkup(), ["Setup scripts", "Latest run", "Trial run"]);

setupState.scriptModalOpen = true;
setupState.scriptDraft = { ...setupState.scripts[0], uid: "", name: "", description: "" };
setupState.draftBinding = {
  scope_type: "project",
  scope_key: "demo",
  scope_label: "Demo",
  priority: 0,
  enabled: true,
};
assertEnglishMarkup(feature.renderMarkup(), ["New setup script", "Execution contract", "Runtime settings"]);

setupState.scriptModalOpen = false;
setupState.runDetailModalOpen = true;
setupState.runDetailScriptUid = "setup-1";
assertEnglishMarkup(feature.renderMarkup(), ["Execution details", "Execution history", "Shell output"]);

locale = "zh-CN";
assert.match(feature.renderMarkup(), /执行详情/);

process.stdout.write("setup preparation English UI VM smoke: ok\n");
