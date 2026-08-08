function createTestSuiteResultHelpers({ getSuiteScriptKey }) {
  function normalizeExecutionResultStatus(status) {
    if (status === "succeeded") {
      return "passed";
    }
    return status || "unknown";
  }

  function isCompletedExecutionResultStatus(status) {
    return ["passed", "failed", "skipped", "timed_out", "interrupted"].includes(
      normalizeExecutionResultStatus(status),
    );
  }

  function mergeTestSuiteScriptResults(previousResults, nextResults) {
    const previous =
      previousResults && typeof previousResults === "object" && !Array.isArray(previousResults)
        ? previousResults
        : {};
    const next =
      nextResults && typeof nextResults === "object" && !Array.isArray(nextResults) ? nextResults : {};
    return { ...previous, ...next };
  }

  function finalizeTestSuiteScriptResults(items, scriptResults, unresolvedStatus = "failed") {
    const finalized = mergeTestSuiteScriptResults({}, scriptResults);
    (items || []).forEach((item) => {
      const key = item.key || getSuiteScriptKey(item.module_name, item.filename);
      if (key && !isCompletedExecutionResultStatus(finalized[key])) {
        finalized[key] = unresolvedStatus;
      }
    });
    return finalized;
  }

  return {
    normalizeExecutionResultStatus,
    isCompletedExecutionResultStatus,
    mergeTestSuiteScriptResults,
    finalizeTestSuiteScriptResults,
  };
}

