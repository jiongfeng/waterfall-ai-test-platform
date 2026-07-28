const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");
const requests = [];
const context = {
  document: {
    querySelector(selector) {
      assert.strictEqual(selector, 'meta[name="csrf-token"]');
      return {
        getAttribute(name) {
          assert.strictEqual(name, "content");
          return "csrf-token-from-page";
        },
      };
    },
  },
  fetch: async (url, options) => {
    requests.push({ url, options });
    return {
      ok: true,
      status: 200,
      async json() {
        return { ok: true };
      },
    };
  },
  window: {},
};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(
    path.join(appDir, "static/js/core/api-client.js"),
    "utf8",
  ),
  context,
);

const client = context.window.createApiClient({
  getProjectKey: () => "demo-project",
});
assert.deepStrictEqual(
  JSON.parse(
    JSON.stringify(
      client.getProjectHeaders({ Accept: "application/json" }),
    ),
  ),
  {
    "X-Project-Key": "demo-project",
    Accept: "application/json",
    "X-CSRF-Token": "csrf-token-from-page",
  },
);

client
  .requestJson("/api/example", {
    method: "POST",
    body: JSON.stringify({ value: 1 }),
  })
  .then((payload) => {
    assert.deepStrictEqual(payload, { ok: true });
    assert.strictEqual(requests.length, 1);
    assert.strictEqual(
      requests[0].options.headers["X-CSRF-Token"],
      "csrf-token-from-page",
    );
    assert.strictEqual(
      requests[0].options.headers["X-Project-Key"],
      "demo-project",
    );
    console.log("api client CSRF VM smoke: ok");
  })
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
