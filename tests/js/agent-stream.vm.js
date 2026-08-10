const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");

function createClassList() {
  const values = new Set(["hidden"]);
  return {
    add: (...names) => names.forEach((name) => values.add(name)),
    remove: (...names) => names.forEach((name) => values.delete(name)),
    contains: (name) => values.has(name),
    toggle(name, force) {
      const enabled = force === undefined ? !values.has(name) : Boolean(force);
      if (enabled) {
        values.add(name);
      } else {
        values.delete(name);
      }
      return enabled;
    },
  };
}

function createElement(name = "element") {
  let textContent = "";
  let textWriteCount = 0;
  return {
    name,
    value: "",
    innerHTML: "",
    checked: false,
    disabled: false,
    files: [],
    dataset: {},
    style: {},
    className: "",
    classList: createClassList(),
    isConnected: true,
    scrollTop: 0,
    scrollHeight: 0,
    get textContent() {
      return textContent;
    },
    set textContent(value) {
      textContent = String(value ?? "");
      textWriteCount += 1;
    },
    get textWriteCount() {
      return textWriteCount;
    },
    addEventListener() {},
    removeEventListener() {},
    appendChild() {},
    remove() {},
    setAttribute(key, value) {
      this[key] = String(value);
    },
    getAttribute(key) {
      return this[key] ?? null;
    },
    removeAttribute(key) {
      delete this[key];
    },
    querySelector() {
      return createElement(`${name}:child`);
    },
    querySelectorAll() {
      return [];
    },
    closest() {
      return null;
    },
    getClientRects() {
      return [1];
    },
    focus() {},
    click() {},
  };
}

const elements = new Map();
const root = createElement("root");
root.querySelector = (selector) => {
  if (!elements.has(selector)) {
    elements.set(selector, createElement(selector));
  }
  return elements.get(selector);
};

const run = {
  run_id: "run-stream",
  requirement_title: "结算需求",
  status: "running",
  current_step: "generate_plans",
  created_at: 1,
  plan_generation: { coverage_profile: "core", coverage_prompt: "核心回归" },
  retry_flows: [],
  active_retry_flows: [],
};
const steps = [
  { step_key: "upload_requirement", status: "succeeded", input: {}, output: {}, counts: {} },
  { step_key: "analyze_requirement", status: "succeeded", input: {}, output: {}, counts: {} },
  { step_key: "review_modules", status: "succeeded", input: {}, output: {}, counts: {} },
  {
    step_key: "generate_plans",
    status: "running",
    input: {
      modules: [{ module_uid: "module-checkout", module_name: "Checkout", plan_name: "Checkout", planner_prompt: "prompt" }],
    },
    output: { plans: [], failures: [], skipped: [] },
    counts: { modules: 1, generated: 0 },
  },
];

const initialEvents = [
  {
    event_id: 1,
    run_id: run.run_id,
    step_key: "generate_plans",
    event_type: "log",
    message: "源计划已生成，正在拆分。",
    payload: {
      artifact_progress: true,
      artifact_type: "plan",
      item_status: "running",
      module_name: "Checkout",
      plan_phase: "splitting",
    },
  },
  { event_id: 2, run_id: run.run_id, step_key: "generate_plans", event_type: "status", message: "status", payload: {} },
  { event_id: 3, run_id: run.run_id, step_key: "generate_plans", event_type: "error", message: "error", payload: {} },
  { event_id: 4, run_id: run.run_id, step_key: "generate_plans", event_type: "decision", message: "decision", payload: {} },
  {
    event_id: 5,
    run_id: run.run_id,
    step_key: "generate_plans",
    event_type: "log",
    message: "retry metadata",
    payload: { retry_flow_progress: true },
  },
];
for (let eventId = 6; eventId <= 3010; eventId += 1) {
  initialEvents.push({
    event_id: eventId,
    run_id: run.run_id,
    step_key: "generate_plans",
    event_type: "log",
    message: `log-${eventId}`,
    payload: {},
  });
}

const liveEvents = [
  initialEvents[0],
  initialEvents[initialEvents.length - 1],
  {
    event_id: 3011,
    run_id: run.run_id,
    step_key: "generate_plans",
    event_type: "log",
    message: "live-3011",
    payload: {},
  },
  {
    event_id: 3012,
    run_id: run.run_id,
    step_key: "generate_plans",
    event_type: "log",
    message: "live-3012",
    payload: {},
  },
];

