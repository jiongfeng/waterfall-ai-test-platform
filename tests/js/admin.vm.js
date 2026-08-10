const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");
const context = {
  window: {
    WaterfallTranslations: {
      "zh-CN": { "auth.builtInAdminDisplayName": "管理员" },
    },
    WaterfallI18n: {
      source: (value) => value,
      t: (key) => (key === "auth.builtInAdminDisplayName" ? "Administrator" : key),
    },
  },
};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(appDir, "static/js/features/admin.js"), "utf8"),
  context,
);

function element() {
  const classes = new Set();
  return {
    textContent: "",
    innerHTML: "",
    placeholder: "",
    title: "",
    classList: {
      toggle(name, force) {
        if (force) {
          classes.add(name);
        } else {
          classes.delete(name);
        }
      },
      contains: (name) => classes.has(name),
    },
    setAttribute(name, value) {
      this[name] = String(value);
    },
    querySelector: () => null,
    querySelectorAll: () => [],
  };
}

const state = {
  activeSection: "unknown",
  isEditing: true,
  auth: { user: null, permissions: new Set(), menus: [] },
  admin: {
    permissions: [],
    roles: [],
    users: [],
    rolesLoaded: false,
    usersLoaded: false,
    userEditingId: null,
    passwordResetUserId: null,
    roleEditingId: null,
  },
};
const sections = {
  REQUIREMENTS: "requirements",
  PLANS: "plans",
  SCRIPTS: "scripts",
  TEST_SUITES: "testSuites",
  AGENT: "agent",
  PROJECT_SETTINGS: "projectSettings",
  USERS: "users",
  ROLES: "roles",
};
const menuItems = [
  { section: sections.PLANS, title: "测试计划" },
  { section: sections.USERS, title: "用户管理" },
];
const elements = {
  requirementsNav: element(),
  plansNav: element(),
  scriptsNav: element(),
  testSuitesNav: element(),
  agentNav: element(),
  projectSettingsNav: element(),
  usersNav: element(),
  rolesNav: element(),
  appShell: element(),
  planCreateWrap: element(),
  requirementUploadWrap: element(),
  requirementHeaderActions: element(),
  appTitle: element(),
  currentUserName: element(),
  moduleSearch: element(),
  userAdminPanel: element(),
  roleAdminPanel: element(),
};
const requests = [];
let renderProjectCalls = 0;
let renderContentCalls = 0;
const feature = context.window.createAdminFeature({
  state,
  elements,
  SECTION: sections,
  MENU_ITEMS: menuItems,
  window: { prompt: () => null },
  projects: {
    renderProjectSelect: () => {
      renderProjectCalls += 1;
    },
  },
  requestJson: async (url) => {
    requests.push(url);
    if (url === "/api/auth/me") {
      return {
        user: { username: "alice", display_name: "Alice" },
        permissions: ["menu.projectSettings"],
        menus: ["plans", "users"],
      };
    }
    if (url === "/api/admin/permissions") {
      return {
        permissions: [
          { code: "menu.plans", name: "测试计划" },
          { code: "menu.users", name: "用户" },
        ],
      };
    }
    if (url === "/api/admin/roles") {
      return {
        roles: [
          {
            id: 1,
            code: "qa",
            name: "测试",
            status: "active",
            permissions: ["menu.plans"],
          },
        ],
      };
    }
    if (url === "/api/admin/users") {
      return {
        users: [
          {
            id: 2,
            username: "alice",
            display_name: "Alice",
            status: "active",
            roles: [{ id: 1, code: "qa", name: "测试" }],
          },
        ],
      };
    }
    throw new Error(`unexpected request ${url}`);
  },
  setNotice: () => {},
  setLoading: () => {},
  renderContent: () => {
    renderContentCalls += 1;
  },
  escapeHtml: String,
});

(async () => {
  await feature.loadAuthContext();
  assert.strictEqual(state.activeSection, "plans");
  assert.strictEqual(state.isEditing, false);
  assert.strictEqual(feature.hasMenu("users"), true);
  assert.strictEqual(feature.hasProjectSettingsPermission(), true);

  feature.renderNavigation();
  assert.strictEqual(elements.appTitle.textContent, "测试计划");
  assert.strictEqual(elements.currentUserName.textContent, "Alice");
  assert.strictEqual(elements.moduleSearch.placeholder, "搜索模块或计划");
  assert.strictEqual(renderProjectCalls, 1);

  state.auth.user = { username: "admin", display_name: "管理员" };
  state.auth.isAdmin = true;
  feature.renderNavigation();
  assert.strictEqual(elements.currentUserName.textContent, "Administrator");
  state.auth.user = { username: "alice", display_name: "管理员" };
  state.auth.isAdmin = false;
  feature.renderNavigation();
  assert.strictEqual(elements.currentUserName.textContent, "管理员");

  await feature.loadAdminUsers();
  assert.deepStrictEqual(requests.slice(-3), [
    "/api/admin/permissions",
    "/api/admin/roles",
    "/api/admin/users",
  ]);
  assert.strictEqual(state.admin.rolesLoaded, true);
  assert.strictEqual(state.admin.usersLoaded, true);
  assert.strictEqual(feature.getRoleSummary(state.admin.users[0].roles), "测试");
  assert.strictEqual(
    feature.getPermissionSummary(["menu.plans", "unknown"]),
    "测试计划、unknown",
  );
  assert.ok(feature.renderRoleCheckboxes([1]).includes("checked"));
  assert.ok(renderContentCalls >= 1);
  process.stdout.write("admin feature VM smoke: ok\n");
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
