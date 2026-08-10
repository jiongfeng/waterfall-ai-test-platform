const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");
const CJK_PATTERN = /[\u3400-\u9fff]/;
const withoutDynamicLeaves = (html) => String(html || "").replace(
  /<([a-z][a-z0-9]*)\b(?=[^>]*\bdata-i18n-dynamic(?:\s|=|>))[^>]*>[\s\S]*?<\/\1>/gi,
  "",
);

function createClassList(initial = []) {
  const classes = new Set(initial);
  return {
    add: (...names) => names.forEach((name) => classes.add(name)),
    remove: (...names) => names.forEach((name) => classes.delete(name)),
    contains: (name) => classes.has(name),
    toggle(name, force) {
      const enabled = typeof force === "boolean" ? force : !classes.has(name);
      if (enabled) {
        classes.add(name);
      } else {
        classes.delete(name);
      }
      return enabled;
    },
    replaceFrom(value) {
      classes.clear();
      String(value || "")
        .split(/\s+/)
        .filter(Boolean)
        .forEach((name) => classes.add(name));
    },
    value: () => Array.from(classes).join(" "),
  };
}

const documentRef = {
  activeElement: null,
  body: null,
};

function createElement({ classes = [], dataset = {} } = {}) {
  const listeners = new Map();
  const attributes = new Map();
  const classList = createClassList(classes);
  const element = {
    innerHTML: "",
    textContent: "",
    value: "",
    disabled: false,
    checked: false,
    indeterminate: false,
    isConnected: true,
    dataset: { ...dataset },
    style: {},
    classList,
    setAttribute(name, value) {
      attributes.set(name, String(value));
    },
    getAttribute(name) {
      return attributes.has(name) ? attributes.get(name) : null;
    },
    removeAttribute(name) {
      attributes.delete(name);
    },
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    removeEventListener(type) {
      listeners.delete(type);
    },
    querySelectorAll() {
      return [];
    },
    focus() {
      documentRef.activeElement = element;
    },
    getClientRects() {
      return [1];
    },
  };
  Object.defineProperty(element, "className", {
    get: () => classList.value(),
    set: (value) => classList.replaceFrom(value),
  });
  return element;
}

const hookNames = [
  "stageMeta",
  "stageTitle",
  "stageSummary",
  "stageStatus",
  "bulkToggle",
  "bulkExit",
  "progressValue",
  "progressBar",
  "processingCount",
  "readyCount",
  "awaitingCount",
  "abandonedCount",
  "filterBar",
  "searchInput",
  "batchBar",
  "selectedCount",
  "batchHint",
  "clearSelection",
  "batchMenuToggle",
  "batchMenu",
  "batchExecute",
  "batchRepair",
  "selectAll",
  "tableBody",
  "tableEmpty",
  "tableFooterTotal",
  "tableFooterHint",
  "detailModal",
  "detailBackdrop",
  "detailClose",
  "detailMeta",
  "detailTitle",
  "detailBadges",
  "historyList",
  "detailContent",
  "actionPanel",
  "editorModal",
  "editorBackdrop",
  "editorClose",
  "editorMeta",
  "editorTitle",
  "editorDescription",
  "editSection",
  "promptSection",
  "scriptEditor",
  "originalPrompt",
  "supplementalPrompt",
  "editorBaseline",
  "editorTarget",
  "editorCancel",
  "editorSave",
  "editorSaveExecute",
  "editorConfirm",
  "localNotice",
];

const elements = Object.fromEntries(hookNames.map((name) => [name, createElement()]));
[
  "bulkExit",
  "batchBar",
  "batchMenu",
  "tableEmpty",
  "detailModal",
  "editorModal",
  "promptSection",
  "editorConfirm",
  "localNotice",
].forEach((name) => elements[name].classList.add("hidden"));

