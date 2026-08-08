function createGenerationFeature(deps) {
  const {
    state,
    elements,
    SECTION,
    PLAN_VIEW_TAB,
    PLAN_GENERATION_MODE,
    COVERAGE_POLICY_START,
    COVERAGE_POLICY_END,
    DEFAULT_COVERAGE_PROFILE,
    SCRIPT_PROMPT_FIXED_TEMPLATE,
    SCRIPT_PROMPT_NOTE_DEFAULT,
    window,
    fetch,
    TextDecoder,
    timers,
    formatDuration,
    requirements,
    getPlanGenerationMode,
    getPlanGenerationPlanFilename,
    getPlanGenerationModuleName,
    getDefaultPlanFilename,
    getDefaultScriptTargetPath,
    getPlanRecordKey,
    renderPlanGenerationModuleOptions,
    setPlanGenerationModuleMode,
    resetPlanGenerationSource,
    isRequirementPlanGeneration,
    setPlanGenerationModuleControlsLocked,
    setupPlanGenerationModuleField,
    normalizePlanGenerationRecord,
    persistPlanGenerationRecords,
    normalizePlanScriptGenerationRecord,
    persistPlanScriptGenerationRecords,
    replaceAllText,
    requestJson,
    encodePathPart,
    getProjectRequestHeaders,
    parseSseBlock,
    setNotice,
    persistViewState,
    renderContent,
    renderSideList,
    renderPlanGenerationRecord,
    loadPlanModules,
    selectPlan,
    selectPlanModule,
    confirmDiscardEdit,
    escapeHtml,
  } = deps;
  const {
    getRequirementModuleByUid,
    saveRequirementModule,
    closeRequirementModuleDetail,
    mergeRequirementModuleUpdate,
  } = requirements;

function resetGenerationJobView() {
  state.generation.jobId = null;
  state.generation.isRunning = false;
  if (state.generation.pollTimer) {
    timers.clearInterval(state.generation.pollTimer);
    state.generation.pollTimer = null;
  }
  stopPlanGenerationDurationTimer();
  elements.planJobOutput.classList.add("hidden");
  elements.planJobStatus.textContent = "任务进行中";
  elements.planJobStatus.className = "job-status";
  elements.planJobLogs.textContent = "";
  elements.planGenerationSubmit.disabled = false;
  elements.planGenerationSubmit.textContent = "确认生成";
}

function appendPlanJobLog(message) {
  elements.planJobOutput.classList.remove("hidden");
  const current = elements.planJobLogs.textContent;
  const prefix = current && !current.endsWith("\n") ? "\n" : "";
  elements.planJobLogs.textContent += `${prefix}${message}\n`;
  elements.planJobLogs.scrollTop = elements.planJobLogs.scrollHeight;
}

function appendPlanJobDelta(text) {
  if (!text) {
    return;
  }

  elements.planJobOutput.classList.remove("hidden");
  elements.planJobLogs.textContent += text;
  elements.planJobLogs.scrollTop = elements.planJobLogs.scrollHeight;
}

function renderPlanStreamStatus(status, error = "") {
  elements.planJobOutput.classList.remove("hidden");
  elements.planJobStatus.className = "job-status";

  if (status === "succeeded") {
    elements.planJobStatus.textContent = "任务成功";
    elements.planJobStatus.classList.add("success");
    elements.planGenerationSubmit.disabled = true;
    elements.planGenerationSubmit.textContent = "已完成";
    return;
  }

  if (status === "failed") {
    elements.planJobStatus.textContent = `任务失败${error ? `：${error}` : ""}`;
    elements.planJobStatus.classList.add("error");
    elements.planGenerationSubmit.disabled = false;
    elements.planGenerationSubmit.textContent = "重试";
    return;
  }

  elements.planJobStatus.textContent = "任务进行中，正在接收实时输出";
  elements.planGenerationSubmit.disabled = true;
  elements.planGenerationSubmit.textContent = "生成中";
}

function renderGenerationTargetPath() {
  const moduleName = getPlanGenerationModuleName() || "<模块名>";
  const planFilename = getPlanGenerationPlanFilename(moduleName);
  const template = state.generation.targetPathTemplate || "";
  elements.planTargetPath.textContent = template
    ? replaceAllText(replaceAllText(template, "<模块名>", moduleName), "<测试计划名>.md", planFilename)
    : `specs/${moduleName}/${planFilename}`;
}

function renderGenerationDuration(element, record, runningLabel, finishedLabel) {
  if (!element) {
    return;
  }

  if (!record?.started_at) {
    element.textContent = "";
    element.classList.add("hidden");
    return;
  }

  const isRunning = record.status === "running";
  const finishedAt = record.finished_at || (isRunning ? Date.now() : record.updated_at);
  const label = isRunning ? runningLabel : finishedLabel;
  element.textContent = `${label}：${formatDuration(finishedAt - record.started_at)}`;
  element.classList.remove("hidden");
}

function refreshPlanGenerationDuration() {
  const key = getPlanRecordKey();
  const record = key ? state.plans.generationRecords[key] : null;
  renderGenerationDuration(elements.planRecordDuration, record, "生成进行时间", "生成耗时");
}

function startPlanGenerationDurationTimer() {
  stopPlanGenerationDurationTimer();
  refreshPlanGenerationDuration();
  state.generation.durationTimer = timers.setInterval(refreshPlanGenerationDuration, 1000);
}

function stopPlanGenerationDurationTimer() {
  if (state.generation.durationTimer) {
    timers.clearInterval(state.generation.durationTimer);
    state.generation.durationTimer = null;
  }
  refreshPlanGenerationDuration();
}

function refreshPlanScriptGenerationDuration() {
  const key = getPlanRecordKey();
  const record = key ? state.plans.scriptGenerationRecords[key] : null;
  renderGenerationDuration(elements.planScriptDuration, record, "生成进行时间", "生成耗时");
}

function startPlanScriptGenerationDurationTimer() {
  stopPlanScriptGenerationDurationTimer();
  refreshPlanScriptGenerationDuration();
  state.scriptGeneration.durationTimer = timers.setInterval(refreshPlanScriptGenerationDuration, 1000);
}

function stopPlanScriptGenerationDurationTimer() {
  if (state.scriptGeneration.durationTimer) {
    timers.clearInterval(state.scriptGeneration.durationTimer);
    state.scriptGeneration.durationTimer = null;
  }
  refreshPlanScriptGenerationDuration();
}

function setPlanGenerationRecord(moduleName, planFilename, updates) {
  const key = getPlanRecordKey(moduleName, planFilename);
  if (!key) {
    return null;
  }

  const filename = planFilename || getDefaultPlanFilename(moduleName);
  const previous = state.plans.generationRecords[key] || {
    status: "idle",
    module_name: moduleName,
    plan_filename: filename,
    prompt: "",
    logs: "",
    error: "",
    target_path: "",
    started_at: null,
    finished_at: null,
  };
  const next = normalizePlanGenerationRecord({
    ...previous,
    ...updates,
    module_name: updates.module_name || previous.module_name || moduleName,
    plan_filename: updates.plan_filename || previous.plan_filename || filename,
    logs: Object.prototype.hasOwnProperty.call(updates, "logs") ? updates.logs : previous.logs || "",
    updated_at: Date.now(),
  });
  state.plans.generationRecords[key] = next;
  persistPlanGenerationRecords(key);
  if (
    state.activeSection === SECTION.PLANS &&
    state.plans.activeTab === PLAN_VIEW_TAB.PLAN_GENERATION &&
    key === getPlanRecordKey()
  ) {
    renderPlanGenerationRecord();
  }
  return next;
}

function getDefaultPlanScriptGenerationRecord(moduleName, planFilename) {
  const filename = planFilename || getDefaultPlanFilename(moduleName);
  const promptFixed = renderScriptPromptFromTemplate(moduleName, filename);
  const promptNote = SCRIPT_PROMPT_NOTE_DEFAULT;
  return {
    status: "idle",
    module_name: moduleName,
    plan_filename: filename,
    prompt_fixed: promptFixed,
    prompt_note: promptNote,
    prompt: `${promptFixed}\n${promptNote}`.trim(),
    logs: "",
    error: "",
    target_path: getDefaultScriptTargetPath(moduleName),
    started_at: null,
    finished_at: null,
    updated_at: Date.now(),
  };
}

function ensurePlanScriptGenerationRecord(
  moduleName = state.plans.selectedModule,
  planFilename = state.plans.selectedPlanFile,
) {
  const key = getPlanRecordKey(moduleName, planFilename);
  if (!key) {
    return null;
  }

  const defaults = getDefaultPlanScriptGenerationRecord(moduleName, planFilename);
  const previous = state.plans.scriptGenerationRecords[key];
  const promptFixed =
    typeof previous?.prompt_fixed === "string" && previous.prompt_fixed
      ? previous.prompt_fixed
      : defaults.prompt_fixed;
  const promptNote =
    typeof previous?.prompt_note === "string" && previous.prompt_note
      ? previous.prompt_note
      : defaults.prompt_note;
  const next = normalizePlanScriptGenerationRecord({
    ...defaults,
    ...(previous || {}),
    module_name: moduleName,
    plan_filename: planFilename || defaults.plan_filename,
    prompt_fixed: promptFixed,
    prompt_note: promptNote,
    prompt: `${promptFixed.trim()}\n${promptNote.trim()}`.trim(),
    target_path: previous?.target_path || defaults.target_path,
  });
  state.plans.scriptGenerationRecords[key] = next;
  persistPlanScriptGenerationRecords(key);
  return next;
}

function setPlanScriptGenerationRecord(moduleName, planFilename, updates) {
  const key = getPlanRecordKey(moduleName, planFilename);
  if (!key) {
    return null;
  }

  const previous =
    state.plans.scriptGenerationRecords[key] || getDefaultPlanScriptGenerationRecord(moduleName, planFilename);
  const next = normalizePlanScriptGenerationRecord({
    ...previous,
    ...updates,
    module_name: updates.module_name || previous.module_name || moduleName,
    plan_filename: updates.plan_filename || previous.plan_filename || planFilename || getDefaultPlanFilename(moduleName),
    logs: Object.prototype.hasOwnProperty.call(updates, "logs") ? updates.logs : previous.logs || "",
    target_path: updates.target_path || previous.target_path || getDefaultScriptTargetPath(moduleName),
    updated_at: Date.now(),
  });
  state.plans.scriptGenerationRecords[key] = next;
  persistPlanScriptGenerationRecords(key);
  return next;
}

function updatePlanScriptGenerationPromptFromInputs() {
  const moduleName = state.plans.selectedModule;
  const planFilename = state.plans.selectedPlanFile;
  if (!moduleName || !planFilename) {
    return;
  }

  const promptFixed = elements.planScriptPromptFixed.value;
  const promptNote = elements.planScriptPromptNote.value;
  setPlanScriptGenerationRecord(moduleName, planFilename, {
    prompt_fixed: promptFixed,
    prompt_note: promptNote,
    prompt: `${promptFixed.trim()}\n${promptNote.trim()}`.trim(),
  });
}

function renderGenerationPromptFromTemplate(moduleName) {
  const value = moduleName || "<模块名>";
  return replaceAllText(state.generation.promptTemplate || "", "<模块名>", value);
}

function getCoverageProfile(profileKey = state.generation.coverageProfile) {
  return (
    state.generation.coverageProfiles.find((item) => item.key === profileKey) ||
    state.generation.coverageProfiles.find((item) => item.key === DEFAULT_COVERAGE_PROFILE) ||
    null
  );
}

function composeCoveragePrompt(basePrompt, coveragePrompt) {
  const base = String(basePrompt || "").trim();
  const policy = String(coveragePrompt || "").trim();
  if (!policy) {
    return base;
  }
  return `${base}\n\n${COVERAGE_POLICY_START}\n${policy}\n${COVERAGE_POLICY_END}`.trim();
}

function replaceCoveragePolicy(prompt, coveragePrompt) {
  const text = String(prompt || "");
  const start = text.indexOf(COVERAGE_POLICY_START);
  const end = text.indexOf(COVERAGE_POLICY_END);
  const block = coveragePrompt
    ? `${COVERAGE_POLICY_START}\n${String(coveragePrompt).trim()}\n${COVERAGE_POLICY_END}`
    : "";
  if (start >= 0 && end > start) {
    return `${text.slice(0, start)}${block}${text.slice(end + COVERAGE_POLICY_END.length)}`.replace(/\n{3,}/g, "\n\n").trim();
  }
  return [text.trim(), block].filter(Boolean).join("\n\n");
}

function extractCoveragePolicy(prompt) {
  const text = String(prompt || "");
  const start = text.indexOf(COVERAGE_POLICY_START);
  const end = text.indexOf(COVERAGE_POLICY_END);
  if (start < 0 || end <= start) {
    return "";
  }
  return text.slice(start + COVERAGE_POLICY_START.length, end).trim();
}

function populateCoverageSelect(select, selectedKey) {
  if (!select) {
    return;
  }
  select.innerHTML = state.generation.coverageProfiles
    .map((item) => `<option value="${escapeHtml(item.key)}" ${item.key === selectedKey ? "selected" : ""}>${escapeHtml(item.label)}</option>`)
    .join("");
}

function renderPlanCoverageState() {
  const profile = getCoverageProfile();
  if (elements.planCoverageDescription) {
    elements.planCoverageDescription.textContent = profile
      ? window.WaterfallI18n.t("plan.coverageDescription", {
        description: profile.description,
        count: profile.suggested_max_cases,
      })
      : "";
  }
  const customized = elements.planPrompt.value.trim() !== state.generation.defaultComposedPrompt.trim();
  elements.planPromptCustomized.textContent = customized ? "· 已自定义" : "";
  elements.planPromptCustomized.className = customized ? "prompt-customized" : "";
}

function resetPlanPromptForCoverage(force = false) {
  if (!force && elements.planPrompt.value.trim() !== state.generation.defaultComposedPrompt.trim()) {
    if (!window.confirm("当前生成语句已被编辑，恢复模板将丢弃这些修改。是否继续？")) {
      return false;
    }
  }
  const profile = getCoverageProfile();
  state.generation.defaultComposedPrompt = composeCoveragePrompt(
    state.generation.basePrompt,
    profile?.template_prompt || "",
  );
  elements.planPrompt.value = state.generation.defaultComposedPrompt;
  renderPlanCoverageState();
  return true;
}

function changePlanCoverageProfile() {
  const nextProfile = elements.planCoverageProfile.value || DEFAULT_COVERAGE_PROFILE;
  const currentPolicy = extractCoveragePolicy(elements.planPrompt.value);
  const currentProfile = getCoverageProfile(state.generation.coverageProfile);
  const promptText = elements.planPrompt.value;
  const hasPolicyBlock =
    promptText.includes(COVERAGE_POLICY_START) && promptText.includes(COVERAGE_POLICY_END);
  const policyWasModified = currentProfile && (!hasPolicyBlock || currentPolicy !== currentProfile.template_prompt);
  if (policyWasModified) {
    if (!window.confirm("覆盖策略段已被编辑，切换档位将替换该策略段，其他修改会保留。是否继续？")) {
      elements.planCoverageProfile.value = state.generation.coverageProfile;
      return;
    }
  }
  state.generation.coverageProfile = nextProfile;
  const profile = getCoverageProfile(nextProfile);
  if (state.generation.autoProfilePlanName && isRequirementPlanGeneration()) {
    const moduleItem = getRequirementModuleByUid(state.generation.requirementModuleUid);
    if (moduleItem) {
      elements.newPlanName.value = `${moduleItem.plan_name || moduleItem.module_name}-${profile?.label || "核心回归"}`;
      renderGenerationTargetPath();
    }
  }
  elements.planPrompt.value = replaceCoveragePolicy(elements.planPrompt.value, profile?.template_prompt || "");
  state.generation.defaultComposedPrompt = composeCoveragePrompt(state.generation.basePrompt, profile?.template_prompt || "");
  renderPlanCoverageState();
}

function updatePromptForModuleName() {
  const nextModuleName = getPlanGenerationModuleName() || "<模块名>";
  const previousModuleName = state.generation.previousModuleName || "<模块名>";
  const currentPrompt = elements.planPrompt.value;
  const wasDefault = currentPrompt.trim() === state.generation.defaultComposedPrompt.trim();
  const nextBasePrompt = renderGenerationPromptFromTemplate(nextModuleName);
  state.generation.basePrompt = nextBasePrompt;
  if (wasDefault) {
    resetPlanPromptForCoverage(true);
  } else {
    elements.planPrompt.value = replaceAllText(currentPrompt, "<模块名>", nextModuleName);
    renderPlanCoverageState();
  }
  state.generation.previousModuleName = nextModuleName;
  renderGenerationTargetPath();
}

function updateTargetForPlanName() {
  renderGenerationTargetPath();
}

function updatePlanGenerationMode() {
  state.generation.mode = getPlanGenerationMode();
  elements.newPlanName.placeholder =
    state.generation.mode === PLAN_GENERATION_MODE.MULTIPLE
      ? "默认：<模块名>-用例索引"
      : "默认与模块名一致";
  renderGenerationTargetPath();
}

async function ensureGenerationDefaults() {
  if (state.generation.defaultsLoaded) {
    return;
  }

  const data = await requestJson("/api/plan-generation-defaults");
  state.generation.promptTemplate = data.prompt_template || "";
  state.generation.targetPathTemplate = data.target_path_template || "";
  state.generation.coverageProfiles = Array.isArray(data.coverage_profiles) ? data.coverage_profiles : [];
  state.generation.defaultCoverageProfile = data.default_coverage_profile || DEFAULT_COVERAGE_PROFILE;
  state.generation.defaultsLoaded = true;
}

async function openPlanGenerationModal() {
  if (state.activeSection !== SECTION.PLANS) {
    return;
  }

  if (state.generation.isRunning) {
    elements.planGenerationModal.classList.remove("hidden");
    return;
  }

  if (!confirmDiscardEdit()) {
    return;
  }

  state.isEditing = false;
  resetPlanGenerationSource();
  renderContent();
  resetGenerationJobView();

  try {
    await ensureGenerationDefaults();
  } catch (error) {
    setNotice(error.message, "error");
    return;
  }

  const initialModuleName = setupPlanGenerationModuleField();
  elements.newPlanName.value = "";
  state.generation.autoProfilePlanName = false;
  elements.planModeMultiple.checked = true;
  elements.planModeSingle.checked = false;
  updatePlanGenerationMode();
  state.generation.coverageProfile = state.generation.defaultCoverageProfile;
  populateCoverageSelect(elements.planCoverageProfile, state.generation.coverageProfile);
  state.generation.basePrompt = renderGenerationPromptFromTemplate(initialModuleName || "<模块名>");
  resetPlanPromptForCoverage(true);
  state.generation.previousModuleName = initialModuleName || "<模块名>";
  renderGenerationTargetPath();
  elements.planGenerationModal.classList.remove("hidden");
  if (state.generation.moduleNameMode === "input") {
    elements.newModuleName.focus();
  } else {
    elements.newModuleNameSelect.focus();
  }
}

async function openRequirementPlanGenerationModal(moduleUid) {
  const requirementUid = state.requirements.selectedUid;
  if (!requirementUid || !moduleUid) {
    return;
  }

  if (state.generation.isRunning) {
    elements.planGenerationModal.classList.remove("hidden");
    return;
  }

  resetGenerationJobView();

  try {
    const updated = await saveRequirementModule(moduleUid, { silent: true });
    const moduleItem = updated || getRequirementModuleByUid(moduleUid);
    if (!moduleItem) {
      throw new Error("候选模块不存在。");
    }
    await ensureGenerationDefaults();

    state.generation.source = "requirement";
    state.generation.requirementUid = requirementUid;
    state.generation.requirementModuleUid = moduleUid;
    closeRequirementModuleDetail();

    renderPlanGenerationModuleOptions(moduleItem.module_name);
    setPlanGenerationModuleMode("input");
    setPlanGenerationModuleControlsLocked(true);
    elements.newModuleName.value = moduleItem.module_name;
    const hasGeneratedPlans = Array.isArray(moduleItem.generated_plans) && moduleItem.generated_plans.length > 0;
    const profileLabel = getCoverageProfile(state.generation.defaultCoverageProfile)?.label || "核心回归";
    elements.newPlanName.value = hasGeneratedPlans ? `${moduleItem.plan_name || moduleItem.module_name}-${profileLabel}` : "";
    state.generation.autoProfilePlanName = hasGeneratedPlans;
    elements.planModeMultiple.checked = true;
    elements.planModeSingle.checked = false;
    updatePlanGenerationMode();
    state.generation.coverageProfile = state.generation.defaultCoverageProfile;
    populateCoverageSelect(elements.planCoverageProfile, state.generation.coverageProfile);
    state.generation.basePrompt = moduleItem.planner_prompt || "";
    resetPlanPromptForCoverage(true);
    state.generation.previousModuleName = moduleItem.module_name || "<模块名>";
    renderGenerationTargetPath();
    elements.planGenerationModal.classList.remove("hidden");
    elements.planModeMultiple.focus();
  } catch (error) {
    resetPlanGenerationSource();
    setNotice(error.message, "error");
  }
}

function closePlanGenerationModal() {
  if (state.generation.isRunning) {
    const confirmed = window.confirm("任务仍在进行中，关闭弹窗后任务会继续在后台执行。是否关闭？");
    if (!confirmed) {
      return;
    }
  }

  if (state.generation.pollTimer) {
    timers.clearInterval(state.generation.pollTimer);
    state.generation.pollTimer = null;
  }

  elements.planGenerationModal.classList.add("hidden");
  resetPlanGenerationSource();
}

function renderPlanJob(job) {
  elements.planJobOutput.classList.remove("hidden");
  elements.planJobLogs.textContent = (job.logs || []).join("\n");
  elements.planJobLogs.scrollTop = elements.planJobLogs.scrollHeight;
  elements.planJobStatus.className = "job-status";

  if (job.status === "succeeded") {
    elements.planJobStatus.textContent = "任务成功";
    elements.planJobStatus.classList.add("success");
    elements.planGenerationSubmit.disabled = true;
    elements.planGenerationSubmit.textContent = "已完成";
    return;
  }

  if (job.status === "failed") {
    elements.planJobStatus.textContent = `任务失败${job.error ? `：${job.error}` : ""}`;
    elements.planJobStatus.classList.add("error");
    elements.planGenerationSubmit.disabled = false;
    elements.planGenerationSubmit.textContent = "重试";
    return;
  }

  elements.planJobStatus.textContent = "任务进行中，通常耗时几分钟";
  elements.planGenerationSubmit.disabled = true;
  elements.planGenerationSubmit.textContent = "生成中";
}

async function pollPlanJob() {
  if (!state.generation.jobId) {
    return;
  }

  try {
    const job = await requestJson(`/api/plan-generation-jobs/${state.generation.jobId}`);
    renderPlanJob(job);

    if (job.status === "succeeded" || job.status === "failed") {
      state.generation.isRunning = false;
      if (state.generation.pollTimer) {
        timers.clearInterval(state.generation.pollTimer);
        state.generation.pollTimer = null;
      }

      if (job.status === "succeeded") {
        state.plans.selectedModule = job.module_name;
        state.plans.selectedPlanFile = job.plan_filename || getDefaultPlanFilename(job.module_name);
        await loadPlanModules();
        await selectPlan(job.module_name, job.plan_filename || getDefaultPlanFilename(job.module_name), true);
      }
    }
  } catch (error) {
    state.generation.isRunning = false;
    if (state.generation.pollTimer) {
      timers.clearInterval(state.generation.pollTimer);
      state.generation.pollTimer = null;
    }
    renderPlanJob({
      status: "failed",
      error: error.message,
      logs: [`轮询任务状态失败：${error.message}`],
    });
  }
}

function showPlanGenerationRecordTab(moduleName, planFilename, targetPath = "") {
  state.activeSection = SECTION.PLANS;
  state.plans.selectedModule = moduleName;
  state.plans.selectedPlanFile = planFilename || getDefaultPlanFilename(moduleName);
  state.plans.expandedModules.add(moduleName);
  state.plans.currentMarkdown = "";
  state.plans.currentHtml = "";
  state.plans.filePath = targetPath;
  state.plans.activeTab = PLAN_VIEW_TAB.PLAN_GENERATION;
  state.isEditing = false;
  persistViewState();
  renderSideList();
  renderContent();
}

async function submitPlanGeneration() {
  const moduleName = getPlanGenerationModuleName();
  const generationMode = getPlanGenerationMode();
  const planName = elements.newPlanName.value.trim() || moduleName;
  const planFilename = getPlanGenerationPlanFilename(moduleName);
  const prompt = elements.planPrompt.value.trim();
  const coverageProfile = state.generation.coverageProfile || state.generation.defaultCoverageProfile;
  const coveragePrompt = extractCoveragePolicy(prompt);
  const promptCustomized = prompt !== state.generation.defaultComposedPrompt.trim();
  const requirementSource = isRequirementPlanGeneration()
    ? {
        requirement_uid: state.generation.requirementUid,
        requirement_module_uid: state.generation.requirementModuleUid,
      }
    : null;
  const moduleNameControl =
    state.generation.moduleNameMode === "input" ? elements.newModuleName : elements.newModuleNameSelect;

  if (!moduleName) {
    moduleNameControl.focus();
    return;
  }

  if (!prompt) {
    elements.planPrompt.focus();
    return;
  }

  if (state.generation.pollTimer) {
    timers.clearInterval(state.generation.pollTimer);
    state.generation.pollTimer = null;
  }

  const targetPath = elements.planTargetPath.textContent || "";
  elements.planGenerationSubmit.disabled = true;
  elements.planGenerationSubmit.textContent = "提交中";
  elements.planJobOutput.classList.remove("hidden");
  elements.planJobStatus.textContent = "正在提交任务";
  elements.planJobStatus.className = "job-status";
  elements.planJobLogs.textContent = "";
  const startedAt = Date.now();
  state.generation.isRunning = true;
  setPlanGenerationRecord(moduleName, planFilename, {
    status: "running",
    module_name: moduleName,
    plan_filename: planFilename,
    prompt,
    coverage_profile: coverageProfile,
    prompt_customized: promptCustomized,
    logs: "",
    error: "",
    target_path: targetPath,
    started_at: startedAt,
    finished_at: null,
  });
  showPlanGenerationRecordTab(moduleName, planFilename, targetPath);
  startPlanGenerationDurationTimer();
  elements.planGenerationModal.classList.add("hidden");

  try {
    const response = await fetch("/api/plan-generation-stream", {
      method: "POST",
      headers: getProjectRequestHeaders({
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({
        module_name: moduleName,
        plan_name: planName,
        plan_filename: planFilename,
        generation_mode: generationMode,
        prompt,
        base_prompt: state.generation.basePrompt,
        coverage_profile: coverageProfile,
        coverage_prompt: coveragePrompt,
        prompt_customized: promptCustomized,
        ...(requirementSource || {}),
      }),
    });

    if (!response.ok) {
      let data;
      try {
        data = await response.json();
      } catch (error) {
        data = { error: `请求失败: ${response.status}` };
      }
      throw new Error(data.error || `请求失败: ${response.status}`);
    }

    if (!response.body) {
      throw new Error("浏览器不支持读取流式响应。");
    }

    state.generation.isRunning = true;
    renderPlanStreamStatus("running");

    const result = await readPlanGenerationStream(response, moduleName, planFilename);
    const finishedAt = Date.now();
    state.generation.isRunning = false;
    if (result.status !== "succeeded" && result.status !== "failed") {
      result.status = "failed";
      result.error = "流式响应提前结束。";
    }
    setPlanGenerationRecord(moduleName, result.plan_filename || planFilename, {
      status: result.status,
      error: result.error || "",
      logs: result.logs || "",
      target_path: result.target_path || elements.planTargetPath.textContent || "",
      finished_at: finishedAt,
    });
    stopPlanGenerationDurationTimer();

    if (result.status === "succeeded") {
      const resultModule = result.module_name || moduleName;
      const resultPlanFilename = result.plan_filename || planFilename;
      if (result.requirement_module) {
        mergeRequirementModuleUpdate(result.requirement_module);
      }
      if (generationMode === PLAN_GENERATION_MODE.MULTIPLE) {
        if (!Array.isArray(result.plans) || !result.plans.length) {
          await splitGeneratedPlanCases(resultModule, resultPlanFilename);
        }
        await loadPlanModules();
        await selectPlanModule(resultModule, true);
      } else {
        state.plans.selectedModule = resultModule;
        state.plans.selectedPlanFile = resultPlanFilename;
        state.plans.activeTab = PLAN_VIEW_TAB.PLAN_GENERATION;
        persistViewState();
        await loadPlanModules();
        await selectPlan(resultModule, resultPlanFilename, true);
      }
      resetPlanGenerationSource();
      return;
    }

    if (result.status !== "failed") {
      renderPlanStreamStatus("failed", "流式响应提前结束。");
    }
    renderContent();
    resetPlanGenerationSource();
  } catch (error) {
    const finishedAt = Date.now();
    state.generation.isRunning = false;
    const current = state.plans.generationRecords[getPlanRecordKey(moduleName, planFilename)] || {};
    const prefix = current.logs && !current.logs.endsWith("\n") ? "\n" : "";
    setPlanGenerationRecord(moduleName, planFilename, {
      status: "failed",
      error: error.message,
      logs: `${current.logs || ""}${prefix}${error.message}\n`,
      finished_at: finishedAt,
    });
    stopPlanGenerationDurationTimer();
    renderPlanStreamStatus("failed", error.message);
    appendPlanJobLog(error.message);
    renderContent();
    resetPlanGenerationSource();
  }
}

async function readPlanGenerationStream(response, moduleName, planFilename) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let result = { status: "running", logs: "" };

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
        result = handlePlanStreamEvent(event, result, moduleName, planFilename);
      }
      boundaryIndex = buffer.indexOf("\n\n");
    }
  }

  buffer += decoder.decode();
  const trailingEvent = parseSseBlock(buffer.trim());
  if (trailingEvent) {
    result = handlePlanStreamEvent(trailingEvent, result, moduleName, planFilename);
  }

  return result;
}

