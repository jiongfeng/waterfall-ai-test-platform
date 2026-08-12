function createModuleExecutionFeature(deps) {
  const {
    state,
    elements,
    SECTION,
    SCRIPT_VIEW_TAB,
    EXECUTION_MODE,
    SCRIPT_RUN_PROMPT_NOTE_DEFAULT,
    document,
    window,
    fetch,
    TextDecoder,
    AbortController,
    timers,
    scriptRepair,
    getModuleRecordKey,
    normalizeModuleExecutionRecord,
    persistModuleExecutionRecords,
    normalizeExecutionModeValue,
    normalizeModuleRepairBatch,
    persistModuleRepairBatches,
    persistViewState,
    requestJson,
    encodePathPart,
    setNotice,
    renderContent,
    getSelectedScriptModule,
    loadScriptTree,
    selectScript,
    parseSseBlock,
    openExecutionModeModal,
    getExecutionModeLabel,
    getProjectRequestHeaders,
    stripSpecSuffix,
    getScriptRunRecordKey,
    createStatusBadge,
    getDbResultStatusInfo,
    getDbExecutionModeLabel,
    formatTimestampMs,
  } = deps;
  const {
    setScriptRunRecord,
    setScriptRepairRecord,
    ensureScriptRepairRecord,
    renderScriptRunPromptFromTemplate,
    renderScriptRunDuration,
    formatRepairDuration,
    executeSelectedScript,
    openScriptRepairRecord,
  } = scriptRepair;

function setModuleExecutionRecord(moduleName, updates) {
  const key = getModuleRecordKey(moduleName);
  if (!key) {
    return null;
  }

  const previous = state.scripts.moduleExecutionRecords[key] || {
    status: "idle",
    module_name: moduleName,
    filenames: [],
    execution_mode: EXECUTION_MODE.BATCH,
    command: "",
    logs: "",
    returncode: undefined,
    report: null,
    report_error: "",
    script_results: {},
    error: "",
    started_at: null,
    finished_at: null,
  };
  const next = normalizeModuleExecutionRecord({
    ...previous,
    ...updates,
    module_name: updates.module_name || previous.module_name || moduleName,
    execution_mode: normalizeExecutionModeValue(updates.execution_mode || previous.execution_mode),
    logs: Object.prototype.hasOwnProperty.call(updates, "logs") ? updates.logs : previous.logs || "",
    script_results: updates.script_results || previous.script_results || {},
    updated_at: Date.now(),
  });
  state.scripts.moduleExecutionRecords[key] = next;
  persistModuleExecutionRecords(key);
  refreshModuleExecutionRecordIfCurrent(moduleName);
  return next;
}

function setModuleRepairBatch(moduleName, updates) {
  const key = getModuleRecordKey(moduleName);
  if (!key) {
    return null;
  }

  const previous = state.scripts.moduleRepairBatches[key] || {
    status: "idle",
    module_name: moduleName,
    filenames: [],
    active_filename: "",
    expanded_filename: "",
    items: {},
    started_at: null,
    finished_at: null,
  };
  const next = normalizeModuleRepairBatch({
    ...previous,
    ...updates,
    module_name: updates.module_name || previous.module_name || moduleName,
    items: updates.items || previous.items || {},
    updated_at: Date.now(),
  });
  state.scripts.moduleRepairBatches[key] = next;
  persistModuleRepairBatches(key);
  refreshModuleRepairRecordIfCurrent(moduleName);
  return next;
}

function setModuleRepairItem(moduleName, filename, updates) {
  const currentBatch =
    state.scripts.moduleRepairBatches[getModuleRecordKey(moduleName)] ||
    setModuleRepairBatch(moduleName, { module_name: moduleName, filenames: [filename], items: {} });
  const previousItem = currentBatch.items?.[filename] || {
    status: "queued",
    logs: "",
    error: "",
    started_at: null,
    finished_at: null,
  };
  const nextItems = {
    ...(currentBatch.items || {}),
    [filename]: {
      ...previousItem,
      ...updates,
      logs: Object.prototype.hasOwnProperty.call(updates, "logs") ? updates.logs : previousItem.logs || "",
      updated_at: Date.now(),
    },
  };
  return setModuleRepairBatch(moduleName, { items: nextItems });
}


function refreshModuleExecutionRecordIfCurrent(moduleName) {
  if (
    state.activeSection === SECTION.SCRIPTS &&
    state.scripts.selectedModule === moduleName &&
    !state.scripts.selectedFile &&
    state.scripts.activeTab === SCRIPT_VIEW_TAB.EXECUTION
  ) {
    renderModuleExecutionRecord();
  }
}

function refreshModuleRepairRecordIfCurrent(moduleName) {
  if (
    state.activeSection === SECTION.SCRIPTS &&
    state.scripts.selectedModule === moduleName &&
    !state.scripts.selectedFile &&
    state.scripts.activeTab === SCRIPT_VIEW_TAB.REPAIR
  ) {
    renderModuleRepairRecord();
  }
}


function enterModuleBulkMode() {
  if (!state.scripts.selectedModule || state.scripts.selectedFile || isAnyScriptJobRunning()) {
    return;
  }

  state.scripts.bulkSelectionMode = true;
  state.scripts.selectedFiles.clear();
  renderModuleScriptList();
}

function cancelModuleBulkMode() {
  state.scripts.bulkSelectionMode = false;
  state.scripts.selectedFiles.clear();
  renderModuleScriptList();
}

function createClientJobId(prefix = "job") {
  if (window.crypto?.randomUUID) {
    return `${prefix}-${window.crypto.randomUUID()}`;
  }

  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function isAbortError(error) {
  return error?.name === "AbortError";
}

function appendCancelledLog(logs, message) {
  const current = logs || "";
  if (current.endsWith(`${message}\n`)) {
    return current;
  }
  const prefix = current && !current.endsWith("\n") ? "\n" : "";
  return `${current}${prefix}${message}\n`;
}

function markModuleRepairItemsCancelled(moduleName, filenames = null, message = "已取消批量修复。") {
  const batch = state.scripts.moduleRepairBatches[getModuleRecordKey(moduleName)];
  if (!batch) {
    return;
  }

  const now = Date.now();
  const targetFilenames = filenames || batch.filenames || [];
  const nextItems = { ...(batch.items || {}) };

  targetFilenames.forEach((filename) => {
    const item = nextItems[filename] || {
      status: "queued",
      logs: "",
      error: "",
      started_at: null,
      finished_at: null,
    };
    if (["succeeded", "failed", "cancelled"].includes(item.status)) {
      return;
    }

    const nextItem = {
      ...item,
      status: "cancelled",
      error: message,
      logs: appendCancelledLog(item.logs, message),
      finished_at: now,
      updated_at: now,
    };
    nextItems[filename] = nextItem;
    setScriptRepairRecord(moduleName, filename, {
      status: "cancelled",
      error: message,
      logs: nextItem.logs,
      finished_at: now,
    });
  });

  setModuleRepairBatch(moduleName, {
    status: "cancelled",
    active_filename: "",
    items: nextItems,
    finished_at: now,
  });
  renderModuleScriptList();
}

async function requestCancelCurrentModuleRepairJob() {
  const jobId = state.moduleRepair.currentJobId;
  if (!jobId) {
    return null;
  }

  return requestJson("/api/script-run-cancel", {
    method: "POST",
    body: JSON.stringify({ job_id: jobId }),
  });
}

function cancelModuleRepairBatch() {
  if (!state.moduleRepair.isRunning || state.moduleRepair.cancelRequested) {
    return;
  }

  const moduleName = state.moduleRepair.moduleName || state.scripts.selectedModule;
  if (!moduleName) {
    return;
  }

  state.moduleRepair.cancelRequested = true;
  markModuleRepairItemsCancelled(moduleName);
  setNotice("正在取消模块脚本批量修复。");
  requestCancelCurrentModuleRepairJob().catch((error) => {
    setNotice(error.message || "取消 OpenCode 任务失败。", "error");
  });

  if (state.moduleRepair.currentController) {
    state.moduleRepair.currentController.abort();
  }
  renderContent();
}

function toggleModuleSelectAll() {
  const scripts = getCurrentModuleScripts();
  if (!scripts.length || isAnyScriptJobRunning()) {
    return;
  }

  if (state.scripts.selectedFiles.size === scripts.length) {
    state.scripts.selectedFiles.clear();
  } else {
    state.scripts.selectedFiles = new Set(scripts.map((script) => script.name));
  }
  renderModuleScriptList();
}

async function deleteSelectedModuleScripts() {
  const moduleName = state.scripts.selectedModule;
  const filenames = Array.from(state.scripts.selectedFiles);
  if (!moduleName || !filenames.length || isAnyScriptJobRunning()) {
    return;
  }

  if (!window.confirm(`确认删除选中的 ${filenames.length} 条测试脚本？`)) {
    return;
  }

  state.scripts.bulkDeletingScripts = true;
  renderModuleScriptList();
  const failures = [];
  const deletedFilenames = new Set();

  try {
    for (const filename of filenames) {
      try {
        await requestJson(`/api/test-scripts/${encodePathPart(moduleName)}/${encodePathPart(filename)}`, {
          method: "DELETE",
        });
        deletedFilenames.add(filename);
      } catch (error) {
        failures.push(`${stripSpecSuffix(filename)}：${error.message}`);
      }
    }

    if (deletedFilenames.has(state.scripts.selectedFile)) {
      state.scripts.selectedFile = null;
    }
    state.scripts.selectedFiles.clear();
    state.scripts.bulkSelectionMode = false;
    await loadScriptTree();

    if (failures.length && deletedFilenames.size) {
      setNotice(`已删除 ${deletedFilenames.size} 条测试脚本，失败 ${failures.length} 条：${failures.join("；")}`, "error");
    } else if (failures.length) {
      setNotice(`批量删除测试脚本失败：${failures.join("；")}`, "error");
    } else {
      setNotice(`已删除 ${deletedFilenames.size} 条测试脚本。`, "success");
    }
  } finally {
    state.scripts.bulkDeletingScripts = false;
    renderContent();
  }
}

async function executeScriptFromModule(filename) {
  const moduleName = state.scripts.selectedModule;
  if (!moduleName || isAnyScriptJobRunning()) {
    return;
  }

  await selectScript(moduleName, filename);
  if (state.scripts.selectedModule === moduleName && state.scripts.selectedFile === filename) {
    await executeSelectedScript();
  }
}

async function openScriptRepairFromModule(filename) {
  const moduleName = state.scripts.selectedModule;
  if (!moduleName || isAnyScriptJobRunning()) {
    return;
  }

  await selectScript(moduleName, filename);
  if (state.scripts.selectedModule === moduleName && state.scripts.selectedFile === filename) {
    openScriptRepairRecord();
  }
}

function applyModuleScriptResultsToRunRecords(moduleName, result) {
  const scriptResults = result?.script_results || {};
  Object.entries(scriptResults).forEach(([filename, status]) => {
    setScriptRunRecord(moduleName, filename, {
      status,
      report: result.report,
      report_error: result.report_error,
      returncode: result.returncode,
    });
  });
}

function appendModuleExecutionRecordLog(moduleName, text) {
  if (!text) {
    return;
  }

  const current = state.scripts.moduleExecutionRecords[getModuleRecordKey(moduleName)] || {};
  setModuleExecutionRecord(moduleName, {
    status: current.status || "running",
    command: current.command || "",
    logs: `${current.logs || ""}${text}`,
  });
}

async function readModuleExecutionStream(response, moduleName) {
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
        result = handleModuleExecutionStreamEvent(event, result, moduleName);
      }
      boundaryIndex = buffer.indexOf("\n\n");
    }
  }

  buffer += decoder.decode();
  const trailingEvent = parseSseBlock(buffer.trim());
  if (trailingEvent) {
    result = handleModuleExecutionStreamEvent(trailingEvent, result, moduleName);
  }

  return result;
}