const filters = ["all", "processing", "ready", "awaiting_human", "abandoned"].map((filter) =>
  createElement({ dataset: { scriptPreparationFilter: filter } }),
);
elements.filterBar.querySelectorAll = (selector) =>
  selector === "[data-script-preparation-filter]" ? filters : [];

const root = createElement({ classes: ["hidden"] });
root.querySelector = (selector) => {
  const match = selector.match(/^\[data-script-preparation-id="([^"]+)"\]$/);
  return match ? elements[match[1]] || null : null;
};

documentRef.body = createElement();
documentRef.activeElement = createElement();

const context = {
  AbortController,
  clearTimeout,
  setTimeout,
  window: {
    confirm: () => true,
    requestAnimationFrame(callback) {
      callback();
    },
  },
  document: documentRef,
  console,
};
vm.createContext(context);
for (const filename of ["static/js/i18n/en.js", "static/js/i18n/zh-CN.js"]) {
  vm.runInContext(fs.readFileSync(path.join(appDir, filename), "utf8"), context);
}
let activeLocale = "zh-CN";
const interpolate = (value, params = {}) => Object.entries(params).reduce(
  (text, [name, replacement]) => text.replaceAll(`{${name}}`, String(replacement)),
  value,
);
context.window.WaterfallI18n = {
  t(key, params = {}) {
    const catalog = context.window.WaterfallTranslations[activeLocale] || {};
    const fallback = context.window.WaterfallTranslations.en || {};
    return interpolate(catalog[key] || fallback[key] || key, params);
  },
  source(value) {
    if (activeLocale !== "en") return value;
    return context.window.WaterfallTranslations.en.source?.[value] || value;
  },
  getLocale: () => activeLocale,
  markDynamic(element, enabled = true) {
    if (enabled) element?.setAttribute?.("data-i18n-dynamic", "");
    else element?.removeAttribute?.("data-i18n-dynamic");
  },
  markDynamicAttributes(element, enabled = true) {
    if (enabled) element?.setAttribute?.("data-i18n-dynamic-attributes", "");
    else element?.removeAttribute?.("data-i18n-dynamic-attributes");
  },
};
vm.runInContext(
  fs.readFileSync(
    path.join(appDir, "static/js/features/agent-script-preparation.js"),
    "utf8",
  ),
  context,
);

const waitingItem = {
  item_id: "script-1",
  module_name: "登录模块",
  plan_filename: "登录<script>alert(1)</script>.md",
  filename: "login.spec.js",
  status: "awaiting_human",
  current_revision_id: "revision-2",
  current_script: {
    content: "test('login', async ({ page }) => {});",
  },
  included_in_suite: false,
  capabilities: {
    execute: { enabled: false, reason: "计划" },
  },
  latest_analysis: {
    summary: "修复后的定位器仍然无法命中登录按钮。",
    recommended_action: "repair",
    prompt_patch: "优先使用 role 定位并等待按钮可见。",
    prompt_options: {
      regenerate: {
        original_prompt: "从登录测试计划重新生成完整脚本。",
        supplemental_prompt: "保留现有认证准备步骤。",
        enabled: true,
      },
      repair: {
        original_prompt: "修复登录脚本中失败的按钮定位。",
        supplemental_prompt: "优先使用 role 定位并等待按钮可见。",
        enabled: true,
      },
    },
  },
  prompt_defaults: {
    regenerate: "从登录测试计划重新生成完整脚本。",
    repair: "修复登录脚本中失败的按钮定位。",
  },
  history: [
    {
      stage_id: "stage-generate",
      sequence_no: 1,
      stage_type: "generate",
      stage_name: "生成脚本",
      status: "succeeded",
      output_revision_id: "revision-1",
      started_at: 1785456000000,
      finished_at: 1785456003000,
    },
    {
      stage_id: "stage-execute-1",
      sequence_no: 2,
      stage_type: "execute",
      status: "failed",
      input_revision_id: "revision-1",
      error: "登录按钮不可见",
      result: { error: "登录按钮不可见", failure_step: "点击登录" },
      started_at: 1785456004000,
      finished_at: 1785456009000,
    },
    {
      stage_id: "stage-repair",
      sequence_no: 3,
      stage_type: "repair",
      status: "succeeded",
      input_revision_id: "revision-1",
      output_revision_id: "revision-2",
      started_at: 1785456010000,
      finished_at: 1785456014000,
    },
    {
      stage_id: "stage-execute-2",
      sequence_no: 4,
      stage_type: "execute",
      status: "failed",
      input_revision_id: "revision-2",
      error: "严格模式命中两个按钮",
      result: { error: "严格模式命中两个按钮" },
      started_at: 1785456015000,
      finished_at: 1785456020000,
    },
    {
      stage_id: "stage-vendor",
      sequence_no: 4.5,
      stage_type: "vendor_extension",
      stage_name: "计划",
      status: "succeeded",
      trigger_source: "角色",
      message: "任务失败",
      started_at: 1785456020000,
      finished_at: 1785456020500,
    },
    {
      stage_id: "stage-human",
      sequence_no: 5,
      stage_type: "human_review",
      stage_name: "待人工处理",
      status: "pending",
      input_revision_id: "revision-2",
      result: {
        analysis: {
          recommended_action: "repair",
        },
      },
      started_at: 1785456021000,
      finished_at: null,
    },
  ],
  updated_at: 1785456021000,
};