function handlePlanStreamEvent({ event, data }, previousResult, moduleName, planFilename) {
  if (event === "status") {
    const nextPlanFilename = data.plan_filename || previousResult.plan_filename || planFilename;
    const nextResult = {
      ...previousResult,
      status: data.status || previousResult.status,
      module_name: data.module_name || previousResult.module_name,
      plan_filename: nextPlanFilename,
      target_path: data.target_path || previousResult.target_path,
      error: data.error || previousResult.error,
    };
    renderPlanStreamStatus(nextResult.status, nextResult.error);
    setPlanGenerationRecord(moduleName, nextPlanFilename, {
      status: nextResult.status,
      plan_filename: nextPlanFilename,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
      target_path: nextResult.target_path || "",
    });
    return nextResult;
  }

  if (event === "log") {
    const text = data.message ? `${data.message}\n` : "";
    appendPlanJobLog(data.message || "");
    const logs = `${previousResult.logs || ""}${text}`;
    setPlanGenerationRecord(moduleName, previousResult.plan_filename || planFilename, { logs });
    return { ...previousResult, logs };
  }

  if (event === "delta") {
    const text = data.text || "";
    appendPlanJobDelta(text);
    const logs = `${previousResult.logs || ""}${text}`;
    setPlanGenerationRecord(moduleName, previousResult.plan_filename || planFilename, { logs });
    return { ...previousResult, logs };
  }

  if (event === "done" && data.ok === false) {
    renderPlanStreamStatus("failed", data.error || "");
    setPlanGenerationRecord(moduleName, previousResult.plan_filename || planFilename, {
      status: "failed",
      error: data.error || previousResult.error || "",
      logs: previousResult.logs || "",
    });
    return { ...previousResult, status: "failed", error: data.error || previousResult.error };
  }

  if (event === "done" && data.ok !== false) {
    const nextResult = {
      ...previousResult,
      status: previousResult.status === "running" ? "succeeded" : previousResult.status,
      plan_filename: data.plan_filename || previousResult.plan_filename || planFilename,
      asset: data.asset || previousResult.asset,
      requirement_module: data.requirement_module || previousResult.requirement_module,
      generation_mode: data.generation_mode || previousResult.generation_mode,
      coverage_profile: data.coverage_profile || previousResult.coverage_profile,
      prompt_customized:
        typeof data.prompt_customized === "boolean" ? data.prompt_customized : previousResult.prompt_customized,
      job_id: data.job_id || previousResult.job_id || "",
      prompt: data.job?.prompt || previousResult.prompt || "",
    };
    setPlanGenerationRecord(moduleName, nextResult.plan_filename || planFilename, {
      status: nextResult.status,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
      target_path: nextResult.target_path || "",
      coverage_profile: nextResult.coverage_profile || "",
      prompt_customized: Boolean(nextResult.prompt_customized),
      job_id: nextResult.job_id || "",
      prompt: nextResult.prompt || "",
    });
    return nextResult;
  }

  return previousResult;
}

