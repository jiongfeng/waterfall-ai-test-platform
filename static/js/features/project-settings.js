function createProjectSettingsFeature(deps) {
  const {
    state,
    elements,
    DEFAULT_COVERAGE_PROFILE,
    PROJECT_SETTINGS_VIEW_TAB,
    fetch,
    TextDecoder,
    setupFeature,
    projects,
    jobs,
    requestJson,
    setNotice,
    setLoading,
    renderContent,
    parseSseBlock,
    getProjectRequestHeaders,
    isPlainObject,
    escapeHtml,
  } = deps;
  const { normalizeProject, loadProjects } = projects;
  const { isAnyScriptJobRunning } = jobs;

function normalizeTargetSystem(value) {
  const target = isPlainObject(value) ? value : {};
  return {
    base_url: typeof target.base_url === "string" ? target.base_url : "",
    login_url: typeof target.login_url === "string" && target.login_url ? target.login_url : "/login",
    username_env:
      typeof target.username_env === "string" && target.username_env
        ? target.username_env
        : "TARGET_SYSTEM_USERNAME",
    password_env:
      typeof target.password_env === "string" && target.password_env
        ? target.password_env
        : "TARGET_SYSTEM_PASSWORD",
    credentials_migration_required: target.credentials_migration_required === true,
  };
}

function projectSettingsField(id) {
  return elements.projectSettingsPanel.querySelector(`#${id}`);
}

function collectProjectSettingsForm() {
  return {
    target_system: {
      base_url: projectSettingsField("projectTargetBaseUrl").value.trim().replace(/\/+$/, ""),
      login_url: projectSettingsField("projectTargetLoginUrl").value.trim() || "/login",
      username_env: projectSettingsField("projectTargetUsernameEnv").value.trim(),
      password_env: projectSettingsField("projectTargetPasswordEnv").value.trim(),
    },
    database_baseline: isPlainObject(state.projectSettings.databaseBaseline)
      ? { ...state.projectSettings.databaseBaseline }
      : state.projectSettings.databaseBaseline,
    plan_generation: {
      default_coverage_profile: projectSettingsField("projectDefaultCoverageProfile").value || DEFAULT_COVERAGE_PROFILE,
    },
  };
}

function setProjectSettingsOutput(message, type = "") {
  state.projectSettings.output = message || "";
  const output = projectSettingsField("projectSettingsOutput");
  if (output) {
    output.textContent = state.projectSettings.output;
    output.dataset.status = type;
  }
}

function appendProjectSettingsOutput(message) {
  if (!message) {
    return;
  }
  const prefix = state.projectSettings.output && !state.projectSettings.output.endsWith("\n") ? "\n" : "";
  setProjectSettingsOutput(`${state.projectSettings.output || ""}${prefix}${message}\n`);
}

function setProjectSettingsBusy(key, value) {
  state.projectSettings[key] = value;
  const busy =
    state.projectSettings.isSaving ||
    state.projectSettings.isGeneratingSeed ||
    state.projectSettings.isTestingSeed ||
    isAnyScriptJobRunning();
  elements.projectSettingsPanel.querySelectorAll("button").forEach((button) => {
    button.disabled = busy;
  });
}

async function loadProjectSettings() {
  setNotice("");
  setLoading(true);
  try {
    const data = await requestJson("/api/project-settings");
    state.projectSettings.loaded = true;
    state.projectSettings.seedScriptPath = data.seed_script_path || "tests/seed/seed.spec.ts";
    state.projectSettings.targetSystem = normalizeTargetSystem(data.target_system);
    state.projectSettings.databaseBaseline = isPlainObject(data.database_baseline)
      ? { ...data.database_baseline }
      : data.database_baseline;
    state.projectSettings.planGeneration = isPlainObject(data.plan_generation)
      ? data.plan_generation
      : { default_coverage_profile: DEFAULT_COVERAGE_PROFILE };
    state.projectSettings.coverageProfiles = Array.isArray(data.coverage_profiles) ? data.coverage_profiles : [];
    state.project.current = normalizeProject(data.project) || state.project.current;
    await setupFeature.load({ silent: true });
    renderContent();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setLoading(false);
  }
}

async function saveProjectSettings(event) {
  event.preventDefault();
  let payload;
  try {
    payload = collectProjectSettingsForm();
  } catch (error) {
    setNotice(error.message, "error");
    return;
  }

  setProjectSettingsBusy("isSaving", true);
  try {
    const data = await requestJson("/api/project-settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    state.projectSettings.loaded = true;
    state.projectSettings.seedScriptPath = data.seed_script_path || state.projectSettings.seedScriptPath;
    state.projectSettings.targetSystem = normalizeTargetSystem(data.target_system);
    state.projectSettings.databaseBaseline = isPlainObject(data.database_baseline)
      ? { ...data.database_baseline }
      : data.database_baseline;
    state.projectSettings.planGeneration = isPlainObject(data.plan_generation)
      ? data.plan_generation
      : state.projectSettings.planGeneration;
    state.projectSettings.coverageProfiles = Array.isArray(data.coverage_profiles)
      ? data.coverage_profiles
      : state.projectSettings.coverageProfiles;
    state.generation.defaultsLoaded = false;
    state.project.current = normalizeProject(data.project) || state.project.current;
    await loadProjects();
    setNotice("项目配置已保存。", "success");
    renderContent();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    state.projectSettings.isSaving = false;
    renderProjectSettingsPanel();
  }
}

async function readProjectSettingsStream(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let result = { status: "running", ok: false, error: "" };

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let boundaryIndex = buffer.indexOf("\n\n");
    while (boundaryIndex >= 0) {
      const block = buffer.slice(0, boundaryIndex);
      buffer = buffer.slice(boundaryIndex + 2);
      const event = parseSseBlock(block);
      if (event) {
        result = handleProjectSettingsStreamEvent(event, result);
      }
      boundaryIndex = buffer.indexOf("\n\n");
    }
  }

  buffer += decoder.decode();
  const trailingEvent = parseSseBlock(buffer.trim());
  if (trailingEvent) {
    result = handleProjectSettingsStreamEvent(trailingEvent, result);
  }
  return result;
}

function handleProjectSettingsStreamEvent({ event, data }, previousResult) {
  if (event === "log") {
    appendProjectSettingsOutput(data.message || "");
    return previousResult;
  }
  if (event === "delta") {
    appendProjectSettingsOutput(data.text || "");
    return previousResult;
  }
  if (event === "status") {
    const status = data.status || previousResult.status;
    if (status === "failed" && data.error) {
      appendProjectSettingsOutput(data.error);
    }
    return { ...previousResult, status, error: data.error || previousResult.error };
  }
  if (event === "done") {
    if (data.ok === false) {
      appendProjectSettingsOutput(data.error || "Seed 生成失败。");
      return { ...previousResult, ok: false, status: data.status || "failed", error: data.error || "" };
    }
    appendProjectSettingsOutput("Seed 生成完成。");
    return { ...previousResult, ok: true, status: "succeeded" };
  }
  return previousResult;
}

async function generateProjectSeed() {
  if (state.projectSettings.isGeneratingSeed) {
    return;
  }
  setProjectSettingsBusy("isGeneratingSeed", true);
  setProjectSettingsOutput("正在生成 Seed...\n");
  try {
    const response = await fetch("/api/project-settings/seed/generate", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...getProjectRequestHeaders(),
      },
      body: JSON.stringify({}),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `请求失败: ${response.status}`);
    }
    const result = await readProjectSettingsStream(response);
    if (result.ok) {
      setNotice("Seed 脚本已生成。", "success");
      await loadProjects();
      return;
    }
    setNotice(result.error || "Seed 生成失败。", "error");
  } catch (error) {
    appendProjectSettingsOutput(error.message);
    setNotice(error.message, "error");
  } finally {
    state.projectSettings.isGeneratingSeed = false;
    renderProjectSettingsPanel();
  }
}

