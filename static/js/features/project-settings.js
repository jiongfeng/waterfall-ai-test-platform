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
    t = (key) => key,
    confirm = (message) => window.confirm(message),
    document: documentObject = window.document,
  } = deps;
  const {
    normalizeProject,
    loadProjects,
    renderProjectSelect = () => {},
  } = projects;
  const { isAnyScriptJobRunning } = jobs;

const SEED_MODE = Object.freeze({
  VISIT_ONLY: "visit_only",
  LOGIN: "login",
});

function normalizeSeedMode(value) {
  return Object.values(SEED_MODE).includes(value) ? value : "";
}

function seedModeLabel(mode) {
  if (mode === SEED_MODE.VISIT_ONLY) {
    return t("projectSettings.seedModeVisitOnly");
  }
  if (mode === SEED_MODE.LOGIN) {
    return t("projectSettings.seedModeLogin");
  }
  return t("projectSettings.seedModeUnknown");
}

function setSeedGenerateMenuOpen(open, { focusFirst = false, restoreFocus = false } = {}) {
  const toggle = elements.projectSettingsPanel.querySelector("#projectSeedGenerateToggle");
  const menu = elements.projectSettingsPanel.querySelector("#projectSeedGenerateMenu");
  if (!toggle || !menu) {
    return;
  }
  menu.hidden = !open;
  toggle.setAttribute("aria-expanded", String(open));
  if (open && focusFirst) {
    menu.querySelector("[data-seed-mode]:not(:disabled)")?.focus();
  } else if (!open && restoreFocus) {
    toggle.focus();
  }
}

documentObject?.addEventListener?.("pointerdown", (event) => {
  const wrap = elements.projectSettingsPanel.querySelector(".project-seed-generate-menu-wrap");
  const menu = elements.projectSettingsPanel.querySelector("#projectSeedGenerateMenu");
  if (menu && !menu.hidden && wrap && !wrap.contains(event.target)) {
    setSeedGenerateMenuOpen(false);
  }
});

documentObject?.addEventListener?.("keydown", (event) => {
  const menu = elements.projectSettingsPanel.querySelector("#projectSeedGenerateMenu");
  if (event.key === "Escape" && menu && !menu.hidden) {
    event.preventDefault();
    setSeedGenerateMenuOpen(false, { restoreFocus: true });
  }
});

function normalizeTargetSystem(value) {
  const target = isPlainObject(value) ? value : {};
  return {
    base_url: typeof target.base_url === "string" ? target.base_url : "",
    login_url: typeof target.login_url === "string" && target.login_url ? target.login_url : "/login",
    username: typeof target.username === "string" ? target.username : "",
    password: typeof target.password === "string" ? target.password : "",
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
      username: projectSettingsField("projectTargetUsername").value.trim(),
      password: projectSettingsField("projectTargetPassword").value,
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
  renderProjectSelect();
}

async function loadProjectSettings() {
  setNotice("");
  setLoading(true);
  try {
    const data = await requestJson("/api/project-settings");
    state.projectSettings.loaded = true;
    state.projectSettings.seedScriptPath = data.seed_script_path || "tests/seed/seed.spec.ts";
    state.projectSettings.seedMode = normalizeSeedMode(data.seed_mode);
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
    state.projectSettings.seedMode = normalizeSeedMode(data.seed_mode) || state.projectSettings.seedMode;
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
    setNotice(t("projectSettings.saved"), "success");
    renderContent();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setProjectSettingsBusy("isSaving", false);
    renderProjectSettingsPanel();
  }
}

async function readProjectSettingsStream(response, expectedProjectKey = "") {
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
      if (
        event &&
        (!expectedProjectKey || state.project.currentKey === expectedProjectKey)
      ) {
        result = handleProjectSettingsStreamEvent(event, result);
      }
      boundaryIndex = buffer.indexOf("\n\n");
    }
  }

  buffer += decoder.decode();
  const trailingEvent = parseSseBlock(buffer.trim());
  if (
    trailingEvent &&
    (!expectedProjectKey || state.project.currentKey === expectedProjectKey)
  ) {
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
      appendProjectSettingsOutput(data.error || t("projectSettings.seedGenerationFailed"));
      return { ...previousResult, ok: false, status: data.status || "failed", error: data.error || "" };
    }
    appendProjectSettingsOutput(t("projectSettings.seedGenerationComplete"));
    if (data.seed_mode_persistence === "failed") {
      appendProjectSettingsOutput(t("projectSettings.seedModePersistenceWarning"));
    }
    return {
      ...previousResult,
      ok: true,
      status: "succeeded",
      seedMode: normalizeSeedMode(data.seed_mode) || previousResult.seedMode || "",
    };
  }
  return previousResult;
}

