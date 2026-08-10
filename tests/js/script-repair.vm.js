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
  "static/js/features/script-repair.js",
]) {
  vm.runInContext(fs.readFileSync(path.join(appDir, filename), "utf8"), context);
}

function createElement() {
  const classes = new Set();
  const element = {
    textContent: "",
    value: "",
    disabled: false,
    scrollTop: 0,
    scrollHeight: 0,
    focus() {},
    classList: {
      add: (...names) => names.forEach((name) => classes.add(name)),
      remove: (...names) => names.forEach((name) => classes.delete(name)),
      contains: (name) => classes.has(name),
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
  scripts: {
    selectedModule: "登录",
    selectedFile: "登录成功.spec.ts",
    activeTab: "script",
    repairRecords: {},
    runRecords: {},
  },
  scriptRun: { isRunning: false, durationTimer: null },
  scriptRecording: { isRunning: false },
  scriptExecution: { isRunning: false },
  moduleExecution: { isRunning: false },
  moduleRepair: { isRunning: false },
  testSuiteExecution: { isRunning: false },
};
const elements = {
  scriptRunPromptFixed: createElement(),
  scriptRunPromptNote: createElement(),
  scriptRunJobOutput: createElement(),
  scriptRunJobStatus: createElement(),
  scriptRunJobLogs: createElement(),
  scriptRunSubmit: createElement(),
  scriptRunDuration: createElement(),
};

let timerId = 20;
const activeTimers = new Set();
const clearedTimers = [];
const timerHost = {
  setInterval() {
    const id = ++timerId;
    activeTimers.add(id);
    return id;
  },
  clearInterval(id) {
    activeTimers.delete(id);
    clearedTimers.push(id);
  },
};
const persistence = { repair: [], run: [] };
const getScriptRunRecordKey = (
  moduleName = state.scripts.selectedModule,
  filename = state.scripts.selectedFile,
) => (moduleName && filename ? `${moduleName}/${filename}` : "");
const feature = context.window.createScriptRepairFeature({
  state,
  elements,
  SECTION: { SCRIPTS: "scripts" },
  SCRIPT_VIEW_TAB: {
    SCRIPT: "script",
    EXECUTION: "execution",
    REPAIR: "repair",
  },
  window: {
    WaterfallI18n: {
      t: (key, { duration }) => ({
        "repair.elapsed": `Repair elapsed: ${duration}`,
        "repair.duration": `Repair time: ${duration}`,
      })[key] || key,
    },
  },
  getScriptRunPromptFixedTemplate: () =>
    "@playwright-test-healer\nUse specs/<module>/<module>.md to repair tests/<module>/<test-script>.spec.ts",
  getScriptRunPromptNoteDefault: () => "Do not remove STEP entries",
  fetch: async () => {
    throw new Error("Focused repair VM paths must not issue fetch");
  },
  TextDecoder,
  timers: context.window.createTimerRuntime(timerHost),
  formatDuration: context.window.formatElapsedDuration,
  replaceAllText: (value, search, replacement) =>
    String(value).split(search).join(replacement),
  stripSpecSuffix: (value) => String(value).replace(/\.spec\.ts$/, ""),
  getScriptRunRecordKey,
  normalizeScriptRepairRecord: (value) => value,
  persistScriptRepairRecords: (key) => persistence.repair.push(key),
  persistScriptRunRecords: (key) => persistence.run.push(key),
  parseSseBlock: context.window.parseSseBlock,
  renderExecutionRecord: () => {},
  persistViewState: () => {},
  renderContent: () => {},
  setNotice: () => {},
  getProjectRequestHeaders: (headers) => headers,
  refreshScriptMetadata: async () => {},
  confirmDiscardEdit: () => true,
});

const key = getScriptRunRecordKey();
const repairRecord = feature.ensureScriptRepairRecord();
assert.ok(repairRecord.prompt.includes("登录成功"));
assert.deepStrictEqual(persistence.repair, [key]);
assert.ok(
  feature.renderScriptRunPromptFromTemplate("login", "login-success.spec.ts").includes(
    "tests/login/login-success.spec.ts",
  ),
);
state.scripts.repairRecords[key].prompt_fixed =
  "@playwright-test-healer\n修复 tests\\登录\\登录成功.spec.ts";
assert.ok(
  feature.ensureScriptRepairRecord().prompt_fixed.includes(
    "tests/登录/登录成功.spec.ts",
  ),
);

feature.setScriptRepairRecord("登录", "登录成功.spec.ts", {
  status: "running",
  started_at: Date.now() - 1500,
  logs: "",
});
state.scriptRun.isRunning = true;
feature.startScriptRunDurationTimer();
const runningTimerId = state.scriptRun.durationTimer;
assert.ok(activeTimers.has(runningTimerId));
assert.ok(elements.scriptRunDuration.textContent.startsWith("Repair elapsed: "));
feature.stopScriptRunDurationTimer();
assert.strictEqual(state.scriptRun.durationTimer, null);
assert.ok(clearedTimers.includes(runningTimerId));

let repairResult = { status: "running", logs: "" };
repairResult = feature.handleScriptRunStreamEvent(
  {
    event: "status",
    data: { status: "running", target_path: "tests/登录/登录成功.spec.ts" },
  },
  repairResult,
  "登录",
  "登录成功.spec.ts",
);
repairResult = feature.handleScriptRunStreamEvent(
  { event: "log", data: { message: "开始修复" } },
  repairResult,
  "登录",
  "登录成功.spec.ts",
);
repairResult = feature.handleScriptRunStreamEvent(
  { event: "delta", data: { text: "增量输出" } },
  repairResult,
  "登录",
  "登录成功.spec.ts",
);
repairResult = feature.handleScriptRunStreamEvent(
  {
    event: "done",
    data: { ok: true, returncode: 0, video: { url: "/video.webm" } },
  },
  repairResult,
  "登录",
  "登录成功.spec.ts",
);
assert.strictEqual(repairResult.status, "succeeded");
assert.strictEqual(repairResult.logs, "开始修复\n增量输出");

let cancelled = feature.handleScriptRunStreamEvent(
  { event: "status", data: { status: "cancelled", error: "用户取消" } },
  { status: "running", logs: "" },
  "登录",
  "登录成功.spec.ts",
);
cancelled = feature.handleScriptRunStreamEvent(
  { event: "done", data: { ok: true } },
  cancelled,
  "登录",
  "登录成功.spec.ts",
);
assert.strictEqual(cancelled.status, "cancelled");
assert.strictEqual(elements.scriptRunJobStatus.textContent, "任务已取消");
assert.strictEqual(elements.scriptRunSubmit.textContent, "重新修复");

const failed = feature.handleScriptRunStreamEvent(
  { event: "done", data: { ok: false, error: "修复失败" } },
  { status: "running", logs: "诊断\n" },
  "登录",
  "登录成功.spec.ts",
);
assert.strictEqual(failed.status, "failed");
assert.ok(elements.scriptRunJobStatus.textContent.includes("修复失败"));

let executionResult = { status: "running", logs: "" };
executionResult = feature.handleScriptExecutionStreamEvent(
  { event: "log", data: { message: "执行中" } },
  executionResult,
  "登录",
  "登录成功.spec.ts",
);
executionResult = feature.handleScriptExecutionStreamEvent(
  {
    event: "done",
    data: {
      status: "succeeded",
      returncode: 0,
      report: { url: "/report" },
    },
  },
  executionResult,
  "登录",
  "登录成功.spec.ts",
);
assert.strictEqual(executionResult.status, "succeeded");
assert.strictEqual(state.scripts.runRecords[key].returncode, 0);

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
  const streamed = await feature.readScriptRunStream(
    streamResponse([
      'event: log\ndata: {"message":"分片',
      '修复"}\n\nevent: done\ndata: {"ok":true,"returncode":0}\n\n',
    ]),
    "登录",
    "登录成功.spec.ts",
  );
  assert.strictEqual(streamed.status, "succeeded");
  assert.strictEqual(streamed.logs, "分片修复\n");
  assert.ok(persistence.repair.length >= 10);
  assert.ok(persistence.run.length >= 2);
  process.stdout.write("script-repair feature VM smoke: ok\n");
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
