const PROJECT_MANAGEMENT_ELEMENT_IDS = [
  "projectManageModal",
  "projectManageClose",
  "projectManageBack",
  "projectManageTitle",
  "projectManageDescription",
  "projectManageFeedback",
  "projectManageListView",
  "projectManageSearch",
  "projectManageCreate",
  "projectManageImport",
  "projectManageSummary",
  "projectManageTableBody",
  "projectManageEmpty",
  "projectCreateView",
  "projectImportView",
  "projectEditView",
  "projectCreateFooter",
  "projectImportFooter",
  "projectEditFooter",
  "projectCreateCancel",
  "projectCreateSubmit",
  "projectCreateWorkspaceHint",
  "newProjectKey",
  "newProjectName",
  "newProjectLanguage",
  "newProjectSpecsDir",
  "newProjectTestsDir",
  "newProjectDescription",
  "projectImportCancel",
  "projectImportSubmit",
  "projectImportWorkspaceHint",
  "projectImportFile",
  "importProjectKey",
  "importProjectName",
  "importProjectSpecsDir",
  "importProjectTestsDir",
  "importProjectDescription",
  "projectEditCancel",
  "projectEditSubmit",
  "editProjectKey",
  "editProjectName",
  "editProjectDescription",
  "projectDeleteModal",
  "projectDeleteClose",
  "projectDeleteCancel",
  "projectDeleteSubmit",
  "projectDeleteName",
  "projectDeleteKey",
  "projectDeleteConfirmation",
];

function getProjectManagementElements(document) {
  return Object.fromEntries(PROJECT_MANAGEMENT_ELEMENT_IDS.map((id) => [id, document.getElementById(id)]));
}