function createTestSuitesFeature(deps) {
  const {
    state,
    elements,
    SECTION,
    TEST_SUITE_VIEW_TAB,
    TEST_SUITE_ALL_MODULE,
    EXECUTION_MODE,
    document,
    window,
    fetch,
    TextDecoder,
    resultHelpers,
    getSuiteScriptKey,
    stripSpecSuffix,
    normalizeTestSuiteExecutionArtifact,
    normalizeTestSuiteExecutionRunList,
    formatTimestampMs,
    getDbExecutionModeLabel,
    getDbResultStatusInfo,
    isAnyScriptJobRunning,
    persistViewState,
    persistTestSuiteExecutionRecords,
    renderContent,
    renderSideList,
    setNotice,
    setLoading,
    requestJson,
    encodePathPart,
    normalizeTestSuite,
    normalizeTestSuiteExecutionRecord,
    normalizeExecutionModeValue,
    parseSseBlock,
    openExecutionModeModal,
    getExecutionModeLabel,
    getProjectRequestHeaders,
  } = deps;
  const {
    normalizeExecutionResultStatus,
    isCompletedExecutionResultStatus,
    mergeTestSuiteScriptResults,
    finalizeTestSuiteScriptResults,
  } = resultHelpers;

function formatSuiteDate(timestamp) {
  const date = new Date(Number(timestamp) || Date.now());
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function getSelectedTestSuite() {
  return state.testSuites.suites.find((suite) => suite.id === state.testSuites.selectedSuiteId) || null;
}

function resetTestSuiteExecutionHistory() {
  state.testSuites.executionHistory.records = [];
  state.testSuites.executionHistory.selectedRunId = null;
  state.testSuites.executionHistory.loadedSuiteId = null;
  state.testSuites.executionHistory.isLoading = false;
  state.testSuites.executionHistory.error = "";
}

function getTestSuiteExecutionRecordKey(suiteId = state.testSuites.selectedSuiteId) {
  return suiteId || "";
}

function getTestSuiteExecutionStatusText(record) {
  if (!record) {
    return "暂无执行记录";
  }
  if (record.status === "running" || record.status === "queued") {
    return "执行中";
  }
  if (record.status === "failed" || record.status === "timed_out" || record.status === "interrupted") {
    return "执行失败";
  }
  if (record.status === "succeeded" || record.status === "passed") {
    return "执行完成";
  }
  return "暂无执行记录";
}

function getSuiteModuleOptions(suite) {
  const counts = new Map();
  (suite?.items || []).forEach((item) => {
    counts.set(item.module_name, (counts.get(item.module_name) || 0) + 1);
  });

  const modules = Array.from(counts.entries())
    .map(([name, count]) => ({ name, label: name, count }))
    .sort((left, right) => left.label.localeCompare(right.label));

  return [
    {
      name: TEST_SUITE_ALL_MODULE,
      label: "全部",
      count: suite?.items?.length || 0,
    },
    ...modules,
  ];
}

function ensureSelectedSuiteModule(suite) {
  const validModules = new Set(getSuiteModuleOptions(suite).map((moduleItem) => moduleItem.name));
  if (!validModules.has(state.testSuites.selectedModule)) {
    state.testSuites.selectedModule = TEST_SUITE_ALL_MODULE;
  }
}

function getSuiteItemsForModule(suite, moduleName = state.testSuites.selectedModule) {
  const items = suite?.items || [];
  if (moduleName === TEST_SUITE_ALL_MODULE) {
    return items;
  }

  return items.filter((item) => item.module_name === moduleName);
}

function renderTestSuiteList() {
  elements.testSuiteListSummary.textContent = `共 ${state.testSuites.suites.length} 个测试集`;
  elements.testSuiteTableBody.replaceChildren();

  if (!state.testSuites.suites.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.textContent = "暂无测试集，点击“新增测试集”创建。";
    row.appendChild(cell);
    elements.testSuiteTableBody.appendChild(row);
    return;
  }

  state.testSuites.suites.forEach((suite) => {
    const row = document.createElement("tr");

    const nameCell = document.createElement("td");
    const nameButton = document.createElement("button");
    nameButton.type = "button";
    nameButton.className = "test-suite-name-button";
    nameButton.textContent = suite.name;
    nameButton.addEventListener("click", () => selectTestSuite(suite.id));
    nameCell.appendChild(nameButton);
    row.appendChild(nameCell);

    const dateCell = document.createElement("td");
    dateCell.textContent = formatSuiteDate(suite.created_at);
    row.appendChild(dateCell);

    const resultCell = document.createElement("td");
    const result = document.createElement("span");
    result.className = "test-suite-result";
    const executionRecord = state.testSuites.executionRecords[getTestSuiteExecutionRecordKey(suite.id)];
    result.textContent = `${suite.items.length} 条脚本 / ${getTestSuiteExecutionStatusText(executionRecord)}`;
    resultCell.appendChild(result);
    row.appendChild(resultCell);

    const actionsCell = document.createElement("td");
    const actions = document.createElement("div");
    actions.className = "module-row-actions";
    const renameButton = document.createElement("button");
    renameButton.type = "button";
    renameButton.className = "secondary-button compact-button";
    renameButton.textContent = "重命名";
    renameButton.addEventListener("click", () => openTestSuiteRenameModal(suite.id));
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "secondary-button compact-button danger-button";
    deleteButton.textContent = "删除";
    deleteButton.addEventListener("click", () => deleteTestSuite(suite.id));
    actions.append(renameButton, deleteButton);
    actionsCell.appendChild(actions);
    row.appendChild(actionsCell);

    elements.testSuiteTableBody.appendChild(row);
  });
}

function renderTestSuiteTabs() {
  const isScriptsTab = state.testSuites.activeTab === TEST_SUITE_VIEW_TAB.SCRIPTS;
  const isExecutionTab = state.testSuites.activeTab === TEST_SUITE_VIEW_TAB.EXECUTION;
  elements.testSuiteScriptsTab.classList.toggle("active", isScriptsTab);
  elements.testSuiteScriptsTab.setAttribute("aria-selected", String(isScriptsTab));
  elements.testSuiteExecutionTab.classList.toggle("active", isExecutionTab);
  elements.testSuiteExecutionTab.setAttribute("aria-selected", String(isExecutionTab));
  elements.testSuiteScriptsContent.classList.toggle("hidden", !isScriptsTab);
  elements.testSuiteExecutionRecord.classList.toggle("hidden", !isExecutionTab);
}

function getTestSuiteScriptResultStatus(record, scriptKey) {
  const scriptResults = record?.script_results;
  if (scriptResults && Object.prototype.hasOwnProperty.call(scriptResults, scriptKey)) {
    const status = normalizeExecutionResultStatus(scriptResults[scriptKey]);
    if (isCompletedExecutionResultStatus(status) || ["running", "queued"].includes(record?.status)) {
      return status;
    }
    return ["running", "queued"].includes(status) ? "interrupted" : "unknown";
  }
  if (["running", "queued"].includes(record?.status)) {
    return record.status;
  }
  return "unknown";
}

function getTestSuiteExecutionRunSummary(record) {
  if (!record) {
    return "请选择一条执行记录。";
  }

  const summary = record.summary || {};
  const passed = Number(summary.passed ?? summary.succeeded) || 0;
  const failed = Number(summary.failed) || 0;
  const skipped = Number(summary.skipped) || 0;
  const unknown = Number(summary.unknown) || 0;
  const total = Number(summary.total) || Number(record.total_files) || (record.results || []).length;
  const parts = [];
  if (total) {
    parts.push(`共 ${total} 条`);
  }
  if (passed) {
    parts.push(`通过 ${passed}`);
  }
  if (failed) {
    parts.push(`失败 ${failed}`);
  }
  if (skipped) {
    parts.push(`跳过 ${skipped}`);
  }
  if (unknown) {
    parts.push(`未知 ${unknown}`);
  }
  return parts.length ? parts.join(" / ") : getTestSuiteExecutionStatusText(record);
}

function buildLocalTestSuiteExecutionRun(suite) {
  const record = suite ? state.testSuites.executionRecords[getTestSuiteExecutionRecordKey(suite.id)] : null;
  const runId = record?.run_id || (record?.started_at ? `local-${suite?.id || "execution"}` : "");
  if (!suite || !record || !runId) {
    return null;
  }

  const report = normalizeTestSuiteExecutionArtifact(record.report);
  const results = (record.items?.length ? record.items : suite.items || []).map((item, index) => {
    const scriptKey = getSuiteScriptKey(item.module_name, item.filename);
    const status = getTestSuiteScriptResultStatus(record, scriptKey);
    return {
      result_id: null,
      run_id: runId,
      order_index: index + 1,
      module_name: item.module_name,
      filename: item.filename,
      script_key: scriptKey,
      script_path: item.path || item.script_path || "",
      script_name: item.display_name || stripSpecSuffix(item.filename),
      status,
      error_message: status === "passed" ? "" : record.error || "",
      stdout_tail: "",
      started_at: record.started_at || null,
      finished_at: record.finished_at || null,
      updated_at: record.updated_at || null,
      report,
      video: null,
    };
  });
  const materializedScriptResults = Object.fromEntries(
    results.filter((result) => result.script_key).map((result) => [result.script_key, result.status]),
  );

  return {
    run_id: runId,
    run_type: "test_suite",
    status: record.status || "running",
    execution_mode: record.execution_mode || EXECUTION_MODE.BATCH,
    database_reset_mode: "",
    suite_id: suite.id,
    command: record.command || "",
    git_commit_sha: "",
    summary: buildExecutionSummaryFromResults(materializedScriptResults),
    total_files: results.length,
    completed_files: results.filter((item) => item.status !== "running").length,
    error: record.error || "",
    started_at: record.started_at || null,
    finished_at: record.finished_at || null,
    created_at: record.started_at || record.updated_at || null,
    updated_at: record.updated_at || null,
    report,
    results,
    logs: record.logs || "",
    report_error: record.report_error || "",
  };
}

function buildExecutionSummaryFromResults(scriptResults) {
  const summary = { total: 0, passed: 0, failed: 0, skipped: 0, unknown: 0 };
  Object.values(scriptResults || {}).forEach((status) => {
    const normalized = normalizeExecutionResultStatus(status);
    summary.total += 1;
    if (normalized === "passed") {
      summary.passed += 1;
    } else if (normalized === "failed") {
      summary.failed += 1;
    } else if (normalized === "skipped") {
      summary.skipped += 1;
    } else {
      summary.unknown += 1;
    }
  });
  return summary;
}

function getTestSuiteProgressCounts(record, suite) {
  const scriptResults =
    record?.script_results && typeof record.script_results === "object" && !Array.isArray(record.script_results)
      ? record.script_results
      : {};
  const items = record?.items?.length ? record.items : suite?.items || [];
  const keys = new Set();
  items.forEach((item) => {
    const key = item.key || getSuiteScriptKey(item.module_name, item.filename);
    if (key) {
      keys.add(key);
    }
  });
  Object.keys(scriptResults).forEach((key) => {
    if (key) {
      keys.add(key);
    }
  });

  let passed = 0;
  let failed = 0;
  let skipped = 0;
  let running = 0;
  let unknown = 0;

  keys.forEach((key) => {
    const status = normalizeExecutionResultStatus(scriptResults[key] || "unknown");
    if (status === "passed") {
      passed += 1;
    } else if (status === "failed" || status === "timed_out") {
      failed += 1;
    } else if (status === "skipped") {
      skipped += 1;
    } else if (status === "running") {
      running += 1;
    } else {
      unknown += 1;
    }
  });

  const total = Number(record?.total_files) || keys.size || items.length || 0;
  const countedCompleted = passed + failed + skipped + unknown;
  const storedCompleted = Number(record?.completed_files) || 0;
  const completed = Math.min(total || countedCompleted, Math.max(storedCompleted, countedCompleted));
  return { total, completed, passed, failed, skipped, running, unknown };
}

function getTestSuiteProgressStatusText(record, counts) {
  if (!record) {
    return "等待开始";
  }
  const status = record.status || "running";
  const modeLabel = getDbExecutionModeLabel(record.execution_mode);
  if (status === "running") {
    return `${modeLabel} / 执行中 ${counts.completed}/${counts.total || 0}`;
  }
  if (status === "succeeded" || status === "passed") {
    return `${modeLabel} / 执行完成`;
  }
  if (status === "failed") {
    return `${modeLabel} / 执行失败`;
  }
  return `${modeLabel} / ${getTestSuiteExecutionStatusText(record)}`;
}

function renderTestSuiteProgressModal(suite) {
  if (
    !state.testSuiteExecution.progressModalVisible ||
    !suite?.id ||
    state.testSuiteExecution.progressModalSuiteId !== suite.id
  ) {
    return;
  }

  const record = state.testSuites.executionRecords[getTestSuiteExecutionRecordKey(suite.id)] || null;
  const counts = getTestSuiteProgressCounts(record, suite);
  const logs = record?.logs || "";
  const progress = counts.total ? Math.round((counts.completed / counts.total) * 100) : 0;

  elements.testSuiteProgressTitle.textContent = `执行测试集：${record?.suite_name || suite.name}`;
  elements.testSuiteProgressStatus.textContent = getTestSuiteProgressStatusText(record, counts);
  elements.testSuiteProgressLog.textContent = logs || window.WaterfallI18n?.source("等待执行输出...") || "等待执行输出...";
  elements.testSuiteProgressCompleted.textContent = `${counts.completed} / ${counts.total || 0}`;
  elements.testSuiteProgressPassed.textContent = String(counts.passed);
  elements.testSuiteProgressFailed.textContent = String(counts.failed);
  elements.testSuiteProgressBar.style.width = `${Math.min(Math.max(progress, 0), 100)}%`;
  elements.testSuiteProgressModal.classList.remove("hidden");

  window.requestAnimationFrame(() => {
    elements.testSuiteProgressLog.scrollTop = elements.testSuiteProgressLog.scrollHeight;
  });
}

function openTestSuiteProgressModal(suite) {
  if (!suite?.id) {
    return;
  }
  state.testSuiteExecution.progressModalVisible = true;
  state.testSuiteExecution.progressModalSuiteId = suite.id;
  renderTestSuiteProgressModal(suite);
}

function closeTestSuiteProgressModal() {
  state.testSuiteExecution.progressModalVisible = false;
  state.testSuiteExecution.progressModalSuiteId = "";
  elements.testSuiteProgressModal.classList.add("hidden");
}

function getRenderableTestSuiteExecutionRecords(suite) {
  const history = state.testSuites.executionHistory;
  const records = history.loadedSuiteId === suite?.id ? [...history.records] : [];
  const localRun = buildLocalTestSuiteExecutionRun(suite);
  if (localRun && !records.some((record) => record.run_id === localRun.run_id)) {
    records.unshift(localRun);
  }
  return records;
}

function ensureSelectedTestSuiteExecutionRun(records) {
  const history = state.testSuites.executionHistory;
  if (!records.length) {
    history.selectedRunId = null;
    return null;
  }
  let selected = records.find((record) => record.run_id === history.selectedRunId);
  if (!selected) {
    selected = records[0];
    history.selectedRunId = selected.run_id;
  }
  return selected;
}

function renderTestSuiteExecutionHistory(records, selectedRecord) {
  const history = state.testSuites.executionHistory;
  elements.testSuiteExecutionHistoryList.replaceChildren();

  if (history.isLoading && !records.length) {
    const loading = document.createElement("div");
    loading.className = "test-suite-execution-history-empty";
    loading.textContent = "正在加载执行记录...";
    elements.testSuiteExecutionHistoryList.appendChild(loading);
    return;
  }

  if (history.error && !records.length) {
    const error = document.createElement("div");
    error.className = "test-suite-execution-history-empty error";
    error.textContent = history.error;
    elements.testSuiteExecutionHistoryList.appendChild(error);
    return;
  }

  if (!records.length) {
    const empty = document.createElement("div");
    empty.className = "test-suite-execution-history-empty";
    empty.textContent = "暂无执行记录";
    elements.testSuiteExecutionHistoryList.appendChild(empty);
    return;
  }

  records.forEach((record) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "test-suite-execution-history-button";
    button.classList.toggle("active", record.run_id === selectedRecord?.run_id);
    button.innerHTML = "<span></span><span></span><span></span>";
    button.querySelector("span:nth-child(1)").textContent = formatTimestampMs(record.started_at || record.created_at);
    button.querySelector("span:nth-child(2)").textContent = getTestSuiteExecutionRunSummary(record);
    button.querySelector("span:nth-child(3)").textContent = getTestSuiteExecutionStatusText(record);
    button.addEventListener("click", () => {
      state.testSuites.executionHistory.selectedRunId = record.run_id;
      renderTestSuiteExecutionRecord();
    });
    elements.testSuiteExecutionHistoryList.appendChild(button);
  });
}

function renderExecutionResultPanel(record, view, options = {}) {
  const report = record?.report;
  const results = record?.results || [];
  const command = record?.command || "";
  const logs = record?.logs || "";
  const hasLogs = Boolean(command || logs);
  const titleText = options.titleText || "执行结果";
  const emptyTitle = options.emptyTitle || "暂无测试集执行记录";
  const emptyMessage = options.emptyMessage || "点击右上角“执行”后，这里会展示历史执行记录和脚本结果。";
  const noResultsTitle = options.noResultsTitle || "暂无脚本结果";
  const noResultsMessage = options.noResultsMessage || record?.error || "这次执行还没有可展示的脚本级结果。";
  const openVideo = options.openVideo || openTestSuiteExecutionVideo;

  view.title.textContent = record
    ? `${titleText} / ${formatTimestampMs(record.started_at || record.created_at)}`
    : titleText;
  view.summary.textContent = record
    ? `${getDbExecutionModeLabel(record.execution_mode)} / ${getTestSuiteExecutionRunSummary(record)}`
    : options.emptySummary || "请选择一条执行记录。";

  view.reportLink.classList.toggle("hidden", !report?.url);
  view.reportLink.href = report?.url || "#";

  view.empty.classList.toggle("hidden", Boolean(record && results.length));
  view.resultWrap.classList.toggle("hidden", !record || !results.length);
  view.resultTableBody.replaceChildren();

  if (!record) {
    view.empty.querySelector("h3").textContent = emptyTitle;
    view.empty.querySelector("p").textContent = emptyMessage;
  } else if (!results.length) {
    view.empty.querySelector("h3").textContent = noResultsTitle;
    view.empty.querySelector("p").textContent = noResultsMessage;
  }

  results.forEach((result) => {
    const row = document.createElement("tr");

    const nameCell = document.createElement("td");
    const scriptName = result.script_name || stripSpecSuffix(result.filename);
    nameCell.textContent = scriptName || result.filename || "-";
    nameCell.title = [result.module_name, result.filename].filter(Boolean).join("/");
    row.appendChild(nameCell);

    const statusCell = document.createElement("td");
    const statusInfo = getDbResultStatusInfo(normalizeExecutionResultStatus(result.status));
    const status = document.createElement("span");
    status.className = `test-suite-status-chip ${statusInfo.className}`.trim();
    status.textContent = statusInfo.label;
    if (result.error_message) {
      status.title = result.error_message;
    }
    statusCell.appendChild(status);
    row.appendChild(statusCell);

    const reportCell = document.createElement("td");
    if (result.report?.url || report?.url) {
      const reportLink = document.createElement("a");
      reportLink.className = "secondary-button compact-button";
      reportLink.href = result.report?.url || report.url;
      reportLink.target = "_blank";
      reportLink.rel = "noreferrer";
      reportLink.textContent = "报告";
      reportCell.appendChild(reportLink);
    } else {
      const disabled = document.createElement("button");
      disabled.type = "button";
      disabled.className = "secondary-button compact-button";
      disabled.disabled = true;
      disabled.textContent = "报告";
      reportCell.appendChild(disabled);
    }
    row.appendChild(reportCell);

    const videoCell = document.createElement("td");
    const videoButton = document.createElement("button");
    videoButton.type = "button";
    videoButton.className = "secondary-button compact-button";
    videoButton.textContent = "播放";
    videoButton.disabled = !result.video?.url;
    if (result.video?.url) {
      videoButton.addEventListener("click", () => openVideo(result));
    }
    videoCell.appendChild(videoButton);
    row.appendChild(videoCell);

    view.resultTableBody.appendChild(row);
  });

  view.logPanel.classList.toggle("hidden", !hasLogs);
  if (hasLogs) {
    const commandLine = command ? `$ ${command}` : "";
    view.log.textContent = [commandLine, logs].filter(Boolean).join("\n");
    view.logStatus.textContent = getTestSuiteExecutionStatusText(record);
  } else {
    view.log.textContent = "";
    view.logStatus.textContent = "";
  }
}

function renderTestSuiteExecutionResult(record) {
  renderExecutionResultPanel(
    record,
    {
      title: elements.testSuiteExecutionResultTitle,
      summary: elements.testSuiteExecutionResultSummary,
      reportLink: elements.testSuiteExecutionReportLink,
      empty: elements.testSuiteExecutionEmpty,
      resultWrap: elements.testSuiteExecutionResultWrap,
      resultTableBody: elements.testSuiteExecutionResultTableBody,
      logPanel: elements.testSuiteExecutionLogPanel,
      logStatus: elements.testSuiteExecutionLogStatus,
      log: elements.testSuiteExecutionLog,
    },
    {
      emptyMessage:
        state.testSuites.executionHistory.error || "点击右上角“执行”后，这里会展示历史执行记录和脚本结果。",
    },
  );
}

function renderTestSuiteExecutionRecord() {
  const suite = getSelectedTestSuite();
  const records = getRenderableTestSuiteExecutionRecords(suite);
  const selectedRecord = ensureSelectedTestSuiteExecutionRun(records);
  renderTestSuiteExecutionHistory(records, selectedRecord);
  renderTestSuiteExecutionResult(selectedRecord);
}

function openTestSuiteExecutionVideo(result) {
  const video = result?.video;
  if (!video?.url) {
    return;
  }

  const title = result.script_name || stripSpecSuffix(result.filename) || "脚本执行视频";
  state.testSuiteVideoModal.video = video;
  state.testSuiteVideoModal.title = title;
  elements.testSuiteVideoModalTitle.textContent = title;
  elements.testSuiteExecutionVideo.src = video.url;
  elements.testSuiteExecutionVideoPath.textContent = video.path || video.relative_path || "";
  elements.testSuiteVideoModal.classList.remove("hidden");
  elements.testSuiteExecutionVideo.load();
  const playPromise = elements.testSuiteExecutionVideo.play();
  if (playPromise && typeof playPromise.catch === "function") {
    playPromise.catch(() => {});
  }
}

function closeTestSuiteExecutionVideo() {
  elements.testSuiteExecutionVideo.pause();
  elements.testSuiteExecutionVideo.removeAttribute("src");
  elements.testSuiteExecutionVideo.load();
  elements.testSuiteExecutionVideoPath.textContent = "";
  elements.testSuiteVideoModal.classList.add("hidden");
  state.testSuiteVideoModal.video = null;
  state.testSuiteVideoModal.title = "";
}

function renderTestSuiteDetail() {
  const suite = getSelectedTestSuite();
  if (!suite) {
    state.testSuites.selectedSuiteId = null;
    state.testSuites.selectedModule = TEST_SUITE_ALL_MODULE;
    state.testSuites.activeTab = TEST_SUITE_VIEW_TAB.SCRIPTS;
    renderTestSuiteList();
    elements.testSuiteListPanel.classList.remove("hidden");
    elements.testSuiteDetailPanel.classList.add("hidden");
    return;
  }

  ensureSelectedSuiteModule(suite);
  const moduleOptions = getSuiteModuleOptions(suite);
  const activeModule =
    moduleOptions.find((moduleItem) => moduleItem.name === state.testSuites.selectedModule) || moduleOptions[0];
  const items = getSuiteItemsForModule(suite, activeModule.name);
  const isExecutionTab = state.testSuites.activeTab === TEST_SUITE_VIEW_TAB.EXECUTION;
  const isBusy = isAnyScriptJobRunning();

  elements.testSuiteModuleList.replaceChildren();
  moduleOptions.forEach((moduleItem) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "test-suite-module-button";
    button.classList.toggle("active", moduleItem.name === activeModule.name);
    button.title = moduleItem.label;
    button.innerHTML = "<span></span><span></span>";
    button.querySelector("span:first-child").textContent = moduleItem.label;
    button.querySelector("span:last-child").textContent = moduleItem.count;
    button.addEventListener("click", () => {
      state.testSuites.selectedModule = moduleItem.name;
      persistViewState();
      renderContent();
    });
    elements.testSuiteModuleList.appendChild(button);
  });

  elements.openAddSuiteScriptsButton.disabled = isBusy;
  elements.executeTestSuiteButton.disabled = !suite.items.length || isBusy;
  elements.executeTestSuiteButton.textContent = state.testSuiteExecution.isRunning ? "执行中" : "执行";
  elements.testSuiteDetailTitle.textContent = isExecutionTab
    ? "执行记录"
    : activeModule.name === TEST_SUITE_ALL_MODULE
      ? "全部脚本"
      : `${activeModule.label} 脚本`;
  elements.testSuiteDetailSummary.textContent = isExecutionTab
    ? state.testSuites.executionHistory.isLoading
      ? "正在加载执行记录"
      : "最近 20 次执行记录"
    : `共 ${items.length} 条脚本`;
  renderTestSuiteTabs();
  renderTestSuiteExecutionRecord();
  if (
    isExecutionTab &&
    !state.testSuites.executionHistory.isLoading &&
    state.testSuites.executionHistory.loadedSuiteId !== suite.id
  ) {
    loadTestSuiteExecutionRecords(suite.id);
  }
  elements.testSuiteScriptTableBody.replaceChildren();

  if (!items.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 3;
    cell.textContent = suite.items.length ? "当前模块暂无脚本。" : "当前测试集暂无脚本，点击“添加”选择脚本。";
    row.appendChild(cell);
    elements.testSuiteScriptTableBody.appendChild(row);
    return;
  }

  items.forEach((item, index) => {
    const row = document.createElement("tr");

    const nameCell = document.createElement("td");
    nameCell.textContent = item.display_name || stripSpecSuffix(item.filename);
    nameCell.title = item.path || item.filename;
    row.appendChild(nameCell);

    const moduleCell = document.createElement("td");
    moduleCell.textContent = item.module_name;
    row.appendChild(moduleCell);

    const actionsCell = document.createElement("td");
    const actions = document.createElement("div");
    actions.className = "module-row-actions";
    const upButton = document.createElement("button");
    upButton.type = "button";
    upButton.className = "secondary-button compact-button";
    upButton.textContent = "上移";
    upButton.disabled = index === 0 || isBusy;
    upButton.addEventListener("click", () => moveTestSuiteItem(suite.id, item.item_id, -1));
    const downButton = document.createElement("button");
    downButton.type = "button";
    downButton.className = "secondary-button compact-button";
    downButton.textContent = "下移";
    downButton.disabled = index === items.length - 1 || isBusy;
    downButton.addEventListener("click", () => moveTestSuiteItem(suite.id, item.item_id, 1));
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "secondary-button compact-button danger-button";
    removeButton.textContent = "移除";
    removeButton.disabled = isBusy;
    removeButton.addEventListener("click", () => removeTestSuiteItem(suite.id, item.item_id));
    actions.append(upButton, downButton, removeButton);
    actionsCell.appendChild(actions);
    row.appendChild(actionsCell);

    elements.testSuiteScriptTableBody.appendChild(row);
  });
}

