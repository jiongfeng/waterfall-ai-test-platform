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

const chinese = context.window.WaterfallTranslations["zh-CN"];
const english = context.window.WaterfallTranslations.en;
assert.deepStrictEqual(
  Object.keys(chinese).sort(),
  Object.keys(english).filter((key) => key !== "source").sort(),
  "Semantic translation keys must remain identical between locales.",
);

const sourceCatalog = english.source;
const templateDirectory = path.join(appDir, "templates");
const templateFiles = [
  "templates/index.html",
  ...fs.readdirSync(path.join(templateDirectory, "partials"))
    .filter((name) => name.endsWith(".html"))
    .map((name) => `templates/partials/${name}`),
];
for (const filename of templateFiles) {
  const template = fs.readFileSync(path.join(appDir, filename), "utf8");
  const values = [
    ...template.matchAll(/>([^<>]*[\u3400-\u9fff][^<>]*)</g),
    ...template.matchAll(/(?:title|aria-label|placeholder)="([^"]*[\u3400-\u9fff][^"]*)"/g),
  ]
    .map((match) => match[1].trim())
    .filter(Boolean);
  const missing = [...new Set(values.filter((value) => !sourceCatalog[value]))];
  assert.deepStrictEqual(
    missing,
    [],
    `${filename} contains a first-party Chinese UI literal without an English translation.`,
  );
}

// Feature modules still contain legacy literals while they are migrated to
// semantic t() calls.  Every simple first-party literal must have an exact
// English source catalog entry, so the DOM bridge cannot silently expose it in
// an English project.  Prompt/asset content is safe: those controls are
// excluded by i18n.js and must remain in its original language.
const featureDirectory = path.join(appDir, "static/js/features");
const agentAcceptanceFeatures = new Set([
  "agent.js",
  "agent-failure-workspace.js",
  "agent-script-preparation.js",
  "requirements.js",
]);
for (const filename of fs.readdirSync(featureDirectory).filter((name) => name.endsWith(".js"))) {
  const source = fs.readFileSync(path.join(featureDirectory, filename), "utf8");
  const literals = [...source.matchAll(/"([^"\n]*[\u3400-\u9fff][^"\n]*)"/g)]
    .map((match) => match[1])
    .filter((value) => !value.includes("${") && !/[<>]/.test(value));
  const missing = [...new Set(literals.filter((value) => !sourceCatalog[value]))];
  assert.deepStrictEqual(
    missing,
    [],
    `${filename} contains a legacy Chinese literal without an English source catalog entry.`,
  );
  if (agentAcceptanceFeatures.has(filename)) {
    const embeddedHtmlValues = [...source.matchAll(/>([^<>`]*[\u3400-\u9fff][^<>`]*)</g)]
      .map((match) => match[1].trim())
      .filter((value) => value && !value.includes("${"));
    const missingEmbeddedHtml = [...new Set(embeddedHtmlValues.filter((value) => !sourceCatalog[value]))];
    assert.deepStrictEqual(
      missingEmbeddedHtml,
      [],
      `${filename} contains an embedded first-party Chinese UI literal without an English translation.`,
    );
  }
}

assert.strictEqual(sourceCatalog["当前项目"], "Current project");
assert.strictEqual(sourceCatalog["暂无测试计划"], "No test plans yet");
assert.strictEqual(sourceCatalog["未执行"], "Not run");
assert.strictEqual(sourceCatalog["未修复"], "Not repaired");
assert.strictEqual(sourceCatalog["搜索模块或用例"], "Search modules or cases");
assert.strictEqual(sourceCatalog["修复"], "Repair");
assert.strictEqual(sourceCatalog["未上传需求"], "No requirements uploaded");
assert.strictEqual(sourceCatalog["角色列表"], "Role list");
assert.strictEqual(sourceCatalog["搜索模块或计划"], "Search modules or plans");
assert.strictEqual(sourceCatalog["暂无角色。"], "No roles yet.");
assert.strictEqual(english["moduleScriptPreparation.bulkGenerate"], "Generate and prepare");
assert.strictEqual(
  english["moduleScriptPreparation.createdNotice"],
  "Script-preparation task created. Scripts are being generated and verified automatically.",
);
assert.strictEqual(
  english["moduleScriptPreparation.footerHint"],
  "Passed scripts remain in the current module; ignoring applies only to this preparation task",
);
assert.match(
  fs.readFileSync(path.join(appDir, "static/app.js"), "utf8"),
  /t\("scripts\.empty\.(?:noMatches|noScripts)"\)/,
  "Script-tree empty states must use semantic translation keys.",
);
const adminFeatureSource = fs.readFileSync(path.join(appDir, "static/js/features/admin.js"), "utf8");
for (const key of ["auth.resetPassword", "auth.systemRoleTag", "auth.permission."]) {
  assert.ok(adminFeatureSource.includes(key), `Admin UI must use the ${key} translation path.`);
}
assert.match(
  fs.readFileSync(path.join(appDir, "templates/index.html"), "utf8"),
  /data-i18n-key="moduleScriptPreparation\.bulkGenerate"/,
  "The module preparation button must use its semantic translation key.",
);
assert.match(
  fs.readFileSync(path.join(appDir, "static/js/i18n.js"), "utf8"),
  /scripts total/,
  "Dynamic count formats must have an English localization path.",
);
const i18nRuntime = fs.readFileSync(path.join(appDir, "static/js/i18n.js"), "utf8");
for (const expected of [
  "Template source:",
  "events loaded",
  "artifacts",
  "Confidence ${value}",
  "passed · ${failed} failed · ${total} total",
  "Duration ${hours}h ${minutes}m",
]) {
  assert.ok(
    i18nRuntime.includes(expected),
    `Agent runtime status format must include ${expected}.`,
  );
}

