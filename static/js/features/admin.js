function createAdminFeature(deps) {
  const {
    state,
    elements,
    SECTION,
    MENU_ITEMS,
    projects,
    requestJson,
    setNotice,
    setLoading,
    renderContent,
    escapeHtml,
  } = deps;
  const { renderProjectSelect } = projects;

function getMenuItem(section) {
  return MENU_ITEMS.find((item) => item.section === section) || null;
}

function hasMenu(section) {
  return state.auth.menus.includes(section);
}

function hasProjectSettingsPermission() {
  return state.auth.permissions.has("menu.projectSettings");
}

function getFirstAllowedSection() {
  return MENU_ITEMS.find((item) => hasMenu(item.section))?.section || null;
}

function isAdminSection(section = state.activeSection) {
  return section === SECTION.USERS || section === SECTION.ROLES;
}

function isProjectSettingsSection(section = state.activeSection) {
  return section === SECTION.PROJECT_SETTINGS;
}

function isAgentSection(section = state.activeSection) {
  return section === SECTION.AGENT;
}

function ensureAllowedActiveSection() {
  if (hasMenu(state.activeSection)) {
    return;
  }

  state.activeSection = getFirstAllowedSection() || SECTION.PLANS;
  state.isEditing = false;
}

async function loadAuthContext() {
  const data = await requestJson("/api/auth/me");
  state.auth.user = data.user || null;
  state.auth.isAdmin = Boolean(data.is_admin);
  state.auth.permissions = new Set(Array.isArray(data.permissions) ? data.permissions : []);
  state.auth.menus = Array.isArray(data.menus) ? data.menus : [];
  ensureAllowedActiveSection();
}

function renderNavigation() {
  const navElements = {
    [SECTION.REQUIREMENTS]: elements.requirementsNav,
    [SECTION.PLANS]: elements.plansNav,
    [SECTION.SCRIPTS]: elements.scriptsNav,
    [SECTION.TEST_SUITES]: elements.testSuitesNav,
    [SECTION.AGENT]: elements.agentNav,
    [SECTION.PROJECT_SETTINGS]: elements.projectSettingsNav,
    [SECTION.USERS]: elements.usersNav,
    [SECTION.ROLES]: elements.rolesNav,
  };
  MENU_ITEMS.forEach((item) => {
    const navElement = navElements[item.section];
    if (!navElement) {
      return;
    }
    navElement.classList.toggle("hidden", !hasMenu(item.section));
    navElement.classList.toggle("active", state.activeSection === item.section);
    navElement.title = item.title;
    navElement.setAttribute("aria-label", item.title);
  });

  const activeMenu = getMenuItem(state.activeSection);
  const adminMode = isAdminSection();
  const projectSettingsMode = isProjectSettingsSection();
  const agentMode = isAgentSection();
  elements.appShell.classList.toggle("suites-mode", state.activeSection === SECTION.TEST_SUITES);
  elements.appShell.classList.toggle("admin-mode", adminMode);
  elements.appShell.classList.toggle("settings-mode", projectSettingsMode);
  elements.appShell.classList.toggle("agent-mode", agentMode);
  elements.planCreateWrap.classList.toggle("hidden", state.activeSection !== SECTION.PLANS);
  elements.requirementUploadWrap.classList.toggle("hidden", state.activeSection !== SECTION.REQUIREMENTS);
  elements.requirementHeaderActions.classList.toggle("hidden", state.activeSection !== SECTION.REQUIREMENTS);
  elements.appTitle.textContent = activeMenu?.title || "测试资源";
  elements.currentUserName.textContent = state.auth.user?.display_name || state.auth.user?.username || "未登录";
  elements.moduleSearch.placeholder =
    state.activeSection === SECTION.REQUIREMENTS
      ? "搜索需求"
      : state.activeSection === SECTION.PLANS
      ? "搜索模块或计划"
      : state.activeSection === SECTION.SCRIPTS
        ? "搜索模块或用例"
        : state.activeSection === SECTION.TEST_SUITES
          ? "搜索测试集"
          : state.activeSection === SECTION.AGENT
            ? "Agent"
            : state.activeSection === SECTION.PROJECT_SETTINGS
              ? "项目配置"
              : "搜索";
  renderProjectSelect();
}
function formatAuthDate(timestamp) {
  const value = Number(timestamp);
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function getStatusText(status) {
  return status === "active" ? "启用" : "禁用";
}

function getRoleSummary(roles) {
  if (!Array.isArray(roles) || !roles.length) {
    return "-";
  }
  return roles.map((role) => role.name || role.code).join("、");
}

function getPermissionSummary(permissionCodes) {
  const names = new Map(state.admin.permissions.map((permission) => [permission.code, permission.name]));
  return (permissionCodes || []).map((code) => names.get(code) || code).join("、") || "-";
}

async function loadAdminPermissions() {
  if (state.admin.permissions.length) {
    return;
  }
  const data = await requestJson("/api/admin/permissions");
  state.admin.permissions = Array.isArray(data.permissions) ? data.permissions : [];
}

async function loadAdminRoles({ render = true } = {}) {
  setNotice("");
  setLoading(true);
  try {
    await loadAdminPermissions();
    const data = await requestJson("/api/admin/roles");
    state.admin.roles = Array.isArray(data.roles) ? data.roles : [];
    state.admin.rolesLoaded = true;
    if (render) {
      renderContent();
    }
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setLoading(false);
  }
}

async function loadAdminUsers() {
  setNotice("");
  setLoading(true);
  try {
    await loadAdminRoles({ render: false });
    const data = await requestJson("/api/admin/users");
    state.admin.users = Array.isArray(data.users) ? data.users : [];
    state.admin.usersLoaded = true;
    renderContent();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setLoading(false);
  }
}

function getEditingUser() {
  if (state.admin.userEditingId === "new") {
    return null;
  }
  return state.admin.users.find((user) => String(user.id) === String(state.admin.userEditingId)) || null;
}

function getEditingRole() {
  if (state.admin.roleEditingId === "new") {
    return null;
  }
  return state.admin.roles.find((role) => String(role.id) === String(state.admin.roleEditingId)) || null;
}

function renderRoleCheckboxes(selectedRoleIds) {
  const selected = new Set((selectedRoleIds || []).map((roleId) => String(roleId)));
  if (!state.admin.roles.length) {
    return '<div class="muted-cell">暂无角色，请先创建角色。</div>';
  }
  return state.admin.roles
    .map((role) => {
      const checked = selected.has(String(role.id)) ? "checked" : "";
      const disabled = role.status !== "active" ? "disabled" : "";
      return `
        <label class="admin-checkbox">
          <input type="checkbox" name="userRole" value="${role.id}" ${checked} ${disabled} />
          <span>${escapeHtml(role.name)}${role.status === "active" ? "" : "（已禁用）"}</span>
        </label>
      `;
    })
    .join("");
}

function renderPermissionCheckboxes(selectedPermissionCodes, disabled = false) {
  const selected = new Set(selectedPermissionCodes || []);
  return state.admin.permissions
    .map((permission) => {
      const checked = selected.has(permission.code) ? "checked" : "";
      return `
        <label class="admin-checkbox">
          <input type="checkbox" name="rolePermission" value="${escapeHtml(permission.code)}" ${checked} ${
            disabled ? "disabled" : ""
          } />
          <span>${escapeHtml(permission.name)}</span>
        </label>
      `;
    })
    .join("");
}

function renderUserAdminForm() {
  if (!state.admin.userEditingId) {
    return "";
  }

  const isNew = state.admin.userEditingId === "new";
  const user = getEditingUser();
  const selectedRoleIds = isNew ? [] : (user?.roles || []).map((role) => role.id);
  return `
    <form class="admin-form" id="userAdminForm">
      <div class="admin-form-header">
        <div>
          <h3>${isNew ? "新增用户" : "编辑用户"}</h3>
          <p>${isNew ? "创建账号并分配角色" : escapeHtml(user?.username || "")}</p>
        </div>
        <button class="secondary-button" id="cancelUserEdit" type="button">取消</button>
      </div>
      <div class="admin-form-grid">
        <label class="form-field">
          <span>用户名</span>
          <input id="adminUsername" type="text" value="${escapeHtml(user?.username || "")}" ${
            isNew ? "" : "disabled"
          } autocomplete="off" />
        </label>
        <label class="form-field">
          <span>显示名称</span>
          <input id="adminDisplayName" type="text" value="${escapeHtml(user?.display_name || "")}" autocomplete="off" />
        </label>
        <label class="form-field">
          <span>状态</span>
          <select id="adminUserStatus">
            <option value="active" ${user?.status !== "disabled" ? "selected" : ""}>启用</option>
            <option value="disabled" ${user?.status === "disabled" ? "selected" : ""}>禁用</option>
          </select>
        </label>
        ${
          isNew
            ? `<label class="form-field">
                <span>初始密码</span>
                <input id="adminPassword" type="password" autocomplete="new-password" />
              </label>`
            : ""
        }
      </div>
      <div class="form-field">
        <span>角色</span>
        <div class="admin-checkbox-grid">${renderRoleCheckboxes(selectedRoleIds)}</div>
      </div>
      <div class="admin-form-actions">
        <button class="primary-button" id="saveUserButton" type="submit">${isNew ? "创建" : "保存"}</button>
      </div>
    </form>
  `;
}

function renderPasswordResetForm() {
  const userId = state.admin.passwordResetUserId;
  if (!userId) {
    return "";
  }
  const user = state.admin.users.find(
    (item) => String(item.id) === String(userId),
  );
  return `
    <form class="admin-form" id="passwordResetForm">
      <div class="admin-form-header">
        <div>
          <h3>重置密码</h3>
          <p>${escapeHtml(user?.username || "")}</p>
        </div>
        <button class="secondary-button" id="cancelPasswordReset" type="button">取消</button>
      </div>
      <div class="admin-form-grid">
        <label class="form-field">
          <span>新密码</span>
          <input id="adminResetPassword" type="password" minlength="8" autocomplete="new-password" required />
        </label>
        <label class="form-field">
          <span>确认新密码</span>
          <input id="adminResetPasswordConfirm" type="password" minlength="8" autocomplete="new-password" required />
        </label>
      </div>
      <div class="admin-form-actions">
        <button class="primary-button" type="submit">确认重置</button>
      </div>
    </form>
  `;
}

function renderUserAdminPanel() {
  elements.userAdminPanel.innerHTML = `
    <div class="admin-toolbar">
      <div>
        <h3>用户列表</h3>
        <p>共 ${state.admin.users.length} 个用户</p>
      </div>
      <button class="primary-button" id="addUserButton" type="button">新增用户</button>
    </div>
    ${renderUserAdminForm()}
    ${renderPasswordResetForm()}
    <div class="module-script-table-wrap">
      <table class="module-script-table admin-table">
        <thead>
          <tr>
            <th>用户名</th>
            <th>显示名称</th>
            <th>状态</th>
            <th>角色</th>
            <th>最近登录</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          ${
            state.admin.users.length
              ? state.admin.users
                  .map(
                    (user) => `
                      <tr>
                        <td>${escapeHtml(user.username)}</td>
                        <td>${escapeHtml(user.display_name)}</td>
                        <td><span class="status-badge ${user.status === "active" ? "success" : "failed"}">${getStatusText(
                          user.status,
                        )}</span></td>
                        <td>${escapeHtml(getRoleSummary(user.roles))}</td>
                        <td>${formatAuthDate(user.last_login_at)}</td>
                        <td>
                          <div class="module-row-actions">
                            <button class="secondary-button" type="button" data-user-edit="${user.id}">编辑</button>
                            <button class="secondary-button" type="button" data-user-reset="${user.id}">重置密码</button>
                          </div>
                        </td>
                      </tr>
                    `,
                  )
                  .join("")
              : '<tr><td colspan="6">暂无用户。</td></tr>'
          }
        </tbody>
      </table>
    </div>
  `;

  elements.userAdminPanel.querySelector("#addUserButton")?.addEventListener("click", () => {
    state.admin.userEditingId = "new";
    renderContent();
  });
  elements.userAdminPanel.querySelector("#cancelUserEdit")?.addEventListener("click", () => {
    state.admin.userEditingId = null;
    renderContent();
  });
  elements.userAdminPanel.querySelector("#userAdminForm")?.addEventListener("submit", saveAdminUser);
  elements.userAdminPanel.querySelector("#passwordResetForm")?.addEventListener("submit", resetAdminUserPassword);
  elements.userAdminPanel.querySelector("#cancelPasswordReset")?.addEventListener("click", () => {
    state.admin.passwordResetUserId = null;
    renderContent();
  });
  elements.userAdminPanel.querySelectorAll("[data-user-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      state.admin.userEditingId = button.dataset.userEdit;
      renderContent();
    });
  });
  elements.userAdminPanel.querySelectorAll("[data-user-reset]").forEach((button) => {
    button.addEventListener("click", () => {
      state.admin.passwordResetUserId = button.dataset.userReset;
      renderContent();
    });
  });
}