function openTestSuiteCreateModal() {
  setNotice("");
  elements.newTestSuiteName.value = "";
  elements.newTestSuiteName.setCustomValidity("");
  elements.testSuiteCreateModal.classList.remove("hidden");
  window.requestAnimationFrame(() => elements.newTestSuiteName.focus());
}

function closeTestSuiteCreateModal() {
  elements.testSuiteCreateModal.classList.add("hidden");
  elements.newTestSuiteName.setCustomValidity("");
}

function openTestSuiteRenameModal(suiteId) {
  const suite = state.testSuites.suites.find((item) => item.id === suiteId);
  if (!suite || isAnyScriptJobRunning()) {
    return;
  }
  setNotice("");
  state.testSuites.renamingSuiteId = suite.id;
  elements.renameTestSuiteName.value = suite.name;
  elements.renameTestSuiteName.setCustomValidity("");
  elements.testSuiteRenameModal.classList.remove("hidden");
  window.requestAnimationFrame(() => {
    elements.renameTestSuiteName.focus();
    elements.renameTestSuiteName.select();
  });
}

function closeTestSuiteRenameModal() {
  elements.testSuiteRenameModal.classList.add("hidden");
  elements.renameTestSuiteName.setCustomValidity("");
  state.testSuites.renamingSuiteId = null;
}

