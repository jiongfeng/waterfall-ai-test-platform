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
    createClientJobId,
    persistViewState,
    loadScriptTree,
    renderSideList,
    createStatusBadge,
    getGenerationStatusInfo,
    getPlanRecordKey,
    openScriptPreparationRun,
    canOpenScriptPreparation = () => true,
  } = deps;
  const {
    setPlanScriptGenerationRecord,
    renderScriptPromptFromTemplate,
    openScriptGenerationModal,
  } = generation;
  const { formatModuleRepairDuration } = moduleExecution;
  const translate = (key, params = {}, fallback = key) => {
    const translated = window.WaterfallI18n?.t?.(key, params);
    return translated && translated !== key ? translated : fallback;
  };

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
    const status = data.status === "cancelled"
      ? "cancelled"
      : data.ok === false
        ? "failed"
        : previousResult.status === "running"
          ? "succeeded"
          : previousResult.status;
    const nextResult = {
      ...previousResult,
      status,
      error: data.error || previousResult.error,
      plan_filename: data.plan_filename || previousResult.plan_filename || planFilename,
      script_filename:
        data.script_filename ||
        previousResult.script_filename ||
        getExpectedScriptFilenameForPlan(planFilename),
    };
    setPlanScriptGenerationBatchItem(moduleName, planFilename, {
      status: nextResult.status,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
      target_path: nextResult.target_path || "",
      script_filename: nextResult.script_filename,
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
  if (!canOpenScriptPreparation()) {
    setNotice(translate(
      "moduleScriptPreparation.permissionDenied",
      {},
      "当前账号没有访问“脚本”菜单的权限，无法启动脚本准备任务。",
    ), "error");
    return null;
  }
  if (typeof openScriptPreparationRun !== "function") {
    return generateSelectedModulePlanScriptsLegacy(moduleName, planFilenames);
  }
  state.scriptGeneration.isRunning = true;
  renderModulePlanList();
  setNotice(translate(
    "moduleScriptPreparation.creatingNotice",
    {},
    "正在创建脚本准备任务，创建后会自动生成、执行并修复失败脚本。",
  ));
  try {
    const result = await requestJson("/api/script-preparation-runs", {
      method: "POST",
      body: JSON.stringify({
        module_name: moduleName,
        plan_filenames: planFilenames,
        client_request_id: createClientJobId("script-preparation"),
      }),
    });
    state.plans.bulkSelectionMode = false;
    state.plans.selectedPlanFiles.clear();
    await openScriptPreparationRun(result, moduleName);
    setNotice(translate(
      "moduleScriptPreparation.createdNotice",
      {},
      "脚本准备任务已创建，正在自动生成并验证脚本。",
    ), "success");
    return result;
  } catch (error) {
    setNotice(error.message || translate(
      "moduleScriptPreparation.createFailed",
      {},
      "创建脚本准备任务失败。",
    ), "error");
    return null;
  } finally {
    state.scriptGeneration.isRunning = false;
    renderContent();
  }
}

async function generateSelectedModulePlanScriptsLegacy(moduleName, planFilenames) {
  const startedAt = Date.now();
  const items = Object.fromEntries(planFilenames.map((planFilename) => [planFilename, {
    status: "queued", logs: "", error: "", target_path: "", script_filename: "", updated_at: startedAt,
  }]));
  state.scriptGeneration.isRunning = true;
  state.scriptGeneration.cancelRequested = false;
  setPlanScriptGenerationBatch(moduleName, {
    status: "running", plan_filenames: planFilenames, items, started_at: startedAt, finished_at: null,
  });
  let failed = false;
  try {
    for (const planFilename of planFilenames) {
      if (state.scriptGeneration.cancelRequested) {
        break;
      }
      const jobId = createClientJobId("generator");
      const promptFixed = renderScriptPromptFromTemplate(moduleName, planFilename);
      const prompt = `${promptFixed.trim()}\n${SCRIPT_PROMPT_NOTE_DEFAULT.trim()}`.trim();
      setPlanScriptGenerationBatchItem(moduleName, planFilename, { status: "running", started_at: Date.now(), job_id: jobId });
      setPlanScriptGenerationRecord(moduleName, planFilename, {
        status: "running", prompt_fixed: promptFixed, prompt_note: SCRIPT_PROMPT_NOTE_DEFAULT, prompt,
        target_path: getDefaultScriptTargetPath(moduleName), started_at: Date.now(), job_id: jobId,
      });
      try {
        const response = await fetch("/api/script-generation-stream", {
          method: "POST",
          headers: getProjectRequestHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ module_name: moduleName, plan_filename: planFilename, prompt, job_id: jobId }),
        });
        if (!response.ok || !response.body) {
          throw new Error(`请求失败: ${response.status || "stream unavailable"}`);
        }
        const result = await readModulePlanScriptGenerationStream(response, moduleName, planFilename);
        const status = ["succeeded", "failed", "cancelled"].includes(result.status) ? result.status : "failed";
        const updates = {
          status, error: result.error || "", logs: result.logs || "",
          target_path: result.target_path || getDefaultScriptTargetPath(moduleName),
          script_filename: result.script_filename || getExpectedScriptFilenameForPlan(planFilename),
          finished_at: Date.now(),
        };
        setPlanScriptGenerationBatchItem(moduleName, planFilename, updates);
        setPlanScriptGenerationRecord(moduleName, planFilename, updates);
        failed ||= status === "failed";
      } catch (error) {
        failed = true;
        const updates = { status: "failed", error: error.message, logs: `${error.message}\n`, finished_at: Date.now() };
        setPlanScriptGenerationBatchItem(moduleName, planFilename, updates);
        setPlanScriptGenerationRecord(moduleName, planFilename, updates);
      }
    }
    setPlanScriptGenerationBatch(moduleName, {
      status: state.scriptGeneration.cancelRequested ? "cancelled" : failed ? "failed" : "succeeded",
      finished_at: Date.now(),
    });
    await loadScriptTree();
  } finally {
    state.scriptGeneration.isRunning = false;
    state.scriptGeneration.cancelRequested = false;
    renderContent();
  }
}