async function saveAdminUser(event) {
  event.preventDefault();
  const isNew = state.admin.userEditingId === "new";
  const roleIds = Array.from(elements.userAdminPanel.querySelectorAll('input[name="userRole"]:checked')).map(
    (input) => Number(input.value),
  );
  const payload = {
    display_name: elements.userAdminPanel.querySelector("#adminDisplayName").value.trim(),
    status: elements.userAdminPanel.querySelector("#adminUserStatus").value,
    role_ids: roleIds,
  };
  if (isNew) {
    payload.username = elements.userAdminPanel.querySelector("#adminUsername").value.trim();
    payload.password = elements.userAdminPanel.querySelector("#adminPassword").value;
  }

  setLoading(true);
  try {
    await requestJson(isNew ? "/api/admin/users" : `/api/admin/users/${state.admin.userEditingId}`, {
      method: isNew ? "POST" : "PUT",
      body: JSON.stringify(payload),
    });
    state.admin.userEditingId = null;
    await loadAdminUsers();
    setNotice(isNew ? "用户已创建。" : "用户已保存。", "success");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setLoading(false);
  }
}

async function resetAdminUserPassword(event) {
  event.preventDefault();
  const userId = state.admin.passwordResetUserId;
  const passwordInput = elements.userAdminPanel.querySelector("#adminResetPassword");
  const confirmInput = elements.userAdminPanel.querySelector("#adminResetPasswordConfirm");
  const password = passwordInput.value;
  if (password !== confirmInput.value) {
    setNotice("两次输入的密码不一致。", "error");
    return;
  }
  setLoading(true);
  try {
    await requestJson(`/api/admin/users/${userId}/reset-password`, {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    passwordInput.value = "";
    confirmInput.value = "";
    state.admin.passwordResetUserId = null;
    setNotice("密码已重置。", "success");
    renderContent();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setLoading(false);
  }
}

function renderRoleAdminForm() {
  if (!state.admin.roleEditingId) {
    return "";
  }

  const isNew = state.admin.roleEditingId === "new";
  const role = getEditingRole();
  const isAdminRole = role?.code === "admin";
  return `
    <form class="admin-form" id="roleAdminForm">
      <div class="admin-form-header">
        <div>
          <h3>${isNew ? "新增角色" : "编辑角色"}</h3>
          <p>${isNew ? "创建角色并选择可访问菜单" : escapeHtml(role?.code || "")}</p>
        </div>
        <button class="secondary-button" id="cancelRoleEdit" type="button">取消</button>
      </div>
      <div class="admin-form-grid">
        <label class="form-field">
          <span>角色编码</span>
          <input id="adminRoleCode" type="text" value="${escapeHtml(role?.code || "")}" ${
            isNew ? "" : "disabled"
          } autocomplete="off" />
        </label>
        <label class="form-field">
          <span>角色名称</span>
          <input id="adminRoleName" type="text" value="${escapeHtml(role?.name || "")}" autocomplete="off" />
        </label>
        <label class="form-field">
          <span>状态</span>
          <select id="adminRoleStatus" ${isAdminRole ? "disabled" : ""}>
            <option value="active" ${role?.status !== "disabled" ? "selected" : ""}>启用</option>
            <option value="disabled" ${role?.status === "disabled" ? "selected" : ""}>禁用</option>
          </select>
        </label>
      </div>
      <label class="form-field">
        <span>描述</span>
        <input id="adminRoleDescription" type="text" value="${escapeHtml(role?.description || "")}" autocomplete="off" />
      </label>
      <div class="form-field">
        <span>菜单权限</span>
        <div class="admin-checkbox-grid">${renderPermissionCheckboxes(
          isAdminRole ? state.admin.permissions.map((permission) => permission.code) : role?.permissions || [],
          isAdminRole,
        )}</div>
      </div>
      <div class="admin-form-actions">
        <button class="primary-button" id="saveRoleButton" type="submit">${isNew ? "创建" : "保存"}</button>
      </div>
    </form>
  `;
}

function renderRoleAdminPanel() {
  elements.roleAdminPanel.innerHTML = `
    <div class="admin-toolbar">
      <div>
        <h3>角色列表</h3>
        <p>共 ${state.admin.roles.length} 个角色</p>
      </div>
      <button class="primary-button" id="addRoleButton" type="button">新增角色</button>
    </div>
    ${renderRoleAdminForm()}
    <div class="module-script-table-wrap">
      <table class="module-script-table admin-table">
        <thead>
          <tr>
            <th>角色编码</th>
            <th>角色名称</th>
            <th>状态</th>
            <th>菜单权限</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          ${
            state.admin.roles.length
              ? state.admin.roles
                  .map(
                    (role) => `
                      <tr>
                        <td>${escapeHtml(role.code)}</td>
                        <td>${escapeHtml(role.name)}${role.is_system ? '<span class="system-tag">系统</span>' : ""}</td>
                        <td><span class="status-badge ${role.status === "active" ? "success" : "failed"}">${getStatusText(
                          role.status,
                        )}</span></td>
                        <td>${escapeHtml(getPermissionSummary(role.permissions))}</td>
                        <td>
                          <div class="module-row-actions">
                            <button class="secondary-button" type="button" data-role-edit="${role.id}">编辑</button>
                          </div>
                        </td>
                      </tr>
                    `,
                  )
                  .join("")
              : '<tr><td colspan="5">暂无角色。</td></tr>'
          }
        </tbody>
      </table>
    </div>
  `;

  elements.roleAdminPanel.querySelector("#addRoleButton")?.addEventListener("click", () => {
    state.admin.roleEditingId = "new";
    renderContent();
  });
  elements.roleAdminPanel.querySelector("#cancelRoleEdit")?.addEventListener("click", () => {
    state.admin.roleEditingId = null;
    renderContent();
  });
  elements.roleAdminPanel.querySelector("#roleAdminForm")?.addEventListener("submit", saveAdminRole);
  elements.roleAdminPanel.querySelectorAll("[data-role-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      state.admin.roleEditingId = button.dataset.roleEdit;
      renderContent();
    });
  });
}