async function submitTestSuiteCreate() {
  const name = elements.newTestSuiteName.value.trim();

  if (!name) {
    elements.newTestSuiteName.setCustomValidity("请输入测试集名字。");
    elements.newTestSuiteName.reportValidity();
    return;
  }

  try {
    const data = await requestJson("/api/test-suites", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    const suite = normalizeTestSuite(data.suite);
    if (suite) {
      state.testSuites.suites = [suite, ...state.testSuites.suites.filter((item) => item.id !== suite.id)];
    }
    state.testSuites.selectedSuiteId = null;
    state.testSuites.selectedModule = TEST_SUITE_ALL_MODULE;
    resetTestSuiteExecutionHistory();
    persistViewState();
    closeTestSuiteCreateModal();
    renderContent();
    setNotice("测试集创建成功。", "success");
  } catch (error) {
    elements.newTestSuiteName.setCustomValidity(error.message || "测试集创建失败。");
    elements.newTestSuiteName.reportValidity();
  }
}

function upsertTestSuiteInState(suite) {
  const normalized = normalizeTestSuite(suite);
  if (!normalized) {
    return null;
  }
  state.testSuites.suites = [
    normalized,
    ...state.testSuites.suites.filter((item) => item.id !== normalized.id),
  ].sort((left, right) => (right.updated_at || right.created_at) - (left.updated_at || left.created_at));
  return normalized;
}

async function loadTestSuiteExecutionRecords(suiteId = state.testSuites.selectedSuiteId, options = {}) {
  const { selectRunId = "", silent = false, force = false } = options;
  if (!suiteId) {
    resetTestSuiteExecutionHistory();
    return;
  }

  const history = state.testSuites.executionHistory;
  if (
    !force &&
    history.loadedSuiteId === suiteId &&
    !history.error &&
    history.records.length &&
    !selectRunId
  ) {
    return;
  }

  history.isLoading = true;
  history.error = "";
  if (!silent && state.activeSection === SECTION.TEST_SUITES && state.testSuites.activeTab === TEST_SUITE_VIEW_TAB.EXECUTION) {
    renderTestSuiteExecutionRecord();
  }

  try {
    const data = await requestJson(`/api/test-suites/${encodePathPart(suiteId)}/execution-records?limit=20`);
    if (state.testSuites.selectedSuiteId !== suiteId) {
      return;
    }
    history.records = normalizeTestSuiteExecutionRunList(data.records || []);
    history.loadedSuiteId = suiteId;
    if (selectRunId && history.records.some((record) => record.run_id === selectRunId)) {
      history.selectedRunId = selectRunId;
    } else if (!history.records.some((record) => record.run_id === history.selectedRunId)) {
      history.selectedRunId = history.records[0]?.run_id || null;
    }
  } catch (error) {
    if (state.testSuites.selectedSuiteId === suiteId) {
      history.error = error.message || "读取测试集执行记录失败。";
      history.records = [];
      history.loadedSuiteId = suiteId;
      history.selectedRunId = null;
    }
  } finally {
    if (state.testSuites.selectedSuiteId === suiteId) {
      history.isLoading = false;
      if (state.activeSection === SECTION.TEST_SUITES && state.testSuites.activeTab === TEST_SUITE_VIEW_TAB.EXECUTION) {
        renderTestSuiteExecutionRecord();
      }
    }
  }
}

async function submitTestSuiteRename() {
  const suiteId = state.testSuites.renamingSuiteId;
  const suite = state.testSuites.suites.find((item) => item.id === suiteId);
  if (!suite || isAnyScriptJobRunning()) {
    return;
  }
  const name = elements.renameTestSuiteName.value.trim();
  if (!name) {
    elements.renameTestSuiteName.setCustomValidity("请输入测试集名字。");
    elements.renameTestSuiteName.reportValidity();
    return;
  }
  if (name === suite.name) {
    closeTestSuiteRenameModal();
    return;
  }

  try {
    const data = await requestJson(`/api/test-suites/${encodePathPart(suite.id)}`, {
      method: "PUT",
      body: JSON.stringify({ name }),
    });
    upsertTestSuiteInState(data.suite);
    closeTestSuiteRenameModal();
    renderContent();
    setNotice("测试集已重命名。", "success");
  } catch (error) {
    elements.renameTestSuiteName.setCustomValidity(error.message || "测试集重命名失败。");
    elements.renameTestSuiteName.reportValidity();
  }
}

async function deleteTestSuite(suiteId) {
  const suite = state.testSuites.suites.find((item) => item.id === suiteId);
  if (!suite || isAnyScriptJobRunning()) {
    return;
  }
  if (!window.confirm(`确认删除测试集“${suite.name}”？`)) {
    return;
  }

  try {
    await requestJson(`/api/test-suites/${encodePathPart(suite.id)}`, { method: "DELETE" });
    state.testSuites.suites = state.testSuites.suites.filter((item) => item.id !== suite.id);
    if (state.testSuites.selectedSuiteId === suite.id) {
      state.testSuites.selectedSuiteId = null;
      state.testSuites.selectedModule = TEST_SUITE_ALL_MODULE;
      resetTestSuiteExecutionHistory();
    }
    persistViewState();
    renderContent();
    setNotice("测试集已删除。", "success");
  } catch (error) {
    setNotice(error.message, "error");
  }
}

function selectTestSuite(suiteId) {
  if (!state.testSuites.suites.some((suite) => suite.id === suiteId)) {
    return;
  }

  state.activeSection = SECTION.TEST_SUITES;
  state.testSuites.selectedSuiteId = suiteId;
  state.testSuites.selectedModule = TEST_SUITE_ALL_MODULE;
  state.testSuites.activeTab = TEST_SUITE_VIEW_TAB.SCRIPTS;
  resetTestSuiteExecutionHistory();
  setNotice("");
  persistViewState();
  renderSideList();
  renderContent();
}

function switchTestSuiteViewTab(nextTab) {
  if (
    state.activeSection !== SECTION.TEST_SUITES ||
    !getSelectedTestSuite() ||
    !Object.values(TEST_SUITE_VIEW_TAB).includes(nextTab) ||
    state.testSuites.activeTab === nextTab
  ) {
    return;
  }

  state.testSuites.activeTab = nextTab;
  persistViewState();
  renderContent();
  if (nextTab === TEST_SUITE_VIEW_TAB.EXECUTION) {
    loadTestSuiteExecutionRecords(getSelectedTestSuite()?.id);
  }
}

function backToTestSuiteList() {
  state.testSuites.selectedSuiteId = null;
  state.testSuites.selectedModule = TEST_SUITE_ALL_MODULE;
  state.testSuites.activeTab = TEST_SUITE_VIEW_TAB.SCRIPTS;
  state.testSuites.selectedScriptKeys.clear();
  resetTestSuiteExecutionHistory();
  setNotice("");
  persistViewState();
  renderContent();
}

function normalizeSuiteAvailableModules(modules) {
  return (Array.isArray(modules) ? modules : [])
    .map((moduleItem) => {
      const moduleName = typeof moduleItem?.name === "string" ? moduleItem.name : "";
      const scripts = (Array.isArray(moduleItem?.scripts) ? moduleItem.scripts : [])
        .map((script) => {
          const filename = typeof script?.name === "string" ? script.name : "";
          if (!filename) {
            return null;
          }
          return {
            module_name: moduleName,
            filename,
            display_name:
              typeof script.display_name === "string" && script.display_name
                ? script.display_name
                : stripSpecSuffix(filename),
            path: typeof script.path === "string" ? script.path : "",
          };
        })
        .filter(Boolean);

      return {
        name: moduleName,
        path: typeof moduleItem?.path === "string" ? moduleItem.path : "",
        scripts,
      };
    })
    .filter((moduleItem) => moduleItem.name && moduleItem.scripts.length)
    .sort((left, right) => left.name.localeCompare(right.name));
}

function getSuiteAvailableEntries(moduleName = state.testSuites.addModalModule) {
  const modules =
    moduleName === TEST_SUITE_ALL_MODULE
      ? state.testSuites.availableModules
      : state.testSuites.availableModules.filter((moduleItem) => moduleItem.name === moduleName);

  return modules.flatMap((moduleItem) =>
    moduleItem.scripts.map((script) => ({
      ...script,
      key: getSuiteScriptKey(script.module_name, script.filename),
    })),
  );
}

function getSuiteExistingScriptKeys(suite = getSelectedTestSuite()) {
  return new Set((suite?.items || []).map((item) => getSuiteScriptKey(item.module_name, item.filename)));
}

function renderSuiteScriptModal() {
  const suite = getSelectedTestSuite();
  const existingKeys = getSuiteExistingScriptKeys(suite);
  const moduleOptions = [
    {
      name: TEST_SUITE_ALL_MODULE,
      label: "全部",
      count: state.testSuites.availableModules.reduce((total, moduleItem) => total + moduleItem.scripts.length, 0),
    },
    ...state.testSuites.availableModules.map((moduleItem) => ({
      name: moduleItem.name,
      label: moduleItem.name,
      count: moduleItem.scripts.length,
    })),
  ];
  const validModules = new Set(moduleOptions.map((moduleItem) => moduleItem.name));
  if (!validModules.has(state.testSuites.addModalModule)) {
    state.testSuites.addModalModule = TEST_SUITE_ALL_MODULE;
  }

  elements.suiteScriptModuleList.replaceChildren();
  moduleOptions.forEach((moduleItem) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "test-suite-module-button";
    button.classList.toggle("active", moduleItem.name === state.testSuites.addModalModule);
    button.title = moduleItem.label;
    button.innerHTML = "<span></span><span></span>";
    button.querySelector("span:first-child").textContent = moduleItem.label;
    button.querySelector("span:last-child").textContent = moduleItem.count;
    button.addEventListener("click", () => {
      state.testSuites.addModalModule = moduleItem.name;
      renderSuiteScriptModal();
    });
    elements.suiteScriptModuleList.appendChild(button);
  });

  const activeModule = moduleOptions.find((moduleItem) => moduleItem.name === state.testSuites.addModalModule);
  const entries = getSuiteAvailableEntries();
  elements.suiteScriptPickerTitle.textContent =
    activeModule?.name === TEST_SUITE_ALL_MODULE ? "全部脚本" : `${activeModule?.label || ""} 脚本`;
  elements.suiteScriptSelectionCount.textContent = `已选择 ${state.testSuites.selectedScriptKeys.size} 条`;
  elements.suiteScriptModalSubmit.disabled = state.testSuites.selectedScriptKeys.size === 0;
  elements.suiteAvailableScriptList.replaceChildren();

  if (!entries.length) {
    const empty = document.createElement("div");
    empty.className = "execution-empty";
    empty.innerHTML = "<h3>暂无可选脚本</h3><p>没有找到符合 tests/&lt;模块名&gt;/*.spec.ts 规则的脚本。</p>";
    elements.suiteAvailableScriptList.appendChild(empty);
    return;
  }

  const header = document.createElement("div");
  header.className = "suite-script-list-header";
  header.innerHTML = "<span>选择</span><span>脚本名称</span><span>模块</span>";
  elements.suiteAvailableScriptList.appendChild(header);

  entries.forEach((entry) => {
    const key = entry.key;
    const isAdded = existingKeys.has(key);
    const option = document.createElement("label");
    option.className = `suite-script-option ${isAdded ? "disabled" : ""}`.trim();
    option.title = entry.path || entry.filename;

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.disabled = isAdded;
    checkbox.checked = isAdded || state.testSuites.selectedScriptKeys.has(key);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        state.testSuites.selectedScriptKeys.add(key);
      } else {
        state.testSuites.selectedScriptKeys.delete(key);
      }
      renderSuiteScriptModal();
    });

    const title = document.createElement("span");
    title.className = "suite-script-option-title";
    title.textContent = entry.display_name || stripSpecSuffix(entry.filename);

    const moduleName = document.createElement("span");
    moduleName.className = "suite-script-option-module";
    moduleName.textContent = isAdded ? "已添加" : entry.module_name;

    option.append(checkbox, title, moduleName);
    elements.suiteAvailableScriptList.appendChild(option);
  });
}