const readyItem = {
  item_id: "script-2",
  module_name: "订单模块",
  plan_filename: "创建订单.md",
  filename: "order.spec.js",
  status: "ready",
  current_revision_id: "revision-1",
  current_script: { content: "test('order', async () => {});" },
  included_in_suite: true,
  history: [
    {
      stage_id: "order-generate",
      sequence_no: 1,
      stage_type: "generate",
      status: "succeeded",
      output_revision_id: "revision-1",
      started_at: 1785456000000,
      finished_at: 1785456003000,
    },
    {
      stage_id: "order-execute",
      sequence_no: 2,
      stage_type: "execute",
      status: "succeeded",
      input_revision_id: "revision-1",
      started_at: 1785456004000,
      finished_at: 1785456008000,
    },
  ],
  updated_at: 1785456008000,
};

const snapshot = {
  status: "awaiting_action",
  summary: "2 条脚本中 1 条已通过，1 条等待人工处理。",
  counts: {
    total: 2,
    busy: 0,
    queued: 0,
    ready: 1,
    awaiting_human: 1,
    abandoned: 0,
  },
  items: [waitingItem, readyItem],
};
let currentSnapshot = snapshot;
let snapshotInterceptor = null;
let itemActionMessage = "";

const requests = [];
async function requestJson(url, options = {}) {
  const body = options.body ? JSON.parse(options.body) : null;
  requests.push({ url, method: options.method || "GET", body });
  if (url.endsWith("/script-preparation")) {
    if (snapshotInterceptor) {
      return snapshotInterceptor(url, options);
    }
    return { snapshot: currentSnapshot };
  }
  const itemMatch = url.match(/\/script-items\/([^/]+)$/);
  if (itemMatch && !options.method) {
    return {
      item: currentSnapshot.items.find((item) => item.item_id === decodeURIComponent(itemMatch[1])),
    };
  }
  if (url.endsWith("/script-items/batch-actions")) {
    return {
      accepted: [{ item_id: "script-1", status: "queued" }],
      rejected: [],
      should_continue: false,
    };
  }
  if (url.endsWith("/script-items/script-1/actions")) {
    const updatedItem = {
      ...waitingItem,
      status: "ready",
      included_in_suite: true,
      history: [
        ...waitingItem.history,
        {
          stage_id: "stage-rerepair",
          sequence_no: 6,
          stage_type: "rerepair",
          status: "succeeded",
          input_revision_id: "revision-2",
          output_revision_id: "revision-3",
          started_at: 1785456022000,
          finished_at: 1785456026000,
        },
        {
          stage_id: "stage-execute-3",
          sequence_no: 7,
          stage_type: "execute",
          status: "succeeded",
          input_revision_id: "revision-3",
          output_revision_id: "revision-3",
          started_at: 1785456027000,
          finished_at: 1785456031000,
        },
      ],
      current_revision_id: "revision-3",
      updated_at: 1785456031000,
    };
    currentSnapshot = {
      ...currentSnapshot,
      status: "succeeded",
      counts: { total: 2, ready: 2, awaiting_human: 0, abandoned: 0, busy: 0, queued: 0 },
      items: currentSnapshot.items.map((item) => (item.item_id === "script-1" ? updatedItem : item)),
    };
    return {
      accepted: true,
      item: updatedItem,
      should_continue: true,
      ...(itemActionMessage ? { message: itemActionMessage } : {}),
    };
  }
  throw new Error(`unexpected request: ${url}`);
}