async function saveAdminRole(event) {
  event.preventDefault();
  const isNew = state.admin.roleEditingId === "new";
  const permissions = Array.from(elements.roleAdminPanel.querySelectorAll('input[name="rolePermission"]:checked')).map(
    (input) => input.value,
  );
  const statusSelect = elements.roleAdminPanel.querySelector("#adminRoleStatus");
  const payload = {
    name: elements.roleAdminPanel.querySelector("#adminRoleName").value.trim(),
    description: elements.roleAdminPanel.querySelector("#adminRoleDescription").value.trim(),
    status: statusSelect.disabled ? "active" : statusSelect.value,
    permissions,
  };
  if (isNew) {
    payload.code = elements.roleAdminPanel.querySelector("#adminRoleCode").value.trim();
  }

  setLoading(true);
  try {
    await requestJson(isNew ? "/api/admin/roles" : `/api/admin/roles/${state.admin.roleEditingId}`, {
      method: isNew ? "POST" : "PUT",
      body: JSON.stringify(payload),
    });
    state.admin.roleEditingId = null;
    await loadAdminRoles();
    setNotice(isNew ? "角色已创建。" : "角色已保存。", "success");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setLoading(false);
  }
}

return {
  getMenuItem,
  hasMenu,
  hasProjectSettingsPermission,
  getFirstAllowedSection,
  isAdminSection,
  isProjectSettingsSection,
  isAgentSection,
  ensureAllowedActiveSection,
  loadAuthContext,
  renderNavigation,
  formatAuthDate,
  getStatusText,
  getRoleSummary,
  getPermissionSummary,
  loadAdminPermissions,
  loadAdminRoles,
  loadAdminUsers,
  getEditingUser,
  getEditingRole,
  renderRoleCheckboxes,
  renderPermissionCheckboxes,
  renderUserAdminForm,
  renderPasswordResetForm,
  renderUserAdminPanel,
  saveAdminUser,
  resetAdminUserPassword,
  renderRoleAdminForm,
  renderRoleAdminPanel,
  saveAdminRole,
};
}

window.createAdminFeature = createAdminFeature;
