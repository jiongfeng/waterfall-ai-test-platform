function createModulePlanGenerationFeature(deps) {
  const {
    state,
    elements,
    SECTION,
    PLAN_VIEW_TAB,
    SCRIPT_PROMPT_NOTE_DEFAULT,
    document,
    window,
    fetch,
    TextDecoder,
    generation,
    moduleExecution,
    getSelectedPlanModule,
    getGeneratedScriptFilenameFromPlan,
    getSelectedScriptModule,
    requestJson,
    encodePathPart,
    stripMarkdownSuffix,
    loadPlanModules,
    setNotice,
    renderContent,
    selectPlan,
    getPlanModuleRecordKey,
    normalizePlanScriptGenerationBatch,
    persistPlanScriptGenerationBatches,
    parseSseBlock,
    getDefaultScriptTargetPath,
    getProjectRequestHeaders,
    persistViewState,
    loadScriptTree,
    renderSideList,
    createStatusBadge,
    getGenerationStatusInfo,
    getPlanRecordKey,
  } = deps;
  const {
    setPlanScriptGenerationRecord,
    renderScriptPromptFromTemplate,
    openScriptGenerationModal,
  } = generation;
  const { formatModuleRepairDuration } = moduleExecution;

function getCurrentModulePlans() {
  return (getSelectedPlanModule()?.plans || []).filter((plan) => !plan.is_index);
}

function getExpectedScriptFilenameForPlan(planFilename) {
  return planFilename ? getGeneratedScriptFilenameFromPlan(planFilename) : "";
}

function findScriptForPlan(moduleName, planFilename) {
  const expectedFilename = getExpectedScriptFilenameForPlan(planFilename);
  const moduleItem = getSelectedScriptModule(moduleName);
  if (!moduleItem || !expectedFilename) {
    return null;
  }
  return moduleItem.scripts.find((script) => script.name === expectedFilename) || null;
}

function pruneModuleSelectedPlanFiles() {
  const validNames = new Set(getCurrentModulePlans().map((plan) => plan.filename));
  Array.from(state.plans.selectedPlanFiles).forEach((filename) => {
    if (!validNames.has(filename)) {
      state.plans.selectedPlanFiles.delete(filename);
    }
  });
}

function isModulePlanActionBusy() {
  return state.scriptGeneration.isRunning || state.generation.isRunning || state.plans.bulkDeletingPlans;
}

function enterModulePlanBulkMode() {
  if (isModulePlanActionBusy()) {
    return;
  }
  state.plans.bulkSelectionMode = true;
  state.plans.selectedPlanFiles.clear();
  renderModulePlanList();
}

function cancelModulePlanBulkMode() {
  state.plans.bulkSelectionMode = false;
  state.plans.selectedPlanFiles.clear();
  renderModulePlanList();
}

function toggleModulePlanSelectAll() {
  if (!state.plans.bulkSelectionMode || isModulePlanActionBusy()) {
    return;
  }
  const plans = getCurrentModulePlans();
  const shouldSelectAll = state.plans.selectedPlanFiles.size !== plans.length;
  state.plans.selectedPlanFiles.clear();
  if (shouldSelectAll) {
    plans.forEach((plan) => state.plans.selectedPlanFiles.add(plan.filename));
  }
  renderModulePlanList();
}

async function deleteSelectedModulePlans() {
  const moduleName = state.plans.selectedModule;
  const planFilenames = Array.from(state.plans.selectedPlanFiles);
  if (!moduleName || !planFilenames.length || isModulePlanActionBusy()) {
    return;
  }

  if (!window.confirm(`确认删除选中的 ${planFilenames.length} 条测试计划？`)) {
    return;
  }

  state.plans.bulkDeletingPlans = true;
  renderModulePlanList();
  const failures = [];
  const deletedFilenames = new Set();

  try {
    for (const planFilename of planFilenames) {
      try {
        await requestJson(`/api/plans/${encodePathPart(moduleName)}/${encodePathPart(planFilename)}`, {
          method: "DELETE",
        });
        deletedFilenames.add(planFilename);
      } catch (error) {
        failures.push(`${stripMarkdownSuffix(planFilename)}：${error.message}`);
      }
    }

    if (deletedFilenames.has(state.plans.selectedPlanFile)) {
      state.plans.selectedPlanFile = null;
    }
    state.plans.selectedPlanFiles.clear();
    state.plans.bulkSelectionMode = false;
    await loadPlanModules();

    if (failures.length && deletedFilenames.size) {
      setNotice(`已删除 ${deletedFilenames.size} 条测试计划，失败 ${failures.length} 条：${failures.join("；")}`, "error");
    } else if (failures.length) {
      setNotice(`批量删除测试计划失败：${failures.join("；")}`, "error");
    } else {
      setNotice(`已删除 ${deletedFilenames.size} 条测试计划。`, "success");
    }
  } finally {
    state.plans.bulkDeletingPlans = false;
    renderContent();
  }
}

async function generatePlanScriptFromModule(planFilename) {
  if (!state.plans.selectedModule || !planFilename || isModulePlanActionBusy()) {
    return;
  }
  await selectPlan(state.plans.selectedModule, planFilename, true);
  openScriptGenerationModal();
}

function setPlanScriptGenerationBatch(moduleName, updates) {
  const key = getPlanModuleRecordKey(moduleName);
  if (!key) {
    return null;
  }

  const previous = state.plans.scriptGenerationBatches[key] || {
    status: "idle",
    module_name: moduleName,
    plan_filenames: [],
    active_plan_filename: "",
    expanded_plan_filename: "",
    items: {},
    started_at: null,
    finished_at: null,
  };
  const next = normalizePlanScriptGenerationBatch({
    ...previous,
    ...updates,
    module_name: updates.module_name || previous.module_name || moduleName,
    updated_at: Date.now(),
  });
  state.plans.scriptGenerationBatches[key] = next;
  persistPlanScriptGenerationBatches(key);
  if (
    state.activeSection === SECTION.PLANS &&
    state.plans.selectedModule === moduleName &&
    !state.plans.selectedPlanFile &&
    state.plans.activeTab === PLAN_VIEW_TAB.SCRIPT_GENERATION
  ) {
    renderModulePlanScriptBatchRecord();
  }
  return next;
}

function setPlanScriptGenerationBatchItem(moduleName, planFilename, updates) {
  const batch =
    state.plans.scriptGenerationBatches[getPlanModuleRecordKey(moduleName)] ||
    setPlanScriptGenerationBatch(moduleName, { module_name: moduleName, plan_filenames: [planFilename], items: {} });
  const previousItem = batch.items?.[planFilename] || {
    status: "queued",
    logs: "",
    error: "",
    target_path: "",
    script_filename: "",
    started_at: null,
    finished_at: null,
    updated_at: Date.now(),
  };
  const nextItems = {
    ...(batch.items || {}),
    [planFilename]: {
      ...previousItem,
      ...updates,
      logs: Object.prototype.hasOwnProperty.call(updates, "logs") ? updates.logs : previousItem.logs || "",
      updated_at: Date.now(),
    },
  };
  return setPlanScriptGenerationBatch(moduleName, { items: nextItems });
}

function appendPlanScriptBatchLog(moduleName, planFilename, text) {
  if (!text) {
    return;
  }
  const batch = state.plans.scriptGenerationBatches[getPlanModuleRecordKey(moduleName)] || {};
  const item = batch.items?.[planFilename] || {};
  const logs = `${item.logs || ""}${text}`;
  setPlanScriptGenerationBatchItem(moduleName, planFilename, { logs });
  setPlanScriptGenerationRecord(moduleName, planFilename, { logs });
}

async function readModulePlanScriptGenerationStream(response, moduleName, planFilename) {
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
        result = handleModulePlanScriptStreamEvent(event, result, moduleName, planFilename);
      }
      boundaryIndex = buffer.indexOf("\n\n");
    }
  }

  buffer += decoder.decode();
  const trailingEvent = parseSseBlock(buffer.trim());
  if (trailingEvent) {
    result = handleModulePlanScriptStreamEvent(trailingEvent, result, moduleName, planFilename);
  }

  return result;
}

