const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const {
  createScriptPreparationHarness,
  createTimerWindow,
} = require("./script-preparation-test-harness");

const appDir = path.resolve(__dirname, "../..");
const { intervals, windowRef } = createTimerWindow();
const context = {
  AbortController,
  clearInterval,
  clearTimeout,
  setInterval,
  setTimeout,
  window: windowRef,
  document: {},
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

assert.strictEqual(
  typeof context.window.createScriptPreparationFeature,
  "function",
  "the shared script-preparation factory must be exported",
);
assert.strictEqual(
  typeof context.window.createAgentScriptPreparationFeature,
  "function",
  "the Agent compatibility factory must remain exported",
);

const moduleHarness = createScriptPreparationHarness();
const agentHarness = createScriptPreparationHarness();
const moduleRequests = [];
const agentRequests = [];
const confirmMessages = [];

const itemFor = (itemId, moduleName) => ({
  item_id: itemId,
  module_name: moduleName,
  plan_filename: `${itemId}.md`,
  filename: `${itemId}.spec.ts`,
  status: "awaiting_human",
  current_revision_id: `${itemId}-revision-1`,
  current_script: {
    content: `test('${itemId}', async () => {});`,
  },
  history: [
    {
      stage_id: `${itemId}-generate`,
      sequence_no: 1,
      stage_type: "generate",
      status: "succeeded",
      output_revision_id: `${itemId}-revision-1`,
      started_at: 1785456000000,
      finished_at: 1785456001000,
    },
    {
      stage_id: `${itemId}-human`,
      sequence_no: 2,
      stage_type: "human_review",
      status: "pending",
      input_revision_id: `${itemId}-revision-1`,
      started_at: 1785456002000,
    },
  ],
});

const moduleItem = itemFor("module-item", "支付模块");
const agentItem = itemFor("agent-item", "登录模块");

function snapshotFor(item) {
  return {
    snapshot: {
      status: "awaiting_action",
      counts: {
        total: 1,
        processing: 0,
        ready: 0,
        awaiting_human: 1,
        abandoned: 0,
      },
      items: [item],
    },
  };
}

const moduleApi = {
  snapshotUrl(runId) {
    return `/module-preparation/${encodeURIComponent(runId)}`;
  },
  itemUrl(runId, itemId) {
    return `/module-preparation/${encodeURIComponent(runId)}/items/${encodeURIComponent(itemId)}`;
  },
  batchActionsUrl(runId) {
    return `/module-preparation/${encodeURIComponent(runId)}/batch-actions`;
  },
};

async function moduleRequestJson(url, options = {}) {
  const request = {
    url,
    method: options.method || "GET",
    body: options.body ? JSON.parse(options.body) : null,
  };
  moduleRequests.push(request);
  if (url.endsWith("/items/module-item")) {
    return { item: moduleItem };
  }
  if (url.endsWith("/batch-actions")) {
    return {
      accepted: [{ item_id: "module-item" }],
      rejected: [],
      should_continue: true,
    };
  }
  if (/^\/module-preparation\/[^/]+$/.test(url)) {
    return snapshotFor(moduleItem);
  }
  throw new Error(`unexpected module request: ${url}`);
}

async function agentRequestJson(url, options = {}) {
  agentRequests.push({
    url,
    method: options.method || "GET",
    body: options.body ? JSON.parse(options.body) : null,
  });
  if (url.endsWith("/script-items/agent-item")) {
    return { item: agentItem };
  }
  if (url.endsWith("/script-preparation")) {
    return snapshotFor(agentItem);
  }
  throw new Error(`unexpected Agent request: ${url}`);
}

const moduleFooter = "执行成功的脚本会保留在当前模块；忽略只影响本次准备。";
const moduleFeature = context.window.createScriptPreparationFeature(
  moduleHarness.root,
  {
    document: moduleHarness.documentRef,
    window: windowRef,
    runId: "module-run-1",
    requestJson: moduleRequestJson,
    api: moduleApi,
    context: {
      footerHint: moduleFooter,
      abandonItemMessage({ title }) {
        return `忽略模块准备项“${title}”吗？已有脚本不会被删除。`;
      },
      abandonBatchMessage({ count }) {
        return `忽略选中的 ${count} 个模块准备项吗？已有脚本不会被删除。`;
      },
    },
    pollIntervalMs: 3_000,
    confirm(message) {
      confirmMessages.push(message);
      return false;
    },
  },
);

const agentFeature = context.window.createAgentScriptPreparationFeature(
  agentHarness.root,
  {
    document: agentHarness.documentRef,
    window: windowRef,
    runId: "agent-run-1",
    requestJson: agentRequestJson,
    pollIntervalMs: 0,
  },
);

(async () => {
  await moduleFeature.activate();
  await agentFeature.activate();

  assert.strictEqual(
    moduleRequests[0].url,
    "/module-preparation/module-run-1",
    "module workbench must use the injected snapshot URL",
  );
  assert.strictEqual(
    agentRequests[0].url,
    "/api/agent/runs/agent-run-1/script-preparation",
    "the compatibility factory must preserve the Agent endpoint fallback",
  );
  assert.strictEqual(moduleHarness.elements.tableFooterHint.textContent, moduleFooter);
  assert.deepStrictEqual(
    Array.from(moduleFeature.getState().items, (item) => item.script_item_id),
    ["module-item"],
  );
  assert.deepStrictEqual(
    Array.from(agentFeature.getState().items, (item) => item.script_item_id),
    ["agent-item"],
  );

  moduleFeature.setFilter("awaiting_human");
  moduleFeature.toggleBatchMode(true);
  moduleFeature.setSelectedItems(["module-item"]);
  assert.strictEqual(moduleFeature.getState().filter, "awaiting_human");
  assert.deepStrictEqual(Array.from(moduleFeature.getState().selectedIds), ["module-item"]);
  assert.strictEqual(agentFeature.getState().filter, "all");
  assert.deepStrictEqual(Array.from(agentFeature.getState().selectedIds), []);
  assert.strictEqual(agentHarness.elements.tableFooterHint.textContent, "只有执行成功的脚本会进入测试集");

  await moduleFeature.openDetail("module-item");
  assert.ok(
    moduleRequests.some((request) => request.url === "/module-preparation/module-run-1/items/module-item"),
    "module detail must use the injected item URL",
  );
  assert.strictEqual(moduleHarness.documentRef.body.classList.contains("agent-modal-open"), true);
  moduleFeature.deactivate();
  assert.strictEqual(moduleHarness.elements.detailModal.classList.contains("hidden"), true);
  assert.strictEqual(moduleHarness.documentRef.body.classList.contains("agent-modal-open"), false);
  assert.strictEqual(moduleHarness.root.classList.contains("hidden"), true);
  await moduleFeature.activate();
  await moduleFeature.openDetail("module-item");
  await moduleFeature.performItemAction("abandon");
  assert.strictEqual(
    confirmMessages.at(-1),
    "忽略模块准备项“module-item.md”吗？已有脚本不会被删除。",
  );

  moduleFeature.closeDetail();
  moduleFeature.toggleBatchMode(true);
  moduleFeature.setSelectedItems(["module-item"]);
  await moduleFeature.performBatchAction("abandon");
  assert.strictEqual(
    confirmMessages.at(-1),
    "忽略选中的 1 个模块准备项吗？已有脚本不会被删除。",
  );

  moduleFeature.setSelectedItems(["module-item"]);
  await moduleFeature.performBatchAction("execute");
  const batchRequest = moduleRequests.find(
    (request) => request.url.endsWith("/batch-actions"),
  );
  assert.ok(batchRequest, "module batch actions must use the injected batch URL");
  assert.strictEqual(batchRequest.url, "/module-preparation/module-run-1/batch-actions");
  assert.strictEqual(batchRequest.body.action, "execute");

  moduleFeature.applyEvent({
    payload: {
      script_preparation: {
        status: "cancelled",
        counts: { total: 1, ready: 0, awaiting_human: 1, abandoned: 0 },
        items: [moduleItem],
      },
    },
  });
  assert.strictEqual(moduleHarness.elements.stageStatus.textContent, "已取消");

  assert.strictEqual(intervals.size, 1, "only the polling module instance owns an interval");
  assert.strictEqual([...intervals.values()][0].delay, 3_000);
  moduleFeature.setRun("module-run-2");
  assert.strictEqual(intervals.size, 1, "switching runs must not stack polling intervals");
  moduleFeature.deactivate();
  assert.strictEqual(intervals.size, 0, "deactivate must clear the polling interval");
  await moduleFeature.activate("module-run-2");
  assert.strictEqual(intervals.size, 1, "reactivation creates exactly one polling interval");
  moduleFeature.destroy();
  assert.strictEqual(intervals.size, 0, "destroy must clear the polling interval");

  agentFeature.destroy();
  assert.strictEqual(intervals.size, 0, "the non-polling Agent instance must not leak timers");
  console.log("module script preparation shared VM smoke: ok");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