function createProjectsFeature(deps) {
  const {
    state,
    elements,
    CURRENT_PROJECT_STORAGE_KEY,
    PROJECT_SETTINGS_VIEW_TAB,
    REQUIREMENT_VIEW_TAB,
    TEST_SUITE_ALL_MODULE,
    document,
    window,
    fetch,
    FormData,
    admin,
    testSuites,
    jobs,
    getStoredProjectKey,
    writeStorageItem,
    isPlainObject,
    getProjectRequestHeaders,
    requestJson,
    readFetchError,
    getDownloadFilename,
    confirmDiscardEdit,
    hydratePlatformRecords,
    loadActiveSection,
    renderSideList,
    renderContent,
    setNotice,
    escapeHtml,
    persistViewState = () => {},
    PROJECT_SETTINGS_SECTION = "projectSettings",
  } = deps;
  const { hasProjectSettingsPermission, hasMenu } = admin;
  const { resetTestSuiteExecutionHistory } = testSuites;
  const { isAnyScriptJobRunning } = jobs;

function normalizeProject(project) {
  if (!project || typeof project !== "object") {
    return null;
  }
  const key = typeof project.project_key === "string" && project.project_key ? project.project_key : project.key;
  if (!key) {
    return null;
  }
  return {
    project_id: Number(project.project_id) || null,
    project_key: key,
    key,
    name: typeof project.name === "string" && project.name ? project.name : key,
    description: typeof project.description === "string" ? project.description : "",
    playwright_project_root: typeof project.playwright_project_root === "string" ? project.playwright_project_root : "",
    specs_dir: typeof project.specs_dir === "string" && project.specs_dir ? project.specs_dir : "specs",
    tests_dir: typeof project.tests_dir === "string" && project.tests_dir ? project.tests_dir : "tests",
    target_system: isPlainObject(project.target_system) ? project.target_system : null,
    database_baseline: isPlainObject(project.database_baseline) ? project.database_baseline : null,
    plan_generation: isPlainObject(project.plan_generation) ? project.plan_generation : null,
    language: project.language === "zh-CN" ? "zh-CN" : "en",
    status: typeof project.status === "string" ? project.status : "active",
    is_default: Boolean(project.is_default),
    is_system: Boolean(project.is_system),
  };
}

function hasConfiguredTargetSystem(project = state.project.current) {
  return Boolean(
    project &&
      isPlainObject(project.target_system) &&
      typeof project.target_system.base_url === "string" &&
      project.target_system.base_url.trim(),
  );
}

function routeUnconfiguredProjectToSettings() {
  const project = state.project.current;
  if (
    !project ||
    hasConfiguredTargetSystem(project) ||
    !(hasMenu?.(PROJECT_SETTINGS_SECTION) ?? hasProjectSettingsPermission())
  ) {
    return false;
  }

  state.isEditing = false;
  state.activeSection = PROJECT_SETTINGS_SECTION;
  state.projectSettings.activeTab = PROJECT_SETTINGS_VIEW_TAB.BASIC;
  persistViewState();
  return true;
}

function notifyUnconfiguredProject() {
  setNotice(
    projectMessage(
      "projectSettings.targetSystemRequired",
      {},
      "This project does not have a target system. Enter the Target system URL, save the settings, and then generate a Seed.",
    ),
    "error",
  );
  elements.projectSettingsPanel
    ?.querySelector?.("#projectTargetBaseUrl")
    ?.focus?.();
}

function resetProjectScopedState() {
  state.isEditing = false;
  state.requirements.items = [];
  state.requirements.selectedUid = null;
  state.requirements.current = null;
  state.requirements.markdown = "";
  state.requirements.html = "";
  state.requirements.modules = [];
  state.requirements.analysisLogs = "";
  state.requirements.analysisStatus = "";
  state.requirements.analysisError = "";
  state.requirements.analysisRunning = false;
  state.requirements.planGenerationRunning = false;
  state.requirements.generatingModuleUid = "";
  state.requirements.modulePlanLogs = {};
  state.requirements.activeTab = REQUIREMENT_VIEW_TAB.PREVIEW;
  state.requirements.detailModuleUid = "";
  state.requirements.bulkSelectionMode = false;
  state.requirements.selectedModuleUids.clear();
  state.requirements.bulkDeletingModules = false;
  state.requirements.isDeleting = false;
  state.requirements.deletingUid = "";
  state.requirements.planGenerationBatches = {};
  state.generation.source = "plans";
  state.generation.requirementUid = "";
  state.generation.requirementModuleUid = "";
  state.plans.modules = [];
  state.plans.expandedModules = new Set();
  state.plans.selectedModule = null;
  state.plans.selectedPlanFile = null;
  state.plans.currentMarkdown = "";
  state.plans.currentHtml = "";
  state.plans.filePath = "";
  state.plans.asset = null;
  state.plans.revisions = [];
  state.plans.relatedScripts = [];
  state.plans.generationRecords = {};
  state.plans.scriptGenerationRecords = {};
  state.plans.scriptGenerationBatches = {};
  state.plans.bulkSelectionMode = false;
  state.plans.selectedPlanFiles.clear();
  state.scripts.modules = [];
  state.scripts.expandedModules = new Set();
  state.scripts.selectedModule = null;
  state.scripts.selectedFile = null;
  state.scripts.currentContent = "";
  state.scripts.filePath = "";
  state.scripts.asset = null;
  state.scripts.revisions = [];
  state.scripts.sourcePlan = null;
  state.scripts.recentResults = [];
  state.scripts.selectedExecutionRunId = "";
  state.scripts.editBaselineRevisionId = null;
  state.scripts.preparationRunId = "";
  state.scripts.preparationModule = "";
  state.scripts.preparationRuns = {};
  state.scripts.runRecords = {};
  state.scripts.repairRecords = {};
  state.scripts.moduleExecutionRecords = {};
  state.scripts.moduleRepairBatches = {};
  state.scripts.bulkSelectionMode = false;
  state.scripts.selectedFiles.clear();
  state.testSuites.suites = [];
  state.testSuites.selectedSuiteId = null;
  state.testSuites.selectedModule = TEST_SUITE_ALL_MODULE;
  state.testSuites.executionRecords = {};
  resetTestSuiteExecutionHistory();
  state.testSuites.availableModules = [];
  state.testSuites.addModalModule = TEST_SUITE_ALL_MODULE;
  state.testSuites.selectedScriptKeys.clear();
  state.testSuiteExecution.progressModalVisible = false;
  state.testSuiteExecution.progressModalSuiteId = "";
  elements.testSuiteProgressModal?.classList.add("hidden");
  state.projectSettings.loaded = false;
  state.projectSettings.output = "";
  state.projectSettings.activeTab = PROJECT_SETTINGS_VIEW_TAB.BASIC;
  Object.assign(state.projectSettings.setup, {
    loaded: false,
    isLoading: false,
    isSaving: false,
    isRunning: false,
    error: "",
    notice: "",
    noticeType: "",
    scripts: [],
    bindings: [],
    runs: [],
    selectedScriptUid: "",
    selectedRunUid: "",
    scriptQuery: "",
    scriptStatusFilter: "all",
    scriptModalOpen: false,
    scriptDraft: null,
    scriptDraftSourceUid: "",
    draftBinding: null,
    draftEnvironmentRows: [],
    runDetailModalOpen: false,
    runDetailScriptUid: "",
  });
}

function renderProjectSelect() {
  elements.projectSelect.replaceChildren();
  state.project.projects.forEach((project) => {
    const option = document.createElement("option");
    option.value = project.project_key;
    option.textContent = project.name;
    option.title = project.playwright_project_root || project.name;
    window.WaterfallI18n?.markDynamic?.(option);
    elements.projectSelect.appendChild(option);
  });
  elements.projectSelect.value = state.project.currentKey || "";
  const projectBusy =
    isAnyScriptJobRunning() ||
    state.isEditing ||
    state.project.isCreating ||
    state.project.isExporting ||
    state.project.isImporting ||
    state.project.isUpdating ||
    state.project.isDeleting;
  const canManageProject = hasProjectSettingsPermission();
  elements.projectSelect.disabled = !state.project.projects.length || projectBusy;
  elements.manageProjectButton.classList.toggle("hidden", !canManageProject);
  elements.manageProjectButton.disabled = !state.project.currentKey || projectBusy;
  elements.projectManageClose.disabled = projectBusy;
  elements.projectManageBack.disabled = projectBusy;
  elements.projectCreateCancel.disabled = state.project.isCreating;
  elements.projectImportCancel.disabled = state.project.isImporting;
  elements.projectEditCancel.disabled = state.project.isUpdating;
  elements.projectDeleteClose.disabled = state.project.isDeleting;
  elements.projectDeleteCancel.disabled = state.project.isDeleting;
  if (!elements.projectManageModal.classList.contains("hidden")) {
    renderProjectManageList();
  }
}

async function loadProjects() {
  const data = await requestJson("/api/projects");
  state.project.projects = (data.projects || []).map(normalizeProject).filter(Boolean);
  state.project.workspaceRoot = typeof data.project_workspace_root === "string" ? data.project_workspace_root : "";
  state.project.defaultLanguage = data.default_project_language === "zh-CN" ? "zh-CN" : "en";
  state.project.defaultKey =
    normalizeProject(data.default_project)?.project_key ||
    state.project.projects.find((project) => project.is_default)?.project_key ||
    state.project.projects[0]?.project_key ||
    "";

  const storedKey = getStoredProjectKey();
  const currentCandidate = normalizeProject(data.current_project);
  const validKeys = new Set(state.project.projects.map((project) => project.project_key));
  const nextKey = validKeys.has(storedKey)
    ? storedKey
    : validKeys.has(currentCandidate?.project_key)
      ? currentCandidate.project_key
      : state.project.defaultKey;

  state.project.currentKey = nextKey;
  state.project.current = state.project.projects.find((project) => project.project_key === nextKey) || null;
  if (nextKey) {
    writeStorageItem(CURRENT_PROJECT_STORAGE_KEY, nextKey);
  }
  renderProjectSelect();
}

function projectText(value) {
  return window.WaterfallI18n?.source?.(value) || value;
}

function projectMessage(key, params, fallback) {
  const translated = window.WaterfallI18n?.t?.(key, params);
  return translated && translated !== key ? translated : fallback;
}

function isProjectOperationBusy() {
  return Boolean(
    isAnyScriptJobRunning() ||
      state.isEditing ||
      state.project.isCreating ||
      state.project.isExporting ||
      state.project.isImporting ||
      state.project.isUpdating ||
      state.project.isDeleting,
  );
}

function setProjectManageFeedback(message = "", type = "") {
  elements.projectManageFeedback.textContent = message;
  elements.projectManageFeedback.classList.toggle("hidden", !message);
  elements.projectManageFeedback.classList.toggle("error", type === "error");
  window.WaterfallI18n?.localizeDom?.(elements.projectManageFeedback);
}

function projectDeleteDisabledReason(project) {
  if (project.is_system) return "系统项目由配置文件托管，不能删除。";
  if (project.is_default) return "默认项目不能删除。";
  if (project.project_key === state.project.currentKey) return "当前项目不能删除，请先切换到其他项目。";
  if (state.project.projects.length <= 1) return "至少需要保留一个有效项目。";
  if (isProjectOperationBusy()) return "当前有任务或项目操作正在进行。";
  return "";
}

function renderProjectManageList() {
  const query = (elements.projectManageSearch.value || "").trim().toLocaleLowerCase();
  const projects = state.project.projects.filter((project) =>
    !query || `${project.name}\n${project.project_key}`.toLocaleLowerCase().includes(query),
  );
  elements.projectManageSummary.textContent = projectMessage(
    "projectManagement.summary",
    { shown: projects.length, total: state.project.projects.length },
    `显示 ${projects.length} 个，共 ${state.project.projects.length} 个项目`,
  );
  elements.projectManageEmpty.classList.toggle("hidden", Boolean(projects.length));
  elements.projectManageTableBody.innerHTML = projects
    .map((project) => {
      const badges = [
        project.project_key === state.project.currentKey ? '<span class="status-badge running">当前</span>' : "",
        project.is_default ? '<span class="status-badge success">默认</span>' : "",
        project.is_system ? '<span class="status-badge">系统项目</span>' : "",
      ].join("");
      const deleteReason = projectDeleteDisabledReason(project);
      const editReason = project.is_system ? "系统项目由配置文件托管，不能修改。" : "";
      const operationsDisabled = isProjectOperationBusy();
      return `
        <tr>
          <td>
            <div class="project-name-cell">
              <div class="project-name-line">
                <strong data-i18n-dynamic>${escapeHtml(project.name)}</strong>${badges}
              </div>
              <small data-i18n-dynamic title="${escapeHtml(project.description || "")}">${escapeHtml(
                project.description || projectText("暂无描述"),
              )}</small>
            </div>
          </td>
          <td><code class="project-key-code" data-i18n-dynamic>${escapeHtml(project.project_key)}</code></td>
          <td>
            <div class="project-row-actions">
              <button class="secondary-button" type="button" data-project-action="edit" data-project-key="${escapeHtml(
                project.project_key,
              )}" ${editReason || operationsDisabled ? "disabled" : ""} title="${escapeHtml(editReason)}">修改</button>
              <button class="secondary-button" type="button" data-project-action="export" data-project-key="${escapeHtml(
                project.project_key,
              )}" ${operationsDisabled ? "disabled" : ""}>导出</button>
              <button class="secondary-button danger-button" type="button" data-project-action="delete" data-project-key="${escapeHtml(
                project.project_key,
              )}" ${deleteReason ? "disabled" : ""} title="${escapeHtml(deleteReason)}">删除</button>
            </div>
          </td>
        </tr>`;
    })
    .join("");
  elements.projectManageCreate.disabled = isProjectOperationBusy();
  elements.projectManageImport.disabled = isProjectOperationBusy();
  window.WaterfallI18n?.localizeDom?.(elements.projectManageListView);
}

function setProjectManageView(view, project = null) {
  state.project.manageView = view;
  const viewConfig = {
    list: {
      title: "管理项目",
      description: "集中管理项目名称、归档文件和项目数据。",
      focus: elements.projectManageSearch,
    },
    create: {
      title: "新增项目",
      description: "平台会自动创建 Playwright 工作区，计划、脚本、测试集和执行记录会按项目隔离。",
      focus: elements.newProjectKey,
    },
    import: {
      title: "导入项目",
      description: "导入会新建项目，只迁移模块、测试计划、测试脚本和测试集。",
      focus: elements.projectImportFile,
    },
    edit: {
      title: "修改项目",
      description: "修改项目显示名称和描述，项目标识及工作区目录保持不变。",
      focus: elements.editProjectName,
    },
  }[view];
  if (!viewConfig) return;

  elements.projectManageTitle.textContent = projectText(viewConfig.title);
  elements.projectManageDescription.textContent = projectText(viewConfig.description);
  elements.projectManageBack.classList.toggle("hidden", view === "list");
  for (const [name, element] of Object.entries({
    list: elements.projectManageListView,
    create: elements.projectCreateView,
    import: elements.projectImportView,
    edit: elements.projectEditView,
  })) {
    element.classList.toggle("hidden", name !== view);
  }
  elements.projectCreateFooter.classList.toggle("hidden", view !== "create");
  elements.projectImportFooter.classList.toggle("hidden", view !== "import");
  elements.projectEditFooter.classList.toggle("hidden", view !== "edit");
  if (view === "list") renderProjectManageList();
  if (view === "edit" && project) {
    state.project.editingKey = project.project_key;
    elements.editProjectKey.value = project.project_key;
    elements.editProjectName.value = project.name;
    elements.editProjectDescription.value = project.description || "";
    elements.editProjectName.setCustomValidity("");
  }
  window.WaterfallI18n?.localizeDom?.(elements.projectManageModal);
  window.requestAnimationFrame(() => viewConfig.focus?.focus());
}

function openProjectManageModal() {
  if (!hasProjectSettingsPermission() || isProjectOperationBusy()) return;
  setNotice("");
  setProjectManageFeedback("");
  elements.projectManageSearch.value = "";
  elements.projectManageModal.classList.remove("hidden");
  setProjectManageView("list");
}

function closeProjectManageModal() {
  if (isProjectOperationBusy()) return;
  elements.projectManageModal.classList.add("hidden");
  setProjectManageFeedback("");
  elements.manageProjectButton.focus();
}

function returnToProjectManageList() {
  if (state.project.isCreating || state.project.isImporting || state.project.isUpdating) return;
  clearProjectCreateValidity();
  clearProjectImportValidity();
  setProjectManageFeedback("");
  setProjectManageView("list");
}

function openProjectEditView(projectKey) {
  const project = state.project.projects.find((item) => item.project_key === projectKey);
  if (!project || project.is_system || isProjectOperationBusy()) return;
  setProjectManageFeedback("");
  setProjectManageView("edit", project);
}

function clearProjectCreateValidity() {
  [
    elements.newProjectKey,
    elements.newProjectName,
    elements.newProjectLanguage,
    elements.newProjectSpecsDir,
    elements.newProjectTestsDir,
    elements.newProjectDescription,
  ].forEach((input) => input?.setCustomValidity(""));
}

function openProjectCreateModal() {
  if (!hasProjectSettingsPermission() || isProjectOperationBusy()) return;
  setNotice("");
  setProjectManageFeedback("");
  clearProjectCreateValidity();
  elements.newProjectKey.value = "";
  elements.newProjectName.value = "";
  elements.newProjectLanguage.value = state.project.defaultLanguage;
  elements.newProjectSpecsDir.value = "specs";
  elements.newProjectTestsDir.value = "tests";
  elements.newProjectDescription.value = "";
  elements.projectCreateWorkspaceHint.textContent = state.project.workspaceRoot
    ? `项目目录将自动创建为：${state.project.workspaceRoot}/<项目标识>`
    : "请先在 config.json 配置 project_workspace_root。";
  elements.projectManageModal.classList.remove("hidden");
  setProjectManageView("create");
}

function closeProjectCreateModal() {
  if (state.project.isCreating) return;
  clearProjectCreateValidity();
  setProjectManageView("list");
}

async function submitProjectCreate() {
  clearProjectCreateValidity();

  const payload = {
    project_key: elements.newProjectKey.value.trim(),
    name: elements.newProjectName.value.trim(),
    language: elements.newProjectLanguage.value,
    specs_dir: elements.newProjectSpecsDir.value.trim() || "specs",
    tests_dir: elements.newProjectTestsDir.value.trim() || "tests",
    description: elements.newProjectDescription.value.trim(),
  };

  if (!payload.project_key) {
    elements.newProjectKey.setCustomValidity("请输入项目标识。");
    elements.newProjectKey.reportValidity();
    return;
  }
  if (!payload.name) {
    elements.newProjectName.setCustomValidity("请输入项目名称。");
    elements.newProjectName.reportValidity();
    return;
  }

  state.project.isCreating = true;
  elements.projectCreateSubmit.disabled = true;
  renderProjectSelect();
  try {
    const data = await requestJson("/api/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const project = normalizeProject(data.project);
    await loadProjects();
    if (project?.project_key && project.project_key !== state.project.currentKey) {
      await switchProject(project.project_key);
    }
    elements.projectManageModal.classList.add("hidden");
    setNotice("项目创建成功，已初始化目录并切换到新项目。", "success");
  } catch (error) {
    elements.newProjectKey.setCustomValidity(error.message || "项目创建失败。");
    elements.newProjectKey.reportValidity();
  } finally {
    state.project.isCreating = false;
    elements.projectCreateSubmit.disabled = false;
    renderProjectSelect();
  }
}

function clearProjectImportValidity() {
  [
    elements.projectImportFile,
    elements.importProjectKey,
    elements.importProjectName,
    elements.importProjectSpecsDir,
    elements.importProjectTestsDir,
    elements.importProjectDescription,
  ].forEach((input) => input?.setCustomValidity(""));
}

function openProjectImportModal() {
  if (!state.project.currentKey || !hasProjectSettingsPermission() || isProjectOperationBusy()) {
    return;
  }
  setNotice("");
  setProjectManageFeedback("");
  clearProjectImportValidity();
  elements.projectImportFile.value = "";
  elements.importProjectKey.value = "";
  elements.importProjectName.value = "";
  elements.importProjectSpecsDir.value = "";
  elements.importProjectTestsDir.value = "";
  elements.importProjectDescription.value = "";
  elements.projectImportWorkspaceHint.textContent = state.project.workspaceRoot
    ? `导入项目目录将创建在：${state.project.workspaceRoot}/<项目标识>`
    : "请先在 config.json 配置 project_workspace_root。";
  elements.projectManageModal.classList.remove("hidden");
  setProjectManageView("import");
}

function closeProjectImportModal() {
  if (state.project.isImporting) {
    return;
  }
  clearProjectImportValidity();
  setProjectManageView("list");
}

async function exportCurrentProject(projectKey = state.project.currentKey) {
  if (!projectKey || state.project.isExporting || isAnyScriptJobRunning() || state.isEditing) {
    return;
  }
  state.project.isExporting = true;
  renderProjectSelect();
  try {
    const response = await fetch("/api/projects/export", {
      headers: getProjectRequestHeaders({ "X-Project-Key": projectKey }),
    });
    if (!response.ok) {
      const message = await readFetchError(response, `导出项目失败：${response.status}`);
      if (response.status === 401) {
        window.location.href = "/login";
      }
      throw new Error(message);
    }
    const blob = await response.blob();
    const fallbackName = `playwright-project-${projectKey || "project"}.zip`;
    const filename = getDownloadFilename(response, fallbackName);
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
    if (elements.projectManageModal.classList.contains("hidden")) {
      setNotice("项目导出文件已开始下载。", "success");
    } else {
      setProjectManageFeedback("项目导出文件已开始下载。", "success");
    }
  } catch (error) {
    if (elements.projectManageModal.classList.contains("hidden")) {
      setNotice(error.message || "项目导出失败。", "error");
    } else {
      setProjectManageFeedback(error.message || "项目导出失败。", "error");
    }
  } finally {
    state.project.isExporting = false;
    renderProjectSelect();
  }
}

async function submitProjectImport() {
  clearProjectImportValidity();
  const file = elements.projectImportFile.files?.[0];
  if (!file) {
    elements.projectImportFile.setCustomValidity("请选择项目导入 zip 文件。");
    elements.projectImportFile.reportValidity();
    return;
  }

  const formData = new FormData();
  formData.append("file", file);
  [
    ["project_key", elements.importProjectKey.value.trim()],
    ["name", elements.importProjectName.value.trim()],
    ["specs_dir", elements.importProjectSpecsDir.value.trim()],
    ["tests_dir", elements.importProjectTestsDir.value.trim()],
    ["description", elements.importProjectDescription.value.trim()],
  ].forEach(([key, value]) => {
    if (value) {
      formData.append(key, value);
    }
  });

  state.project.isImporting = true;
  elements.projectImportSubmit.disabled = true;
  elements.projectImportSubmit.textContent = "导入中";
  renderProjectSelect();
  try {
    const response = await fetch("/api/projects/import", {
      method: "POST",
      headers: getProjectRequestHeaders(),
      body: formData,
    });
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = { error: `接口返回不是 JSON: ${error}` };
    }
    if (!response.ok) {
      if (response.status === 401) {
        window.location.href = data.redirect || "/login";
      }
      throw new Error(data.error || `导入项目失败：${response.status}`);
    }
    const project = normalizeProject(data.project);
    state.project.isImporting = false;
    elements.projectImportSubmit.disabled = false;
    elements.projectImportSubmit.textContent = "导入并切换";
    await loadProjects();
    if (project?.project_key && project.project_key !== state.project.currentKey) {
      await switchProject(project.project_key);
    }
    elements.projectManageModal.classList.add("hidden");
    const counts = data.counts || {};
    setNotice(
      `项目导入成功：${counts.modules || 0} 个模块，${counts.plans || 0} 个计划，${counts.scripts || 0} 个脚本，${counts.test_suites || 0} 个测试集。`,
      "success",
    );
  } catch (error) {
    const message = error.message || "项目导入失败。";
    if (message.includes("项目标识") || message.includes("已存在")) {
      elements.importProjectKey.setCustomValidity(message);
      elements.importProjectKey.reportValidity();
    }
    setNotice(message, "error");
  } finally {
    state.project.isImporting = false;
    elements.projectImportSubmit.disabled = false;
    elements.projectImportSubmit.textContent = "导入并切换";
    renderProjectSelect();
  }
}

async function submitProjectEdit() {
  const projectKey = state.project.editingKey;
  const name = elements.editProjectName.value.trim();
  const description = elements.editProjectDescription.value.trim();
  elements.editProjectName.setCustomValidity("");
  if (!projectKey || state.project.isUpdating) return;
  if (!name) {
    elements.editProjectName.setCustomValidity("请输入项目名称。");
    elements.editProjectName.reportValidity();
    return;
  }

  state.project.isUpdating = true;
  elements.projectEditSubmit.disabled = true;
  renderProjectSelect();
  try {
    await requestJson(`/api/projects/${encodeURIComponent(projectKey)}`, {
      method: "PATCH",
      body: JSON.stringify({ name, description }),
    });
    await loadProjects();
    setProjectManageView("list");
    setProjectManageFeedback("项目修改成功。", "success");
  } catch (error) {
    const message = error.message || "项目修改失败。";
    elements.editProjectName.setCustomValidity(message);
    elements.editProjectName.reportValidity();
    setProjectManageFeedback(message, "error");
  } finally {
    state.project.isUpdating = false;
    elements.projectEditSubmit.disabled = false;
    renderProjectSelect();
  }
}

function updateProjectDeleteSubmitState() {
  const project = state.project.projects.find(
    (item) => item.project_key === state.project.deletingKey,
  );
  elements.projectDeleteSubmit.disabled =
    !project ||
    state.project.isDeleting ||
    elements.projectDeleteConfirmation.value.trim() !== project.name;
}

function openProjectDeleteModal(projectKey) {
  const project = state.project.projects.find((item) => item.project_key === projectKey);
  if (!project || projectDeleteDisabledReason(project)) return;
  state.project.deletingKey = project.project_key;
  elements.projectDeleteName.textContent = project.name;
  elements.projectDeleteKey.textContent = project.project_key;
  elements.projectDeleteConfirmation.value = "";
  elements.projectDeleteConfirmation.setCustomValidity("");
  updateProjectDeleteSubmitState();
  elements.projectDeleteModal.classList.remove("hidden");
  window.WaterfallI18n?.markDynamic?.(elements.projectDeleteName);
  window.WaterfallI18n?.markDynamic?.(elements.projectDeleteKey);
  window.WaterfallI18n?.localizeDom?.(elements.projectDeleteModal);
  window.requestAnimationFrame(() => elements.projectDeleteConfirmation.focus());
}

function closeProjectDeleteModal() {
  if (state.project.isDeleting) return;
  const projectKey = state.project.deletingKey;
  elements.projectDeleteModal.classList.add("hidden");
  elements.projectDeleteConfirmation.value = "";
  state.project.deletingKey = "";
  elements.projectManageTableBody
    .querySelector?.(`[data-project-action="delete"][data-project-key="${projectKey}"]`)
    ?.focus();
}

async function submitProjectDelete() {
  const project = state.project.projects.find(
    (item) => item.project_key === state.project.deletingKey,
  );
  if (!project || state.project.isDeleting) return;
  if (elements.projectDeleteConfirmation.value.trim() !== project.name) {
    elements.projectDeleteConfirmation.setCustomValidity("输入的项目名称不匹配。");
    elements.projectDeleteConfirmation.reportValidity();
    return;
  }

  state.project.isDeleting = true;
  elements.projectDeleteSubmit.disabled = true;
  renderProjectSelect();
  try {
    await requestJson(`/api/projects/${encodeURIComponent(project.project_key)}`, {
      method: "DELETE",
      body: JSON.stringify({ confirmation_name: elements.projectDeleteConfirmation.value.trim() }),
    });
    elements.projectDeleteModal.classList.add("hidden");
    state.project.deletingKey = "";
    await loadProjects();
    setProjectManageView("list");
    setProjectManageFeedback("项目及其数据库数据和本地工作区目录已永久删除。", "success");
  } catch (error) {
    const message = error.message || "项目删除失败。";
    elements.projectDeleteConfirmation.setCustomValidity(message);
    elements.projectDeleteConfirmation.reportValidity();
    setProjectManageFeedback(message, "error");
  } finally {
    state.project.isDeleting = false;
    updateProjectDeleteSubmitState();
    renderProjectSelect();
  }
}

function handleProjectManageTableClick(event) {
  const button = event.target.closest("[data-project-action]");
  if (!button || button.disabled) return;
  const projectKey = button.dataset.projectKey || "";
  if (button.dataset.projectAction === "edit") {
    openProjectEditView(projectKey);
  } else if (button.dataset.projectAction === "export") {
    exportCurrentProject(projectKey);
  } else if (button.dataset.projectAction === "delete") {
    openProjectDeleteModal(projectKey);
  }
}

function trapProjectModalFocus(event, modal) {
  const focusable = Array.from(
    modal.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((element) => !element.closest(".hidden"));
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && (document.activeElement === first || !modal.contains(document.activeElement))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || !modal.contains(document.activeElement))) {
    event.preventDefault();
    first.focus();
  }
}

