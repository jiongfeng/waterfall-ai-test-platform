const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");
const elementCache = new Map();

function createClassList() {
  const values = new Set(["hidden"]);
  return {
    add: (...names) => names.forEach((name) => values.add(name)),
    remove: (...names) => names.forEach((name) => values.delete(name)),
    contains: (name) => values.has(name),
    toggle(name, force) {
      const enabled = force === undefined ? !values.has(name) : Boolean(force);
      if (enabled) {
        values.add(name);
      } else {
        values.delete(name);
      }
      return enabled;
    },
  };
}

function createElement(key = "element") {
  if (elementCache.has(key)) {
    return elementCache.get(key);
  }
  const element = {
    id: key,
    value: "",
    textContent: "",
    innerHTML: "",
    checked: false,
    disabled: false,
    files: [],
    dataset: {},
    style: {},
    classList: createClassList(),
    scrollTop: 0,
    scrollHeight: 0,
    children: [],
    addEventListener() {},
    removeEventListener() {},
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    replaceChildren(...children) {
      this.children = children;
    },
    remove() {},
    focus() {},
    click() {},
    reset() {},
    load() {},
    setCustomValidity() {},
    setAttribute(name, value) {
      this[name] = String(value);
    },
    getAttribute(name) {
      return this[name] ?? null;
    },
    removeAttribute(name) {
      delete this[name];
    },
    querySelector(selector) {
      return createElement(`${key}:${selector}`);
    },
    querySelectorAll() {
      return [];
    },
    closest() {
      return null;
    },
    contains() {
      return false;
    },
    getBoundingClientRect() {
      return { top: 0, left: 0, width: 100, height: 20 };
    },
    play: async () => {},
    pause() {},
  };
  const proxy = new Proxy(element, {
    get(target, property) {
      if (property in target) {
        return target[property];
      }
      return property === "options" ? [] : "";
    },
  });
  elementCache.set(key, proxy);
  return proxy;
}

const storage = new Map();
const requests = [];
function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    async json() {
      return data;
    },
    async text() {
      return JSON.stringify(data);
    },
    async blob() {
      return new Blob([JSON.stringify(data)]);
    },
  };
}

async function fetchStub(url) {
  const requestUrl = String(url);
  requests.push(requestUrl);
  if (requestUrl.includes("/api/auth/me")) {
    return jsonResponse({
      user: { username: "vm-user", display_name: "VM User" },
      permissions: ["menu.plans", "menu.projectSettings"],
      menus: ["plans"],
    });
  }
  if (requestUrl.includes("/api/projects")) {
    const project = {
      project_id: 1,
      project_key: "vm",
      name: "VM",
      is_default: true,
    };
    return jsonResponse({
      projects: [project],
      current_project: project,
      default_project: project,
      project_workspace_root: "/tmp",
    });
  }
  if (requestUrl.includes("/api/platform-records")) {
    return jsonResponse({ records: {} });
  }
  if (requestUrl.includes("/api/modules")) {
    return jsonResponse({ modules: [] });
  }
  return jsonResponse({});
}

const windowObject = {
  WaterfallI18n: {
    t: (key) => (key === "status.notGenerated" ? "Not generated" : key),
    source: (value) => value,
    log: (value) => value,
    getLocale: () => "en",
    setLocale() {},
    localizeDom() {},
  },
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
  },
  location: { href: "", assign() {} },
  addEventListener() {},
  removeEventListener() {},
  setInterval,
  clearInterval,
  setTimeout,
  clearTimeout,
  requestAnimationFrame: (callback) => callback(),
  confirm: () => true,
  crypto: { randomUUID: () => "vm-uuid" },
  fetch: fetchStub,
};
const documentObject = {
  body: createElement("body"),
  getElementById: (id) => createElement(id),
  createElement: (tag) =>
    createElement(`created:${tag}:${elementCache.size}`),
  querySelector: (selector) => createElement(`document:${selector}`),
  querySelectorAll: () => [],
  addEventListener() {},
};
const context = {
  window: windowObject,
  document: documentObject,
  fetch: fetchStub,
  console,
  TextDecoder,
  TextEncoder,
  AbortController,
  FormData,
  Blob,
  URL,
  CSS: { escape: String },
  DOMPurify: { sanitize: String },
  marked: { parse: String },
  setInterval,
  clearInterval,
  setTimeout,
  clearTimeout,
};
context.globalThis = context;
vm.createContext(context);

for (const filename of [
  "static/js/core/api-client.js",
  "static/js/core/sse.js",
  "static/js/core/timers.js",
  "static/js/features/test-suites.js",
  "static/js/features/requirements.js",
  "static/js/features/platform-record-store.js",
  "static/js/features/generation.js",
  "static/js/features/script-repair.js",
  "static/js/features/module-execution.js",
  "static/js/features/module-plan-generation.js",
  "static/js/features/admin.js",
  "static/js/features/projects.js",
  "static/js/features/plan-transfer.js",
  "static/js/features/project-settings.js",
  "static/js/features/setup-preparation.js",
  "static/js/features/agent-progress.js",
  "static/js/features/agent.js",
  "static/app.js",
]) {
  vm.runInContext(
    fs.readFileSync(path.join(appDir, filename), "utf8"),
    context,
    { filename },
  );
}

assert.strictEqual(
  vm.runInContext("getGenerationStatusInfo({}).label", context),
  "Not generated",
  "The empty generation status badge must use the semantic English translation.",
);

setTimeout(() => {
  for (const endpoint of [
    "/api/auth/me",
    "/api/projects",
    "/api/platform-records",
    "/api/modules",
  ]) {
    assert.ok(
      requests.some((url) => url.includes(endpoint)),
      `bootstrap did not request ${endpoint}`,
    );
  }
  process.stdout.write("assembled frontend bootstrap VM smoke: ok\n");
}, 50);