async function generateProjectSeed(requestedMode = SEED_MODE.LOGIN) {
  const mode = normalizeSeedMode(requestedMode);
  if (!mode) {
    throw new Error(`Unsupported Seed mode: ${requestedMode}`);
  }
  if (state.projectSettings.isGeneratingSeed) {
    return;
  }
  const requestProjectKey = state.project.currentKey;
  const currentMode = normalizeSeedMode(state.projectSettings.seedMode);
  if (
    currentMode &&
    currentMode !== mode &&
    !confirm(
      t("projectSettings.confirmSeedModeOverwrite", {
        current: seedModeLabel(currentMode),
        next: seedModeLabel(mode),
      }),
    )
  ) {
    return;
  }
  setProjectSettingsBusy("isGeneratingSeed", true);
  setProjectSettingsOutput(`${t("projectSettings.generatingSeedMode", { mode: seedModeLabel(mode) })}\n`);
  try {
    const response = await fetch("/api/project-settings/seed/generate", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...getProjectRequestHeaders(),
      },
      body: JSON.stringify({ mode }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || t("error.requestFailed", { status: response.status }));
    }
    const result = await readProjectSettingsStream(
      response,
      requestProjectKey,
    );
    if (state.project.currentKey !== requestProjectKey) {
      return;
    }
    if (result.ok) {
      state.projectSettings.seedMode = normalizeSeedMode(result.seedMode) || mode;
      setNotice(t("projectSettings.seedGenerated"), "success");
      await loadProjects();
      return;
    }
    setNotice(result.error || t("projectSettings.seedGenerationFailed"), "error");
  } catch (error) {
    appendProjectSettingsOutput(error.message);
    setNotice(error.message, "error");
  } finally {
    setProjectSettingsBusy("isGeneratingSeed", false);
    renderProjectSettingsPanel();
  }
}