function sseEvent(event) {
  return `event: agent-event\ndata: ${JSON.stringify(event)}\n\n`;
}

let releaseStream;
let releasePaused;
const pausedPromise = new Promise((resolve) => {
  releasePaused = () => resolve({
    value: new TextEncoder().encode(
      `${sseEvent({
        event_id: 3013,
        run_id: run.run_id,
        step_key: "generate_plans",
        event_type: "log",
        message: "live-before-paused",
        payload: {},
      })}event: paused\ndata: ${JSON.stringify({ run_id: run.run_id, status: "awaiting_script_action" })}\n\n`,
    ),
    done: false,
  });
});
const streamPromise = new Promise((resolve) => {
  releaseStream = () => {
    const chunks = [
      new TextEncoder().encode(liveEvents.slice(0, 2).map(sseEvent).join("")),
      new TextEncoder().encode(liveEvents.slice(2).map(sseEvent).join("")),
    ];
    resolve({
      ok: true,
      status: 200,
      body: {
        getReader() {
          let waitingForPaused = true;
          return {
            async read() {
              if (chunks.length) {
                return { value: chunks.shift(), done: false };
              }
              if (waitingForPaused) {
                waitingForPaused = false;
                return pausedPromise;
              }
              return { value: undefined, done: true };
            },
          };
        },
      },
    });
  };
});

const streamRequests = [];
async function fetchStub(url) {
  streamRequests.push(String(url));
  return streamPromise;
}

let apiRun = run;
let runDetailRequests = 0;
let userContentTranslationAttempts = 0;
let deferredTailRunId = "";
let resolveDeferredTail;
const runPayloads = new Map([[run.run_id, { run, steps }]]);
const eventPayloads = new Map([[run.run_id, initialEvents]]);

async function requestJson(url) {
  if (url === "/api/requirements") {
    return { requirements: [] };
  }
  if (url === "/api/plan-generation-defaults") {
    return {
      default_coverage_profile: "core",
      coverage_profiles: [{ key: "core", label: "核心回归", template_prompt: "核心回归" }],
    };
  }
  if (url === "/api/agent/runs") {
    return { runs: [apiRun] };
  }
  const detailMatch = String(url).match(/^\/api\/agent\/runs\/([^/?]+)$/);
  if (detailMatch) {
    runDetailRequests += 1;
    const payload = runPayloads.get(detailMatch[1]);
    if (!payload) {
      throw new Error(`unexpected run detail: ${url}`);
    }
    return { ...payload, retry_flows: [], active_retry_flows: [] };
  }
  const eventsMatch = String(url).match(/^\/api\/agent\/runs\/([^/]+)\/events\?/);
  if (eventsMatch) {
    const runId = eventsMatch[1];
    if (runId === deferredTailRunId) {
      return new Promise((resolve) => {
        resolveDeferredTail = resolve;
      });
    }
    return { events: eventPayloads.get(runId) || [] };
  }
  throw new Error(`unexpected request: ${url}`);
}

const intervals = new Map();
let nextIntervalId = 1;
const timeouts = new Map();
let nextTimeoutId = 1;
const windowListeners = new Map();
const storage = new Map();
const windowObject = {
  WaterfallI18n: {
    source(value) {
      if (value === "未生成") userContentTranslationAttempts += 1;
      return value;
    },
    log: (value) => value,
    platformFailure: (_stepKey, value) => String(value).startsWith("拆分计划失败：多计划拆分检测到已有文件内容冲突；")
      ? "Plan splitting failed: Existing file-content conflicts were found; no plan files were written, registered, or deleted: old-case.md"
      : value,
    getLocale: () => "en",
  },
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
  },
  location: { href: "" },
  addEventListener(type, listener) {
    windowListeners.set(type, listener);
  },
  removeEventListener(type, listener) {
    if (windowListeners.get(type) === listener) {
      windowListeners.delete(type);
    }
  },
  setInterval(callback, delay) {
    const id = nextIntervalId++;
    intervals.set(id, { callback, delay });
    return id;
  },
  clearInterval(id) {
    intervals.delete(id);
  },
  setTimeout(callback, delay) {
    const id = nextTimeoutId++;
    timeouts.set(id, { callback, delay: Number(delay) || 0 });
    return id;
  },
  clearTimeout(id) {
    timeouts.delete(id);
  },
  confirm: () => true,
};