function handleModulePlanScriptStreamEvent({ event, data }, previousResult, moduleName, planFilename) {
  if (event === "status") {
    const nextResult = {
      ...previousResult,
      status: data.status || previousResult.status,
      module_name: data.module_name || previousResult.module_name,
      plan_filename: data.plan_filename || previousResult.plan_filename || planFilename,
      target_path: data.target_path || previousResult.target_path,
      error: data.error || previousResult.error,
    };
    setPlanScriptGenerationBatchItem(moduleName, planFilename, {
      status: nextResult.status,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
      target_path: nextResult.target_path || "",
    });
    setPlanScriptGenerationRecord(moduleName, planFilename, {
      status: nextResult.status,
      plan_filename: planFilename,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
      target_path: nextResult.target_path || "",
    });
    renderModulePlanList();
    return nextResult;
  }

  if (event === "log") {
    const text = data.message ? `${data.message}\n` : "";
    appendPlanScriptBatchLog(moduleName, planFilename, text);
    return { ...previousResult, logs: `${previousResult.logs || ""}${text}` };
  }

  if (event === "delta") {
    const text = data.text || "";
    appendPlanScriptBatchLog(moduleName, planFilename, text);
    return { ...previousResult, logs: `${previousResult.logs || ""}${text}` };
  }

  if (event === "done") {
    const status = data.ok === false ? "failed" : previousResult.status === "running" ? "succeeded" : previousResult.status;
    const nextResult = {
      ...previousResult,
      status,
      error: data.error || previousResult.error,
      plan_filename: data.plan_filename || previousResult.plan_filename || planFilename,
    };
    setPlanScriptGenerationBatchItem(moduleName, planFilename, {
      status: nextResult.status,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
      target_path: nextResult.target_path || "",
      script_filename: getExpectedScriptFilenameForPlan(planFilename),
      finished_at: Date.now(),
    });
    setPlanScriptGenerationRecord(moduleName, planFilename, {
      status: nextResult.status,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
      target_path: nextResult.target_path || "",
      finished_at: Date.now(),
    });
    renderModulePlanList();
    return nextResult;
  }

  return previousResult;
}