async function openSuiteScriptModal() {
  const suite = getSelectedTestSuite();
  if (!suite) {
    return;
  }

  setNotice("");
  state.testSuites.addModalModule = TEST_SUITE_ALL_MODULE;
  state.testSuites.selectedScriptKeys.clear();
  state.testSuites.availableModules = [];
  elements.suiteScriptModal.classList.remove("hidden");
  elements.suiteAvailableScriptList.textContent = "正在加载脚本...";
  elements.suiteScriptModalSubmit.disabled = true;

  try {
    const data = await requestJson("/api/test-scripts");
    state.testSuites.availableModules = normalizeSuiteAvailableModules(data.modules || []);
    renderSuiteScriptModal();
  } catch (error) {
    elements.suiteAvailableScriptList.textContent = error.message;
  }
}

function closeSuiteScriptModal() {
  elements.suiteScriptModal.classList.add("hidden");
  state.testSuites.selectedScriptKeys.clear();
}

async function submitSuiteScripts() {
  const suite = getSelectedTestSuite();
  if (!suite) {
    return;
  }

  const existingKeys = getSuiteExistingScriptKeys(suite);
  const entriesByKey = new Map(getSuiteAvailableEntries(TEST_SUITE_ALL_MODULE).map((entry) => [entry.key, entry]));
  const additions = Array.from(state.testSuites.selectedScriptKeys)
    .map((key) => entriesByKey.get(key))
    .filter(Boolean)
    .filter((entry) => !existingKeys.has(entry.key))
    .map((entry) => ({
      module_name: entry.module_name,
      filename: entry.filename,
      display_name: entry.display_name,
      path: entry.path,
    }));

  if (!additions.length) {
    elements.suiteScriptModalSubmit.disabled = true;
    return;
  }

  try {
    const data = await requestJson(`/api/test-suites/${encodePathPart(suite.id)}/items`, {
      method: "POST",
      body: JSON.stringify({ items: additions }),
    });
    const updatedSuite = upsertTestSuiteInState(data.suite);
    state.testSuites.selectedSuiteId = updatedSuite?.id || suite.id;
    state.testSuites.selectedModule = TEST_SUITE_ALL_MODULE;
    persistViewState();
    closeSuiteScriptModal();
    renderContent();
    setNotice(`已添加 ${additions.length} 条脚本。`, "success");
  } catch (error) {
    setNotice(error.message, "error");
  }
}