function resetScriptGenerationView() {
  state.scriptGeneration.isRunning = false;
  stopPlanScriptGenerationDurationTimer();
  elements.planScriptJobStatus.textContent = "任务进行中";
  elements.planScriptJobStatus.className = "job-status";
  elements.planScriptJobLogs.textContent = "";
  elements.planScriptGenerationSubmit.disabled = false;
  elements.planScriptGenerationSubmit.textContent = "确认生成";
  elements.planScriptJobOutput.classList.add("hidden");
}

function renderScriptPromptFromTemplate(moduleName, planFilename = getDefaultPlanFilename(moduleName)) {
  return replaceAllText(
    replaceAllText(SCRIPT_PROMPT_FIXED_TEMPLATE, "<模块名>", moduleName),
    "<测试计划文件名>",
    planFilename,
  );
}

function getScriptGenerationPrompt() {
  return `${elements.planScriptPromptFixed.value.trim()}\n${elements.planScriptPromptNote.value.trim()}`.trim();
}

function appendScriptJobLog(message) {
  elements.planScriptJobOutput.classList.remove("hidden");
  const current = elements.planScriptJobLogs.textContent;
  const prefix = current && !current.endsWith("\n") ? "\n" : "";
  elements.planScriptJobLogs.textContent += `${prefix}${message}\n`;
  elements.planScriptJobLogs.scrollTop = elements.planScriptJobLogs.scrollHeight;
}

