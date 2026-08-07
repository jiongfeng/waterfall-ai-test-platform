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
for (const filename of ["templates/index.html", "templates/partials/agent_panel.html"]) {
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

assert.strictEqual(sourceCatalog["当前项目"], "Current project");
assert.strictEqual(sourceCatalog["暂无测试计划"], "No test plans yet");
process.stdout.write("i18n catalog and template coverage: ok\n");