function takeTimeout(delay) {
  const entry = Array.from(timeouts.entries()).find(([, timer]) => timer.delay === delay);
  assert.ok(entry, `missing timeout ${delay}; active=${Array.from(timeouts.values()).map((timer) => timer.delay)}`);
  timeouts.delete(entry[0]);
  return entry[1].callback;
}

const documentObject = {
  activeElement: createElement("active"),
  body: createElement("body"),
  createElement: (tag) => createElement(`created:${tag}`),
};

const scriptPreparation = {
  getState: () => ({ active: false, runId: "", items: [] }),
  applyEvent() {},
  activate: async () => {},
  deactivate() {},
  destroy() {},
  render() {},
  setRun() {},
};

const context = {
  window: windowObject,
  document: documentObject,
  fetch: fetchStub,
  console,
  TextDecoder,
  TextEncoder,
  AbortController,
  FormData,
  Blob,
  URLSearchParams,
  DEFAULT_COVERAGE_PROFILE: "core",
  readStorageItem: (key) => storage.get(key) ?? null,
  writeStorageItem: (key, value) => storage.set(key, String(value)),
  safeJsonParse: (value, fallback = {}) => {
    try {
      return typeof value === "string" ? JSON.parse(value) : value ?? fallback;
    } catch (_error) {
      return fallback;
    }
  },
  createAgentScriptPreparationFeature: () => scriptPreparation,
};
context.globalThis = context;
vm.createContext(context);
for (const filename of [
  "static/js/core/sse.js",
  "static/js/features/agent-progress.js",
  "static/js/features/agent.js",
]) {
  vm.runInContext(fs.readFileSync(path.join(appDir, filename), "utf8"), context, { filename });
}

const feature = context.window.createAgentAutoTest(root, {
  apiClient: {
    requestJson,
    getProjectHeaders: (headers = {}) => headers,
    getDownloadFilename: (_response, fallback) => fallback,
  },
  projectKey: "vm",
  parseSseBlock: context.window.parseSseBlock,
  renderExecutionResultPanel() {},
});

async function flushPromises() {
  for (let index = 0; index < 12; index += 1) {
    await Promise.resolve();
  }
}

