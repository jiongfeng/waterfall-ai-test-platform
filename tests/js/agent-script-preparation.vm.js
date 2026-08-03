const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");

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
      return attributes.get(name) || null;
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

const requests = [];
async function requestJson(url, options = {}) {
  const body = options.body ? JSON.parse(options.body) : null;
  requests.push({ url, method: options.method || "GET", body });
  if (url.endsWith("/script-preparation")) {
    if (snapshotInterceptor) {
      return snapshotInterceptor(url);
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
    return { accepted: true, item: updatedItem, should_continue: true };
  }
  throw new Error(`unexpected request: ${url}`);
}

const notices = [];
const feature = context.window.createAgentScriptPreparationFeature(root, {
  document: documentRef,
  window: context.window,
  runId: "agent-1",
  requestJson,
  setNotice: (message, type) => notices.push({ message, type }),
  confirm: () => true,
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

  feature.openEditor("regenerate");
  assert.strictEqual(elements.originalPrompt.value, "从登录测试计划重新生成完整脚本。");
  assert.strictEqual(elements.supplementalPrompt.value, "保留现有认证准备步骤。");
  assert.strictEqual(elements.detailModal.inert, true);
  assert.strictEqual(elements.detailModal.getAttribute("aria-hidden"), "true");
  assert.strictEqual(elements.editorModal.getAttribute("aria-hidden"), "false");
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
