const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");
const context = { window: {} };
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(appDir, "static/js/features/plan-transfer.js"), "utf8"),
  context,
);

function element({ hidden = false } = {}) {
  const classes = new Set(hidden ? ["hidden"] : []);
  const listeners = new Map();
  return {
    value: "",
    textContent: "",
    disabled: false,
    checked: false,
    indeterminate: false,
    files: [],
    children: [],
    classList: {
      add: (...names) => names.forEach((name) => classes.add(name)),
      remove: (...names) => names.forEach((name) => classes.delete(name)),
      contains: (name) => classes.has(name),
    },
    addEventListener(type, callback) {
      listeners.set(type, callback);
    },
    trigger(type, event = {}) {
      return listeners.get(type)?.({ target: this, ...event });
    },
    append(...children) {
      this.children.push(...children);
    },
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    replaceChildren(...children) {
      this.children = [...children];
    },
    setCustomValidity(message) {
      this.validationMessage = message;
    },
    reportValidity() {
      this.reportedValidity = true;
      return !this.validationMessage;
    },
    focus() {
      this.focused = true;
    },
    click() {
      this.clicked = true;
    },
    remove() {
      this.removed = true;
    },
  };
}

const elements = {};
for (const name of [
  "exportPlansButton",
  "importPlansButton",
  "planExportClose",
  "planExportCancel",
  "planExportSubmit",
  "planExportSearch",
  "planExportSelectAll",
  "planExportSelectionCount",
  "planExportTree",
  "planImportClose",
  "planImportCancel",
  "planImportSubmit",
  "planImportFile",
  "planImportConflictPolicy",
]) {
  elements[name] = element();
}
elements.planExportModal = element({ hidden: true });
elements.planImportModal = element({ hidden: true });

const state = {
  isEditing: false,
  project: { currentKey: "alpha" },
  plans: {
    selectedModule: "贷款管理",
    selectedPlanFile: "核心回归.md",
    modules: [
      {
        name: "贷款管理",
        plans: [
          { name: "核心回归", filename: "核心回归.md" },
          { name: "边界检查", filename: "边界检查.md" },
        ],
      },
      {
        name: "用户管理",
        plans: [{ name: "权限检查", filename: "权限检查.md" }],
      },
    ],
  },
};

class FakeFormData {
  constructor() {
    this.entries = [];
  }

  append(name, value) {
    this.entries.push([name, value]);
  }
}

const requests = [];
const responses = [
  {
    ok: true,
    headers: {},
    blob: async () => ({ kind: "xlsx" }),
  },
  {
    ok: true,
    json: async () => ({ created: 1, overwritten: 1, skipped: 0 }),
  },
  {
    ok: false,
    json: async () => ({ error: "发现同名测试计划" }),
  },
];
const notices = [];
const createdLinks = [];
let planReloads = 0;
const windowListeners = new Map();
const feature = context.window.createPlanTransferFeature({
  state,
  elements,
  document: {
    body: {
      appendChild(link) {
        createdLinks.push(link);
      },
    },
    createElement: () => element(),
  },
  window: {
    requestAnimationFrame: (callback) => callback(),
    addEventListener: (type, callback) => windowListeners.set(type, callback),
    URL: {
      createObjectURL: () => "blob:plans",
      revokeObjectURL: (url) => assert.strictEqual(url, "blob:plans"),
    },
  },
  fetch: async (url, options) => {
    requests.push({ url, options });
    return responses.shift();
  },
  FormData: FakeFormData,
  getProjectRequestHeaders: () => ({ "X-Project-Key": "alpha" }),
  readFetchError: async (response) => (await response.json()).error,
  getDownloadFilename: () => "测试计划-alpha.xlsx",
  loadPlanModules: async () => {
    planReloads += 1;
  },
  setNotice: (message, type) => notices.push({ message, type }),
  stripMarkdownSuffix: (value) => value.replace(/\.md$/, ""),
});
feature.bind();

(async () => {
  elements.exportPlansButton.trigger("click");
  assert.strictEqual(elements.planExportModal.classList.contains("hidden"), false);
  assert.strictEqual(elements.planExportSelectionCount.textContent, "已选择 1 条");
  assert.strictEqual(elements.planExportSubmit.disabled, false);

  elements.planExportSelectAll.checked = true;
  elements.planExportSelectAll.trigger("change");
  assert.strictEqual(elements.planExportSelectionCount.textContent, "已选择 3 条");
  await feature.submitExport();

  assert.strictEqual(requests[0].url, "/api/plans/export-xlsx");
  assert.strictEqual(requests[0].options.method, "POST");
  assert.deepStrictEqual(JSON.parse(requests[0].options.body), {
    plans: [
      { module_name: "贷款管理", plan_filename: "核心回归.md" },
      { module_name: "贷款管理", plan_filename: "边界检查.md" },
      { module_name: "用户管理", plan_filename: "权限检查.md" },
    ],
  });
  assert.strictEqual(createdLinks[0].download, "测试计划-alpha.xlsx");
  assert.strictEqual(createdLinks[0].clicked, true);

  elements.importPlansButton.trigger("click");
  const file = { name: "plans.xlsx" };
  elements.planImportFile.files = [file];
  elements.planImportConflictPolicy.value = "overwrite";
  await feature.submitImport();

  assert.strictEqual(requests[1].url, "/api/plans/import-xlsx");
  assert.strictEqual(requests[1].options.headers["X-Project-Key"], "alpha");
  assert.deepStrictEqual(requests[1].options.body.entries, [
    ["file", file],
    ["conflict_policy", "overwrite"],
  ]);
  assert.strictEqual(planReloads, 1);
  assert.ok(notices.at(-1).message.includes("新增 1 条，覆盖 1 条"));

  feature.openImportModal();
  elements.planImportFile.files = [file];
  await feature.submitImport();
  assert.strictEqual(planReloads, 1);
  assert.strictEqual(notices.at(-1).type, "error");
  assert.ok(notices.at(-1).message.includes("同名"));

  elements.planImportFile.files = [];
  await feature.submitImport();
  assert.strictEqual(elements.planImportFile.reportedValidity, true);

  console.log("plan transfer feature VM smoke: ok");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
