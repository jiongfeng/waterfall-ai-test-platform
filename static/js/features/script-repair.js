function createScriptRepairFeature(deps) {
  const {
    state,
    elements,
    SECTION,
    SCRIPT_VIEW_TAB,
    window,
    getScriptRunPromptFixedTemplate,
    getScriptRunPromptNoteDefault,
    fetch,
    TextDecoder,
    timers,
    formatDuration,
    replaceAllText,
    stripSpecSuffix,
    getScriptRunRecordKey,
    normalizeScriptRepairRecord,
    persistScriptRepairRecords,
    persistScriptRunRecords,
    parseSseBlock,
    renderExecutionRecord,
    persistViewState,
    renderContent,
    setNotice,
    getProjectRequestHeaders,
    createClientJobId,
    refreshScriptMetadata,
    confirmDiscardEdit,
  } = deps;
  const formatRepairDuration = formatDuration;

function renderScriptRunPromptFromTemplate(moduleName, filename) {
  const scriptName = stripSpecSuffix(filename);
  return replaceAllText(
    replaceAllText(
      replaceAllText(
        replaceAllText(getScriptRunPromptFixedTemplate(), "<模块名>", moduleName),
        "<module>",
        moduleName,
      ),
      "<测试脚本名>",
      scriptName,
    ),
    "<test-script>",
    scriptName,
  );
}

function normalizeScriptRunPromptFixedPathSeparators(promptFixed) {
  if (typeof promptFixed !== "string" || !promptFixed.includes("@playwright-test-healer")) {
    return promptFixed;
  }

  return replaceAllText(promptFixed, "\\", "/");
}

function getScriptRunPrompt() {
  return `${elements.scriptRunPromptFixed.value.trim()}\n${elements.scriptRunPromptNote.value.trim()}`.trim();
}

function renderScriptRunDuration(record = null) {
  if (!elements.scriptRunDuration) {
    return;
  }

  if (!record?.started_at) {
    elements.scriptRunDuration.textContent = "";
    elements.scriptRunDuration.classList.add("hidden");
    return;
  }

  const isRunning = ["running", "cancelling"].includes(record.status) && state.scriptRun.isRunning;
  const finishedAt = record.finished_at || (isRunning ? Date.now() : record.updated_at);
  const duration = formatRepairDuration(finishedAt - record.started_at);
  const key = isRunning ? "repair.elapsed" : "repair.duration";
  const fallback = isRunning ? `修复进行时间：${duration}` : `修复耗时：${duration}`;
  const translated = window.WaterfallI18n?.t?.(key, { duration });
  elements.scriptRunDuration.textContent = translated && translated !== key ? translated : fallback;
  elements.scriptRunDuration.classList.remove("hidden");
}

function refreshScriptRunDuration() {
  const key = getScriptRunRecordKey();
  const record = key ? state.scripts.repairRecords[key] : null;
  renderScriptRunDuration(record);
}

function startScriptRunDurationTimer() {
  stopScriptRunDurationTimer();
  refreshScriptRunDuration();
  state.scriptRun.durationTimer = timers.setInterval(refreshScriptRunDuration, 1000);
}

function stopScriptRunDurationTimer() {
  if (state.scriptRun.durationTimer) {
    timers.clearInterval(state.scriptRun.durationTimer);
    state.scriptRun.durationTimer = null;
  }
  refreshScriptRunDuration();
}

function getDefaultScriptRepairRecord(moduleName, filename) {
  const promptFixed = renderScriptRunPromptFromTemplate(moduleName, filename);
  const promptNote = getScriptRunPromptNoteDefault();
  return {
    status: "idle",
    prompt_fixed: promptFixed,
    prompt_note: promptNote,
    prompt: `${promptFixed}\n${promptNote}`.trim(),
    logs: "",
    error: "",
    target_path: "",
    started_at: null,
    finished_at: null,
    updated_at: Date.now(),
  };
}

function ensureScriptRepairRecord(moduleName = state.scripts.selectedModule, filename = state.scripts.selectedFile) {
  const key = getScriptRunRecordKey(moduleName, filename);
  if (!key) {
    return null;
  }

  const defaults = getDefaultScriptRepairRecord(moduleName, filename);
  const previous = state.scripts.repairRecords[key];
  const previousPromptFixed =
    typeof previous?.prompt_fixed === "string"
      ? normalizeScriptRunPromptFixedPathSeparators(previous.prompt_fixed)
      : "";
  const promptFixed =
    previousPromptFixed || defaults.prompt_fixed;
  const promptNote =
    typeof previous?.prompt_note === "string" && previous.prompt_note
      ? previous.prompt_note
      : defaults.prompt_note;
  const next = normalizeScriptRepairRecord({
    ...defaults,
    ...(previous || {}),
    prompt_fixed: promptFixed,
    prompt_note: promptNote,
    prompt: `${promptFixed.trim()}\n${promptNote.trim()}`.trim(),
  });
  state.scripts.repairRecords[key] = next;
  persistScriptRepairRecords(key);
  return next;
}

function setScriptRepairRecord(moduleName, filename, updates) {
  const key = getScriptRunRecordKey(moduleName, filename);
  if (!key) {
    return null;
  }

  const previous = state.scripts.repairRecords[key] || getDefaultScriptRepairRecord(moduleName, filename);
  const next = normalizeScriptRepairRecord({
    ...previous,
    ...updates,
    logs: Object.prototype.hasOwnProperty.call(updates, "logs") ? updates.logs : previous.logs || "",
    updated_at: Date.now(),
  });
  state.scripts.repairRecords[key] = next;
  persistScriptRepairRecords(key);
  return next;
}

function updateScriptRepairPromptFromInputs() {
  const moduleName = state.scripts.selectedModule;
  const filename = state.scripts.selectedFile;
  if (!moduleName || !filename) {
    return;
  }

  const promptFixed = elements.scriptRunPromptFixed.value;
  const promptNote = elements.scriptRunPromptNote.value;
  setScriptRepairRecord(moduleName, filename, {
    prompt_fixed: promptFixed,
    prompt_note: promptNote,
    prompt: `${promptFixed.trim()}\n${promptNote.trim()}`.trim(),
  });
}

function appendScriptRunJobLog(message) {
  elements.scriptRunJobOutput.classList.remove("hidden");
  const current = elements.scriptRunJobLogs.textContent;
  const prefix = current && !current.endsWith("\n") ? "\n" : "";
  elements.scriptRunJobLogs.textContent += `${prefix}${message}\n`;
  elements.scriptRunJobLogs.scrollTop = elements.scriptRunJobLogs.scrollHeight;
}

function appendScriptRunJobDelta(text) {
  if (!text) {
    return;
  }

  elements.scriptRunJobOutput.classList.remove("hidden");
  elements.scriptRunJobLogs.textContent += text;
  elements.scriptRunJobLogs.scrollTop = elements.scriptRunJobLogs.scrollHeight;
}

function renderScriptRunStreamStatus(status, error = "") {
  elements.scriptRunJobOutput.classList.remove("hidden");
  elements.scriptRunJobStatus.className = "job-status";

  if (status === "succeeded") {
    elements.scriptRunJobStatus.textContent = "任务成功";
    elements.scriptRunJobStatus.classList.add("success");
    elements.scriptRunSubmit.disabled = true;
    elements.scriptRunSubmit.textContent = "已完成";
    return;
  }

  if (status === "failed") {
    elements.scriptRunJobStatus.textContent = `任务失败${error ? `：${error}` : ""}`;
    elements.scriptRunJobStatus.classList.add("error");
    elements.scriptRunSubmit.disabled = false;
    elements.scriptRunSubmit.textContent = "重试";
    return;
  }

  if (status === "cancelled") {
    elements.scriptRunJobStatus.textContent = "任务已取消";
    elements.scriptRunJobStatus.classList.add("cancelled");
    elements.scriptRunSubmit.disabled = false;
    elements.scriptRunSubmit.textContent = "重新修复";
    return;
  }

  if (status === "cancelling") {
    elements.scriptRunJobStatus.textContent = "正在终止任务";
    elements.scriptRunSubmit.disabled = true;
    elements.scriptRunSubmit.textContent = "正在终止…";
    return;
  }

  elements.scriptRunJobStatus.textContent = "任务进行中，正在接收实时输出";
  elements.scriptRunSubmit.disabled = false;
  elements.scriptRunSubmit.textContent = "终止修复";
}

function setScriptRunRecord(moduleName, filename, result) {
  const key = getScriptRunRecordKey(moduleName, filename);
  if (!key) {
    return;
  }

  const previous = state.scripts.runRecords[key] || {};
  const hasVideo = Object.prototype.hasOwnProperty.call(result, "video");
  const hasReport = Object.prototype.hasOwnProperty.call(result, "report");
  const hasLogs = Object.prototype.hasOwnProperty.call(result, "logs");
  const hasOutput = Object.prototype.hasOwnProperty.call(result, "output");
  const hasReturncode = Object.prototype.hasOwnProperty.call(result, "returncode");

  state.scripts.runRecords[key] = {
    status: result.status || previous.status || "succeeded",
    run_id: result.run_id || previous.run_id || "",
    result_id: Number(result.result_id) || previous.result_id || null,
    command: result.command || previous.command || "",
    logs: hasLogs ? result.logs : hasOutput ? result.output : previous.logs || "",
    returncode: hasReturncode ? result.returncode : previous.returncode,
    video: hasVideo ? result.video : previous.video || null,
    report: hasReport ? result.report : previous.report || null,
    video_error: result.video_error || result.error || previous.video_error || "",
    report_error: result.report_error || previous.report_error || "",
    updated_at: Date.now(),
  };
  persistScriptRunRecords(key);
}

function appendScriptRunRecordLog(moduleName, filename, text) {
  if (!text) {
    return;
  }

  const current = state.scripts.runRecords[getScriptRunRecordKey(moduleName, filename)] || {};
  setScriptRunRecord(moduleName, filename, {
    status: current.status || "running",
    command: current.command || "",
    logs: `${current.logs || ""}${text}`,
  });

  if (
    state.activeSection === SECTION.SCRIPTS &&
    state.scripts.selectedModule === moduleName &&
    state.scripts.selectedFile === filename &&
    state.scripts.activeTab === SCRIPT_VIEW_TAB.EXECUTION
  ) {
    renderExecutionRecord();
  }
}

function refreshExecutionRecordIfCurrent(moduleName, filename) {
  if (
    state.activeSection === SECTION.SCRIPTS &&
    state.scripts.selectedModule === moduleName &&
    state.scripts.selectedFile === filename &&
    state.scripts.activeTab === SCRIPT_VIEW_TAB.EXECUTION
  ) {
    renderExecutionRecord();
  }
}

async function readScriptExecutionStream(response, moduleName, filename) {
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
        result = handleScriptExecutionStreamEvent(event, result, moduleName, filename);
      }
      boundaryIndex = buffer.indexOf("\n\n");
    }
  }

  buffer += decoder.decode();
  const trailingEvent = parseSseBlock(buffer.trim());
  if (trailingEvent) {
    result = handleScriptExecutionStreamEvent(trailingEvent, result, moduleName, filename);
  }

  return result;
}

