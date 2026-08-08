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
process.stdout.write("i18n catalog and template coverage: ok\n");