async function testProjectSeed() {
  if (state.projectSettings.isTestingSeed) {
    return;
  }
  setProjectSettingsBusy("isTestingSeed", true);
  setProjectSettingsOutput(`${t("projectSettings.testingSeed")}\n`);
  try {
    const data = await requestJson("/api/project-settings/seed/test", { method: "POST", body: JSON.stringify({}) });
    const lines = [
      t("projectSettings.outputStatus", { value: data.status || "" }),
      t("projectSettings.outputCommand", { value: data.command || "" }),
      data.output || "",
      data.error || "",
      data.report?.url ? t("projectSettings.outputReport", { value: data.report.url }) : data.report_error || "",
      data.video?.url ? t("projectSettings.outputVideo", { value: data.video.url }) : data.video_error || "",
    ].filter(Boolean);
    setProjectSettingsOutput(lines.join("\n"));
    setNotice(data.status === "succeeded" ? t("projectSettings.seedTestPassed") : data.error || t("projectSettings.seedTestFailed"), data.status === "succeeded" ? "success" : "error");
  } catch (error) {
    setProjectSettingsOutput(error.message);
    setNotice(error.message, "error");
  } finally {
    setProjectSettingsBusy("isTestingSeed", false);
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
        <h3>${t("projectSettings.loading")}</h3>
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
  const currentSeedMode = normalizeSeedMode(settings.seedMode);
  const settingsTabs = `
    <div class="project-settings-top-tabs" role="tablist" aria-label="${t("projectSettings.tabsLabel")}">
      <button class="content-tab ${settings.activeTab === PROJECT_SETTINGS_VIEW_TAB.BASIC ? "active" : ""}" id="projectSettingsBasicTab" type="button" role="tab" aria-selected="${settings.activeTab === PROJECT_SETTINGS_VIEW_TAB.BASIC}">${t("projectSettings.basic")}</button>
      <button class="content-tab ${settings.activeTab === PROJECT_SETTINGS_VIEW_TAB.SETUP ? "active" : ""}" id="projectSettingsSetupTab" type="button" role="tab" aria-selected="${settings.activeTab === PROJECT_SETTINGS_VIEW_TAB.SETUP}">${t("projectSettings.setup")}</button>
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
            <h3>${t("projectSettings.targetSystem")}</h3>
            <p>${escapeHtml(state.project.current?.name || "")}</p>
          </div>
          <button class="primary-button" id="projectSettingsSave" type="submit" ${busy ? "disabled" : ""}>${t("projectSettings.save")}</button>
        </div>
        <div class="admin-form-grid project-settings-grid">
          <label class="form-field">
            <span>${t("projectSettings.targetBaseUrl")}</span>
            <input id="projectTargetBaseUrl" type="url" value="${escapeHtml(target.base_url)}" placeholder="http://127.0.0.1:8080" required aria-required="true" />
          </label>
          <label class="form-field">
            <span>${t("projectSettings.loginUrl")}</span>
            <input id="projectTargetLoginUrl" type="text" value="${escapeHtml(target.login_url)}" placeholder="/login" />
          </label>
          <label class="form-field">
            <span>${t("projectSettings.loginUsername")}</span>
            <input id="projectTargetUsername" type="text" value="${escapeHtml(target.username)}" autocomplete="username" />
          </label>
          <label class="form-field">
            <span>${t("projectSettings.loginPassword")}</span>
            <input id="projectTargetPassword" type="password" value="${escapeHtml(target.password)}" autocomplete="current-password" />
          </label>
          <label class="form-field">
            <span>${t("projectSettings.seedScript")}</span>
            <input type="text" value="${escapeHtml(settings.seedScriptPath)}" readonly />
          </label>
          <div class="project-seed-current" aria-live="polite">
            <span>${t("projectSettings.currentSeedMode")}</span>
            <strong data-seed-mode="${escapeHtml(currentSeedMode || "unknown")}">${escapeHtml(seedModeLabel(currentSeedMode))}</strong>
          </div>
        </div>
        <div class="project-seed-guidance">
          <p>${t("projectSettings.visitSeedHint")}</p>
          <p>${t("projectSettings.loginSeedHint")}</p>
          <p class="project-seed-overwrite-hint">${escapeHtml(t("projectSettings.seedOverwriteHint", { path: settings.seedScriptPath }))}</p>
        </div>
        <div class="project-settings-actions">
          <div class="project-seed-generate-menu-wrap">
            <button class="secondary-button project-seed-generate-toggle" id="projectSeedGenerateToggle" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="projectSeedGenerateMenu" ${busy ? "disabled" : ""}>
              <span>${t("projectSettings.generateSeed")}</span>
              <span class="project-seed-generate-caret" aria-hidden="true">▾</span>
            </button>
            <div class="project-seed-generate-menu" id="projectSeedGenerateMenu" role="menu" hidden>
              <button type="button" role="menuitem" data-seed-mode="${SEED_MODE.VISIT_ONLY}" ${busy ? "disabled" : ""}>
                <strong>${t("projectSettings.generateVisitSeed")}</strong>
                <span>${t("projectSettings.generateVisitSeedDescription")}</span>
              </button>
              <button type="button" role="menuitem" data-seed-mode="${SEED_MODE.LOGIN}" ${busy ? "disabled" : ""}>
                <strong>${t("projectSettings.generateLoginSeed")}</strong>
                <span>${t("projectSettings.generateLoginSeedDescription")}</span>
              </button>
            </div>
          </div>
          <button class="secondary-button" id="projectSeedTest" type="button" ${busy ? "disabled" : ""}>${t("projectSettings.testSeed")}</button>
        </div>
      </div>

      <div class="project-settings-section">
        <div class="project-settings-header">
          <div>
            <h3>${t("projectSettings.planGeneration")}</h3>
            <p>${t("projectSettings.coverageHint")}</p>
          </div>
        </div>
        <div class="admin-form-grid project-settings-grid">
          <label class="form-field">
            <span>${t("projectSettings.defaultCoverage")}</span>
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
      <div class="job-status">${t("projectSettings.executionOutput")}</div>
      <pre id="projectSettingsOutput">${escapeHtml(settings.output || "")}</pre>
    </div>
    </div>
  `;

  bindProjectSettingsTabs();
  elements.projectSettingsPanel.querySelector("#projectSettingsForm")?.addEventListener("submit", saveProjectSettings);
  const seedGenerateToggle = elements.projectSettingsPanel.querySelector("#projectSeedGenerateToggle");
  const seedGenerateMenu = elements.projectSettingsPanel.querySelector("#projectSeedGenerateMenu");
  seedGenerateToggle?.addEventListener("click", () => {
    setSeedGenerateMenuOpen(Boolean(seedGenerateMenu?.hidden), { focusFirst: true });
  });
  seedGenerateToggle?.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSeedGenerateMenuOpen(true, { focusFirst: true });
    }
  });
  seedGenerateMenu?.addEventListener("keydown", (event) => {
    const buttons = Array.from(
      seedGenerateMenu.querySelectorAll("[data-seed-mode]:not(:disabled)"),
    );
    if (!buttons.length) {
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setSeedGenerateMenuOpen(false, { restoreFocus: true });
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      return;
    }
    event.preventDefault();
    const currentIndex = buttons.indexOf(documentObject?.activeElement);
    const nextIndex = event.key === "Home"
      ? 0
      : event.key === "End"
        ? buttons.length - 1
        : event.key === "ArrowUp"
          ? (currentIndex <= 0 ? buttons.length - 1 : currentIndex - 1)
          : (currentIndex + 1) % buttons.length;
    buttons[nextIndex].focus();
  });
  seedGenerateMenu?.querySelectorAll("[data-seed-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      setSeedGenerateMenuOpen(false);
      return generateProjectSeed(button.dataset.seedMode);
    });
  });
  elements.projectSettingsPanel.querySelector("#projectSeedTest")?.addEventListener("click", testProjectSeed);
}

return {
  normalizeTargetSystem,
  normalizeSeedMode,
  seedModeLabel,
  setSeedGenerateMenuOpen,
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