function handleProjectModalKeydown(event) {
  const deleteOpen = !elements.projectDeleteModal.classList.contains("hidden");
  const manageOpen = !elements.projectManageModal.classList.contains("hidden");
  if (!deleteOpen && !manageOpen) return;
  if (event.key === "Tab") {
    trapProjectModalFocus(event, deleteOpen ? elements.projectDeleteModal : elements.projectManageModal);
    event.stopImmediatePropagation();
  } else if (event.key === "Escape") {
    if (deleteOpen) {
      closeProjectDeleteModal();
    } else if (state.project.manageView === "list") {
      closeProjectManageModal();
    } else {
      returnToProjectManageList();
    }
    event.stopImmediatePropagation();
  }
}

function bindProjectManagementEvents() {
  elements.projectSelect.addEventListener("change", () => switchProject(elements.projectSelect.value));
  elements.manageProjectButton.addEventListener("click", openProjectManageModal);
  elements.projectManageClose.addEventListener("click", closeProjectManageModal);
  elements.projectManageBack.addEventListener("click", returnToProjectManageList);
  elements.projectManageCreate.addEventListener("click", openProjectCreateModal);
  elements.projectManageImport.addEventListener("click", openProjectImportModal);
  elements.projectManageSearch.addEventListener("input", renderProjectManageList);
  elements.projectManageTableBody.addEventListener("click", handleProjectManageTableClick);
  elements.projectCreateCancel.addEventListener("click", closeProjectCreateModal);
  elements.projectCreateSubmit.addEventListener("click", submitProjectCreate);
  elements.projectImportCancel.addEventListener("click", closeProjectImportModal);
  elements.projectImportSubmit.addEventListener("click", submitProjectImport);
  elements.projectEditCancel.addEventListener("click", returnToProjectManageList);
  elements.projectEditSubmit.addEventListener("click", submitProjectEdit);
  elements.projectDeleteClose.addEventListener("click", closeProjectDeleteModal);
  elements.projectDeleteCancel.addEventListener("click", closeProjectDeleteModal);
  elements.projectDeleteSubmit.addEventListener("click", submitProjectDelete);
  elements.projectDeleteConfirmation.addEventListener("input", () => {
    elements.projectDeleteConfirmation.setCustomValidity("");
    updateProjectDeleteSubmitState();
  });
  elements.editProjectName.addEventListener("input", () => elements.editProjectName.setCustomValidity(""));
  [
    elements.newProjectKey,
    elements.newProjectName,
    elements.newProjectLanguage,
    elements.newProjectSpecsDir,
    elements.newProjectTestsDir,
    elements.newProjectDescription,
  ].forEach((input) => input.addEventListener("input", () => input.setCustomValidity("")));
  [
    elements.projectImportFile,
    elements.importProjectKey,
    elements.importProjectName,
    elements.importProjectSpecsDir,
    elements.importProjectTestsDir,
    elements.importProjectDescription,
  ].forEach((input) => input.addEventListener("input", () => input.setCustomValidity("")));
  [
    [elements.newProjectDescription, submitProjectCreate],
    [elements.importProjectDescription, submitProjectImport],
    [elements.editProjectDescription, submitProjectEdit],
  ].forEach(([input, submit]) =>
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        submit();
      }
    }),
  );
  elements.projectDeleteConfirmation.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !elements.projectDeleteSubmit.disabled) {
      event.preventDefault();
      submitProjectDelete();
    }
  });
  window.addEventListener("keydown", handleProjectModalKeydown, true);
}

