const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");
const translationsContext = { window: {} };
vm.createContext(translationsContext);
for (const language of ["zh-CN", "en"]) {
  vm.runInContext(
    fs.readFileSync(path.join(appDir, `static/js/i18n/${language}.js`), "utf8"),
    translationsContext,
  );
}

class FakeElement {
  constructor(attributes = {}, textContent = "") {
    this.nodeType = 1;
    this.parentElement = null;
    this.attributes = new Map(Object.entries(attributes));
    this.textContent = textContent;
  }

  hasAttribute(name) {
    return this.attributes.has(name);
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  closest(selector) {
    for (let element = this; element; element = element.parentElement) {
      if (selector.includes("[data-i18n-dynamic]") && element.hasAttribute("data-i18n-dynamic")) {
        return element;
      }
      if (selector.includes("[data-i18n-skip]") && element.hasAttribute("data-i18n-skip")) {
        return element;
      }
    }
    return null;
  }

  matches() {
    return false;
  }
}

const staticOptions = [
  new FakeElement({ "data-i18n-key": "planImport.conflict.reject" }, "拒绝整个导入（默认）"),
  new FakeElement({ "data-i18n-key": "planImport.conflict.skip" }, "跳过同名计划"),
  new FakeElement({ "data-i18n-key": "planImport.conflict.overwrite" }, "覆盖并创建新版本"),
];
const dynamicOption = new FakeElement(
  { "data-i18n-key": "planImport.conflict.reject", "data-i18n-dynamic": "" },
  "用户命名的选项",
);
const body = new FakeElement();
body.querySelectorAll = () => [...staticOptions, dynamicOption];

class FakeMutationObserver {
  observe() {}
  disconnect() {}
}

const runtimeContext = {
  window: {
    WaterfallTranslations: translationsContext.window.WaterfallTranslations,
    confirm: () => true,
  },
  document: {
    body,
    documentElement: { dataset: { locale: "en" }, lang: "en" },
    createTreeWalker() {
      return { currentNode: null, nextNode: () => false };
    },
  },
  Node: { ELEMENT_NODE: 1, TEXT_NODE: 3 },
  NodeFilter: { SHOW_TEXT: 4 },
  MutationObserver: FakeMutationObserver,
};
vm.createContext(runtimeContext);
vm.runInContext(fs.readFileSync(path.join(appDir, "static/js/i18n.js"), "utf8"), runtimeContext);
runtimeContext.window.WaterfallI18n.setLocale("en");

assert.deepStrictEqual(
  staticOptions.map((option) => option.textContent),
  ["Reject the entire import (default)", "Skip duplicate plans", "Overwrite and create a new version"],
);
assert.strictEqual(dynamicOption.textContent, "用户命名的选项", "Dynamic option text must remain unchanged.");

const template = fs.readFileSync(path.join(appDir, "templates/partials/plan_transfer_modals.html"), "utf8");
for (const key of ["reject", "skip", "overwrite"]) {
  assert.match(
    template,
    new RegExp(`<option value="${key}" data-i18n-key="planImport\\.conflict\\.${key}">`),
  );
}

process.stdout.write("static option i18n VM smoke: ok\n");
