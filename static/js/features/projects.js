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
  } = deps;
  const { hasProjectSettingsPermission } = admin;
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
    status: typeof project.status === "string" ? project.status : "active",
    is_default: Boolean(project.is_default),
  };
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
    elements.projectSelect.appendChild(option);
  });
  elements.projectSelect.value = state.project.currentKey || "";
  const projectBusy = isAnyScriptJobRunning() || state.isEditing || state.project.isExporting || state.project.isImporting;
  const canManageProject = hasProjectSettingsPermission();
  elements.projectSelect.disabled = !state.project.projects.length || projectBusy;
  elements.createProjectButton.disabled = projectBusy;
  elements.exportProjectButton.classList.toggle("hidden", !canManageProject);
  elements.importProjectButton.classList.toggle("hidden", !canManageProject);
  elements.exportProjectButton.disabled = !state.project.currentKey || projectBusy;
  elements.importProjectButton.disabled = !state.project.currentKey || projectBusy;
}

async function loadProjects() {
  const data = await requestJson("/api/projects");
  state.project.projects = (data.projects || []).map(normalizeProject).filter(Boolean);
  state.project.workspaceRoot = typeof data.project_workspace_root === "string" ? data.project_workspace_root : "";
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

function clearProjectCreateValidity() {
  [
    elements.newProjectKey,
    elements.newProjectName,
    elements.newProjectSpecsDir,
    elements.newProjectTestsDir,
    elements.newProjectDescription,
  ].forEach((input) => input?.setCustomValidity(""));
}

function openProjectCreateModal() {
  setNotice("");
  clearProjectCreateValidity();
  elements.newProjectKey.value = "";
  elements.newProjectName.value = "";
  elements.newProjectSpecsDir.value = "specs";
  elements.newProjectTestsDir.value = "tests";
  elements.newProjectDescription.value = "";
  elements.projectCreateWorkspaceHint.textContent = state.project.workspaceRoot
    ? `项目目录将自动创建为：${state.project.workspaceRoot}/<项目标识>`
    : "请先在 config.json 配置 project_workspace_root。";
  elements.projectCreateModal.classList.remove("hidden");
  window.requestAnimationFrame(() => elements.newProjectKey.focus());
}

function closeProjectCreateModal() {
  elements.projectCreateModal.classList.add("hidden");
  clearProjectCreateValidity();
}

async function submitProjectCreate() {
  clearProjectCreateValidity();

  const payload = {
    project_key: elements.newProjectKey.value.trim(),
    name: elements.newProjectName.value.trim(),
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

  elements.projectCreateSubmit.disabled = true;
  try {
    const data = await requestJson("/api/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const project = normalizeProject(data.project);
    closeProjectCreateModal();
    await loadProjects();
    if (project?.project_key && project.project_key !== state.project.currentKey) {
      await switchProject(project.project_key);
    }
    setNotice("项目创建成功，已初始化目录并切换到新项目。", "success");
  } catch (error) {
    elements.newProjectKey.setCustomValidity(error.message || "项目创建失败。");
    elements.newProjectKey.reportValidity();
  } finally {
    elements.projectCreateSubmit.disabled = false;
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
  if (!state.project.currentKey || !hasProjectSettingsPermission() || isAnyScriptJobRunning() || state.isEditing) {
    return;
  }
  setNotice("");
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
  elements.projectImportModal.classList.remove("hidden");
  window.requestAnimationFrame(() => elements.projectImportFile.focus());
}

function closeProjectImportModal() {
  if (state.project.isImporting) {
    return;
  }
  elements.projectImportModal.classList.add("hidden");
  clearProjectImportValidity();
}

async function exportCurrentProject() {
  if (!state.project.currentKey || state.project.isExporting || isAnyScriptJobRunning() || state.isEditing) {
    return;
  }
  state.project.isExporting = true;
  renderProjectSelect();
  try {
    const response = await fetch("/api/projects/export", {
      headers: getProjectRequestHeaders(),
    });
    if (!response.ok) {
      const message = await readFetchError(response, `导出项目失败：${response.status}`);
      if (response.status === 401) {
        window.location.href = "/login";
      }
      throw new Error(message);
    }
    const blob = await response.blob();
    const fallbackName = `playwright-project-${state.project.currentKey || "project"}.zip`;
    const filename = getDownloadFilename(response, fallbackName);
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
    setNotice("项目导出文件已开始下载。", "success");
  } catch (error) {
    setNotice(error.message || "项目导出失败。", "error");
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
    closeProjectImportModal();
    await loadProjects();
    if (project?.project_key && project.project_key !== state.project.currentKey) {
      await switchProject(project.project_key);
    }
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

  state.project.currentKey = projectKey;
  state.project.current = state.project.projects.find((project) => project.project_key === projectKey) || null;
  writeStorageItem(CURRENT_PROJECT_STORAGE_KEY, projectKey);
  resetProjectScopedState();
  setNotice("");
  renderProjectSelect();
  renderSideList();
  renderContent();
  await hydratePlatformRecords();
  await loadActiveSection();
}

return {
  normalizeProject,
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
  switchProject,
};
}

window.createProjectsFeature = createProjectsFeature;