(async () => {
  await feature.activate("vm");

  const eventSummary = elements.get('[data-agent-id="eventSummary"]');
  const eventLog = elements.get('[data-agent-id="eventLog"]');
  const artifactList = elements.get('[data-agent-id="artifactList"]');
  assert.strictEqual(eventSummary.textContent, "已加载 3010 条事件，内存保留 3005 条");
  assert.ok(
    artifactList.innerHTML.includes("已生成计划 · 正在拆分单用例计划。"),
    "an old artifact progress event must survive normal-log eviction and expose the splitting phase",
  );
  assert.ok(
    streamRequests.some((url) => url.endsWith("/events-stream?after_id=3010")),
    `stream must resume after the reset event set: ${streamRequests.join(", ")}`,
  );

  const renderWritesBeforeStream = eventLog.textWriteCount;
  releaseStream();
  await new Promise((resolve) => setImmediate(resolve));
  await flushPromises();

  assert.strictEqual(
    Array.from(timeouts.values()).filter((timer) => timer.delay === 100).length,
    1,
    "without requestAnimationFrame, multiple stream reads must coalesce into one 100ms fallback render",
  );
  assert.strictEqual(eventLog.textWriteCount, renderWritesBeforeStream, "stream reads must not synchronously rerender the event log");

  takeTimeout(100)();
  assert.strictEqual(eventLog.textWriteCount, renderWritesBeforeStream + 1);
  assert.strictEqual(
    eventSummary.textContent,
    "已加载 3012 条事件，内存保留 3005 条",
    "duplicate ids must not grow the persistent id set, and only 3000 normal logs may be retained",
  );
  assert.ok(artifactList.innerHTML.includes("已生成计划 · 正在拆分单用例计划。"));

  const detailRequestsBeforePaused = runDetailRequests;
  run.status = "awaiting_script_action";
  releasePaused();
  await new Promise((resolve) => setImmediate(resolve));
  await flushPromises();
  assert.ok(runDetailRequests > detailRequestsBeforePaused, "a paused SSE terminal event must immediately refresh run detail");
  assert.strictEqual(
    Array.from(timeouts.values()).filter((timer) => timer.delay === 100).length,
    0,
    "the paused refresh must clear a pending fallback render",
  );
  assert.strictEqual(eventSummary.textContent, "已加载 3013 条事件，内存保留 3005 条");

  const progressRun = {
    ...run,
    run_id: "run-module-progress",
    status: "cancelled",
    current_step: "generate_plans",
  };
  const splitCasePlans = Array.from({ length: 11 }, (_, index) => ({
    module_name: "Product catalog",
    plan_filename: `case-${index + 1}.md`,
  }));
  const progressSteps = [
    { step_key: "upload_requirement", status: "succeeded", input: {}, output: {}, counts: {} },
    {
      step_key: "generate_plans",
      status: "cancelled",
      input: { modules: Array.from({ length: 6 }, (_, index) => ({ module_uid: `module-${index + 1}` })) },
      output: {
        plans: splitCasePlans,
        failures: [
          { module_name: "Login", plan_filename: "user-failure.md", error: "未生成" },
          {
            module_name: "Login",
            plan_filename: "platform-failure.md",
            error: "拆分计划失败：多计划拆分检测到已有文件内容冲突；未写入、登记或删除任何计划文件：old-case.md",
          },
        ],
        skipped: [],
      },
      counts: { modules: 6, generated: 11, failed: 1, skipped: 0 },
    },
  ];
  runPayloads.set(progressRun.run_id, { run: progressRun, steps: progressSteps });
  eventPayloads.set(progressRun.run_id, []);
  apiRun = progressRun;
  await feature.setProject("vm-progress-units");
  const stepTimeline = elements.get('[data-agent-id="stepTimeline"]');
  assert.ok(stepTimeline.innerHTML.includes("2 / 6 个模块"), stepTimeline.innerHTML);
  assert.ok(!stepTimeline.innerHTML.includes("11 / 6"), "case totals must never be divided by module totals");
  assert.ok(artifactList.innerHTML.includes("未生成"), "third-party failure text must remain verbatim");
  assert.ok(artifactList.innerHTML.includes("Plan splitting failed:"), "known historical platform failures must localize");
  assert.strictEqual(userContentTranslationAttempts, 0, "third-party failure messages must not enter source localization");

  const terminalSteps = [
    { step_key: "upload_requirement", status: "succeeded", input: {}, output: {}, counts: {} },
    { step_key: "generate_plans", status: "succeeded", input: {}, output: { plans: [] }, counts: {} },
  ];
  const staleRun = {
    ...run,
    run_id: "run-stale-tail",
    requirement_title: "旧请求",
    status: "succeeded",
    current_step: "generate_plans",
  };
  const currentRun = {
    ...run,
    run_id: "run-current-tail",
    requirement_title: "当前请求",
    status: "succeeded",
    current_step: "generate_plans",
  };
  const staleEvents = [{
    event_id: 4001,
    run_id: staleRun.run_id,
    step_key: "generate_plans",
    event_type: "status",
    message: "stale-tail-event",
    payload: {},
  }];
  const currentEvents = [{
    event_id: 5001,
    run_id: currentRun.run_id,
    step_key: "generate_plans",
    event_type: "status",
    message: "current-tail-event",
    payload: {},
  }];
  runPayloads.set(staleRun.run_id, { run: staleRun, steps: terminalSteps });
  runPayloads.set(currentRun.run_id, { run: currentRun, steps: terminalSteps });
  eventPayloads.set(currentRun.run_id, currentEvents);

  apiRun = staleRun;
  deferredTailRunId = staleRun.run_id;
  const staleProjectLoad = feature.setProject("vm-stale-tail");
  await new Promise((resolve) => setImmediate(resolve));
  await flushPromises();
  assert.ok(resolveDeferredTail, "the stale run tail request must be pending before switching projects");

  apiRun = currentRun;
  await feature.setProject("vm-current-tail");
  assert.strictEqual(eventSummary.textContent, "已加载 1 条事件");
  assert.ok(eventLog.textContent.includes("current-tail-event"));

  resolveDeferredTail({ events: staleEvents });
  await staleProjectLoad;
  await flushPromises();
  assert.strictEqual(eventSummary.textContent, "已加载 1 条事件", "a stale tail response must not reset the current run's event ids");
  assert.ok(eventLog.textContent.includes("current-tail-event"));
  assert.ok(!eventLog.textContent.includes("stale-tail-event"));

  feature.destroy();
  assert.strictEqual(intervals.size, 0, "destroy must clear the refresh interval");
  assert.strictEqual(timeouts.size, 0, "destroy must clear scheduled fallback renders");
  process.stdout.write("agent stream retention VM smoke: ok\n");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