async function removeTestSuiteItem(suiteId, itemId) {
  const suite = state.testSuites.suites.find((item) => item.id === suiteId);
  if (!suite || !itemId || isAnyScriptJobRunning()) {
    return;
  }
  if (!window.confirm("确认从测试集中移除这个脚本？")) {
    return;
  }

  try {
    const data = await requestJson(`/api/test-suites/${encodePathPart(suite.id)}/items/${encodeURIComponent(itemId)}`, {
      method: "DELETE",
    });
    upsertTestSuiteInState(data.suite);
    renderContent();
    setNotice("脚本已移除。", "success");
  } catch (error) {
    setNotice(error.message, "error");
  }
}

async function moveTestSuiteItem(suiteId, itemId, delta) {
  const suite = state.testSuites.suites.find((item) => item.id === suiteId);
  if (!suite || !itemId || isAnyScriptJobRunning()) {
    return;
  }
  const items = [...suite.items];
  const index = items.findIndex((item) => item.item_id === itemId);
  const nextIndex = index + delta;
  if (index < 0 || nextIndex < 0 || nextIndex >= items.length) {
    return;
  }
  [items[index], items[nextIndex]] = [items[nextIndex], items[index]];

  try {
    const data = await requestJson(`/api/test-suites/${encodePathPart(suite.id)}/items/reorder`, {
      method: "PUT",
      body: JSON.stringify({ item_ids: items.map((item) => item.item_id) }),
    });
    upsertTestSuiteInState(data.suite);
    renderContent();
  } catch (error) {
    setNotice(error.message, "error");
  }
}