function appendScriptJobDelta(text) {
  if (!text) {
    return;
  }

  elements.planScriptJobOutput.classList.remove("hidden");
  elements.planScriptJobLogs.textContent += text;
  elements.planScriptJobLogs.scrollTop = elements.planScriptJobLogs.scrollHeight;
}

function renderScriptStreamStatus(status, error = "") {
  elements.planScriptJobOutput.classList.remove("hidden");
  elements.planScriptJobStatus.className = "job-status";

  if (status === "succeeded") {
    elements.planScriptJobStatus.textContent = "任务成功";
    elements.planScriptJobStatus.classList.add("success");
    elements.planScriptGenerationSubmit.disabled = state.scriptGeneration.isRunning;
    elements.planScriptGenerationSubmit.textContent = "重新生成";
    return;
  }

  if (status === "failed") {
    elements.planScriptJobStatus.textContent = `任务失败${error ? `：${error}` : ""}`;
    elements.planScriptJobStatus.classList.add("error");
    elements.planScriptGenerationSubmit.disabled = state.scriptGeneration.isRunning;
    elements.planScriptGenerationSubmit.textContent = "重试";
    return;
  }

  elements.planScriptJobStatus.textContent = "任务进行中，正在接收实时输出";
  elements.planScriptGenerationSubmit.disabled = true;
  elements.planScriptGenerationSubmit.textContent = "生成中";
}