function markPlanScriptGenerationItemsCancelled(moduleName) {
  const batch = state.plans.scriptGenerationBatches[getPlanModuleRecordKey(moduleName)];
  (batch?.plan_filenames || []).forEach((planFilename) => {
    const item = batch.items?.[planFilename];
    if (item && ["queued", "running", "cancelling"].includes(item.status)) {
      const updates = {
        status: "cancelled",
        error: item.error || "用户终止了生成任务。",
        finished_at: Date.now(),
      };
      setPlanScriptGenerationBatchItem(moduleName, planFilename, updates);
      setPlanScriptGenerationRecord(moduleName, planFilename, updates);
    }
  });
}

async function cancelModulePlanScriptGenerationBatch() {
  if (!state.scriptGeneration.isRunning || state.scriptGeneration.cancelRequested) {
    return;
  }
  if (!window.confirm("确定终止本次生成吗？已产生的日志会保留，未完成的结果不会保存。")) {
    return;
  }

  const moduleName = state.plans.selectedModule;
  const jobId = state.scriptGeneration.currentJobId;
  state.scriptGeneration.cancelRequested = true;
  setPlanScriptGenerationBatch(moduleName, { status: "cancelling" });
  renderContent();
  try {
    if (jobId) {
      await requestJson(`/api/jobs/${encodePathPart(jobId)}/cancel`, {
        method: "POST",
        body: JSON.stringify({}),
      });
    }
    markPlanScriptGenerationItemsCancelled(moduleName);
  } catch (error) {
    state.scriptGeneration.cancelRequested = false;
    setPlanScriptGenerationBatch(moduleName, { status: "running" });
    renderContent();
    setNotice(error.message, "error");
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
  elements.modulePlanBulkGenerate.disabled = selectedCount === 0 || isBusy || !canOpenScriptPreparation();
  elements.modulePlanBulkGenerate.title = canOpenScriptPreparation()
    ? ""
    : translate(
        "moduleScriptPreparation.permissionRequired",
        {},
        "需要“脚本”菜单权限",
      );
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
    checkbox.setAttribute(
      "aria-label",
      `${window.WaterfallI18n?.t?.("action.select") || "Select"} ${
        plan.name || stripMarkdownSuffix(plan.filename)
      }`,
    );
    window.WaterfallI18n?.markDynamicAttributes?.(checkbox);
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
    window.WaterfallI18n?.markDynamic?.(nameButton);
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
    window.WaterfallI18n?.markDynamic?.(scriptCell);
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
    batch?.status === "cancelling"
      ? `正在终止批量生成：已取消 ${statusCounts.cancelled || 0}，成功 ${statusCounts.succeeded || 0}，失败 ${statusCounts.failed || 0}`
      : batch?.status === "running" || isRunningBatch
      ? `批量生成脚本进行中：成功 ${statusCounts.succeeded || 0}，失败 ${statusCounts.failed || 0}，进行中 ${
          statusCounts.running || 0
        }，排队 ${statusCounts.queued || 0}`
      : `批量生成脚本记录：成功 ${statusCounts.succeeded || 0}，失败 ${statusCounts.failed || 0}，取消 ${statusCounts.cancelled || 0}`;
  if (elements.modulePlanScriptBatchCancelButton) {
    const canCancel = state.scriptGeneration.isRunning && ["running", "cancelling"].includes(batch?.status);
    elements.modulePlanScriptBatchCancelButton.classList.toggle("hidden", !canCancel);
    elements.modulePlanScriptBatchCancelButton.disabled = state.scriptGeneration.cancelRequested;
    elements.modulePlanScriptBatchCancelButton.textContent = state.scriptGeneration.cancelRequested
      ? "正在终止…"
      : "终止生成";
  }

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
    window.WaterfallI18n?.markDynamic?.(title);
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
  cancelModulePlanScriptGenerationBatch,
  renderModulePlanList,
  renderModulePlanScriptBatchRecord,
};
}

window.createModulePlanGenerationFeature = createModulePlanGenerationFeature;