async function testProjectSeed() {
  if (state.projectSettings.isTestingSeed) {
    return;
  }
  setProjectSettingsBusy("isTestingSeed", true);
  setProjectSettingsOutput("正在执行 Seed 测试...\n");
  try {
    const data = await requestJson("/api/project-settings/seed/test", { method: "POST", body: JSON.stringify({}) });
    const lines = [
      `状态：${data.status || ""}`,
      `命令：${data.command || ""}`,
      data.output || "",
      data.error || "",
      data.report?.url ? `报告：${data.report.url}` : data.report_error || "",
      data.video?.url ? `视频：${data.video.url}` : data.video_error || "",
    ].filter(Boolean);
    setProjectSettingsOutput(lines.join("\n"));
    setNotice(data.status === "succeeded" ? "Seed 测试通过。" : data.error || "Seed 测试失败。", data.status === "succeeded" ? "success" : "error");
  } catch (error) {
    setProjectSettingsOutput(error.message);
    setNotice(error.message, "error");
  } finally {
    state.projectSettings.isTestingSeed = false;
    renderProjectSettingsPanel();
  }
}

function switchProjectSettingsViewTab(nextTab) {
  if (!Object.values(PROJECT_SETTINGS_VIEW_TAB).includes(nextTab)) {
    return;
  }
  state.projectSettings.activeTab = nextTab;
  renderProjectSettingsPanel();
  if (nextTab === PROJECT_SETTINGS_VIEW_TAB.SETUP && !state.projectSettings.setup.loaded) {
    setupFeature.load();
  }
}