function handleScriptExecutionStreamEvent({ event, data }, previousResult, moduleName, filename) {
  if (event === "status") {
    const nextResult = {
      ...previousResult,
      status: data.status || previousResult.status,
      command: data.command || previousResult.command,
      run_id: data.run_id || previousResult.run_id,
      result_id: data.result_id || previousResult.result_id,
      target_path: data.target_path || previousResult.target_path,
      error: data.error || previousResult.error,
      returncode: Object.prototype.hasOwnProperty.call(data, "returncode") ? data.returncode : previousResult.returncode,
      logs: previousResult.logs || "",
      output: data.output || previousResult.output,
      video: Object.prototype.hasOwnProperty.call(data, "video") ? data.video : previousResult.video,
      video_error: data.video_error || previousResult.video_error,
      report: Object.prototype.hasOwnProperty.call(data, "report") ? data.report : previousResult.report,
      report_error: data.report_error || previousResult.report_error,
    };
    setScriptRunRecord(moduleName, filename, nextResult);
    refreshExecutionRecordIfCurrent(moduleName, filename);
    return nextResult;
  }

  if (event === "log") {
    const text = data.message ? `${data.message}\n` : "";
    appendScriptRunRecordLog(moduleName, filename, text);
    return {
      ...previousResult,
      logs: `${previousResult.logs || ""}${text}`,
    };
  }

  if (event === "delta") {
    const text = data.text || "";
    appendScriptRunRecordLog(moduleName, filename, text);
    return {
      ...previousResult,
      logs: `${previousResult.logs || ""}${text}`,
    };
  }

  if (event === "done") {
    const status = data.status || (data.ok === false ? "failed" : "succeeded");
    const nextResult = {
      ...previousResult,
      status,
      error: data.error || previousResult.error,
      returncode: Object.prototype.hasOwnProperty.call(data, "returncode") ? data.returncode : previousResult.returncode,
      run_id: data.run_id || previousResult.run_id,
      result_id: data.result_id || previousResult.result_id,
      output: data.output || previousResult.logs || "",
      logs: previousResult.logs || data.output || "",
      video: Object.prototype.hasOwnProperty.call(data, "video") ? data.video : previousResult.video,
      video_error: data.video_error || previousResult.video_error,
      report: Object.prototype.hasOwnProperty.call(data, "report") ? data.report : previousResult.report,
      report_error: data.report_error || previousResult.report_error,
    };
    setScriptRunRecord(moduleName, filename, nextResult);
    refreshExecutionRecordIfCurrent(moduleName, filename);
    return nextResult;
  }

  return previousResult;
}