function openScriptGenerationModal() {
  if (state.activeSection !== SECTION.PLANS || !state.plans.selectedModule || !state.plans.selectedPlanFile) {
    return;
  }

  if (!confirmDiscardEdit()) {
    return;
  }

  state.isEditing = false;
  state.plans.activeTab = PLAN_VIEW_TAB.SCRIPT_GENERATION;
  ensurePlanScriptGenerationRecord();
  persistViewState();
  renderContent();
  elements.planScriptPromptNote.focus();
}

function closeScriptGenerationModal() {
  if (state.scriptGeneration.isRunning) {
    const confirmed = window.confirm("任务仍在进行中，关闭弹窗后任务会继续在后台执行。是否关闭？");
    if (!confirmed) {
      return;
    }
  }

  elements.scriptGenerationModal.classList.add("hidden");
}

async function submitScriptGeneration() {
  const moduleName = state.plans.selectedModule;
  const planFilename = state.plans.selectedPlanFile;
  const prompt = getScriptGenerationPrompt();

  if (!moduleName || !planFilename || !prompt) {
    return;
  }

  updatePlanScriptGenerationPromptFromInputs();
  const startedAt = Date.now();
  state.scriptGeneration.isRunning = true;
  state.plans.activeTab = PLAN_VIEW_TAB.SCRIPT_GENERATION;
  persistViewState();
  setPlanScriptGenerationRecord(moduleName, planFilename, {
    status: "running",
    prompt_fixed: elements.planScriptPromptFixed.value,
    prompt_note: elements.planScriptPromptNote.value,
    prompt,
    plan_filename: planFilename,
    logs: "",
    error: "",
    target_path: getDefaultScriptTargetPath(moduleName),
    started_at: startedAt,
    finished_at: null,
  });
  elements.planScriptGenerationSubmit.disabled = true;
  elements.planScriptGenerationSubmit.textContent = "提交中";
  elements.planScriptJobOutput.classList.remove("hidden");
  elements.planScriptJobStatus.textContent = "正在提交任务";
  elements.planScriptJobStatus.className = "job-status";
  elements.planScriptJobLogs.textContent = "";
  startPlanScriptGenerationDurationTimer();
  renderContent();

  try {
    const response = await fetch("/api/script-generation-stream", {
      method: "POST",
      headers: getProjectRequestHeaders({
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({
        module_name: moduleName,
        plan_filename: planFilename,
        prompt,
      }),
    });

    if (!response.ok) {
      let data;
      try {
        data = await response.json();
      } catch (error) {
        data = { error: `请求失败: ${response.status}` };
      }
      throw new Error(data.error || `请求失败: ${response.status}`);
    }

    if (!response.body) {
      throw new Error("浏览器不支持读取流式响应。");
    }

    renderScriptStreamStatus("running");

    const result = await readScriptGenerationStream(response, moduleName, planFilename);
    const finishedAt = Date.now();
    state.scriptGeneration.isRunning = false;
    if (result.status !== "succeeded" && result.status !== "failed") {
      result.status = "failed";
      result.error = "流式响应提前结束。";
    }
    setPlanScriptGenerationRecord(moduleName, result.plan_filename || planFilename, {
      status: result.status,
      error: result.error || "",
      logs: result.logs || "",
      target_path: result.target_path || getDefaultScriptTargetPath(moduleName),
      finished_at: finishedAt,
    });
    stopPlanScriptGenerationDurationTimer();
    renderContent();

    if (result.status === "succeeded") {
      try {
        await selectPlan(moduleName, result.plan_filename || planFilename, true);
        state.plans.activeTab = PLAN_VIEW_TAB.SCRIPT_GENERATION;
        persistViewState();
      } catch (error) {
        // 生成成功不因刷新元数据失败而回退。
      }
      setNotice("测试脚本生成完成。", "success");
      return;
    }

    if (result.status === "failed") {
      setNotice(result.error || "测试脚本生成失败。", "error");
      return;
    }

    if (result.status !== "failed") {
      renderScriptStreamStatus("failed", "流式响应提前结束。");
    }
  } catch (error) {
    const finishedAt = Date.now();
    state.scriptGeneration.isRunning = false;
    const current = state.plans.scriptGenerationRecords[getPlanRecordKey(moduleName, planFilename)] || {};
    const prefix = current.logs && !current.logs.endsWith("\n") ? "\n" : "";
    setPlanScriptGenerationRecord(moduleName, planFilename, {
      status: "failed",
      error: error.message,
      logs: `${current.logs || ""}${prefix}${error.message}\n`,
      finished_at: finishedAt,
    });
    stopPlanScriptGenerationDurationTimer();
    renderContent();
    renderScriptStreamStatus("failed", error.message);
  }
}

async function readScriptGenerationStream(response, moduleName, planFilename) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let result = { status: "running", logs: "" };

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
        result = handleScriptStreamEvent(event, result, moduleName, planFilename);
      }
      boundaryIndex = buffer.indexOf("\n\n");
    }
  }

  buffer += decoder.decode();
  const trailingEvent = parseSseBlock(buffer.trim());
  if (trailingEvent) {
    result = handleScriptStreamEvent(trailingEvent, result, moduleName, planFilename);
  }

  return result;
}

