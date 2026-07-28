const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");
const source = fs.readFileSync(
  path.join(appDir, "static/js/features/setup-preparation.js"),
  "utf8",
);
const context = { window: {} };
vm.createContext(context);
vm.runInContext(source, context);

const setupState = {
  scripts: [],
  bindings: [],
  runs: [],
  draftEnvironmentRows: [],
  scriptQuery: "",
  scriptStatusFilter: "all",
};
const feature = context.window.createSetupPreparation({
  setupState,
  root: {},
  getProject: () => ({ project_key: "default", name: "Default" }),
  getProjectKey: () => "default",
  getTestSuites: () => [],
  getScriptModules: () => [],
  isActive: () => false,
  requestJson: async () => ({}),
  encodePathPart: encodeURIComponent,
  isPlainObject: (value) =>
    Boolean(value && typeof value === "object" && !Array.isArray(value)),
  escapeHtml: String,
  stripSpecSuffix: String,
  renderHost: () => {},
});

const legacySecret = "legacy-plaintext-secret";
const legacy = feature.normalizeScript({
  uid: "legacy",
  environment_overrides: { API_TOKEN: legacySecret },
  credentials_migration_required: true,
  legacy_environment_keys: ["API_TOKEN"],
});
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(legacy.environment_refs)),
  {},
);
assert.strictEqual(legacy.credentials_migration_required, true);
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(legacy.legacy_environment_keys)),
  ["API_TOKEN"],
);
assert.ok(!JSON.stringify(legacy).includes(legacySecret));

const current = feature.normalizeScript({
  uid: "current",
  environment_refs: {
    API_TOKEN: "TARGET_SETUP_API_TOKEN",
  },
});
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(current.environment_refs)),
  { API_TOKEN: "TARGET_SETUP_API_TOKEN" },
);
assert.strictEqual(current.credentials_migration_required, false);

const migrationRows = feature.environmentRows(
  current.environment_refs,
  ["API_TOKEN", "DATABASE_URL"],
);
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(migrationRows)).map(
    ({ key, reference }) => ({ key, reference }),
  ),
  [
    {
      key: "API_TOKEN",
      reference: "TARGET_SETUP_API_TOKEN",
    },
    { key: "DATABASE_URL", reference: "" },
  ],
);

assert.ok(!source.includes("script.environment_overrides"));
assert.ok(!source.includes("data-setup-environment-value"));
assert.ok(source.includes("data-setup-environment-reference"));
assert.ok(source.includes("原值已封存且不会执行"));

process.stdout.write("setup preparation environment refs VM smoke: ok\n");