const notices = [];
const confirmMessages = [];
let confirmResult = true;
const feature = context.window.createAgentScriptPreparationFeature(root, {
  document: documentRef,
  window: context.window,
  runId: "agent-1",
  requestJson,
  setNotice: (message, type, dynamic) => notices.push({ message, type, dynamic }),
  confirm: (message) => {
    confirmMessages.push(message);
    return confirmResult;
  },
});

(async () => {
  await feature.activate();

  assert.strictEqual(requests[0].url, "/api/agent/runs/agent-1/script-preparation");
  assert.strictEqual(elements.progressValue.textContent, "1 / 2");
  assert.strictEqual(elements.awaitingCount.textContent, "1");
  assert.ok(elements.tableBody.innerHTML.includes("v2"));
  assert.strictEqual(
    feature.getState().items.find((item) => item.script_item_id === "script-2").last_verified_version,
    1,
  );
  assert.ok(elements.tableBody.innerHTML.includes("&lt;script&gt;"));
  assert.ok(!elements.tableBody.innerHTML.includes("<script>alert(1)</script>"));

  feature.setFilter("awaiting_human");
  assert.strictEqual(feature.getState().filter, "awaiting_human");
  feature.toggleBatchMode(true);
  feature.setSelectedItems(["script-1"]);
  await feature.performBatchAction("repair");

  const batchRequest = requests.find((entry) => entry.url.endsWith("/batch-actions"));
  assert.strictEqual(batchRequest.body.action, "repair");
  assert.strictEqual(batchRequest.body.items[0].item_id, "script-1");
  assert.strictEqual(
    batchRequest.body.items[0].original_prompt,
    "修复登录脚本中失败的按钮定位。",
  );
  assert.strictEqual(
    batchRequest.body.items[0].supplemental_prompt,
    "优先使用 role 定位并等待按钮可见。",
  );

  await feature.openDetail("script-1");
  assert.strictEqual(feature.getState().selectedHistoryId, "stage-human");
  assert.strictEqual(elements.detailModal.getAttribute("aria-hidden"), "false");
  assert.ok(elements.historyList.innerHTML.includes("生成脚本"));
  assert.ok(elements.historyList.innerHTML.includes("待人工处理"));
  assert.ok(elements.detailContent.innerHTML.includes("AI 分析"));
  [
    "按建议重新修复",
    "人工编辑脚本",
    "重新执行当前版本",
    "改为重新生成",
    "忽略脚本",
  ].forEach((label) => assert.ok(elements.actionPanel.innerHTML.includes(label), label));

  activeLocale = "en";
  feature.render();
  assert.strictEqual(elements.stageTitle.textContent, "Script preparation");
  assert.ok(elements.stageMeta.innerHTML.includes("data-i18n-dynamic>agent-1</span>"));
  assert.ok(elements.stageMeta.innerHTML.includes("Script preparation</span>"));
  assert.strictEqual(elements.stageMeta.getAttribute("data-i18n-dynamic"), null);
  assert.strictEqual(elements.stageSummary.textContent, snapshot.summary, "API summaries must remain verbatim");
  assert.strictEqual(elements.stageSummary.getAttribute("data-i18n-dynamic"), "");
  assert.ok(elements.historyList.innerHTML.includes("Run v1"));
  assert.ok(elements.historyList.innerHTML.includes("Automatic repair v1 → v2"));
  assert.ok(elements.historyList.innerHTML.includes('<strong data-i18n-dynamic>计划</strong>'));
  assert.doesNotMatch(withoutDynamicLeaves(elements.historyList.innerHTML), CJK_PATTERN);
  assert.doesNotMatch(elements.detailBadges.innerHTML, CJK_PATTERN);
  assert.ok(elements.detailContent.innerHTML.includes("Recommendation: Repair current candidate v2"));
  assert.ok(elements.detailContent.innerHTML.includes("Analysis is based on candidate v2"));
  assert.ok(elements.detailContent.innerHTML.includes('data-i18n-dynamic>修复后的定位器仍然无法命中登录按钮。'));
  assert.ok(elements.actionPanel.innerHTML.includes("Use recommendation: Repair again"));
  assert.ok(elements.actionPanel.innerHTML.includes("Repair again supplemental Prompt"));
  assert.ok(elements.actionPanel.innerHTML.includes('data-i18n-dynamic>优先使用 role 定位并等待按钮可见。'));
  assert.ok(elements.actionPanel.innerHTML.includes('data-i18n-dynamic-attributes title="计划"'));
  assert.strictEqual(elements.detailMeta.getAttribute("data-i18n-dynamic"), null);
  assert.ok(elements.detailMeta.innerHTML.includes("Script preparation</span>"));
  assert.ok(elements.detailMeta.innerHTML.includes("data-i18n-dynamic>登录模块</span>"));

  feature.selectHistory("stage-vendor");
  assert.ok(elements.detailContent.innerHTML.includes('data-i18n-dynamic>计划</span>'));
  assert.ok(elements.detailContent.innerHTML.includes('data-i18n-dynamic>角色</strong>'));
  assert.ok(elements.detailContent.innerHTML.includes('data-i18n-dynamic>任务失败</p>'));
  assert.ok(elements.actionPanel.innerHTML.includes('data-i18n-dynamic>角色</strong>'));
  feature.returnToLatest();

  confirmResult = false;
  await feature.performItemAction("abandon");
  assert.ok(confirmMessages.at(-1).startsWith("Ignore “"));
  assert.ok(confirmMessages.at(-1).endsWith("This script will not be included in the test suite."));
  feature.toggleBatchMode(true);
  feature.setSelectedItems(["script-1"]);
  await feature.performBatchAction("abandon");
  assert.strictEqual(
    confirmMessages.at(-1),
    "Ignore the selected 1 scripts? They will not be included in the test suite.",
  );
  assert.doesNotMatch(confirmMessages.at(-1), CJK_PATTERN);
  confirmResult = true;

  feature.openEditor("regenerate");
  assert.strictEqual(elements.originalPrompt.value, "从登录测试计划重新生成完整脚本。");
  assert.strictEqual(elements.supplementalPrompt.value, "保留现有认证准备步骤。");
  assert.strictEqual(elements.detailModal.inert, true);
  assert.strictEqual(elements.detailModal.getAttribute("aria-hidden"), "true");
  assert.strictEqual(elements.editorModal.getAttribute("aria-hidden"), "false");
  assert.strictEqual(elements.editorMeta.getAttribute("data-i18n-dynamic"), null);
  assert.ok(elements.editorMeta.innerHTML.includes("Script preparation</span>"));
  assert.doesNotMatch(elements.editorBaseline.textContent, CJK_PATTERN);
  assert.doesNotMatch(elements.editorTarget.textContent, CJK_PATTERN);
  elements.originalPrompt.value = "用户尚未提交的 Prompt 草稿";
  elements.supplementalPrompt.value = "用户尚未提交的补充要求";
  feature.render();
  assert.strictEqual(elements.originalPrompt.value, "用户尚未提交的 Prompt 草稿");
  assert.strictEqual(elements.supplementalPrompt.value, "用户尚未提交的补充要求");
  feature.closeEditor();
  assert.strictEqual(elements.detailModal.inert, false);
  assert.strictEqual(elements.detailModal.getAttribute("aria-hidden"), "false");

  await feature.performItemAction("repair", {
    original_prompt: "人工更新后的修复 Prompt",
    supplemental_prompt: "仅匹配可见按钮",
  });
  const actionRequest = requests.find((entry) => entry.url.endsWith("/script-1/actions"));
  assert.strictEqual(actionRequest.body.action, "repair");
  assert.strictEqual(actionRequest.body.expected_revision_id, "revision-2");
  assert.strictEqual(actionRequest.body.supplemental_prompt, "仅匹配可见按钮");
  assert.strictEqual(feature.getState().selectedHistoryId, "stage-execute-3");
  assert.strictEqual(
    feature.getState().items.find((item) => item.script_item_id === "script-1").last_verified_version,
    3,
  );
  assert.ok(notices.some((notice) => notice.type === "success"));
  assert.doesNotMatch(notices.at(-1).message, CJK_PATTERN);
  assert.strictEqual(notices.at(-1).dynamic, false);

  itemActionMessage = "用户原始消息：计划";
  await feature.performItemAction("repair", {
    original_prompt: "人工更新后的修复 Prompt",
    supplemental_prompt: "仅匹配可见按钮",
  });
  assert.strictEqual(notices.at(-1).message, itemActionMessage);
  assert.strictEqual(notices.at(-1).dynamic, true);
  itemActionMessage = "";
  activeLocale = "zh-CN";

  feature.selectHistory("stage-generate");
  const firstSseItem = {
    ...currentSnapshot.items[0],
    status: "awaiting_human",
    included_in_suite: false,
    history: [
      ...currentSnapshot.items[0].history,
      {
        stage_id: "stage-sse-human-1",
        sequence_no: 8,
        stage_type: "human_review",
        status: "pending",
        input_revision_id: "revision-3",
        started_at: 1785456032000,
      },
    ],
  };
  feature.applyEvent({ payload: { step_output: { items: [firstSseItem] } } });
  assert.strictEqual(feature.getState().selectedHistoryId, "stage-generate");
  feature.returnToLatest();
  const secondSseItem = {
    ...firstSseItem,
    history: [
      ...firstSseItem.history,
      {
        stage_id: "stage-sse-human-2",
        sequence_no: 9,
        stage_type: "human_review",
        status: "pending",
        input_revision_id: "revision-3",
        started_at: 1785456033000,
      },
    ],
  };
  feature.applyEvent({ payload: { step_output: { items: [secondSseItem] } } });
  assert.strictEqual(feature.getState().selectedHistoryId, "stage-sse-human-2");

  feature.closeDetail();
  const noScriptItem = {
    item_id: "script-no-candidate",
    module_name: "支付模块",
    plan_filename: "支付.md",
    filename: "支付.spec.ts",
    status: "awaiting_human",
    current_script: null,
    current_revision_id: null,
    latest_analysis: {
      summary: "自动分析失败，请人工选择下一步。",
      recommended_action: "",
      analysis_error: "模型服务暂不可用",
      prompt_options: {
        regenerate: { original_prompt: "重新生成支付脚本", supplemental_prompt: "", enabled: true },
        repair: { original_prompt: "", supplemental_prompt: "", enabled: false },
      },
    },
    prompt_defaults: { regenerate: "重新生成支付脚本", repair: "" },
    history: [
      {
        stage_id: "no-script-generate",
        sequence_no: 1,
        stage_type: "generate",
        status: "failed",
        error: "生成器没有产出候选文件",
        started_at: 1785456040000,
        finished_at: 1785456041000,
      },
      {
        stage_id: "no-script-human",
        sequence_no: 2,
        stage_type: "human_review",
        status: "pending",
        started_at: 1785456042000,
      },
    ],
  };
  currentSnapshot = {
    status: "awaiting_action",
    counts: { total: 1, ready: 0, awaiting_human: 1, abandoned: 0, busy: 0, queued: 0 },
    items: [noScriptItem],
  };
  await feature.activate("agent-no-script");
  await feature.openDetail("script-no-candidate");
  assert.ok(elements.detailContent.innerHTML.includes("分析失败"));
  assert.ok(elements.detailContent.innerHTML.includes("未形成自动推荐"));
  assert.match(elements.actionPanel.innerHTML, /data-script-preparation-action="item-edit" disabled/);
  assert.match(elements.actionPanel.innerHTML, /data-script-preparation-action="item-execute" disabled/);
  assert.match(elements.actionPanel.innerHTML, /data-script-preparation-action="item-repair" disabled/);
  assert.ok(elements.actionPanel.innerHTML.includes("尚未生成候选脚本"));
  feature.openEditor("repair");
  assert.strictEqual(feature.getState().editorMode, "");
  feature.toggleBatchMode(true);
  feature.setSelectedItems(["script-no-candidate"]);
  assert.strictEqual(elements.batchExecute.disabled, true);
  assert.strictEqual(elements.batchRepair.disabled, true);
  feature.closeDetail();

  const deferredResolvers = {};
  snapshotInterceptor = (url) => new Promise((resolve) => {
    deferredResolvers[url.includes("agent-race-a") ? "a" : "b"] = resolve;
  });
  const staleLoad = feature.activate("agent-race-a");
  const currentLoad = feature.activate("agent-race-b");
  deferredResolvers.b({
    snapshot: {
      status: "succeeded",
      counts: { total: 1, ready: 1, awaiting_human: 0, abandoned: 0 },
      items: [{ ...readyItem, item_id: "race-b-item" }],
    },
  });
  await currentLoad;
  deferredResolvers.a({
    snapshot: {
      status: "succeeded",
      counts: { total: 1, ready: 1, awaiting_human: 0, abandoned: 0 },
      items: [{ ...readyItem, item_id: "race-a-item" }],
    },
  });
  await staleLoad;
  assert.strictEqual(feature.getState().runId, "agent-race-b");
  assert.deepStrictEqual(feature.getState().items.map((item) => item.script_item_id), ["race-b-item"]);

  snapshotInterceptor = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
  });
  activeLocale = "en";
  const timedFeature = context.window.createAgentScriptPreparationFeature(root, {
    document: documentRef,
    window: context.window,
    runId: "agent-timeout",
    requestJson,
    snapshotTimeoutMs: 1_000,
  });
  await timedFeature.activate();
  assert.match(timedFeature.getState().snapshotError, /Script preparation progress timed out/);
  assert.ok(elements.localNotice.textContent.includes("Script-preparation progress is temporarily unavailable"));
  assert.doesNotMatch(elements.localNotice.textContent, CJK_PATTERN);
  assert.strictEqual(elements.localNotice.getAttribute("data-i18n-dynamic"), "");
  timedFeature.destroy();
  snapshotInterceptor = null;

  const partialSource = fs.readFileSync(
    path.join(appDir, "templates/partials/agent_script_preparation.html"),
    "utf8",
  );
  assert.match(partialSource, /data-script-preparation-id="detailBackdrop"[\s\S]*?tabindex="-1"/);
  assert.match(partialSource, /data-script-preparation-id="editorBackdrop"[\s\S]*?tabindex="-1"/);

  feature.destroy();
  console.log("agent script preparation VM smoke: ok");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