function handleModuleExecutionStreamEvent({ event, data }, previousResult, moduleName) {
  if (event === "status") {
    const nextResult = {
      ...previousResult,
      status: data.status || previousResult.status,
      module_name: data.module_name || previousResult.module_name,
      filenames: data.filenames || previousResult.filenames || [],
      execution_mode: normalizeExecutionModeValue(data.execution_mode || previousResult.execution_mode),
      command: data.command || previousResult.command,
      error: data.error || previousResult.error,
      returncode: Object.prototype.hasOwnProperty.call(data, "returncode") ? data.returncode : previousResult.returncode,
      logs: previousResult.logs || "",
      output: data.output || previousResult.output,
      report: Object.prototype.hasOwnProperty.call(data, "report") ? data.report : previousResult.report,
      report_error: data.report_error || previousResult.report_error,
      script_results: data.script_results || previousResult.script_results || {},
    };
    setModuleExecutionRecord(moduleName, nextResult);
    applyModuleScriptResultsToRunRecords(moduleName, nextResult);
    renderModuleScriptList();
    return nextResult;
  }

  if (event === "log") {
    const text = data.message ? `${data.message}\n` : "";
    appendModuleExecutionRecordLog(moduleName, text);
    return { ...previousResult, logs: `${previousResult.logs || ""}${text}` };
  }

  if (event === "delta") {
    const text = data.text || "";
    appendModuleExecutionRecordLog(moduleName, text);
    return { ...previousResult, logs: `${previousResult.logs || ""}${text}` };
  }

  if (event === "done") {
    const status = data.status || (data.ok === false ? "failed" : "succeeded");
    const nextResult = {
      ...previousResult,
      status,
      error: data.error || previousResult.error,
      returncode: Object.prototype.hasOwnProperty.call(data, "returncode") ? data.returncode : previousResult.returncode,
      output: data.output || previousResult.logs || "",
      logs: previousResult.logs || data.output || "",
      execution_mode: normalizeExecutionModeValue(data.execution_mode || previousResult.execution_mode),
      report: Object.prototype.hasOwnProperty.call(data, "report") ? data.report : previousResult.report,
      report_error: data.report_error || previousResult.report_error,
      script_results: data.script_results || previousResult.script_results || {},
    };
    setModuleExecutionRecord(moduleName, nextResult);
    applyModuleScriptResultsToRunRecords(moduleName, nextResult);
    renderModuleScriptList();
    return nextResult;
  }

  return previousResult;
}