function handleScriptStreamEvent({ event, data }, previousResult, moduleName, planFilename) {
  if (event === "status") {
    const nextPlanFilename = data.plan_filename || previousResult.plan_filename || planFilename;
    const nextResult = {
      ...previousResult,
      status: data.status || previousResult.status,
      module_name: data.module_name || previousResult.module_name,
      plan_filename: nextPlanFilename,
      target_path: data.target_path || previousResult.target_path,
      error: data.error || previousResult.error,
    };
    renderScriptStreamStatus(nextResult.status, nextResult.error);
    setPlanScriptGenerationRecord(moduleName, nextPlanFilename, {
      status: nextResult.status,
      plan_filename: nextPlanFilename,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
      target_path: nextResult.target_path || "",
    });
    return nextResult;
  }

  if (event === "log") {
    const text = data.message ? `${data.message}\n` : "";
    appendScriptJobLog(data.message || "");
    const logs = `${previousResult.logs || ""}${text}`;
    setPlanScriptGenerationRecord(moduleName, previousResult.plan_filename || planFilename, { logs });
    return { ...previousResult, logs };
  }

  if (event === "delta") {
    const text = data.text || "";
    appendScriptJobDelta(text);
    const logs = `${previousResult.logs || ""}${text}`;
    setPlanScriptGenerationRecord(moduleName, previousResult.plan_filename || planFilename, { logs });
    return { ...previousResult, logs };
  }

  if (event === "done" && data.ok === false) {
    renderScriptStreamStatus("failed", data.error || "");
    setPlanScriptGenerationRecord(moduleName, previousResult.plan_filename || planFilename, {
      status: "failed",
      error: data.error || previousResult.error || "",
      logs: previousResult.logs || "",
    });
    return { ...previousResult, status: "failed", error: data.error || previousResult.error };
  }

  if (event === "done" && data.ok !== false) {
    const nextResult = {
      ...previousResult,
      status: previousResult.status === "running" ? "succeeded" : previousResult.status,
      plan_filename: data.plan_filename || previousResult.plan_filename || planFilename,
    };
    setPlanScriptGenerationRecord(moduleName, nextResult.plan_filename || planFilename, {
      status: nextResult.status,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
      target_path: nextResult.target_path || "",
    });
    return nextResult;
  }

  return previousResult;
}