let confirmedMessage = "";
const runtimeContext = {
  window: {
    WaterfallTranslations: context.window.WaterfallTranslations,
    confirm(message) {
      confirmedMessage = message;
      return true;
    },
  },
  document: { documentElement: { dataset: { locale: "en" } } },
};
vm.createContext(runtimeContext);
vm.runInContext(i18nRuntime, runtimeContext);
const runtime = runtimeContext.window.WaterfallI18n;
assert.strictEqual(runtime.source("管理员"), "管理员", "User-authored text must not be translated globally.");
assert.strictEqual(runtime.t("auth.builtInAdminDisplayName"), "Administrator");
assert.strictEqual(runtime.t("scripts.empty.noScripts"), "No test scripts found");
assert.strictEqual(runtime.t("auth.resetPassword"), "Reset password");
assert.strictEqual(runtime.t("auth.permission.menu.plans"), "Test plans");
assert.strictEqual(runtime.t("scriptPreparation.cancelledError"), "The script-preparation task was cancelled.");
assert.strictEqual(
  runtime.source("项目目录将自动创建为：/workspace/<项目标识>"),
  "The project directory will be created as: /workspace/<project-key>",
);
assert.strictEqual(runtime.source("共 7 个候选模块"), "7 candidate modules");
assert.strictEqual(runtime.source("置信度 97%"), "Confidence 97%");
assert.strictEqual(runtime.source("2 / 6 个模块"), "2 / 6 modules");
assert.strictEqual(runtime.source("共 11 条单用例计划"), "11 single-case plans");
assert.strictEqual(runtime.source("未生成"), "未生成", "User-authored text must not be translated as a status.");
assert.strictEqual(runtime.t("status.notGenerated"), "Not generated");
assert.strictEqual(runtimeContext.window.confirm("确定停止当前任务吗？已生成的结果会保留。"), true);
assert.strictEqual(confirmedMessage, "Stop the current task? Generated results will be kept.");
runtimeContext.window.confirm("确认删除选中的 3 条测试计划？");
assert.strictEqual(confirmedMessage, "Delete the selected 3 test plans?");
runtimeContext.window.confirm("确定删除“checkout.spec.ts”吗？脚本会被归档，失败历史仍会保留。");
assert.strictEqual(
  confirmedMessage,
  "Delete “checkout.spec.ts”? The script will be archived and its failure history will be kept.",
);
runtimeContext.window.confirm("“checkout.spec.ts”没有可删除脚本，确定保留失败记录并忽略该项吗？");
assert.strictEqual(
  confirmedMessage,
  "“checkout.spec.ts” has no script to delete. Keep the failure record and ignore this item?",
);
assert.strictEqual(
  runtime.log("工具完成：playwright-test_browser_click\nconst label = '用户内容';"),
  "Tool completed: playwright-test_browser_click\nconst label = '用户内容';",
  "Only known platform log lines should be localized; generated code must remain unchanged.",
);
assert.strictEqual(
  runtime.source("拆分计划失败：多计划拆分检测到已有文件内容冲突；未写入、登记或删除任何计划文件：case-1.md"),
  "Plan splitting failed: Existing file-content conflicts were found while splitting the multi-case plan; no plan files were written, registered, or deleted: case-1.md",
);
assert.strictEqual(
  runtime.platformFailure("generate_plans", "拆分计划失败：多计划拆分检测到已有文件内容冲突；未写入、登记或删除任何计划文件：case-1.md"),
  "Plan splitting failed: Existing file-content conflicts were found while splitting the multi-case plan; no plan files were written, registered, or deleted: case-1.md",
);
assert.strictEqual(runtime.platformFailure("generate_scripts", "未生成"), "未生成");