async function executeSelectedModuleScripts() {
  const moduleName = state.scripts.selectedModule;
  const filenames = Array.from(state.scripts.selectedFiles);
  if (!moduleName || !filenames.length || isAnyScriptJobRunning()) {
    return;
  }

  const executionMode = await openExecutionModeModal({
    title: "选择模块批量执行模式",
    summary: `将执行 ${filenames.length} 个脚本。默认模式保持现有批量执行行为。`,
    target: "module",
  });
  if (!executionMode) {
    return;
  }
  if (isAnyScriptJobRunning()) {
    return;
  }

  const startedAt = Date.now();
  state.moduleExecution.isRunning = true;
  state.scripts.bulkSelectionMode = false;
  state.scripts.selectedFiles.clear();
  state.scripts.selectedFile = null;
  state.scripts.activeTab = SCRIPT_VIEW_TAB.EXECUTION;
  persistViewState();
  setModuleExecutionRecord(moduleName, {
    status: "running",
    module_name: moduleName,
    filenames,
    execution_mode: executionMode,
    command: "",
    logs: "",
    returncode: undefined,
    report: null,
    report_error: "正在执行所选脚本，执行完成后会自动显示本次 Playwright Report。",
    script_results: Object.fromEntries(filenames.map((filename) => [filename, "running"])),
    error: "",
    started_at: startedAt,
    finished_at: null,
  });
  filenames.forEach((filename) => {
    setScriptRunRecord(moduleName, filename, {
      status: "running",
      report: null,
      report_error: "正在通过模块批量执行运行脚本。",
    });
  });
  renderContent();
  setNotice(`正在${getExecutionModeLabel(executionMode)}，请稍候。`);

  try {
    const response = await fetch("/api/module-script-execution-stream", {
      method: "POST",
      headers: getProjectRequestHeaders({
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({
        module_name: moduleName,
        filenames,
        execution_mode: executionMode,
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

    const result = await readModuleExecutionStream(response, moduleName);
    const finishedAt = Date.now();
    if (result.status !== "succeeded" && result.status !== "failed") {
      result.status = "failed";
      result.error = "流式响应提前结束。";
    }
    setModuleExecutionRecord(moduleName, {
      status: result.status,
      error: result.error || "",
      logs: result.logs || "",
      report: result.report,
      report_error: result.report_error || "",
      script_results: result.script_results || {},
      execution_mode: result.execution_mode || executionMode,
      returncode: result.returncode,
      finished_at: finishedAt,
    });
    applyModuleScriptResultsToRunRecords(moduleName, result);

    if (result.status === "succeeded") {
      setNotice(result.report ? "模块脚本批量执行完成，Playwright Report 已更新。" : "模块脚本批量执行完成，未找到 Playwright Report。", result.report ? "success" : "");
    } else {
      setNotice(result.error || "模块脚本批量执行失败。", "error");
    }
  } catch (error) {
    const finishedAt = Date.now();
    const current = state.scripts.moduleExecutionRecords[getModuleRecordKey(moduleName)] || {};
    const prefix = current.logs && !current.logs.endsWith("\n") ? "\n" : "";
    const failedResults = Object.fromEntries(filenames.map((filename) => [filename, "failed"]));
    setModuleExecutionRecord(moduleName, {
      status: "failed",
      error: error.message,
      logs: `${current.logs || ""}${prefix}${error.message}\n`,
      script_results: failedResults,
      execution_mode: executionMode,
      finished_at: finishedAt,
    });
    applyModuleScriptResultsToRunRecords(moduleName, { script_results: failedResults, report: null, report_error: error.message });
    setNotice(error.message, "error");
  } finally {
    state.moduleExecution.isRunning = false;
    renderContent();
  }
}


function appendModuleRepairJobLog(moduleName, filename, text) {
  if (!text) {
    return;
  }

  const batch = state.scripts.moduleRepairBatches[getModuleRecordKey(moduleName)] || {};
  const item = batch.items?.[filename] || {};
  const logs = `${item.logs || ""}${text}`;
  setModuleRepairItem(moduleName, filename, { logs });
  setScriptRepairRecord(moduleName, filename, { logs });
}

async function readModuleRepairScriptStream(response, moduleName, filename) {
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
        result = handleModuleRepairStreamEvent(event, result, moduleName, filename);
      }
      boundaryIndex = buffer.indexOf("\n\n");
    }
  }

  buffer += decoder.decode();
  const trailingEvent = parseSseBlock(buffer.trim());
  if (trailingEvent) {
    result = handleModuleRepairStreamEvent(trailingEvent, result, moduleName, filename);
  }

  return result;
}

function handleModuleRepairStreamEvent({ event, data }, previousResult, moduleName, filename) {
  if (event === "status") {
    const nextResult = {
      ...previousResult,
      status: data.status || previousResult.status,
      module_name: data.module_name || previousResult.module_name,
      target_path: data.target_path || previousResult.target_path,
      error: data.error || previousResult.error,
      returncode: Object.prototype.hasOwnProperty.call(data, "returncode") ? data.returncode : previousResult.returncode,
      video: Object.prototype.hasOwnProperty.call(data, "video") ? data.video : previousResult.video,
      video_error: data.video_error || previousResult.video_error,
      report: Object.prototype.hasOwnProperty.call(data, "report") ? data.report : previousResult.report,
      report_error: data.report_error || previousResult.report_error,
    };
    setModuleRepairItem(moduleName, filename, {
      status: nextResult.status,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
    });
    setScriptRepairRecord(moduleName, filename, {
      status: nextResult.status,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
      target_path: nextResult.target_path || "",
    });
    renderModuleScriptList();
    return nextResult;
  }

  if (event === "log") {
    const text = data.message ? `${data.message}\n` : "";
    appendModuleRepairJobLog(moduleName, filename, text);
    return { ...previousResult, logs: `${previousResult.logs || ""}${text}` };
  }

  if (event === "delta") {
    const text = data.text || "";
    appendModuleRepairJobLog(moduleName, filename, text);
    return { ...previousResult, logs: `${previousResult.logs || ""}${text}` };
  }

  if (event === "done") {
    const status = data.status || (data.ok === false ? "failed" : "succeeded");
    const nextResult = {
      ...previousResult,
      status,
      error: data.error || previousResult.error,
      returncode: Object.prototype.hasOwnProperty.call(data, "returncode") ? data.returncode : previousResult.returncode,
      video: Object.prototype.hasOwnProperty.call(data, "video") ? data.video : previousResult.video,
      video_error: data.video_error || previousResult.video_error,
      report: Object.prototype.hasOwnProperty.call(data, "report") ? data.report : previousResult.report,
      report_error: data.report_error || previousResult.report_error,
    };
    setModuleRepairItem(moduleName, filename, {
      status: nextResult.status,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
      finished_at: Date.now(),
    });
    setScriptRepairRecord(moduleName, filename, {
      status: nextResult.status,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
      target_path: nextResult.target_path || "",
      finished_at: Date.now(),
    });
    renderModuleScriptList();
    return nextResult;
  }

  return previousResult;
}

async function repairSelectedModuleScripts() {
  const moduleName = state.scripts.selectedModule;
  const filenames = Array.from(state.scripts.selectedFiles);
  if (!moduleName || !filenames.length || isAnyScriptJobRunning()) {
    return;
  }

  const startedAt = Date.now();
  const initialItems = Object.fromEntries(
    filenames.map((filename) => [
      filename,
      {
        status: "queued",
        logs: "",
        error: "",
        started_at: null,
        finished_at: null,
        updated_at: Date.now(),
      },
    ]),
  );
  state.moduleRepair.isRunning = true;
  state.moduleRepair.cancelRequested = false;
  state.moduleRepair.currentController = null;
  state.moduleRepair.currentJobId = "";
  state.moduleRepair.activeFilename = "";
  state.moduleRepair.moduleName = moduleName;
  state.scripts.bulkSelectionMode = false;
  state.scripts.selectedFiles.clear();
  state.scripts.selectedFile = null;
  state.scripts.activeTab = SCRIPT_VIEW_TAB.REPAIR;
  persistViewState();
  setModuleRepairBatch(moduleName, {
    status: "running",
    module_name: moduleName,
    filenames,
    active_filename: "",
    expanded_filename: filenames[0],
    items: initialItems,
    started_at: startedAt,
    finished_at: null,
  });
  renderContent();
  setNotice("正在批量修复脚本，请稍候。");

  let hasFailure = false;
  let wasCancelled = false;
  const renderTimer = timers.setInterval(() => refreshModuleRepairRecordIfCurrent(moduleName), 1000);

  try {
    for (const [index, filename] of filenames.entries()) {
      if (state.moduleRepair.cancelRequested) {
        wasCancelled = true;
        markModuleRepairItemsCancelled(moduleName, filenames.slice(index));
        break;
      }

      const promptFixed = renderScriptRunPromptFromTemplate(moduleName, filename);
      const promptNote = SCRIPT_RUN_PROMPT_NOTE_DEFAULT;
      const prompt = `${promptFixed.trim()}\n${promptNote.trim()}`.trim();
      const itemStartedAt = Date.now();
      setModuleRepairBatch(moduleName, { active_filename: filename, expanded_filename: filename });
      setModuleRepairItem(moduleName, filename, {
        status: "running",
        logs: "",
        error: "",
        started_at: itemStartedAt,
        finished_at: null,
      });
      setScriptRepairRecord(moduleName, filename, {
        status: "running",
        prompt_fixed: promptFixed,
        prompt_note: promptNote,
        prompt,
        logs: "",
        error: "",
        started_at: itemStartedAt,
        finished_at: null,
      });
      renderModuleScriptList();

      const jobId = createClientJobId("module-repair");
      const controller = new AbortController();
      state.moduleRepair.currentController = controller;
      state.moduleRepair.currentJobId = jobId;
      state.moduleRepair.activeFilename = filename;

      try {
        const response = await fetch("/api/script-run-stream", {
          method: "POST",
          headers: getProjectRequestHeaders({
            "Content-Type": "application/json",
          }),
          signal: controller.signal,
          body: JSON.stringify({
            module_name: moduleName,
            filename,
            prompt,
            job_id: jobId,
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

        const result = await readModuleRepairScriptStream(response, moduleName, filename);
        const finishedAt = Date.now();
        if (state.moduleRepair.cancelRequested && result.status !== "cancelled") {
          result.status = "cancelled";
          result.error = "已取消批量修复。";
          result.logs = appendCancelledLog(result.logs, result.error);
        }
        if (!["succeeded", "failed", "cancelled"].includes(result.status)) {
          result.status = "failed";
          result.error = "流式响应提前结束。";
        }
        setModuleRepairItem(moduleName, filename, {
          status: result.status,
          error: result.error || "",
          logs: result.logs || "",
          finished_at: finishedAt,
        });
        setScriptRepairRecord(moduleName, filename, {
          status: result.status,
          error: result.error || "",
          logs: result.logs || "",
          target_path: result.target_path || "",
          finished_at: finishedAt,
        });
        if (result.status !== "cancelled") {
          setScriptRunRecord(moduleName, filename, {
            status: result.status,
            error: result.error,
            video: result.video,
            video_error: result.video_error,
            report: result.report,
            report_error: result.report_error,
            returncode: result.returncode,
          });
        }
        if (result.status === "failed") {
          hasFailure = true;
        }
        if (result.status === "cancelled") {
          wasCancelled = true;
          markModuleRepairItemsCancelled(moduleName, filenames.slice(index + 1));
          break;
        }
      } catch (error) {
        const finishedAt = Date.now();
        if (state.moduleRepair.cancelRequested || isAbortError(error)) {
          wasCancelled = true;
          markModuleRepairItemsCancelled(moduleName, filenames.slice(index));
          const batch = state.scripts.moduleRepairBatches[getModuleRecordKey(moduleName)] || {};
          const item = batch.items?.[filename] || {};
          setScriptRepairRecord(moduleName, filename, {
            status: "cancelled",
            error: item.error || "已取消批量修复。",
            logs: item.logs || "已取消批量修复。\n",
            finished_at: finishedAt,
          });
          break;
        }

        hasFailure = true;
        const batch = state.scripts.moduleRepairBatches[getModuleRecordKey(moduleName)] || {};
        const item = batch.items?.[filename] || {};
        const prefix = item.logs && !item.logs.endsWith("\n") ? "\n" : "";
        const logs = `${item.logs || ""}${prefix}${error.message}\n`;
        setModuleRepairItem(moduleName, filename, {
          status: "failed",
          error: error.message,
          logs,
          finished_at: finishedAt,
        });
        setScriptRepairRecord(moduleName, filename, {
          status: "failed",
          error: error.message,
          logs,
          finished_at: finishedAt,
        });
      } finally {
        if (state.moduleRepair.currentJobId === jobId) {
          state.moduleRepair.currentController = null;
          state.moduleRepair.currentJobId = "";
          state.moduleRepair.activeFilename = "";
        }
      }
    }

    const finalBatch = state.scripts.moduleRepairBatches[getModuleRecordKey(moduleName)] || {};
    const finalItems = finalBatch.items || {};
    const finalStatuses = filenames.map((filename) => finalItems[filename]?.status).filter(Boolean);
    wasCancelled = wasCancelled || finalStatuses.includes("cancelled") || state.moduleRepair.cancelRequested;
    hasFailure = hasFailure || finalStatuses.includes("failed");
    setModuleRepairBatch(moduleName, {
      status: wasCancelled ? "cancelled" : hasFailure ? "failed" : "succeeded",
      active_filename: "",
      finished_at: Date.now(),
    });
    if (wasCancelled) {
      setNotice("模块脚本批量修复已取消。");
    } else {
      setNotice(hasFailure ? "模块脚本批量修复完成，存在失败脚本。" : "模块脚本批量修复完成。", hasFailure ? "error" : "success");
    }
  } finally {
    timers.clearInterval(renderTimer);
    state.moduleRepair.isRunning = false;
    state.moduleRepair.cancelRequested = false;
    state.moduleRepair.currentController = null;
    state.moduleRepair.currentJobId = "";
    state.moduleRepair.activeFilename = "";
    state.moduleRepair.moduleName = "";
    renderContent();
  }
}


function renderExecutionHistory() {
  const results = state.scripts.recentResults || [];
  const selectedRunId = state.scripts.selectedExecutionRunId || "";
  elements.executionHistoryPanel.classList.toggle("hidden", !results.length);
  elements.executionHistorySummary.textContent = results.length ? `最近 ${results.length} 次执行记录` : "最近执行记录";
  elements.executionHistoryTableBody.replaceChildren();

  results.forEach((result) => {
    const row = document.createElement("tr");
    const isSelected = Boolean(result.run_id && result.run_id === selectedRunId);
    row.className = `execution-history-row${isSelected ? " selected" : ""}`;
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-selected", String(isSelected));
    const selectResult = () => {
      if (!result.run_id || state.scripts.selectedExecutionRunId === result.run_id) {
        return;
      }
      state.scripts.selectedExecutionRunId = result.run_id;
      renderExecutionHistory();
      renderExecutionRecord();
    };
    row.addEventListener("click", selectResult);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectResult();
      }
    });
    const statusCell = document.createElement("td");
    statusCell.appendChild(createStatusBadge(getDbResultStatusInfo(result.status)));
    row.appendChild(statusCell);

    const modeCell = document.createElement("td");
    modeCell.textContent = getDbExecutionModeLabel(result.execution_mode);
    row.appendChild(modeCell);

    const timeCell = document.createElement("td");
    timeCell.textContent = formatTimestampMs(result.finished_at || result.updated_at || result.started_at);
    row.appendChild(timeCell);

    const revisionCell = document.createElement("td");
    revisionCell.textContent = result.script_revision_id ? `#${result.script_revision_id}` : "-";
    row.appendChild(revisionCell);

    const runCell = document.createElement("td");
    runCell.textContent = result.run_id || "-";
    runCell.title = result.error_message || result.stdout_tail || "";
    row.appendChild(runCell);

    elements.executionHistoryTableBody.appendChild(row);
  });
}

function getExecutionStatusInfo(record) {
  if (record?.status === "running") {
    return { label: "执行中", className: "running" };
  }
  if (record?.status === "cancelled") {
    return { label: "已取消", className: "cancelled" };
  }
  if (record?.status === "succeeded") {
    return { label: "成功", className: "success" };
  }
  if (record?.status === "failed") {
    return { label: "失败", className: "error" };
  }
  return { label: "未执行", className: "" };
}

function getRepairStatusInfo(recordOrItem) {
  if (recordOrItem?.status === "queued") {
    return { label: "排队", className: "queued" };
  }
  if (recordOrItem?.status === "running") {
    return { label: "进行中", className: "running" };
  }
  if (recordOrItem?.status === "cancelled") {
    return { label: "已取消", className: "cancelled" };
  }
  if (recordOrItem?.status === "succeeded") {
    return { label: "成功", className: "success" };
  }
  if (recordOrItem?.status === "failed") {
    return { label: "失败", className: "error" };
  }
  return { label: "未修复", className: "" };
}

function getCurrentModuleScripts() {
  return getSelectedScriptModule()?.scripts || [];
}

function pruneModuleSelectedFiles() {
  const validNames = new Set(getCurrentModuleScripts().map((script) => script.name));
  Array.from(state.scripts.selectedFiles).forEach((filename) => {
    if (!validNames.has(filename)) {
      state.scripts.selectedFiles.delete(filename);
    }
  });
}

function isAnyScriptJobRunning() {
  return (
    state.scriptGeneration.isRunning ||
    state.generation.isRunning ||
    state.scriptRecording.isRunning ||
    state.scriptExecution.isRunning ||
    state.moduleExecution.isRunning ||
    state.scriptRun.isRunning ||
    state.moduleRepair.isRunning ||
    state.testSuiteExecution.isRunning ||
    state.requirements.analysisRunning ||
    state.requirements.bulkDeletingModules ||
    state.scripts.bulkDeletingScripts ||
    state.requirements.planGenerationRunning ||
    state.project.isExporting ||
    state.project.isImporting
  );
}

function renderModuleScriptList() {
  const scripts = getCurrentModuleScripts();
  pruneModuleSelectedFiles();
  const isBulkMode = state.scripts.bulkSelectionMode;
  const selectedCount = state.scripts.selectedFiles.size;
  const isBusy = isAnyScriptJobRunning();

  elements.moduleScriptSummary.textContent = `共 ${scripts.length} 条脚本`;
  elements.moduleScriptActions.classList.toggle("hidden", isBulkMode);
  elements.moduleBulkActions.classList.toggle("hidden", !isBulkMode);
  elements.moduleSelectHeader.classList.toggle("hidden", !isBulkMode);
  elements.moduleSelectionCount.textContent = `已选择 ${selectedCount} 条`;
  elements.moduleBulkToggle.disabled = !scripts.length || isBusy;
  elements.moduleBulkExecute.disabled = selectedCount === 0 || isBusy;
  elements.moduleBulkRepair.disabled = selectedCount === 0 || isBusy;
  elements.moduleBulkDelete.disabled = selectedCount === 0 || isBusy;
  elements.moduleSelectAll.disabled = !scripts.length || isBusy;
  elements.moduleSelectAll.checked = Boolean(scripts.length && selectedCount === scripts.length);
  elements.moduleSelectAll.indeterminate = selectedCount > 0 && selectedCount < scripts.length;

  elements.moduleScriptTableBody.replaceChildren();
  if (!scripts.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.textContent = "当前模块暂无脚本。";
    row.appendChild(cell);
    elements.moduleScriptTableBody.appendChild(row);
    return;
  }

  scripts.forEach((script) => {
    const row = document.createElement("tr");

    const selectCell = document.createElement("td");
    selectCell.className = "module-select-cell";
    selectCell.classList.toggle("hidden", !isBulkMode);
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.scripts.selectedFiles.has(script.name);
    checkbox.disabled = isBusy;
    checkbox.setAttribute(
      "aria-label",
      `${window.WaterfallI18n?.t?.("action.select") || "Select"} ${
        script.display_name || stripSpecSuffix(script.name)
      }`,
    );
    window.WaterfallI18n?.markDynamicAttributes?.(checkbox);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        state.scripts.selectedFiles.add(script.name);
      } else {
        state.scripts.selectedFiles.delete(script.name);
      }
      renderModuleScriptList();
    });
    selectCell.appendChild(checkbox);
    row.appendChild(selectCell);

    const nameCell = document.createElement("td");
    const nameButton = document.createElement("button");
    nameButton.type = "button";
    nameButton.className = "module-script-name-button";
    nameButton.textContent = script.display_name || stripSpecSuffix(script.name);
    nameButton.title = script.path || script.name;
    window.WaterfallI18n?.markDynamic?.(nameButton);
    nameButton.addEventListener("click", () => selectScript(state.scripts.selectedModule, script.name));
    nameCell.appendChild(nameButton);
    row.appendChild(nameCell);

    const executionCell = document.createElement("td");
    executionCell.appendChild(createStatusBadge(getExecutionStatusInfo(state.scripts.runRecords[getScriptRunRecordKey(state.scripts.selectedModule, script.name)])));
    row.appendChild(executionCell);

    const repairCell = document.createElement("td");
    const batch = state.scripts.moduleRepairBatches[getModuleRecordKey()];
    const batchItem = batch?.items?.[script.name];
    const repairRecord = batchItem || state.scripts.repairRecords[getScriptRunRecordKey(state.scripts.selectedModule, script.name)];
    repairCell.appendChild(createStatusBadge(getRepairStatusInfo(repairRecord)));
    row.appendChild(repairCell);

    const actionsCell = document.createElement("td");
    const actions = document.createElement("div");
    actions.className = "module-row-actions";
    const executeButton = document.createElement("button");
    executeButton.type = "button";
    executeButton.className = "secondary-button";
    executeButton.textContent = "执行";
    executeButton.disabled = isBusy;
    executeButton.addEventListener("click", () => executeScriptFromModule(script.name));
    const repairButton = document.createElement("button");
    repairButton.type = "button";
    repairButton.className = "secondary-button";
    repairButton.textContent = "修复";
    repairButton.disabled = isBusy;
    repairButton.addEventListener("click", () => openScriptRepairFromModule(script.name));
    actions.append(executeButton, repairButton);
    actionsCell.appendChild(actions);
    row.appendChild(actionsCell);

    elements.moduleScriptTableBody.appendChild(row);
  });
}

function renderModuleExecutionRecord() {
  const record = state.scripts.moduleExecutionRecords[getModuleRecordKey()];
  const report = record?.report;
  const command = record?.command || "";
  const logs = record?.logs || "";
  const hasExecutionLogs = Boolean(command || logs);

  elements.moduleExecutionEmpty.classList.toggle("hidden", Boolean(report || hasExecutionLogs));
  elements.moduleExecutionLogPanel.classList.toggle("hidden", !hasExecutionLogs);
  elements.moduleExecutionReportWrap.classList.toggle("hidden", !report);

  if (hasExecutionLogs) {
    const commandLine = command ? `$ ${command}` : "";
    elements.moduleExecutionLog.textContent = [commandLine, logs].filter(Boolean).join("\n");
    elements.moduleExecutionLogStatus.textContent =
      record?.status === "running"
        ? "执行中"
        : record?.status === "failed"
          ? "执行失败"
          : record?.status === "succeeded"
            ? "执行完成"
            : "";
    elements.moduleExecutionLog.scrollTop = elements.moduleExecutionLog.scrollHeight;
  } else {
    elements.moduleExecutionLog.textContent = "";
    elements.moduleExecutionLogStatus.textContent = "";
  }

  if (report) {
    const reportUrl = report.url || "";
    if (elements.moduleExecutionReportFrame.getAttribute("src") !== reportUrl) {
      elements.moduleExecutionReportFrame.src = reportUrl;
    }
    elements.moduleExecutionReportLink.href = reportUrl || "#";
    elements.moduleExecutionReportPath.textContent = report.path || report.relative_path || "";
  } else {
    elements.moduleExecutionReportFrame.removeAttribute("src");
    elements.moduleExecutionReportLink.href = "#";
    elements.moduleExecutionReportPath.textContent = "";
  }

  let emptyTitle = "暂无模块执行记录";
  if (record?.status === "running") {
    emptyTitle = "正在批量执行脚本";
  } else if (record?.status === "failed") {
    emptyTitle = "批量执行失败";
  } else if (record?.status === "succeeded") {
    emptyTitle = "未找到 Playwright Report";
  }
  elements.moduleExecutionEmpty.querySelector("h3").textContent = emptyTitle;
  elements.moduleExecutionEmpty.querySelector("p").textContent =
    record?.report_error || "在脚本 tab 选择脚本并批量执行后，这里会展示模块执行日志和 Playwright Report。";
}

function formatModuleRepairDuration(item) {
  if (!item?.started_at) {
    return "";
  }
  const finishedAt = item.finished_at || (item.status === "running" ? Date.now() : item.updated_at);
  const duration = formatRepairDuration(finishedAt - item.started_at);
  const key = item.status === "running" ? "duration.elapsed" : "duration.total";
  const fallback = item.status === "running" ? `进行时间：${duration}` : `耗时：${duration}`;
  const translated = window.WaterfallI18n?.t?.(key, { duration });
  return translated && translated !== key ? translated : fallback;
}

function renderModuleRepairRecord() {
  const batch = state.scripts.moduleRepairBatches[getModuleRecordKey()];
  const filenames = batch?.filenames || [];
  const hasBatch = Boolean(batch && filenames.length);
  const activeRepairModule = state.moduleRepair.moduleName || state.scripts.selectedModule;
  const isActiveBatch = hasBatch && activeRepairModule === (batch.module_name || state.scripts.selectedModule);
  const isRunningBatch =
    isActiveBatch &&
    batch.status === "running" &&
    state.moduleRepair.isRunning;
  const statusCounts = filenames.reduce(
    (counts, filename) => {
      const status = batch?.items?.[filename]?.status || "queued";
      counts[status] = (counts[status] || 0) + 1;
      return counts;
    },
    {},
  );
  elements.moduleRepairEmpty.classList.toggle("hidden", hasBatch);
  elements.moduleRepairList.classList.toggle("hidden", !hasBatch);
  elements.moduleRepairRecordHeader.classList.toggle("hidden", !hasBatch);
  elements.moduleRepairCancelButton.classList.toggle(
    "hidden",
    !isActiveBatch || (!isRunningBatch && !state.moduleRepair.cancelRequested),
  );
  elements.moduleRepairCancelButton.disabled = !isRunningBatch || state.moduleRepair.cancelRequested;
  elements.moduleRepairCancelButton.textContent = state.moduleRepair.cancelRequested ? "取消中" : "取消修复";
  elements.moduleRepairSummary.textContent =
    batch?.status === "running"
      ? `批量修复进行中：成功 ${statusCounts.succeeded || 0}，失败 ${statusCounts.failed || 0}，进行中 ${
          statusCounts.running || 0
        }，排队 ${statusCounts.queued || 0}`
      : batch?.status === "cancelled"
        ? `批量修复已取消：已取消 ${statusCounts.cancelled || 0}，成功 ${statusCounts.succeeded || 0}，失败 ${
            statusCounts.failed || 0
          }`
        : `批量修复记录：成功 ${statusCounts.succeeded || 0}，失败 ${statusCounts.failed || 0}，已取消 ${
            statusCounts.cancelled || 0
          }`;
  elements.moduleRepairList.replaceChildren();

  if (!hasBatch) {
    return;
  }

  filenames.forEach((filename) => {
    const item = batch.items?.[filename] || { status: "queued", logs: "", error: "" };
    const isExpanded = batch.expanded_filename === filename || (!batch.expanded_filename && item.status === "running");
    const wrapper = document.createElement("div");
    wrapper.className = "module-repair-item";

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "module-repair-toggle";
    toggle.setAttribute("aria-expanded", String(isExpanded));
    toggle.addEventListener("click", () => {
      setModuleRepairBatch(state.scripts.selectedModule, {
        expanded_filename: isExpanded ? "" : filename,
      });
    });

    const title = document.createElement("span");
    title.className = "module-repair-title";
    title.textContent = stripSpecSuffix(filename);
    window.WaterfallI18n?.markDynamic?.(title);
    const duration = document.createElement("span");
    duration.className = "module-repair-duration";
    duration.textContent = formatModuleRepairDuration(item);
    const badge = createStatusBadge(getRepairStatusInfo(item));
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

    elements.moduleRepairList.appendChild(wrapper);
  });
}

function renderExecutionRecord() {
  const liveRecord = state.scripts.runRecords[getScriptRunRecordKey()];
  const selectedRunId = state.scripts.selectedExecutionRunId || "";
  const historicalResult = (state.scripts.recentResults || []).find(
    (result) => result.run_id && result.run_id === selectedRunId,
  );
  const record = historicalResult
    ? {
        status: historicalResult.status,
        run_id: historicalResult.run_id,
        result_id: historicalResult.result_id,
        command: historicalResult.command,
        logs: historicalResult.stdout_tail,
        report: historicalResult.report,
        video: historicalResult.video,
        video_error: historicalResult.error_message,
      }
    : liveRecord;
  const video = record?.video;
  const report = record?.report;
  const command = record?.command || "";
  const logs = record?.logs || record?.output || "";
  const hasExecutionLogs = Boolean(command || logs);

  elements.executionEmpty.classList.toggle("hidden", Boolean(video || report || hasExecutionLogs));
  elements.executionLogPanel.classList.toggle("hidden", !hasExecutionLogs);
  elements.executionReportWrap.classList.toggle("hidden", !report);
  elements.executionVideoWrap.classList.toggle("hidden", !video);

  if (hasExecutionLogs) {
    const commandLine = command ? `$ ${command}` : "";
    const logText = [commandLine, logs].filter(Boolean).join("\n");
    elements.executionLog.textContent = logText;
    elements.executionLogStatus.textContent = getDbResultStatusInfo(record?.status).label;
    elements.executionLog.scrollTop = elements.executionLog.scrollHeight;
  } else {
    elements.executionLog.textContent = "";
    elements.executionLogStatus.textContent = "";
  }

  if (report) {
    const reportUrl = report.url || "";
    if (elements.executionReportFrame.getAttribute("src") !== reportUrl) {
      elements.executionReportFrame.src = reportUrl;
    }
    elements.executionReportLink.href = reportUrl || "#";
    elements.executionReportPath.textContent = report.path || report.relative_path || "";
  } else {
    elements.executionReportFrame.removeAttribute("src");
    elements.executionReportLink.href = "#";
    elements.executionReportPath.textContent = "";
  }

  if (video) {
    elements.executionVideo.src = video.url || "";
    elements.executionVideoPath.textContent = video.path || video.relative_path || "";
    return;
  }

  elements.executionVideo.removeAttribute("src");
  elements.executionVideo.load();
  elements.executionVideoPath.textContent = "";
  let emptyTitle = "暂无执行记录";
  if (record?.status === "running") {
    emptyTitle = "正在执行脚本";
  } else if (record?.status === "failed") {
    emptyTitle = "执行失败";
  } else if (record?.status === "succeeded" || record?.status === "passed") {
    emptyTitle = "未找到执行视频";
  }
  elements.executionEmpty.querySelector("h3").textContent = emptyTitle;
  elements.executionEmpty.querySelector("p").textContent =
    record?.video_error || "点击右上角“执行脚本”或“修复脚本”后，这里会展示本条脚本的执行视频。";
}

function renderScriptRepairRecord() {
  const record = ensureScriptRepairRecord();
  if (!record) {
    elements.scriptRunPromptFixed.value = "";
    elements.scriptRunPromptNote.value = "";
    elements.scriptRunJobLogs.textContent = "";
    renderScriptRunDuration(null);
    elements.scriptRunJobOutput.classList.add("hidden");
    elements.scriptRunSubmit.disabled = true;
    elements.scriptRunSubmit.textContent = "确认修复";
    return;
  }

  elements.scriptRunPromptFixed.value = record.prompt_fixed;
  elements.scriptRunPromptNote.value = record.prompt_note;
  elements.scriptRunJobLogs.textContent = record.logs || "";
  elements.scriptRunJobLogs.scrollTop = elements.scriptRunJobLogs.scrollHeight;
  elements.scriptRunJobOutput.classList.toggle("hidden", !record.logs && record.status === "idle");
  elements.scriptRunJobStatus.className = "job-status";
  renderScriptRunDuration(record);

  if (record.status === "succeeded") {
    elements.scriptRunJobStatus.textContent = "任务成功";
    elements.scriptRunJobStatus.classList.add("success");
    elements.scriptRunSubmit.disabled = state.scriptRun.isRunning;
    elements.scriptRunSubmit.textContent = "重新修复";
    return;
  }

  if (record.status === "failed") {
    elements.scriptRunJobStatus.textContent = `任务失败${record.error ? `：${record.error}` : ""}`;
    elements.scriptRunJobStatus.classList.add("error");
    elements.scriptRunSubmit.disabled = state.scriptRun.isRunning;
    elements.scriptRunSubmit.textContent = "重试";
    return;
  }

  if (record.status === "cancelled") {
    elements.scriptRunJobStatus.textContent = "任务已取消";
    elements.scriptRunJobStatus.classList.add("cancelled");
    elements.scriptRunSubmit.disabled = state.scriptRun.isRunning;
    elements.scriptRunSubmit.textContent = "重新修复";
    return;
  }

  if (record.status === "running" || state.scriptRun.isRunning) {
    elements.scriptRunJobStatus.textContent = "任务进行中，正在接收实时输出";
    elements.scriptRunSubmit.disabled = true;
    elements.scriptRunSubmit.textContent = "修复中";
    return;
  }

  elements.scriptRunJobStatus.textContent = "任务进行中";
  elements.scriptRunSubmit.disabled = state.scriptExecution.isRunning || state.scriptRun.isRunning;
  elements.scriptRunSubmit.textContent = "确认修复";
}

return {
  setModuleExecutionRecord,
  setModuleRepairBatch,
  setModuleRepairItem,
  refreshModuleExecutionRecordIfCurrent,
  refreshModuleRepairRecordIfCurrent,
  enterModuleBulkMode,
  cancelModuleBulkMode,
  createClientJobId,
  isAbortError,
  appendCancelledLog,
  markModuleRepairItemsCancelled,
  requestCancelCurrentModuleRepairJob,
  cancelModuleRepairBatch,
  toggleModuleSelectAll,
  deleteSelectedModuleScripts,
  executeScriptFromModule,
  openScriptRepairFromModule,
  applyModuleScriptResultsToRunRecords,
  appendModuleExecutionRecordLog,
  readModuleExecutionStream,
  handleModuleExecutionStreamEvent,
  executeSelectedModuleScripts,
  appendModuleRepairJobLog,
  readModuleRepairScriptStream,
  handleModuleRepairStreamEvent,
  repairSelectedModuleScripts,
  renderExecutionHistory,
  getExecutionStatusInfo,
  getRepairStatusInfo,
  getCurrentModuleScripts,
  pruneModuleSelectedFiles,
  isAnyScriptJobRunning,
  renderModuleScriptList,
  renderModuleExecutionRecord,
  formatModuleRepairDuration,
  renderModuleRepairRecord,
  renderExecutionRecord,
  renderScriptRepairRecord,
};
}

window.createModuleExecutionFeature = createModuleExecutionFeature;