function refreshTestSuiteExecutionRecordIfCurrent(suiteId) {
  if (
    state.activeSection === SECTION.TEST_SUITES &&
    state.testSuites.selectedSuiteId === suiteId &&
    state.testSuites.activeTab === TEST_SUITE_VIEW_TAB.EXECUTION
  ) {
    renderTestSuiteExecutionRecord();
  }
}

function setTestSuiteExecutionRecord(suite, updates) {
  if (!suite?.id) {
    return null;
  }

  const key = getTestSuiteExecutionRecordKey(suite.id);
  const previous = state.testSuites.executionRecords[key] || {
    status: "idle",
    run_id: "",
    job_id: "",
    suite_id: suite.id,
    suite_name: suite.name,
    items: suite.items || [],
    execution_mode: EXECUTION_MODE.BATCH,
    command: "",
    logs: "",
    returncode: undefined,
    total_files: suite.items?.length || 0,
    completed_files: 0,
    report: null,
    report_error: "",
    script_results: {},
    error: "",
    started_at: null,
    finished_at: null,
  };
  const next = normalizeTestSuiteExecutionRecord({
    ...previous,
    ...updates,
    run_id: Object.prototype.hasOwnProperty.call(updates, "run_id") ? updates.run_id || "" : previous.run_id || "",
    job_id: Object.prototype.hasOwnProperty.call(updates, "job_id") ? updates.job_id || "" : previous.job_id || "",
    suite_id: suite.id,
    suite_name: updates.suite_name || previous.suite_name || suite.name,
    items: updates.items || previous.items || suite.items || [],
    execution_mode: normalizeExecutionModeValue(updates.execution_mode || previous.execution_mode),
    logs: Object.prototype.hasOwnProperty.call(updates, "logs") ? updates.logs : previous.logs || "",
    script_results: updates.script_results || previous.script_results || {},
    total_files: Number(updates.total_files) || Number(previous.total_files) || suite.items?.length || 0,
    completed_files: Object.prototype.hasOwnProperty.call(updates, "completed_files")
      ? Number(updates.completed_files) || 0
      : Number(previous.completed_files) || 0,
    updated_at: Date.now(),
  });
  state.testSuites.executionRecords[key] = next;
  persistTestSuiteExecutionRecords(key);
  refreshTestSuiteExecutionRecordIfCurrent(suite.id);
  renderTestSuiteProgressModal(suite);
  return next;
}

function appendTestSuiteExecutionRecordLog(suite, text) {
  if (!text || !suite) {
    return;
  }

  const current = state.testSuites.executionRecords[getTestSuiteExecutionRecordKey(suite.id)] || {};
  setTestSuiteExecutionRecord(suite, {
    status: current.status || "running",
    command: current.command || "",
    logs: `${current.logs || ""}${text}`,
  });
}

function handleTestSuiteExecutionStreamEvent({ event, data }, previousResult, suite) {
  if (event === "status") {
    const nextResult = {
      ...previousResult,
      status: data.status || previousResult.status,
      run_id: data.run_id || previousResult.run_id || "",
      job_id: data.job_id || previousResult.job_id || "",
      suite_id: data.suite_id || previousResult.suite_id || suite.id,
      suite_name: data.suite_name || previousResult.suite_name || suite.name,
      items: previousResult.items || suite.items || [],
      execution_mode: normalizeExecutionModeValue(data.execution_mode || previousResult.execution_mode),
      command: data.command || previousResult.command,
      error: data.error || previousResult.error,
      returncode: Object.prototype.hasOwnProperty.call(data, "returncode") ? data.returncode : previousResult.returncode,
      total_files: Number(data.total_files) || Number(previousResult.total_files) || suite.items?.length || 0,
      completed_files: Object.prototype.hasOwnProperty.call(data, "completed_files")
        ? Number(data.completed_files) || 0
        : Number(previousResult.completed_files) || 0,
      logs: previousResult.logs || "",
      output: data.output || previousResult.output,
      report: Object.prototype.hasOwnProperty.call(data, "report") ? data.report : previousResult.report,
      report_error: data.report_error || previousResult.report_error,
      script_results: mergeTestSuiteScriptResults(previousResult.script_results, data.script_results),
    };
    setTestSuiteExecutionRecord(suite, nextResult);
    return nextResult;
  }

  if (event === "log") {
    const text = data.message ? `${data.message}\n` : "";
    appendTestSuiteExecutionRecordLog(suite, text);
    return { ...previousResult, logs: `${previousResult.logs || ""}${text}` };
  }

  if (event === "delta") {
    const text = data.text || "";
    appendTestSuiteExecutionRecordLog(suite, text);
    return { ...previousResult, logs: `${previousResult.logs || ""}${text}` };
  }

  if (event === "done") {
    const status = data.status || (data.ok === false ? "failed" : "succeeded");
    const nextResult = {
      ...previousResult,
      status,
      run_id: data.run_id || previousResult.run_id || "",
      job_id: data.job_id || previousResult.job_id || "",
      error: data.error || previousResult.error,
      returncode: Object.prototype.hasOwnProperty.call(data, "returncode") ? data.returncode : previousResult.returncode,
      total_files: Number(data.total_files) || Number(previousResult.total_files) || suite.items?.length || 0,
      completed_files: Object.prototype.hasOwnProperty.call(data, "completed_files")
        ? Number(data.completed_files) || 0
        : Number(previousResult.completed_files) || suite.items?.length || 0,
      output: data.output || previousResult.logs || "",
      logs: previousResult.logs || data.output || "",
      execution_mode: normalizeExecutionModeValue(data.execution_mode || previousResult.execution_mode),
      report: Object.prototype.hasOwnProperty.call(data, "report") ? data.report : previousResult.report,
      report_error: data.report_error || previousResult.report_error,
      script_results: mergeTestSuiteScriptResults(previousResult.script_results, data.script_results),
    };
    setTestSuiteExecutionRecord(suite, nextResult);
    return nextResult;
  }

  return previousResult;
}