async function splitGeneratedPlanCases(moduleName, planFilename) {
  appendPlanJobLog("正在拆分单用例计划。");
  const result = await requestJson(
    `/api/plans/${encodePathPart(moduleName)}/${encodePathPart(planFilename)}/split-cases`,
    {
      method: "POST",
      body: JSON.stringify({ overwrite: false }),
    },
  );
  const created = result.created || [];
  const skipped = result.skipped || [];
  const message = [
    `拆分完成：新增 ${created.length} 个单用例计划。`,
    skipped.length ? `跳过 ${skipped.length} 个已存在或无效计划。` : "",
  ]
    .filter(Boolean)
    .join("");
  appendPlanJobLog(message);
  const current = state.plans.generationRecords[getPlanRecordKey(moduleName, planFilename)] || {};
  const prefix = current.logs && !current.logs.endsWith("\n") ? "\n" : "";
  setPlanGenerationRecord(moduleName, planFilename, {
    logs: `${current.logs || ""}${prefix}${message}\n`,
  });
  setNotice(message, created.length ? "success" : "");
  return result;
}

return {
  resetGenerationJobView,
  appendPlanJobLog,
  appendPlanJobDelta,
  renderPlanStreamStatus,
  renderGenerationTargetPath,
  renderGenerationDuration,
  refreshPlanGenerationDuration,
  startPlanGenerationDurationTimer,
  stopPlanGenerationDurationTimer,
  refreshPlanScriptGenerationDuration,
  startPlanScriptGenerationDurationTimer,
  stopPlanScriptGenerationDurationTimer,
  setPlanGenerationRecord,
  getDefaultPlanScriptGenerationRecord,
  ensurePlanScriptGenerationRecord,
  setPlanScriptGenerationRecord,
  updatePlanScriptGenerationPromptFromInputs,
  renderGenerationPromptFromTemplate,
  getCoverageProfile,
  composeCoveragePrompt,
  replaceCoveragePolicy,
  extractCoveragePolicy,
  populateCoverageSelect,
  renderPlanCoverageState,
  resetPlanPromptForCoverage,
  changePlanCoverageProfile,
  updatePromptForModuleName,
  updateTargetForPlanName,
  updatePlanGenerationMode,
  ensureGenerationDefaults,
  openPlanGenerationModal,
  openRequirementPlanGenerationModal,
  closePlanGenerationModal,
  renderPlanJob,
  pollPlanJob,
  showPlanGenerationRecordTab,
  submitPlanGeneration,
  readPlanGenerationStream,
  handlePlanStreamEvent,
  resetScriptGenerationView,
  renderScriptPromptFromTemplate,
  getScriptGenerationPrompt,
  appendScriptJobLog,
  appendScriptJobDelta,
  renderScriptStreamStatus,
  openScriptGenerationModal,
  closeScriptGenerationModal,
  submitScriptGeneration,
  readScriptGenerationStream,
  handleScriptStreamEvent,
  splitGeneratedPlanCases,
};
}

window.createGenerationFeature = createGenerationFeature;