function bindProjectSettingsTabs() {
  elements.projectSettingsPanel.querySelector("#projectSettingsBasicTab")?.addEventListener("click", () =>
    switchProjectSettingsViewTab(PROJECT_SETTINGS_VIEW_TAB.BASIC),
  );
  elements.projectSettingsPanel.querySelector("#projectSettingsSetupTab")?.addEventListener("click", () =>
    switchProjectSettingsViewTab(PROJECT_SETTINGS_VIEW_TAB.SETUP),
  );
}
function renderProjectSettingsPanel() {
  const settings = state.projectSettings;
  if (!settings.loaded) {
    elements.projectSettingsPanel.innerHTML = `
      <div class="project-settings-empty">
        <h3>正在读取项目配置</h3>
      </div>
    `;
    return;
  }

  const target = normalizeTargetSystem(settings.targetSystem);
  const planGeneration = isPlainObject(settings.planGeneration)
    ? settings.planGeneration
    : { default_coverage_profile: DEFAULT_COVERAGE_PROFILE };
  const coverageProfiles = settings.coverageProfiles.length
    ? settings.coverageProfiles
    : state.generation.coverageProfiles;
  const busy =
    settings.isSaving ||
    settings.isGeneratingSeed ||
    settings.isTestingSeed ||
    isAnyScriptJobRunning();
  const settingsTabs = `
    <div class="project-settings-top-tabs" role="tablist" aria-label="项目配置内容">
      <button class="content-tab ${settings.activeTab === PROJECT_SETTINGS_VIEW_TAB.BASIC ? "active" : ""}" id="projectSettingsBasicTab" type="button" role="tab" aria-selected="${settings.activeTab === PROJECT_SETTINGS_VIEW_TAB.BASIC}">基础配置</button>
      <button class="content-tab ${settings.activeTab === PROJECT_SETTINGS_VIEW_TAB.SETUP ? "active" : ""}" id="projectSettingsSetupTab" type="button" role="tab" aria-selected="${settings.activeTab === PROJECT_SETTINGS_VIEW_TAB.SETUP}">测试准备</button>
    </div>
  `;

  if (settings.activeTab === PROJECT_SETTINGS_VIEW_TAB.SETUP) {
    elements.projectSettingsPanel.innerHTML = `${settingsTabs}${setupFeature.renderMarkup()}`;
    bindProjectSettingsTabs();
    setupFeature.bindEvents();
    return;
  }

  elements.projectSettingsPanel.innerHTML = `
    ${settingsTabs}
    <div class="project-settings-basic-panel" role="tabpanel" aria-labelledby="projectSettingsBasicTab">
    <form class="project-settings-form" id="projectSettingsForm">
      <div class="project-settings-section">
        <div class="project-settings-header">
          <div>
            <h3>被测系统</h3>
            <p>${escapeHtml(state.project.current?.name || "")}</p>
          </div>
          <button class="primary-button" id="projectSettingsSave" type="submit" ${busy ? "disabled" : ""}>保存配置</button>
        </div>
        <div class="admin-form-grid project-settings-grid">
          <label class="form-field">
            <span>被测系统地址</span>
            <input id="projectTargetBaseUrl" type="url" value="${escapeHtml(target.base_url)}" placeholder="http://127.0.0.1:8080" />
          </label>
          <label class="form-field">
            <span>登录页地址</span>
            <input id="projectTargetLoginUrl" type="text" value="${escapeHtml(target.login_url)}" placeholder="/login" />
          </label>
          <label class="form-field">
            <span>登录用户名环境变量</span>
            <input id="projectTargetUsernameEnv" type="text" value="${escapeHtml(target.username_env)}" autocomplete="off" spellcheck="false" />
          </label>
          <label class="form-field">
            <span>登录密码环境变量</span>
            <input id="projectTargetPasswordEnv" type="text" value="${escapeHtml(target.password_env)}" autocomplete="off" spellcheck="false" />
          </label>
          <label class="form-field">
            <span>Seed 脚本</span>
            <input type="text" value="${escapeHtml(settings.seedScriptPath)}" readonly />
          </label>
        </div>
        ${
          target.credentials_migration_required
            ? '<p class="field-hint warning">检测到旧版明文凭据。保存本页后将只保留环境变量名称；请先在运行环境中配置对应变量。</p>'
            : '<p class="field-hint">平台只保存环境变量名称，不读取、回显或发送真实凭据给模型。</p>'
        }
        <div class="project-settings-actions">
          <button class="secondary-button" id="projectSeedGenerate" type="button" ${busy ? "disabled" : ""}>生成 Seed</button>
          <button class="secondary-button" id="projectSeedTest" type="button" ${busy ? "disabled" : ""}>测试 Seed</button>
        </div>
      </div>

      <div class="project-settings-section">
        <div class="project-settings-header">
          <div>
            <h3>计划生成</h3>
            <p>默认档位只决定生成弹窗最初加载的 Prompt 模板，用户仍可修改。</p>
          </div>
        </div>
        <div class="admin-form-grid project-settings-grid">
          <label class="form-field">
            <span>默认档位模板</span>
            <select id="projectDefaultCoverageProfile">
              ${coverageProfiles
                .map(
                  (item) =>
                    `<option value="${escapeHtml(item.key)}" ${item.key === planGeneration.default_coverage_profile ? "selected" : ""}>${escapeHtml(item.label)}</option>`,
                )
                .join("")}
            </select>
          </label>
        </div>
      </div>

    </form>
    <div class="job-output project-settings-output-wrap">
      <div class="job-status">执行输出</div>
      <pre id="projectSettingsOutput">${escapeHtml(settings.output || "")}</pre>
    </div>
    </div>
  `;

  bindProjectSettingsTabs();
  elements.projectSettingsPanel.querySelector("#projectSettingsForm")?.addEventListener("submit", saveProjectSettings);
  elements.projectSettingsPanel.querySelector("#projectSeedGenerate")?.addEventListener("click", generateProjectSeed);
  elements.projectSettingsPanel.querySelector("#projectSeedTest")?.addEventListener("click", testProjectSeed);
}

return {
  normalizeTargetSystem,
  projectSettingsField,
  collectProjectSettingsForm,
  setProjectSettingsOutput,
  appendProjectSettingsOutput,
  setProjectSettingsBusy,
  loadProjectSettings,
  saveProjectSettings,
  readProjectSettingsStream,
  handleProjectSettingsStreamEvent,
  generateProjectSeed,
  testProjectSeed,
  switchProjectSettingsViewTab,
  bindProjectSettingsTabs,
  renderProjectSettingsPanel,
};
}

window.createProjectSettingsFeature = createProjectSettingsFeature;