async function generateSelectedModulePlanScripts() {
  const moduleName = state.plans.selectedModule;
  const planFilenames = Array.from(state.plans.selectedPlanFiles);
  if (!moduleName || !planFilenames.length || isModulePlanActionBusy()) {
    return;
  }

  const startedAt = Date.now();
  const initialItems = Object.fromEntries(
    planFilenames.map((planFilename) => [
      planFilename,
      {
        status: "queued",
        logs: "",
        error: "",
        target_path: "",
        script_filename: "",
        started_at: null,
        finished_at: null,
        updated_at: Date.now(),
      },
    ]),
  );
  state.scriptGeneration.isRunning = true;
  state.plans.bulkSelectionMode = false;
  state.plans.selectedPlanFiles.clear();
  state.plans.selectedPlanFile = null;
  state.plans.activeTab = PLAN_VIEW_TAB.SCRIPT_GENERATION;
  persistViewState();
  setPlanScriptGenerationBatch(moduleName, {
    status: "running",
    module_name: moduleName,
    plan_filenames: planFilenames,
    active_plan_filename: "",
    expanded_plan_filename: planFilenames[0],
    items: initialItems,
    started_at: startedAt,
    finished_at: null,
  });
  renderContent();
  setNotice("正在批量生成脚本，请稍候。");

  let hasFailure = false;
  const renderTimer = window.setInterval(() => renderModulePlanScriptBatchRecord(), 1000);

  try {
    for (const planFilename of planFilenames) {
      const promptFixed = renderScriptPromptFromTemplate(moduleName, planFilename);
      const promptNote = SCRIPT_PROMPT_NOTE_DEFAULT;
      const prompt = `${promptFixed.trim()}\n${promptNote.trim()}`.trim();
      const itemStartedAt = Date.now();
      setPlanScriptGenerationBatch(moduleName, {
        active_plan_filename: planFilename,
        expanded_plan_filename: planFilename,
      });
      setPlanScriptGenerationBatchItem(moduleName, planFilename, {
        status: "running",
        logs: "",
        error: "",
        started_at: itemStartedAt,
        finished_at: null,
      });
      setPlanScriptGenerationRecord(moduleName, planFilename, {
        status: "running",
        prompt_fixed: promptFixed,
        prompt_note: promptNote,
        prompt,
        plan_filename: planFilename,
        logs: "",
        error: "",
        target_path: getDefaultScriptTargetPath(moduleName),
        started_at: itemStartedAt,
        finished_at: null,
      });
      renderModulePlanList();

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

        const result = await readModulePlanScriptGenerationStream(response, moduleName, planFilename);
        const finishedAt = Date.now();
        if (result.status !== "succeeded" && result.status !== "failed") {
          result.status = "failed";
          result.error = "流式响应提前结束。";
        }
        setPlanScriptGenerationBatchItem(moduleName, planFilename, {
          status: result.status,
          error: result.error || "",
          logs: result.logs || "",
          target_path: result.target_path || getDefaultScriptTargetPath(moduleName),
          script_filename: getExpectedScriptFilenameForPlan(planFilename),
          finished_at: finishedAt,
        });
        setPlanScriptGenerationRecord(moduleName, planFilename, {
          status: result.status,
          error: result.error || "",
          logs: result.logs || "",
          target_path: result.target_path || getDefaultScriptTargetPath(moduleName),
          finished_at: finishedAt,
        });
        if (result.status === "failed") {
          hasFailure = true;
        }
      } catch (error) {
        hasFailure = true;
        const finishedAt = Date.now();
        const batch = state.plans.scriptGenerationBatches[getPlanModuleRecordKey(moduleName)] || {};
        const item = batch.items?.[planFilename] || {};
        const prefix = item.logs && !item.logs.endsWith("\n") ? "\n" : "";
        const logs = `${item.logs || ""}${prefix}${error.message}\n`;
        setPlanScriptGenerationBatchItem(moduleName, planFilename, {
          status: "failed",
          error: error.message,
          logs,
          finished_at: finishedAt,
        });
        setPlanScriptGenerationRecord(moduleName, planFilename, {
          status: "failed",
          error: error.message,
          logs,
          finished_at: finishedAt,
        });
      }
    }

    setPlanScriptGenerationBatch(moduleName, {
      status: hasFailure ? "failed" : "succeeded",
      active_plan_filename: "",
      finished_at: Date.now(),
    });
    await loadScriptTree();
    state.activeSection = SECTION.PLANS;
    state.plans.selectedModule = moduleName;
    state.plans.selectedPlanFile = null;
    state.plans.activeTab = PLAN_VIEW_TAB.SCRIPT_GENERATION;
    persistViewState();
    renderSideList();
    setNotice(hasFailure ? "批量生成脚本完成，存在失败计划。" : "批量生成脚本完成。", hasFailure ? "error" : "success");
  } finally {
    window.clearInterval(renderTimer);
    state.scriptGeneration.isRunning = false;
    renderContent();
  }
}