async function executeSelectedScript() {
  const moduleName = state.scripts.selectedModule;
  const filename = state.scripts.selectedFile;

  if (
    state.activeSection !== SECTION.SCRIPTS ||
    !moduleName ||
    !filename ||
    state.scriptRecording.isRunning ||
    state.scriptExecution.isRunning ||
    state.moduleExecution.isRunning ||
    state.moduleRepair.isRunning ||
    state.testSuiteExecution.isRunning
  ) {
    return;
  }

  state.scriptExecution.isRunning = true;
  state.scripts.activeTab = SCRIPT_VIEW_TAB.EXECUTION;
  state.scripts.selectedExecutionRunId = "";
  persistViewState();
  setScriptRunRecord(moduleName, filename, {
    status: "running",
    video: null,
    video_error: "正在执行脚本，执行完成后会自动显示本次视频。",
  });
  renderContent();
  setNotice("正在执行脚本，请稍候。");

  try {
    const response = await fetch("/api/script-execution-stream", {
      method: "POST",
      headers: getProjectRequestHeaders({
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({
        module_name: moduleName,
        filename,
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

    const result = await readScriptExecutionStream(response, moduleName, filename);
    setScriptRunRecord(moduleName, filename, result);
    try {
      await refreshScriptMetadata(moduleName, filename);
    } catch (error) {
      // 保留本次执行结果；元数据下次刷新会补齐。
    }
    state.scripts.activeTab = SCRIPT_VIEW_TAB.EXECUTION;
    persistViewState();

    if (result.status === "succeeded") {
      const details = [];
      details.push(result.report ? "HTML report 已更新" : "未找到 HTML report");
      details.push(result.video ? "执行视频已更新" : "未找到执行视频");
      setNotice(`脚本执行完成，${details.join("，")}。`, result.report || result.video ? "success" : "");
      return;
    }

    const message = result.report || result.video ? "脚本执行失败，执行记录已更新。" : result.error || "脚本执行失败。";
    setNotice(message, "error");
  } catch (error) {
    setScriptRunRecord(moduleName, filename, { status: "failed", error: error.message });
    state.scripts.activeTab = SCRIPT_VIEW_TAB.EXECUTION;
    persistViewState();
    setNotice(error.message, "error");
  } finally {
    state.scriptExecution.isRunning = false;
    renderContent();
  }
}

function openScriptRepairRecord() {
  if (state.activeSection !== SECTION.SCRIPTS || !state.scripts.selectedModule || !state.scripts.selectedFile) {
    return;
  }

  if (state.moduleExecution.isRunning || state.moduleRepair.isRunning || state.testSuiteExecution.isRunning) {
    return;
  }

  if (state.isEditing && !confirmDiscardEdit()) {
    return;
  }

  state.isEditing = false;
  state.scripts.activeTab = SCRIPT_VIEW_TAB.REPAIR;
  ensureScriptRepairRecord();
  persistViewState();
  renderContent();
  elements.scriptRunPromptNote.focus();
}

async function submitScriptRun() {
  const moduleName = state.scripts.selectedModule;
  const filename = state.scripts.selectedFile;
  const prompt = getScriptRunPrompt();

  if (!moduleName || !filename || !prompt) {
    return;
  }

  if (state.moduleExecution.isRunning || state.moduleRepair.isRunning || state.testSuiteExecution.isRunning) {
    return;
  }

  updateScriptRepairPromptFromInputs();
  const startedAt = Date.now();
  const jobId = createClientJobId("healer");
  state.scriptRun.isRunning = true;
  state.scriptRun.cancelRequested = false;
  state.scriptRun.currentJobId = jobId;
  state.scripts.activeTab = SCRIPT_VIEW_TAB.REPAIR;
  persistViewState();
  setScriptRepairRecord(moduleName, filename, {
    status: "running",
    prompt_fixed: elements.scriptRunPromptFixed.value,
    prompt_note: elements.scriptRunPromptNote.value,
    prompt,
    job_id: jobId,
    logs: "",
    error: "",
    started_at: startedAt,
    finished_at: null,
  });
  elements.scriptRunSubmit.disabled = true;
  elements.scriptRunSubmit.textContent = "提交中";
  elements.scriptRunJobOutput.classList.remove("hidden");
  elements.scriptRunJobStatus.textContent = "正在提交任务";
  elements.scriptRunJobStatus.className = "job-status";
  elements.scriptRunJobLogs.textContent = "";
  startScriptRunDurationTimer();
  renderContent();

  try {
    const response = await fetch("/api/script-run-stream", {
      method: "POST",
      headers: getProjectRequestHeaders({
        "Content-Type": "application/json",
      }),
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

    renderScriptRunStreamStatus("running");

    const result = await readScriptRunStream(response, moduleName, filename);
    const finishedAt = Date.now();
    state.scriptRun.isRunning = false;
    if (!["succeeded", "failed", "cancelled"].includes(result.status)) {
      result.status = "failed";
      result.error = "流式响应提前结束。";
    }
    setScriptRepairRecord(moduleName, filename, {
      status: result.status,
      error: result.error || "",
      logs: result.logs || "",
      target_path: result.target_path || "",
      finished_at: finishedAt,
    });
    state.scriptRun.currentJobId = "";
    state.scriptRun.cancelRequested = false;
    setScriptRunRecord(moduleName, filename, {
      status: result.status,
      error: result.error,
      video: result.video,
      video_error: result.video_error,
      report: result.report,
      report_error: result.report_error,
      returncode: result.returncode,
    });
    state.scripts.activeTab = SCRIPT_VIEW_TAB.REPAIR;
    persistViewState();
    stopScriptRunDurationTimer();
    renderContent();

    if (result.status === "succeeded") {
      setNotice(result.video ? "测试脚本修复完成，执行视频已更新。" : "测试脚本修复完成，未找到执行视频。", result.video ? "success" : "");
      return;
    }

    if (result.status === "failed") {
      renderScriptRunStreamStatus("failed", result.error || "");
      setNotice(result.error || "测试脚本修复失败。", "error");
      return;
    }

    if (result.status === "cancelled") {
      setNotice("测试脚本修复已终止。", "");
    }
  } catch (error) {
    const finishedAt = Date.now();
    const wasCancelled = state.scriptRun.cancelRequested;
    state.scriptRun.isRunning = false;
    const current = state.scripts.repairRecords[getScriptRunRecordKey(moduleName, filename)] || {};
    const prefix = current.logs && !current.logs.endsWith("\n") ? "\n" : "";
    setScriptRepairRecord(moduleName, filename, {
      status: wasCancelled ? "cancelled" : "failed",
      error: error.message,
      logs: `${current.logs || ""}${prefix}${error.message}\n`,
      finished_at: finishedAt,
    });
    state.scripts.activeTab = SCRIPT_VIEW_TAB.REPAIR;
    persistViewState();
    stopScriptRunDurationTimer();
    state.scriptRun.currentJobId = "";
    state.scriptRun.cancelRequested = false;
    renderContent();
    renderScriptRunStreamStatus(wasCancelled ? "cancelled" : "failed", error.message);
    setNotice(wasCancelled ? "测试脚本修复已终止。" : error.message, wasCancelled ? "" : "error");
  }
}

async function cancelScriptRun() {
  const jobId = state.scriptRun.currentJobId;
  if (!state.scriptRun.isRunning || !jobId || state.scriptRun.cancelRequested) {
    return;
  }
  if (!window.confirm("确定终止本次生成吗？已产生的日志会保留，未完成的结果不会保存。")) {
    return;
  }

  state.scriptRun.cancelRequested = true;
  setScriptRepairRecord(state.scripts.selectedModule, state.scripts.selectedFile, {
    status: "cancelling",
  });
  renderContent();
  try {
    const response = await fetch("/api/script-run-cancel", {
      method: "POST",
      headers: getProjectRequestHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ job_id: jobId }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `请求失败: ${response.status}`);
    }
  } catch (error) {
    state.scriptRun.cancelRequested = false;
    setScriptRepairRecord(state.scripts.selectedModule, state.scripts.selectedFile, {
      status: "running",
    });
    renderContent();
    setNotice(error.message, "error");
  }
}

async function readScriptRunStream(response, moduleName, filename) {
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
        result = handleScriptRunStreamEvent(event, result, moduleName, filename);
      }
      boundaryIndex = buffer.indexOf("\n\n");
    }
  }

  buffer += decoder.decode();
  const trailingEvent = parseSseBlock(buffer.trim());
  if (trailingEvent) {
    result = handleScriptRunStreamEvent(trailingEvent, result, moduleName, filename);
  }

  return result;
}

function handleScriptRunStreamEvent({ event, data }, previousResult, moduleName, filename) {
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
    renderScriptRunStreamStatus(nextResult.status, nextResult.error);
    setScriptRepairRecord(moduleName, filename, {
      status: nextResult.status,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
      target_path: nextResult.target_path || "",
    });
    return nextResult;
  }

  if (event === "log") {
    const text = data.message ? `${data.message}\n` : "";
    appendScriptRunJobLog(data.message || "");
    const logs = `${previousResult.logs || ""}${text}`;
    setScriptRepairRecord(moduleName, filename, { logs });
    return { ...previousResult, logs };
  }

  if (event === "delta") {
    const text = data.text || "";
    appendScriptRunJobDelta(text);
    const logs = `${previousResult.logs || ""}${text}`;
    setScriptRepairRecord(moduleName, filename, { logs });
    return { ...previousResult, logs };
  }

  if (event === "done") {
    if (data.ok === false) {
      const status = data.status === "cancelled" ? "cancelled" : "failed";
      renderScriptRunStreamStatus(status, data.error || "");
      setScriptRepairRecord(moduleName, filename, {
        status,
        error: data.error || previousResult.error || "",
        logs: previousResult.logs || "",
      });
      return { ...previousResult, status, error: data.error || previousResult.error };
    }

    const nextResult = {
      ...previousResult,
      status: previousResult.status === "running" ? "succeeded" : previousResult.status,
      returncode: Object.prototype.hasOwnProperty.call(data, "returncode") ? data.returncode : previousResult.returncode,
      video: Object.prototype.hasOwnProperty.call(data, "video") ? data.video : previousResult.video,
      video_error: data.video_error || previousResult.video_error,
      report: Object.prototype.hasOwnProperty.call(data, "report") ? data.report : previousResult.report,
      report_error: data.report_error || previousResult.report_error,
    };
    setScriptRepairRecord(moduleName, filename, {
      status: nextResult.status,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
      target_path: nextResult.target_path || "",
    });
    return nextResult;
  }

  return previousResult;
}

return {
  renderScriptRunPromptFromTemplate,
  normalizeScriptRunPromptFixedPathSeparators,
  getScriptRunPrompt,
  formatRepairDuration,
  renderScriptRunDuration,
  refreshScriptRunDuration,
  startScriptRunDurationTimer,
  stopScriptRunDurationTimer,
  getDefaultScriptRepairRecord,
  ensureScriptRepairRecord,
  setScriptRepairRecord,
  updateScriptRepairPromptFromInputs,
  appendScriptRunJobLog,
  appendScriptRunJobDelta,
  renderScriptRunStreamStatus,
  setScriptRunRecord,
  appendScriptRunRecordLog,
  refreshExecutionRecordIfCurrent,
  readScriptExecutionStream,
  handleScriptExecutionStreamEvent,
  executeSelectedScript,
  openScriptRepairRecord,
  submitScriptRun,
  cancelScriptRun,
  readScriptRunStream,
  handleScriptRunStreamEvent,
};
}

window.createScriptRepairFeature = createScriptRepairFeature;