class FakeTextNode {
  constructor(value) {
    this.nodeType = 3;
    this.nodeValue = value;
    this.parentElement = null;
  }
}

class FakeElement {
  constructor(attributes = {}) {
    this.nodeType = 1;
    this.parentElement = null;
    this.childNodes = [];
    this.attributes = new Map(Object.entries(attributes));
  }

  append(...nodes) {
    nodes.forEach((node) => {
      node.parentElement = this;
      this.childNodes.push(node);
    });
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  hasAttribute(name) {
    return this.attributes.has(name);
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  closest(selector) {
    for (let element = this; element; element = element.parentElement) {
      if (selector.includes("[data-i18n-dynamic]") && element.hasAttribute("data-i18n-dynamic")) return element;
      if (
        selector.includes("[data-i18n-dynamic-attributes]") &&
        element.hasAttribute("data-i18n-dynamic-attributes")
      ) {
        return element;
      }
    }
    return null;
  }

  matches() {
    return false;
  }

  querySelectorAll() {
    const descendants = [];
    const visit = (element) => {
      element.childNodes.forEach((child) => {
        if (child.nodeType !== 1) return;
        descendants.push(child);
        visit(child);
      });
    };
    visit(this);
    return descendants;
  }
}

let observedMutations = null;
class FakeMutationObserver {
  constructor(callback) {
    this.callback = callback;
    observedMutations = this;
  }

  observe() {}
  disconnect() {}
}

const platformNode = new FakeElement({ title: "用户" });
const platformText = new FakeTextNode("计划");
platformNode.append(platformText);
const dynamicNode = new FakeElement({ "data-i18n-dynamic": "", title: "用户" });
const dynamicText = new FakeTextNode("计划");
dynamicNode.append(dynamicText);
const attributeOnlyNode = new FakeElement({ "data-i18n-dynamic-attributes": "", title: "角色" });
const attributeOnlyText = new FakeTextNode("失败");
attributeOnlyNode.append(attributeOnlyText);
const body = new FakeElement();
body.append(platformNode, dynamicNode, attributeOnlyNode);
const observerDocument = {
  body,
  documentElement: { dataset: { locale: "en" }, lang: "en" },
  createTreeWalker(root) {
    const nodes = [];
    const visit = (element) => {
      element.childNodes.forEach((child) => {
        if (child.nodeType === 3) nodes.push(child);
        else visit(child);
      });
    };
    visit(root);
    let index = -1;
    return {
      currentNode: null,
      nextNode() {
        index += 1;
        this.currentNode = nodes[index] || null;
        return Boolean(this.currentNode);
      },
    };
  },
};
const observerContext = {
  window: { WaterfallTranslations: context.window.WaterfallTranslations, confirm: () => true },
  document: observerDocument,
  Node: { ELEMENT_NODE: 1, TEXT_NODE: 3 },
  NodeFilter: { SHOW_TEXT: 4 },
  MutationObserver: FakeMutationObserver,
};
vm.createContext(observerContext);
vm.runInContext(i18nRuntime, observerContext);
observerContext.window.WaterfallI18n.setLocale("en");
assert.strictEqual(platformText.nodeValue, "Plans");
assert.strictEqual(platformNode.getAttribute("title"), "Users");
assert.strictEqual(dynamicText.nodeValue, "计划", "Marked user content must survive the initial DOM walk.");
assert.strictEqual(dynamicNode.getAttribute("title"), "用户", "Marked dynamic attributes must stay verbatim.");
assert.strictEqual(attributeOnlyText.nodeValue, "Failed", "Attribute-only boundaries must not suppress platform text.");
assert.strictEqual(attributeOnlyNode.getAttribute("title"), "角色");

const addedDynamic = new FakeElement({ "data-i18n-dynamic": "", title: "用户" });
const addedDynamicText = new FakeTextNode("失败");
addedDynamic.append(addedDynamicText);
const addedPlatform = new FakeElement();
const addedPlatformText = new FakeTextNode("失败");
addedPlatform.append(addedPlatformText);
body.append(addedDynamic, addedPlatform);
observedMutations.callback([{ type: "childList", addedNodes: [addedDynamic, addedPlatform] }]);
assert.strictEqual(addedDynamicText.nodeValue, "失败", "Observer must preserve dynamically inserted payload text.");
assert.strictEqual(addedDynamic.getAttribute("title"), "用户");
assert.strictEqual(addedPlatformText.nodeValue, "Failed", "Observer must still localize newly inserted platform text.");

runtimeContext.document.documentElement.dataset.locale = "zh-CN";
assert.strictEqual(runtime.log("工具完成：browser_click"), "工具完成：browser_click");
process.stdout.write("i18n catalog and template coverage: ok\n");