function renderModulePlanList() {
  const plans = getCurrentModulePlans();
  pruneModuleSelectedPlanFiles();
  const isBulkMode = state.plans.bulkSelectionMode;
  const selectedCount = state.plans.selectedPlanFiles.size;
  const isBusy = isModulePlanActionBusy();

  elements.modulePlanSummary.textContent = `共 ${plans.length} 条单用例计划`;
  elements.modulePlanActions.classList.toggle("hidden", isBulkMode);
  elements.modulePlanBulkActions.classList.toggle("hidden", !isBulkMode);
  elements.modulePlanSelectHeader.classList.toggle("hidden", !isBulkMode);
  elements.modulePlanSelectionCount.textContent = `已选择 ${selectedCount} 条`;
  elements.modulePlanBulkToggle.disabled = !plans.length || isBusy;
  elements.modulePlanBulkGenerate.disabled = selectedCount === 0 || isBusy;
  elements.modulePlanBulkDelete.disabled = selectedCount === 0 || isBusy;
  elements.modulePlanSelectAll.disabled = !plans.length || isBusy;
  elements.modulePlanSelectAll.checked = Boolean(plans.length && selectedCount === plans.length);
  elements.modulePlanSelectAll.indeterminate = selectedCount > 0 && selectedCount < plans.length;

  elements.modulePlanTableBody.replaceChildren();
  if (!plans.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.textContent = "当前模块暂无单用例计划。";
    row.appendChild(cell);
    elements.modulePlanTableBody.appendChild(row);
    return;
  }

  plans.forEach((plan) => {
    const row = document.createElement("tr");

    const selectCell = document.createElement("td");
    selectCell.className = "module-select-cell";
    selectCell.classList.toggle("hidden", !isBulkMode);
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.plans.selectedPlanFiles.has(plan.filename);
    checkbox.disabled = isBusy;
    checkbox.setAttribute("aria-label", `选择 ${plan.name || stripMarkdownSuffix(plan.filename)}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        state.plans.selectedPlanFiles.add(plan.filename);
      } else {
        state.plans.selectedPlanFiles.delete(plan.filename);
      }
      renderModulePlanList();
    });
    selectCell.appendChild(checkbox);
    row.appendChild(selectCell);

    const nameCell = document.createElement("td");
    const nameButton = document.createElement("button");
    nameButton.type = "button";
    nameButton.className = "module-script-name-button";
    nameButton.textContent = plan.name || stripMarkdownSuffix(plan.filename);
    nameButton.title = plan.path || plan.filename;
    nameButton.addEventListener("click", () => selectPlan(state.plans.selectedModule, plan.filename));
    nameCell.appendChild(nameButton);
    row.appendChild(nameCell);

    const generationCell = document.createElement("td");
    const batch = state.plans.scriptGenerationBatches[getPlanModuleRecordKey()];
    const batchItem = batch?.items?.[plan.filename];
    const script = findScriptForPlan(state.plans.selectedModule, plan.filename);
    const generationRecord =
      batchItem ||
      state.plans.scriptGenerationRecords[getPlanRecordKey(state.plans.selectedModule, plan.filename)] ||
      (script ? { status: "succeeded" } : null);
    generationCell.appendChild(createStatusBadge(getGenerationStatusInfo(generationRecord)));
    row.appendChild(generationCell);

    const scriptCell = document.createElement("td");
    scriptCell.textContent = script?.name || getExpectedScriptFilenameForPlan(plan.filename);
    if (!script) {
      scriptCell.className = "muted-cell";
    }
    row.appendChild(scriptCell);

    const actionsCell = document.createElement("td");
    const actions = document.createElement("div");
    actions.className = "module-row-actions";
    const openButton = document.createElement("button");
    openButton.type = "button";
    openButton.className = "secondary-button";
    openButton.textContent = "打开";
    openButton.disabled = isBusy;
    openButton.addEventListener("click", () => selectPlan(state.plans.selectedModule, plan.filename));
    const generateButton = document.createElement("button");
    generateButton.type = "button";
    generateButton.className = "secondary-button";
    generateButton.textContent = "生成脚本";
    generateButton.disabled = isBusy;
    generateButton.addEventListener("click", () => generatePlanScriptFromModule(plan.filename));
    actions.append(openButton, generateButton);
    actionsCell.appendChild(actions);
    row.appendChild(actionsCell);

    elements.modulePlanTableBody.appendChild(row);
  });
}

function renderModulePlanScriptBatchRecord() {
  const batch = state.plans.scriptGenerationBatches[getPlanModuleRecordKey()];
  const planFilenames = batch?.plan_filenames || [];
  const hasBatch = Boolean(batch && planFilenames.length);
  const isRunningBatch = hasBatch && batch.status === "running" && state.scriptGeneration.isRunning;
  const statusCounts = planFilenames.reduce(
    (counts, planFilename) => {
      const status = batch?.items?.[planFilename]?.status || "queued";
      counts[status] = (counts[status] || 0) + 1;
      return counts;
    },
    {},
  );

  elements.modulePlanScriptBatchEmpty.classList.toggle("hidden", hasBatch);
  elements.modulePlanScriptBatchList.classList.toggle("hidden", !hasBatch);
  elements.modulePlanScriptBatchHeader.classList.toggle("hidden", !hasBatch);
  elements.modulePlanScriptBatchSummary.textContent =
    batch?.status === "running" || isRunningBatch
      ? `批量生成脚本进行中：成功 ${statusCounts.succeeded || 0}，失败 ${statusCounts.failed || 0}，进行中 ${
          statusCounts.running || 0
        }，排队 ${statusCounts.queued || 0}`
      : `批量生成脚本记录：成功 ${statusCounts.succeeded || 0}，失败 ${statusCounts.failed || 0}`;

  elements.modulePlanScriptBatchList.replaceChildren();
  if (!hasBatch) {
    return;
  }

  planFilenames.forEach((planFilename) => {
    const item = batch.items?.[planFilename] || { status: "queued", logs: "", error: "" };
    const isExpanded = batch.expanded_plan_filename === planFilename || (!batch.expanded_plan_filename && item.status === "running");
    const wrapper = document.createElement("div");
    wrapper.className = "module-repair-item";

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "module-repair-toggle";
    toggle.setAttribute("aria-expanded", String(isExpanded));
    toggle.addEventListener("click", () => {
      setPlanScriptGenerationBatch(state.plans.selectedModule, {
        expanded_plan_filename: isExpanded ? "" : planFilename,
      });
    });

    const title = document.createElement("span");
    title.className = "module-repair-title";
    title.textContent = stripMarkdownSuffix(planFilename);
    const duration = document.createElement("span");
    duration.className = "module-repair-duration";
    duration.textContent = formatModuleRepairDuration(item);
    const badge = createStatusBadge(getGenerationStatusInfo(item));
    toggle.append(title, duration, badge);
    wrapper.appendChild(toggle);

    if (isExpanded) {
      const output = document.createElement("div");
      output.className = "module-repair-output";
      const pre = document.createElement("pre");
      pre.textContent = item.logs || item.error || "暂无实时输出。";
      output.appendChild(pre);
      wrapper.appendChild(output);
      window.requestAnimationFrame(() => {
        pre.scrollTop = pre.scrollHeight;
      });
    }

    elements.modulePlanScriptBatchList.appendChild(wrapper);
  });
}

return {
  getCurrentModulePlans,
  getExpectedScriptFilenameForPlan,
  findScriptForPlan,
  pruneModuleSelectedPlanFiles,
  isModulePlanActionBusy,
  enterModulePlanBulkMode,
  cancelModulePlanBulkMode,
  toggleModulePlanSelectAll,
  deleteSelectedModulePlans,
  generatePlanScriptFromModule,
  setPlanScriptGenerationBatch,
  setPlanScriptGenerationBatchItem,
  appendPlanScriptBatchLog,
  readModulePlanScriptGenerationStream,
  handleModulePlanScriptStreamEvent,
  generateSelectedModulePlanScripts,
  renderModulePlanList,
  renderModulePlanScriptBatchRecord,
};
}

window.createModulePlanGenerationFeature = createModulePlanGenerationFeature;
