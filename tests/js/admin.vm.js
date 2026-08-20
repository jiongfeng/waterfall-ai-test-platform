const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appDir = path.resolve(__dirname, "../..");
const context = {
  window: {
    WaterfallTranslations: {
      "zh-CN": {
        "auth.builtInAdminDisplayName": "管理员",
        "auth.builtInAdminRoleName": "管理员",
      },
    },
    WaterfallI18n: {
      source: (value) => value,
      getLocale: () => "en",
      t: (key) => ({
        "auth.builtInAdminDisplayName": "Administrator",
        "auth.builtInAdminRoleName": "Administrator",
        "auth.status.active": "Enable",
        "auth.status.disabled": "Disable",
        "auth.initialPassword": "Initial password",
        "auth.resetPassword": "Reset password",
        "auth.newPassword": "New password",
        "auth.confirmNewPassword": "Confirm new password",
        "auth.confirmReset": "Confirm reset",
        "auth.systemRoleTag": "System",
        "auth.permission.menu.plans": "Test plans",
        "auth.permission.menu.users": "User management",
      })[key] || key,
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
  assert.strictEqual(feature.getStatusText("active"), "Enable");
  assert.strictEqual(feature.getPermissionDisplayName({ code: "menu.users", name: "用户" }), "User management");
  assert.strictEqual(
    feature.getPermissionSummary(["menu.plans", "unknown"]),
    "Test plans, unknown",
  );
  assert.ok(feature.renderRoleCheckboxes([1]).includes("checked"));

  state.admin.userEditingId = "new";
  const newUserForm = feature.renderUserAdminForm();
  assert.ok(newUserForm.includes(">Enable</option>"));
  assert.ok(newUserForm.includes(">Disable</option>"));
  assert.ok(newUserForm.includes(">Initial password</span>"));
  assert.ok(!newUserForm.includes("初始密码"));

  state.admin.userEditingId = null;
  state.admin.passwordResetUserId = 2;
  const passwordResetForm = feature.renderPasswordResetForm();
  for (const label of ["Reset password", "New password", "Confirm new password", "Confirm reset"]) {
    assert.ok(passwordResetForm.includes(label), label);
  }
  assert.ok(!passwordResetForm.includes("重置密码"));

  state.admin.passwordResetUserId = null;
  state.admin.roleEditingId = "new";
  const newRoleForm = feature.renderRoleAdminForm();
  assert.ok(newRoleForm.includes(">Enable</option>"));
  assert.ok(newRoleForm.includes(">Disable</option>"));
  assert.ok(newRoleForm.includes(">Test plans</span>"));

  state.admin.roleEditingId = null;
  state.admin.roles.push({
    id: 99,
    code: "admin",
    name: "管理员",
    status: "active",
    is_system: true,
    permissions: ["menu.plans", "menu.users"],
  });
  feature.renderRoleAdminPanel();
  assert.ok(elements.roleAdminPanel.innerHTML.includes('<span class="system-tag">System</span>'));
  assert.ok(elements.roleAdminPanel.innerHTML.includes("Test plans, User management"));
  assert.ok(renderContentCalls >= 1);
  process.stdout.write("admin feature VM smoke: ok\n");
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