async function readTestSuiteExecutionStream(response, suite) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let result = { status: "running", logs: "", items: suite.items || [], total_files: suite.items?.length || 0, completed_files: 0 };

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
        result = handleTestSuiteExecutionStreamEvent(event, result, suite);
      }
      boundaryIndex = buffer.indexOf("\n\n");
    }
  }

  buffer += decoder.decode();
  const trailingEvent = parseSseBlock(buffer.trim());
  if (trailingEvent) {
    result = handleTestSuiteExecutionStreamEvent(trailingEvent, result, suite);
  }

  return result;
}

async function executeSelectedTestSuite() {
  const suite = getSelectedTestSuite();
  if (!suite || !suite.items.length || isAnyScriptJobRunning()) {
    return;
  }

  const executionMode = await openExecutionModeModal({
    title: "选择测试集执行模式",
    summary: `将执行测试集“${suite.name}”中的 ${suite.items.length} 个脚本。默认模式保持现有批量执行行为。`,
    target: "test-suite",
  });
  if (!executionMode) {
    return;
  }
  if (isAnyScriptJobRunning()) {
    return;
  }

  const startedAt = Date.now();
  state.testSuiteExecution.isRunning = true;
  state.testSuites.activeTab = TEST_SUITE_VIEW_TAB.EXECUTION;
  state.testSuites.executionHistory.selectedRunId = `local-${suite.id}`;
  persistViewState();
  setTestSuiteExecutionRecord(suite, {
    status: "running",
    run_id: "",
    job_id: "",
    suite_name: suite.name,
    items: suite.items,
    execution_mode: executionMode,
    command: "",
    logs: "",
    returncode: undefined,
    total_files: suite.items.length,
    completed_files: 0,
    report: null,
    report_error: "正在执行测试集，执行完成后会自动显示本次 Playwright Report。",
    script_results: Object.fromEntries(suite.items.map((item) => [getSuiteScriptKey(item.module_name, item.filename), "running"])),
    error: "",
    started_at: startedAt,
    finished_at: null,
  });
  renderContent();
  openTestSuiteProgressModal(suite);
  setNotice(`正在${getExecutionModeLabel(executionMode)}，请稍候。`);

  let completedRunId = "";
  try {
    const response = await fetch(`/api/test-suites/${encodePathPart(suite.id)}/execution-stream`, {
      method: "POST",
      headers: getProjectRequestHeaders({
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({
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

    const result = await readTestSuiteExecutionStream(response, suite);
    completedRunId = result.run_id || "";
    const finishedAt = Date.now();
    if (result.status !== "succeeded" && result.status !== "failed") {
      result.status = "failed";
      result.error = "流式响应提前结束。";
    }
    result.script_results = finalizeTestSuiteScriptResults(
      suite.items,
      result.script_results,
      result.status === "failed" ? "interrupted" : "unknown",
    );
    result.completed_files = suite.items.length;
    setTestSuiteExecutionRecord(suite, {
      status: result.status,
      run_id: result.run_id || "",
      job_id: result.job_id || "",
      error: result.error || "",
      logs: result.logs || "",
      report: result.report,
      report_error: result.report_error || "",
      script_results: result.script_results || {},
      execution_mode: result.execution_mode || executionMode,
      returncode: result.returncode,
      total_files: Number(result.total_files) || suite.items.length,
      completed_files: Number(result.completed_files) || suite.items.length,
      finished_at: finishedAt,
    });

    if (result.status === "succeeded") {
      setNotice(result.report ? "测试集执行完成，Playwright Report 已更新。" : "测试集执行完成，未找到 Playwright Report。", result.report ? "success" : "");
    } else {
      setNotice(result.error || "测试集执行失败。", "error");
    }
  } catch (error) {
    const finishedAt = Date.now();
    const current = state.testSuites.executionRecords[getTestSuiteExecutionRecordKey(suite.id)] || {};
    completedRunId = current.run_id || "";
    const prefix = current.logs && !current.logs.endsWith("\n") ? "\n" : "";
    const failedResults = finalizeTestSuiteScriptResults(suite.items, current.script_results, "interrupted");
    setTestSuiteExecutionRecord(suite, {
      status: "failed",
      run_id: completedRunId,
      error: error.message,
      logs: `${current.logs || ""}${prefix}${error.message}\n`,
      script_results: failedResults,
      execution_mode: executionMode,
      total_files: suite.items.length,
      completed_files: suite.items.length,
      finished_at: finishedAt,
    });
    setNotice(error.message, "error");
  } finally {
    state.testSuiteExecution.isRunning = false;
    if (completedRunId) {
      await loadTestSuiteExecutionRecords(suite.id, { selectRunId: completedRunId, silent: true, force: true });
    }
    renderContent();
  }
}

async function loadTestSuites() {
  setNotice("");
  setLoading(true);

  try {
    const data = await requestJson("/api/test-suites");
    state.testSuites.suites = (data.suites || []).map(normalizeTestSuite).filter(Boolean);
    if (
      state.testSuites.selectedSuiteId &&
      !state.testSuites.suites.some((suite) => suite.id === state.testSuites.selectedSuiteId)
    ) {
      state.testSuites.selectedSuiteId = null;
      state.testSuites.selectedModule = TEST_SUITE_ALL_MODULE;
      resetTestSuiteExecutionHistory();
    }
    renderSideList();
    renderContent();
    if (state.testSuites.selectedSuiteId && state.testSuites.activeTab === TEST_SUITE_VIEW_TAB.EXECUTION) {
      loadTestSuiteExecutionRecords(state.testSuites.selectedSuiteId);
    }
  } catch (error) {
    state.testSuites.suites = [];
    state.testSuites.selectedSuiteId = null;
    state.testSuites.selectedModule = TEST_SUITE_ALL_MODULE;
    resetTestSuiteExecutionHistory();
    renderSideList();
    renderContent();
    setNotice(error.message, "error");
  } finally {
    setLoading(false);
  }
}

return {
  getSelectedTestSuite,
  resetTestSuiteExecutionHistory,
  getTestSuiteExecutionRecordKey,
  getTestSuiteExecutionStatusText,
  renderTestSuiteList,
  renderExecutionResultPanel,
  renderTestSuiteExecutionRecord,
  closeTestSuiteProgressModal,
  closeTestSuiteExecutionVideo,
  renderTestSuiteDetail,
  openTestSuiteCreateModal,
  closeTestSuiteCreateModal,
  closeTestSuiteRenameModal,
  submitTestSuiteCreate,
  loadTestSuiteExecutionRecords,
  submitTestSuiteRename,
  selectTestSuite,
  switchTestSuiteViewTab,
  backToTestSuiteList,
  openSuiteScriptModal,
  closeSuiteScriptModal,
  submitSuiteScripts,
  executeSelectedTestSuite,
  loadTestSuites,
  // Pure/status operations are exported for focused VM regression tests.
  getSuiteModuleOptions,
  getSuiteItemsForModule,
  getTestSuiteScriptResultStatus,
  getTestSuiteExecutionRunSummary,
  buildExecutionSummaryFromResults,
  getTestSuiteProgressCounts,
  getTestSuiteProgressStatusText,
  getRenderableTestSuiteExecutionRecords,
  ensureSelectedTestSuiteExecutionRun,
  normalizeSuiteAvailableModules,
  getSuiteAvailableEntries,
  getSuiteExistingScriptKeys,
  upsertTestSuiteInState,
  moveTestSuiteItem,
  handleTestSuiteExecutionStreamEvent,
  readTestSuiteExecutionStream,
};
}

window.createTestSuiteResultHelpers = createTestSuiteResultHelpers;
window.createTestSuitesFeature = createTestSuitesFeature;