async function switchProject(projectKey) {
  if (!projectKey || projectKey === state.project.currentKey) {
    return;
  }
  if (isAnyScriptJobRunning()) {
    setNotice("任务运行中，暂不能切换项目。", "error");
    renderProjectSelect();
    return;
  }
  if (!confirmDiscardEdit()) {
    renderProjectSelect();
    return;
  }

  const previousLanguage = state.project.current?.language || "en";
  const nextProject = state.project.projects.find((project) => project.project_key === projectKey) || null;
  state.project.currentKey = projectKey;
  state.project.current = nextProject;
  writeStorageItem(CURRENT_PROJECT_STORAGE_KEY, projectKey);
  if (nextProject && (nextProject.language || "en") !== previousLanguage) {
    window.location.reload();
    return;
  }
  resetProjectScopedState();
  const requiresTargetSystem = routeUnconfiguredProjectToSettings();
  setNotice("");
  renderProjectSelect();
  renderSideList();
  renderContent();
  await hydratePlatformRecords();
  await loadActiveSection();
  if (requiresTargetSystem) {
    notifyUnconfiguredProject();
  }
}

return {
  normalizeProject,
  hasConfiguredTargetSystem,
  routeUnconfiguredProjectToSettings,
  notifyUnconfiguredProject,
  resetProjectScopedState,
  renderProjectSelect,
  loadProjects,
  clearProjectCreateValidity,
  openProjectCreateModal,
  closeProjectCreateModal,
  submitProjectCreate,
  clearProjectImportValidity,
  openProjectImportModal,
  closeProjectImportModal,
  exportCurrentProject,
  submitProjectImport,
  openProjectManageModal,
  closeProjectManageModal,
  returnToProjectManageList,
  renderProjectManageList,
  openProjectEditView,
  submitProjectEdit,
  openProjectDeleteModal,
  closeProjectDeleteModal,
  submitProjectDelete,
  handleProjectManageTableClick,
  bindProjectManagementEvents,
  switchProject,
};
}

window.getProjectManagementElements = getProjectManagementElements;
window.createProjectsFeature = createProjectsFeature;
