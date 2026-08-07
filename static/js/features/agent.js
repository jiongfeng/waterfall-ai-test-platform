function createAgentAutoTest(root, options = {}) {
  if (!root) {
    throw new Error("Agent 挂载容器不存在。");
  }
  const renderExecutionResultPanel = options.renderExecutionResultPanel;
  const parseSseBlock = options.parseSseBlock;

  const agentElement = (name) => {
    const element = root.querySelector(`[data-agent-id="${name}"]`);
    if (!element) {
      throw new Error(`Agent 页面元素不存在：${name}`);
    }
    return element;
  };

const AGENT_STEPS = [
  ["upload_requirement", "需求"],
  ["analyze_requirement", "需求解析"],
  ["review_modules", "模块审查"],
  ["generate_plans", "计划生成"],
  ["prepare_scripts", "脚本准备"],
  ["create_suite", "测试集"],
  ["run_suite", "执行"],
];

const INITIAL_EVENT_LIMIT = 200;
const MAX_DETAIL_JSON_CHARS = 6000;
const RETRY_FLOW_ACTIVE_STATUSES = new Set(["queued", "running", "finalizing", "cancelling"]);
const RETRY_FLOW_TERMINAL_STATUSES = new Set(["succeeded", "failed", "blocked", "cancelled"]);
const RETRY_FLOW_PHASES = ["generating", "executing", "repairing", "verifying"];

const state = {
  currentProjectKey: options.projectKey || "",
  isActive: false,
  hasLoaded: false,
  requirements: [],
  runs: [],
  selectedRunId: "",
  selectedRun: null,
  steps: [],
  events: [],
  activeStepKey: "upload_requirement",
  lastEventId: 0,
  streamController: null,
  refreshTimer: null,
  runMenuOpen: false,
  runSearchQuery: "",
  currentArtifacts: [],
  openArtifact: null,
  executionResultKey: "",
  executionResultRecord: null,
  executionResultLoading: false,
  executionResultError: "",
  executionResultRequestId: 0,
  runDetailRequestId: 0,
  jobCache: new Map(),
  contentCache: new Map(),
  coverageProfiles: [],
  defaultCoverageProfile: DEFAULT_COVERAGE_PROFILE,
  retryFlows: [],
  activeRetryFlows: [],
  openRetryFlowId: "",
  retryRequestPending: false,
  retryTerminalRefreshPending: false,
  artifactModalOpener: null,
};

const elements = {
  currentRunMain: agentElement("currentRunMain"),
  currentRunTitle: agentElement("currentRunTitle"),
  currentRunStatus: agentElement("currentRunStatus"),
  runDropdown: agentElement("runDropdown"),
  runSearch: agentElement("runSearch"),
  runList: agentElement("runList"),
  newRunButton: agentElement("newRunButton"),
  launchForm: agentElement("launchForm"),
  requirementFile: agentElement("requirementFile"),
  fileLabel: agentElement("fileLabel"),
  requirementSelect: agentElement("requirementSelect"),
  coverageProfile: agentElement("coverageProfile"),
  coveragePrompt: agentElement("coveragePrompt"),
  coverageCustomized: agentElement("coverageCustomized"),
  coverageReset: agentElement("coverageReset"),
  startButton: agentElement("startButton"),
  resumeButton: agentElement("resumeButton"),
  cancelButton: agentElement("cancelButton"),
  cancelButtonLabel: agentElement("cancelButtonLabel"),
  runSubtitle: agentElement("runSubtitle"),
  runTitle: agentElement("runTitle"),
  runStatus: agentElement("runStatus"),
  planGenerationDetails: agentElement("planGenerationDetails"),
  planGenerationMeta: agentElement("planGenerationMeta"),
  planGenerationPrompt: agentElement("planGenerationPrompt"),
  stepTimeline: agentElement("stepTimeline"),
  artifactPanel: agentElement("artifactPanel"),
  artifactStageSummary: agentElement("artifactStageSummary"),
  artifactList: agentElement("artifactList"),
  eventSummary: agentElement("eventSummary"),
  eventLog: agentElement("eventLog"),
  executionResultPanel: agentElement("executionResultPanel"),
  executionResultTitle: agentElement("executionResultTitle"),
  executionResultSummary: agentElement("executionResultSummary"),
  executionResultReportLink: agentElement("executionResultReportLink"),
  executionResultEmpty: agentElement("executionResultEmpty"),
  executionResultWrap: agentElement("executionResultWrap"),
  executionResultTableBody: agentElement("executionResultTableBody"),
  executionResultLogPanel: agentElement("executionResultLogPanel"),
  executionResultLogStatus: agentElement("executionResultLogStatus"),
  executionResultLog: agentElement("executionResultLog"),
  artifactModal: agentElement("artifactModal"),
  artifactModalBackdrop: agentElement("artifactModalBackdrop"),
  artifactModalClose: agentElement("artifactModalClose"),
  artifactModalTitle: agentElement("artifactModalTitle"),
  artifactModalMeta: agentElement("artifactModalMeta"),
  artifactPromptText: agentElement("artifactPromptText"),
  artifactContentTitle: agentElement("artifactContentTitle"),
  artifactContentText: agentElement("artifactContentText"),
  artifactDiagnosticActions: agentElement("artifactDiagnosticActions"),
  artifactDiagnosticHint: agentElement("artifactDiagnosticHint"),
  artifactDiagnosticDownload: agentElement("artifactDiagnosticDownload"),
  artifactRetryProgress: agentElement("artifactRetryProgress"),
  artifactRetryProgressTitle: agentElement("artifactRetryProgressTitle"),
  artifactRetryProgressStatus: agentElement("artifactRetryProgressStatus"),
  artifactRetryProgressBadge: agentElement("artifactRetryProgressBadge"),
  artifactRetryProgressSteps: agentElement("artifactRetryProgressSteps"),
  artifactRetryProgressNote: agentElement("artifactRetryProgressNote"),
  artifactRetryOption: agentElement("artifactRetryOption"),
  artifactRetryAutoRepair: agentElement("artifactRetryAutoRepair"),
  artifactRetryCancelButton: agentElement("artifactRetryCancelButton"),
  artifactRetryButton: agentElement("artifactRetryButton"),
  retryStatusBar: agentElement("retryStatusBar"),
  retryStatusTitle: agentElement("retryStatusTitle"),
  retryStatusMeta: agentElement("retryStatusMeta"),
  retryStatusView: agentElement("retryStatusView"),
  newTaskModal: agentElement("newTaskModal"),
  newTaskModalBackdrop: agentElement("newTaskModalBackdrop"),
  newTaskModalClose: agentElement("newTaskModalClose"),
  newTaskCancelButton: agentElement("newTaskCancelButton"),
  notice: agentElement("notice"),
};

const agentApiClient = options.apiClient || createApiClient({
  getProjectKey: () => state.currentProjectKey,
  onUnauthorized(data) {
    window.location.href = data.redirect || "/login";
  },
});
const {
  getProjectHeaders,
  requestJson,
  getDownloadFilename,
} = agentApiClient;
const scriptPreparation = createAgentScriptPreparationFeature(
  root.querySelector('[data-script-preparation-id="root"]'),
  {
    apiClient: agentApiClient,
    getRunId: () => state.selectedRunId,
    setNotice,
    window,
    document,
  },
);

function agentCoverageProfile(key = elements.coverageProfile.value) {
  return state.coverageProfiles.find((item) => item.key === key) || state.coverageProfiles[0] || null;
}

function agentCoverageMeta(run) {
  const generation = isPlainObject(run?.plan_generation) ? run.plan_generation : {};
  const profile = state.coverageProfiles.find((item) => item.key === generation.coverage_profile);
  return `模板来源：${profile?.label || "核心回归"}${generation.prompt_customized ? " · 已自定义" : ""}`;
}

function renderAgentCoverageState() {
  const profile = agentCoverageProfile();
  const customized = elements.coveragePrompt.value.trim() !== String(profile?.template_prompt || "").trim();
  elements.coverageCustomized.textContent = customized ? "· 已自定义" : "";
}

function populateAgentCoveragePrompt(profileKey = state.defaultCoverageProfile) {
  elements.coverageProfile.innerHTML = state.coverageProfiles
    .map((item) => `<option value="${escapeHtml(item.key)}" ${item.key === profileKey ? "selected" : ""}>${escapeHtml(item.label)}</option>`)
    .join("");
  elements.coverageProfile.dataset.previous = profileKey;
  elements.coveragePrompt.value = agentCoverageProfile(profileKey)?.template_prompt || "";
  renderAgentCoverageState();
}

async function loadAgentCoverageDefaults() {
  const data = await requestJson("/api/plan-generation-defaults");
  state.coverageProfiles = Array.isArray(data.coverage_profiles) ? data.coverage_profiles : [];
  state.defaultCoverageProfile = data.default_coverage_profile || DEFAULT_COVERAGE_PROFILE;
  populateAgentCoveragePrompt(state.defaultCoverageProfile);
}

function changeAgentCoverageProfile() {
  const previousKey = elements.coverageProfile.dataset.previous || state.defaultCoverageProfile;
  const previousTemplate = agentCoverageProfile(previousKey)?.template_prompt || "";
  if (
    elements.coveragePrompt.value.trim() !== String(previousTemplate).trim() &&
    !window.confirm("Agent 计划生成策略已被编辑，切换档位将替换这些修改。是否继续？")
  ) {
    elements.coverageProfile.value = previousKey;
    return;
  }
  const nextKey = elements.coverageProfile.value;
  elements.coverageProfile.dataset.previous = nextKey;
  elements.coveragePrompt.value = agentCoverageProfile(nextKey)?.template_prompt || "";
  renderAgentCoverageState();
}

function resetAgentCoveragePrompt() {
  const template = agentCoverageProfile()?.template_prompt || "";
  if (elements.coveragePrompt.value.trim() !== String(template).trim() && !window.confirm("恢复模板将丢弃当前策略修改。是否继续？")) {
    return;
  }
  elements.coveragePrompt.value = template;
  renderAgentCoverageState();
}

function setNotice(message, type = "") {
  elements.notice.textContent = message || "";
  elements.notice.setAttribute("role", type === "error" ? "alert" : "status");
  elements.notice.className = `notice ${type || ""}`.trim();
  elements.notice.classList.toggle("hidden", !message);
  if (message) {
    window.clearTimeout(setNotice.timer);
    setNotice.timer = window.setTimeout(() => setNotice(""), 5000);
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function encodePathPart(value) {
  return encodeURIComponent(String(value || ""));
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function isPlainObject(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function formatJsonPreview(value) {
  const text = JSON.stringify(value, null, 2);
  if (text.length <= MAX_DETAIL_JSON_CHARS) {
    return text;
  }
  return `${text.slice(0, MAX_DETAIL_JSON_CHARS)}\n... 已截断，完整内容请查看日志下载或数据库记录。`;
}

function formatClock(timestamp) {
  const value = Number(timestamp);
  if (!value) {
    return "";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function formatDateTime(timestamp) {
  const value = Number(timestamp);
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDuration(startedAt, finishedAt, status) {
  const start = Number(startedAt);
  if (!start) {
    return "";
  }
  const end = Number(finishedAt) || (isActiveStatus(status) ? Date.now() : 0);
  if (!end || end < start) {
    return "";
  }
  const totalSeconds = Math.max(0, Math.floor((end - start) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    const restMinutes = minutes % 60;
    return `耗时 ${hours}h ${restMinutes}m`;
  }
  if (minutes > 0) {
    return `耗时 ${minutes}m ${String(seconds).padStart(2, "0")}s`;
  }
  return `耗时 ${seconds}s`;
}

function statusText(status) {
  return (
    {
      queued: "等待中",
      running: "运行中",
      finalizing: "收尾中",
      succeeded: "完成",
      succeeded_with_unresolved: "部分成功",
      failed: "失败",
      cancelled: "已取消",
      cancelling: "取消中",
      skipped: "跳过",
      passed: "通过",
      repaired: "已修复",
      recovered: "已恢复",
      resolved: "已恢复",
      retrying: "重试中",
      blocked: "受阻",
      verifying: "复验中",
      excluded: "已排除",
      awaiting_action: "等待处理",
      awaiting_script_action: "待人工处理",
      awaiting_human: "待人工处理",
      ready: "已通过",
      abandoned: "已放弃",
      generating: "生成中",
      executing: "执行中",
      repairing: "修复中",
      analyzing: "分析中",
      unresolved: "未解决",
      ignored: "保留未解决",
      kept_unresolved: "保留未解决",
      deleted: "已删除",
      pending_verification: "待执行验证",
      idle: "idle",
    }[status] || status || "idle"
  );
}

function isActiveStatus(status) {
  return ["queued", "running", "cancelling"].includes(status);
}

function isResumableStatus(status) {
  return ["failed", "cancelled"].includes(status);
}

function isScriptPreparationPaused(run = state.selectedRun) {
  return Boolean(
    run &&
      (run.status === "awaiting_script_action" ||
        (run.current_step === "prepare_scripts" && getStep("prepare_scripts")?.status === "awaiting_action")),
  );
}

function normalizeRetryFlow(value) {
  if (!isPlainObject(value)) {
    return null;
  }
  const retryFlowId = String(value.retry_flow_id || value.flow_id || value.id || "").trim();
  if (!retryFlowId) {
    return null;
  }
  const status = String(value.status || "queued").toLowerCase();
  let currentPhase = String(value.current_phase || value.phase || "queued").toLowerCase();
  const phaseAliases = {
    generate_scripts: "generating",
    generation: "generating",
    generated: "executing",
    execute_scripts: "executing",
    execution: "executing",
    repair_scripts: "repairing",
    repair: "repairing",
    verification: "verifying",
    verify: "verifying",
    verified: "completed",
  };
  currentPhase = phaseAliases[currentPhase] || currentPhase;
  if (RETRY_FLOW_TERMINAL_STATUSES.has(status) && !currentPhase) {
    currentPhase = "completed";
  }
  return {
    ...value,
    retry_flow_id: retryFlowId,
    status,
    current_phase: currentPhase || "queued",
    root_attempt_id: String(value.root_attempt_id || value.source_attempt_id || ""),
    item_key: String(value.item_key || ""),
    module_name: String(value.module_name || value.item?.module_name || ""),
    plan_filename: String(value.plan_filename || value.item?.plan_filename || ""),
    filename: String(value.filename || value.item?.filename || ""),
    progress_message: String(value.progress_message || ""),
    auto_repair: value.auto_repair !== false,
  };
}

function isActiveRetryFlow(flow) {
  return Boolean(flow && RETRY_FLOW_ACTIVE_STATUSES.has(flow.status));
}

function shouldObserveSelectedRun() {
  return Boolean(isActiveStatus(state.selectedRun?.status) || isScriptPreparationPaused() || state.activeRetryFlows.length);
}

function shouldRefreshAgentProject() {
  return Boolean(shouldObserveSelectedRun() || state.runs.some(runHasActiveRetrySummary));
}

function retryFlowTitle(flow) {
  return flow?.filename || flow?.plan_filename || flow?.item_key || "脚本";
}

function retryPhaseText(phase) {
  return (
    {
      queued: "等待开始",
      generating: "正在重新生成",
      executing: "正在执行新脚本",
      repairing: "执行失败，正在自动修复",
      verifying: "修复完成，正在复验",
      completed: "流程已完成",
    }[phase] || phase || "等待开始"
  );
}

function retryFlowProgressText(flow) {
  if (!flow) {
    return "等待重试";
  }
  if (flow.progress_message) {
    return flow.progress_message;
  }
  if (flow.status === "succeeded") {
    return "已恢复并验证通过";
  }
  if (flow.status === "failed") {
    return "重试并验证失败";
  }
  if (flow.status === "blocked") {
    return "执行受阻，等待处理";
  }
  if (flow.status === "cancelling") {
    return "正在停止重试";
  }
  if (flow.status === "cancelled") {
    return "重试已停止";
  }
  return retryPhaseText(flow.current_phase);
}

function selectedRunStorageKey(projectKey = state.currentProjectKey) {
  return `agent:selected-run:${projectKey || "default"}`;
}

function retryFlowStorageKey(kind, projectKey = state.currentProjectKey) {
  return `agent:retry-flow:${kind}:${projectKey || "default"}`;
}

function readStoredIdSet(key) {
  return new Set(asArray(safeJsonParse(readStorageItem(key), [])).map((item) => String(item)).filter(Boolean));
}

function writeStoredIdSet(key, values) {
  writeStorageItem(key, JSON.stringify(Array.from(values).slice(-100)));
}

function persistSelectedRunId(runId = state.selectedRunId) {
  if (runId) {
    writeStorageItem(selectedRunStorageKey(), runId);
  }
}

function markRetryFlowWatched(flowId) {
  if (!flowId) {
    return;
  }
  const key = retryFlowStorageKey("watched");
  const values = readStoredIdSet(key);
  values.add(String(flowId));
  writeStoredIdSet(key, values);
}

function markRetryFlowNotified(flowId) {
  if (!flowId) {
    return;
  }
  const key = retryFlowStorageKey("notified");
  const values = readStoredIdSet(key);
  values.add(String(flowId));
  writeStoredIdSet(key, values);
}

function recomputeActiveRetryFlows() {
  state.activeRetryFlows = state.retryFlows.filter(isActiveRetryFlow);
  state.activeRetryFlows.forEach((flow) => markRetryFlowWatched(flow.retry_flow_id));
}

function mergeRetryFlow(flowValue) {
  const next = normalizeRetryFlow(flowValue);
  if (!next) {
    return null;
  }
  const index = state.retryFlows.findIndex((item) => item.retry_flow_id === next.retry_flow_id);
  if (index >= 0) {
    state.retryFlows[index] = normalizeRetryFlow({ ...state.retryFlows[index], ...next });
  } else {
    state.retryFlows.push(next);
  }
  recomputeActiveRetryFlows();
  return state.retryFlows.find((item) => item.retry_flow_id === next.retry_flow_id) || next;
}

function applyRetryFlowData(data, { replace = true } = {}) {
  const runData = isPlainObject(data?.run) ? data.run : {};
  const hasAll = Array.isArray(data?.retry_flows) || Array.isArray(runData.retry_flows);
  const hasActive = Array.isArray(data?.active_retry_flows) || Array.isArray(runData.active_retry_flows);
  if (!hasAll && !hasActive) {
    return false;
  }
  const all = asArray(data?.retry_flows || runData.retry_flows);
  const active = asArray(data?.active_retry_flows || runData.active_retry_flows);
  const source = hasAll ? [...all, ...active] : [...state.retryFlows, ...active];
  const merged = new Map();
  if (!replace && !hasAll) {
    state.retryFlows.forEach((flow) => merged.set(flow.retry_flow_id, flow));
  }
  source.forEach((value) => {
    const flow = normalizeRetryFlow(value);
    if (!flow) {
      return;
    }
    const previous = merged.get(flow.retry_flow_id) || {};
    merged.set(flow.retry_flow_id, normalizeRetryFlow({ ...previous, ...flow }));
  });
  state.retryFlows = Array.from(merged.values()).filter(Boolean);
  recomputeActiveRetryFlows();
  return true;
}

function notifyCompletedRetryFlows() {
  if (!state.isActive || !state.hasLoaded) {
    return;
  }
  const watched = readStoredIdSet(retryFlowStorageKey("watched"));
  const notified = readStoredIdSet(retryFlowStorageKey("notified"));
  const completed = state.retryFlows
    .filter((flow) => RETRY_FLOW_TERMINAL_STATUSES.has(flow.status) && watched.has(flow.retry_flow_id) && !notified.has(flow.retry_flow_id))
    .sort((left, right) => Number(left.finished_at || left.updated_at || 0) - Number(right.finished_at || right.updated_at || 0));
  if (!completed.length) {
    return;
  }
  if (completed.length === 1) {
    const flow = completed[0];
    const successful = flow.status === "succeeded";
    setNotice(
      `“${retryFlowTitle(flow)}”${successful ? "已重新生成并验证通过" : retryFlowProgressText(flow)}。`,
      successful ? "success" : "error",
    );
  } else {
    const succeeded = completed.filter((flow) => flow.status === "succeeded").length;
    setNotice(
      `${completed.length} 个脚本的重试并验证已结束：${succeeded} 个通过，${completed.length - succeeded} 个失败或受阻。`,
      succeeded === completed.length ? "success" : "error",
    );
  }
  completed.forEach((flow) => markRetryFlowNotified(flow.retry_flow_id));
}

function runHasActiveRetrySummary(run) {
  return Boolean(
    asArray(run?.active_retry_flows).length ||
      Number(run?.active_retry_flow_count || run?.retrying_count || 0) > 0 ||
      (run?.run_id === state.selectedRunId && state.activeRetryFlows.length),
  );
}

function getActiveRun() {
  return state.runs.find((run) => isActiveStatus(run.status)) || null;
}

function setBadge(element, status) {
  element.textContent = statusText(status);
  element.className = `status-badge ${status || ""}`.trim();
}

function getStep(stepKey) {
  return state.steps.find((step) => step.step_key === stepKey) || null;
}

function getStepOutput(stepKey) {
  return getStep(stepKey)?.output || {};
}

function getStepInput(stepKey) {
  return getStep(stepKey)?.input || {};
}

function agentStepLabel(stepKey) {
  return (AGENT_STEPS.find(([key]) => key === stepKey) || [stepKey, stepKey])[1];
}

function countSummary(counts) {
  if (!counts || typeof counts !== "object") {
    return "";
  }
  const labels = {
    generated: "生成",
    kept: "保留",
    updated: "修改",
    deleted: "删除",
    failed: "失败",
    excluded: "排除",
    recovered: "恢复",
    repaired: "修复",
    retrying: "重试中",
    resolved: "已恢复",
    handled: "已处理",
    unresolved: "未处理",
    generation_failed: "生成失败",
    repair_failed: "修复失败",
    scripts: "脚本",
    plans: "计划",
    modules: "模块",
    passed: "通过",
    skipped: "跳过",
    unknown: "未知",
    total: "总数",
    suite_count: "测试集",
  };
  return Object.entries(counts)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([key, value]) => `<span class="count-pill">${escapeHtml(labels[key] || key)} ${escapeHtml(value)}</span>`)
    .join("");
}

function progressText(stepKey, step) {
  const counts = step?.counts || {};
  const output = step?.output || {};
  if (stepKey === "generate_plans" && (counts.modules || counts.generated || counts.failed)) {
    return `${Number(counts.generated || 0)} / ${Number(counts.modules || counts.generated || 0)}`;
  }
  if (stepKey === "prepare_scripts" && counts.total) {
    return `${Number(counts.ready || 0) + Number(counts.abandoned || 0)} / ${Number(counts.total)}`;
  }
  if (stepKey === "run_suite" && (counts.total || counts.passed || counts.failed)) {
    return `${Number(counts.passed || 0)} / ${Number(counts.total || counts.passed || 0)}`;
  }
  if (counts.generated) {
    return String(counts.generated);
  }
  if (counts.kept) {
    return String(counts.kept);
  }
  if (counts.total) {
    return String(counts.total);
  }
  if (asArray(output.plans).length) {
    return String(asArray(output.plans).length);
  }
  if (asArray(output.scripts).length) {
    return String(asArray(output.scripts).length);
  }
  return "";
}

function renderRunList() {
  const run = state.selectedRun || state.runs.find((item) => item.run_id === state.selectedRunId) || null;
  const activeRun = getActiveRun();
  const pausedRun = state.runs.find((item) => item.status === "awaiting_script_action") || null;
  const activeRetryRun = state.runs.find(runHasActiveRetrySummary) || null;
  const selectedRetryCount = state.activeRetryFlows.length;
  const normalizedQuery = state.runSearchQuery.trim().toLocaleLowerCase("zh-CN");
  const visibleRuns = normalizedQuery
    ? state.runs.filter((item) =>
        [item.requirement_title, item.run_id, agentStepLabel(item.current_step), statusText(item.status)]
          .filter(Boolean)
          .some((value) => String(value).toLocaleLowerCase("zh-CN").includes(normalizedQuery)),
      )
    : state.runs;

  elements.currentRunMain.setAttribute("aria-expanded", String(state.runMenuOpen));
  elements.runDropdown.classList.toggle("hidden", !state.runMenuOpen);
  if (!run) {
    elements.currentRunTitle.textContent = "选择任务";
    setBadge(elements.currentRunStatus, "idle");
  } else {
    elements.currentRunTitle.textContent = run.requirement_title || run.run_id;
    setBadge(elements.currentRunStatus, selectedRetryCount ? "retrying" : run.status);
  }

  elements.newRunButton.disabled = Boolean(activeRun || pausedRun || activeRetryRun);
  elements.newRunButton.title = activeRun
    ? "当前项目已有任务运行中"
    : pausedRun
      ? "当前项目有脚本等待人工处理"
    : activeRetryRun
      ? "当前项目有脚本正在重试并验证"
      : "新建 Agent 任务";

  const canResume = Boolean(run && isResumableStatus(run.status) && !activeRun && !pausedRun && !activeRetryRun);
  elements.resumeButton.textContent = "恢复任务";
  elements.resumeButton.disabled = !canResume;
  elements.resumeButton.title = canResume
      ? "从当前阶段恢复任务"
      : activeRun
        ? "当前项目已有任务运行中"
        : pausedRun
          ? "请先完成脚本准备中的人工处理"
          : activeRetryRun
            ? "请等待单项重试并验证完成"
            : "只有失败或已取消的任务可以恢复";

  const isCancelling = run?.status === "cancelling";
  const canStop = Boolean(run && ["queued", "running", "awaiting_script_action"].includes(run.status));
  elements.cancelButton.disabled = !canStop;
  elements.cancelButtonLabel.textContent = isCancelling ? "正在停止…" : "停止任务";
  elements.cancelButton.title = canStop ? "停止当前任务" : isCancelling ? "任务正在停止" : "当前任务未在运行";

  elements.runList.innerHTML = visibleRuns.length
    ? visibleRuns
        .map(
          (item) => {
            const retrying = runHasActiveRetrySummary(item);
            return `
            <button class="run-item ${item.run_id === state.selectedRunId ? "active" : ""}" type="button" data-run-id="${escapeHtml(item.run_id)}">
              <span class="run-item-title">${escapeHtml(item.requirement_title || item.run_id)}</span>
              <span class="run-item-meta">${escapeHtml(agentStepLabel(item.current_step))} · ${retrying ? "有脚本正在重试 · " : ""}${escapeHtml(agentCoverageMeta(item))} · ${formatDateTime(item.created_at)}</span>
              <span class="status-badge ${escapeHtml(retrying ? "retrying" : item.status)}">${escapeHtml(statusText(retrying ? "retrying" : item.status))}</span>
            </button>
          `;
          },
        )
        .join("")
    : `<div class="run-list-empty">${state.runs.length ? "没有匹配的任务" : "暂无任务"}</div>`;

  elements.runList.querySelectorAll("[data-run-id]").forEach((button) => {
    button.addEventListener("click", () => selectRun(button.dataset.runId));
  });
}

function renderRequirements() {
  const current = elements.requirementSelect.value;
  const placeholder = window.WaterfallI18n?.source("或选择已有需求") || "或选择已有需求";
  elements.requirementSelect.innerHTML = `<option value="">${escapeHtml(placeholder)}</option>`;
  state.requirements.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.requirement_uid;
    option.textContent = item.title || item.filename || item.requirement_uid;
    elements.requirementSelect.appendChild(option);
  });
  if (current && state.requirements.some((item) => item.requirement_uid === current)) {
    elements.requirementSelect.value = current;
  }
}

function retryTimelineMeta(stepKey) {
  const flows = state.activeRetryFlows;
  if (!flows.length) {
    return "";
  }
  const phases = flows.map((flow) => flow.current_phase);
  if (stepKey === "generate_scripts") {
    const generating = phases.filter((phase) => ["queued", "generating"].includes(phase)).length;
    return generating ? `${generating} 项重试生成中` : `${flows.length} 项已重新生成`;
  }
  if (stepKey === "execute_scripts") {
    const executing = phases.filter((phase) => phase === "executing").length;
    const advanced = phases.filter((phase) => ["repairing", "verifying"].includes(phase)).length;
    if (executing) {
      return `${executing} 项单独执行中`;
    }
    return advanced ? `${advanced} 项执行后待处理` : "";
  }
  if (stepKey === "repair_scripts") {
    const repairing = phases.filter((phase) => phase === "repairing").length;
    const verifying = phases.filter((phase) => phase === "verifying").length;
    if (repairing) {
      return `${repairing} 项自动修复中`;
    }
    return verifying ? `${verifying} 项修复后复验中` : "";
  }
  return "";
}

function renderRetryStatusBar() {
  const flows = state.activeRetryFlows;
  elements.retryStatusBar.classList.toggle("hidden", !flows.length);
  if (!flows.length) {
    elements.retryStatusView.dataset.retryFlowId = "";
    return;
  }
  const flow = flows[0];
  elements.retryStatusTitle.textContent = flows.length > 1 ? `正在重试并验证 ${flows.length} 个脚本` : "正在重试并验证脚本";
  elements.retryStatusMeta.textContent = `${retryFlowTitle(flow)} · ${retryFlowProgressText(flow)}${
    flows.length > 1 ? ` · 另有 ${flows.length - 1} 项` : ""
  }`;
  elements.retryStatusView.dataset.retryFlowId = flow.retry_flow_id;
}

function renderTimeline() {
  elements.stepTimeline.innerHTML = AGENT_STEPS.map(([key, label], index) => {
    const step = getStep(key) || { status: "queued", counts: {}, error: "" };
    const status = step.status || "queued";
    const active = key === state.activeStepKey;
    const progress = progressText(key, step);
    const duration = formatDuration(step.started_at, step.finished_at, status);
    const time = formatClock(step.started_at);
    const retryMeta = retryTimelineMeta(key);
    return `
      <button class="timeline-step ${escapeHtml(status)} ${active ? "active" : ""}" type="button" data-step-key="${escapeHtml(key)}">
        <span class="timeline-rail" aria-hidden="true">
          <span class="timeline-marker">${
            status === "succeeded" ? "✓" : status === "running" ? "▶" : status === "failed" ? "!" : status === "awaiting_action" ? "…" : ""
          }</span>
        </span>
        <span class="timeline-body">
          <span class="timeline-title">
            <span class="timeline-index">${index + 1}</span>
            <span>${escapeHtml(label)}${progress ? ` <b>${escapeHtml(progress)}</b>` : ""}</span>
          </span>
          <span class="timeline-meta">
            ${
              time
                ? `<span>${escapeHtml(time)}</span><span class="${status === "running" ? "meta-running" : ""}">${escapeHtml(statusText(status))}</span>${
                    duration ? `<span>${escapeHtml(duration)}</span>` : ""
                  }`
                : `<span>${escapeHtml(statusText(status))}</span>`
            }
            ${retryMeta ? `<span class="timeline-retry-meta">${escapeHtml(retryMeta)}</span>` : ""}
          </span>
        </span>
      </button>
    `;
  }).join("");

  elements.stepTimeline.querySelectorAll("[data-step-key]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeStepKey = button.dataset.stepKey;
      renderTimeline();
      renderArtifacts();
      if (state.activeStepKey === "run_suite") {
        loadAgentExecutionResult();
      }
    });
  });
}

function findModule(moduleName, moduleUid = "") {
  const modules = [
    ...asArray(getStepOutput("review_modules").modules),
    ...asArray(getStepOutput("analyze_requirement").modules),
  ];
  return (
    modules.find((item) => moduleUid && item.module_uid === moduleUid) ||
    modules.find((item) => item.module_name === moduleName || item.plan_name === moduleName) ||
    null
  );
}

function latestEvents(stepKey, predicate = () => true, limit = 30) {
  return state.events.filter((event) => event.step_key === stepKey && predicate(event)).slice(-limit);
}

function latestArtifactProgress(stepKey, itemStatus = "") {
  return (
    latestEvents(
      stepKey,
      (event) =>
        event.payload?.artifact_progress &&
        (!itemStatus || event.payload?.item_status === itemStatus),
      1,
    )[0]?.payload || null
  );
}

function hasArtifactProgress(stepKey) {
  return Boolean(latestArtifactProgress(stepKey));
}

function eventLogText(stepKey, predicate = () => true) {
  const rows = latestEvents(stepKey, predicate, 40);
  return rows
    .map((event) => {
      const payload = event.payload && Object.keys(event.payload).length ? `\n${formatJsonPreview(event.payload)}` : "";
      return `${formatDateTime(event.created_at)} ${event.event_type}: ${event.message}${payload}`;
    })
    .join("\n\n");
}

function findJobIdForModule(stepKey, moduleName) {
  const event = latestEvents(
    stepKey,
    (item) => item.job_id && (!moduleName || item.payload?.module_name === moduleName || item.message?.includes(moduleName)),
    1,
  )[0];
  return event?.job_id || "";
}

function findJobIdForArtifact(stepKey, predicate) {
  const event = latestEvents(stepKey, (item) => item.job_id && predicate(item), 1)[0];
  return event?.job_id || "";
}

function findJobIdForScriptItem(stepKey, moduleName, planFilename = "", filename = "") {
  const exactJobId = findJobIdForArtifact(stepKey, (event) => {
    const payload = event.payload || {};
    if (moduleName && payload.module_name !== moduleName) {
      return false;
    }
    if (planFilename && payload.plan_filename === planFilename) {
      return true;
    }
    if (filename && payload.filename === filename) {
      return true;
    }
    return !planFilename && !filename;
  });
  return exactJobId || findJobIdForModule(stepKey, moduleName);
}

function getPlanJobId(plan) {
  return plan.job_id || plan.asset?.source_job_id || findJobIdForModule("generate_plans", plan.module_name);
}

function getScriptJobId(script) {
  return script.job_id || script.repair_job_id || script.asset?.source_job_id || findJobIdForModule("generate_scripts", script.module_name);
}

function artifactMeta(parts) {
  return parts.filter(Boolean).join(" · ");
}

function retryItemKeyCandidates(item) {
  const source = isPlainObject(item?.contentObject) ? item.contentObject : item || {};
  const moduleName = String(source.module_name || item?.moduleName || "").trim();
  const planFilename = String(source.plan_filename || item?.planFilename || "").trim();
  const filename = String(source.filename || item?.filename || "").trim();
  return new Set(
    [
      source.item_key,
      item?.itemKey,
      moduleName && planFilename ? `${moduleName}/${planFilename}` : "",
      moduleName && filename ? `${moduleName}/${filename}` : "",
      planFilename,
      filename,
    ]
      .map((value) => String(value || "").trim())
      .filter(Boolean),
  );
}

function findRetryFlowForArtifact(artifact) {
  if (!artifact) {
    return null;
  }
  const content = isPlainObject(artifact.contentObject) ? artifact.contentObject : {};
  const explicitFlowId = content.retry_flow_id || content.resolved_by_retry_flow_id || artifact.retryFlowId || "";
  if (explicitFlowId) {
    const exactFlow = state.retryFlows.find((flow) => flow.retry_flow_id === explicitFlowId);
    if (exactFlow) {
      return exactFlow;
    }
  }
  const attemptId = String(artifact.attemptId || content.attempt_id || content.failure_id || "");
  const candidates = state.retryFlows.filter((flow) => {
    if (attemptId && [flow.root_attempt_id, flow.generation_attempt_id].includes(attemptId)) {
      return true;
    }
    const artifactModuleName = String(content.module_name || artifact.moduleName || "").trim();
    const flowModuleName = String(flow.module_name || "").trim();
    if (!artifactModuleName || !flowModuleName || artifactModuleName !== flowModuleName) {
      return false;
    }
    const artifactKeys = retryItemKeyCandidates(artifact);
    const flowKeys = retryItemKeyCandidates(flow);
    return Array.from(artifactKeys).some((key) => flowKeys.has(key));
  });
  return (
    candidates.find(isActiveRetryFlow) ||
    candidates.sort((left, right) => Number(right.updated_at || right.created_at || 0) - Number(left.updated_at || left.created_at || 0))[0] ||
    null
  );
}

function decorateArtifactRetryStatus(artifact) {
  const content = isPlainObject(artifact?.contentObject) ? artifact.contentObject : {};
  const flow = findRetryFlowForArtifact(artifact);
  const retryStatus = String(content.retry_status || content.verification_status || "").toLowerCase();
  let status = artifact.status;
  if (flow && isActiveRetryFlow(flow)) {
    status = "retrying";
  } else if (flow?.status === "succeeded" || ["resolved", "verified", "passed", "succeeded"].includes(retryStatus)) {
    status = "resolved";
  } else if (["queued", "running", "retrying", "generating", "executing", "repairing", "verifying"].includes(retryStatus)) {
    status = "retrying";
  }
  return {
    ...artifact,
    originalStatus: artifact.originalStatus || artifact.status,
    status,
    retryFlowId: flow?.retry_flow_id || content.retry_flow_id || content.resolved_by_retry_flow_id || "",
    retryFlow: flow || null,
  };
}

function uniqueArtifacts(artifacts) {
  const unique = new Map();
  artifacts.forEach((artifact) => {
    const existing = unique.get(artifact.id);
    unique.set(artifact.id, existing ? { ...existing, ...artifact } : artifact);
  });
  return Array.from(unique.values());
}

function planArtifact(plan, index, sourceStep = "generate_plans", status = "succeeded") {
  const moduleItem = findModule(plan.module_name, plan.module_uid);
  return {
    id: `${sourceStep}:plan:${plan.module_name || ""}:${plan.plan_filename || plan.filename || index}`,
    type: "plan",
    kindLabel: "测试计划",
    title: plan.plan_filename || plan.filename || plan.name || `计划 ${index + 1}`,
    subtitle: artifactMeta([plan.module_name, plan.path]),
    status,
    jobId: getPlanJobId(plan) || moduleItem?.source_job_id || "",
    promptText: moduleItem?.planner_prompt || "",
    promptObject: moduleItem || getStepInput(sourceStep),
    contentTitle: "计划文本",
    contentLoader: { type: "plan", moduleName: plan.module_name, planFilename: plan.plan_filename || plan.filename },
    contentObject: plan,
  };
}

function scriptArtifact(script, index, sourceStep = "generate_scripts", status = "succeeded") {
  return decorateArtifactRetryStatus({
    id: `${sourceStep}:script:${script.module_name || ""}:${script.filename || index}`,
    type: "script",
    kindLabel: "测试脚本",
    title: script.filename || script.plan_filename || `脚本 ${index + 1}`,
    subtitle: artifactMeta([script.module_name, script.path || script.plan_filename]),
    status,
    jobId: getScriptJobId(script),
    promptObject: { step_input: getStepInput(sourceStep), source: script },
    contentTitle: "脚本文本",
    contentLoader: { type: "script", moduleName: script.module_name, filename: script.filename },
    contentObject: script,
  });
}

function jsonArtifact({
  id,
  type,
  kindLabel,
  title,
  subtitle,
  status = "succeeded",
  sourceStep,
  promptText = "",
  promptObject,
  contentTitle,
  contentObject,
  attemptId = "",
  jobId = "",
}) {
  return decorateArtifactRetryStatus({
    id,
    type,
    kindLabel,
    title,
    subtitle,
    status,
    promptText,
    promptObject: promptObject || getStepInput(sourceStep),
    contentTitle: contentTitle || "结构化结果",
    contentObject,
    attemptId,
    jobId,
    sourceStep,
  });
}

function pendingPlanArtifacts(step) {
  if (step?.status !== "running") {
    return [];
  }
  const stepModules = asArray(getStepInput("generate_plans").modules);
  const reviewedModules = asArray(getStepOutput("review_modules").modules);
  const analyzedModules = asArray(getStepOutput("analyze_requirement").modules);
  const modules = stepModules.length ? stepModules : reviewedModules.length ? reviewedModules : analyzedModules;
  const generated = new Set(asArray(step.output?.plans).map((plan) => plan.module_name));
  const failed = new Set(asArray(step.output?.failures).map((item) => item.module_name));
  const skipped = new Set(asArray(step.output?.skipped).map((item) => item.module_name));
  const runningPayload = latestArtifactProgress("generate_plans", "running");
  const latestRunning = runningPayload?.module_name || "";
  const hasProgress = hasArtifactProgress("generate_plans");
  return modules
    .filter((item) => !generated.has(item.module_name) && !failed.has(item.module_name) && !skipped.has(item.module_name))
    .map((item, index) => {
      const running = latestRunning ? item.module_name === latestRunning : !hasProgress && index === 0;
      return {
        id: `generate_plans:pending:${item.module_uid || item.module_name}`,
        type: "plan",
        kindLabel: "测试计划",
        title: item.plan_name || item.module_name,
        subtitle: artifactMeta([item.module_name, running ? "正在生成" : "等待生成"]),
        status: running ? "running" : "queued",
        jobId: findJobIdForModule("generate_plans", item.module_name),
        promptText: item.planner_prompt || "",
        promptObject: item,
        contentTitle: running ? "实时输出" : "等待信息",
        contentText: eventLogText("generate_plans", (event) => event.payload?.module_name === item.module_name || event.message?.includes(item.module_name)) || "暂无实时输出。",
        contentObject: item,
      };
    });
}

function pendingScriptArtifacts(step) {
  if (step?.status !== "running") {
    return [];
  }
  const stepPlans = asArray(getStepInput("generate_scripts").plans);
  const reviewedPlans = asArray(getStepOutput("review_plans").plans);
  const generatedPlans = asArray(getStepOutput("generate_plans").plans);
  const plans = stepPlans.length ? stepPlans : reviewedPlans.length ? reviewedPlans : generatedPlans;
  const generated = new Set(asArray(step.output?.scripts).map((script) => `${script.module_name}/${script.plan_filename || script.filename}`));
  const failed = new Set(asArray(step.output?.failures).map((item) => `${item.module_name}/${item.plan_filename || item.filename}`));
  const runningPayload = latestArtifactProgress("generate_scripts", "running");
  const latestRunning = runningPayload ? `${runningPayload.module_name}/${runningPayload.plan_filename || runningPayload.filename || ""}` : "";
  const hasProgress = hasArtifactProgress("generate_scripts");
  return plans
    .filter((plan) => !generated.has(`${plan.module_name}/${plan.plan_filename}`) && !failed.has(`${plan.module_name}/${plan.plan_filename}`))
    .map((plan, index) => {
      const key = `${plan.module_name}/${plan.plan_filename}`;
      const running = latestRunning ? key === latestRunning : !hasProgress && index === 0;
      return {
        id: `generate_scripts:pending:${plan.module_name}:${plan.plan_filename}`,
        type: "script",
        kindLabel: "测试脚本",
        title: plan.plan_filename,
        subtitle: artifactMeta([plan.module_name, running ? "正在生成" : "等待生成"]),
        status: running ? "running" : "queued",
        jobId: findJobIdForScriptItem("generate_scripts", plan.module_name, plan.plan_filename),
        promptObject: { source_plan: plan, step_input: getStepInput("generate_scripts") },
        contentTitle: "实时输出",
        contentText: eventLogText("generate_scripts", (event) => event.payload?.module_name === plan.module_name || event.message?.includes(plan.module_name)) || "暂无实时输出。",
        contentObject: plan,
      };
    });
}

function pendingRepairArtifacts(step) {
  if (step?.status !== "running") {
    return [];
  }
  const stepScripts = asArray(getStepInput("repair_scripts").scripts);
  const scripts = stepScripts.length ? stepScripts : asArray(getStepOutput("execute_scripts").failures);
  const itemKey = (item) => `${item.module_name || ""}/${item.filename || item.plan_filename || ""}`;
  const repaired = new Set(asArray(step.output?.scripts).map(itemKey));
  const failed = new Set(asArray(step.output?.failures).map(itemKey));
  const runningPayload = latestArtifactProgress("repair_scripts", "running");
  const latestRunning = runningPayload ? `${runningPayload.module_name || ""}/${runningPayload.filename || runningPayload.plan_filename || ""}` : "";
  const hasProgress = hasArtifactProgress("repair_scripts");
  return scripts
    .filter((script) => !repaired.has(itemKey(script)) && !failed.has(itemKey(script)))
    .map((script, index) => {
      const key = itemKey(script);
      const running = latestRunning ? key === latestRunning : !hasProgress && index === 0;
      return {
        id: `repair_scripts:pending:${script.module_name || ""}:${script.filename || script.plan_filename || index}`,
        type: "script",
        kindLabel: "待修复脚本",
        title: script.filename || script.plan_filename || `脚本 ${index + 1}`,
        subtitle: artifactMeta([script.module_name, running ? "正在修复" : "等待修复"]),
        status: running ? "running" : "queued",
        jobId: findJobIdForScriptItem("repair_scripts", script.module_name, "", script.filename || script.plan_filename),
        promptObject: { source_script: script, step_input: getStepInput("repair_scripts") },
        contentTitle: "实时输出",
        contentText:
          eventLogText("repair_scripts", (event) => event.payload?.module_name === script.module_name || event.message?.includes(script.module_name)) ||
          "暂无实时输出。",
        contentObject: script,
      };
    });
}

function failureArtifacts(stepKey, failures, typeLabel, baseStatus = "failed") {
  return asArray(failures).map((item, index) =>
    jsonArtifact({
      id: `${stepKey}:failure:${item.module_name || ""}:${item.plan_filename || item.filename || index}`,
      type: "failure",
      kindLabel: typeLabel,
      title: item.plan_filename || item.filename || item.module_name || `失败项 ${index + 1}`,
      subtitle: artifactMeta([item.module_name, item.error]),
      status: baseStatus,
      sourceStep: stepKey,
      attemptId: item.attempt_id || item.failure_id || item.root_attempt_id || "",
      jobId:
        item.job_id ||
        (stepKey === "generate_plans"
          ? findJobIdForModule(stepKey, item.module_name)
          : findJobIdForScriptItem(stepKey, item.module_name, item.plan_filename, item.filename)),
      contentTitle: "失败详情",
      contentObject: item,
    }),
  );
}

function artifactsForStep(stepKey) {
  const step = getStep(stepKey);
  const output = step?.output || {};
  const input = step?.input || {};
  switch (stepKey) {
    case "upload_requirement":
      return [
        jsonArtifact({
          id: "upload_requirement:requirement",
          type: "requirement",
          kindLabel: "需求",
          title: state.selectedRun?.requirement_title || "需求文件",
          subtitle: artifactMeta([state.selectedRun?.requirement_uid, state.selectedRun?.run_id]),
          status: state.selectedRun ? "succeeded" : "queued",
          sourceStep: stepKey,
          promptText: "上传 Markdown 需求或选择已有需求后启动 Agent。",
          contentTitle: "需求记录",
          contentObject: {
            requirement_uid: state.selectedRun?.requirement_uid,
            requirement_title: state.selectedRun?.requirement_title,
            run_id: state.selectedRun?.run_id,
            created_by: state.selectedRun?.created_by,
          },
        }),
      ];
    case "analyze_requirement":
      return asArray(output.modules).map((item, index) =>
        jsonArtifact({
          id: `analyze_requirement:module:${item.module_uid || index}`,
          type: "module",
          kindLabel: "候选模块",
          title: item.module_name || item.plan_name || `模块 ${index + 1}`,
          subtitle: artifactMeta([item.business_goal, item.confidence ? `置信度 ${item.confidence}` : ""]),
          status: "succeeded",
          sourceStep: stepKey,
          promptText: item.planner_prompt || "",
          promptObject: input,
          contentTitle: "模块分析",
          contentObject: item,
        }),
      );
    case "review_modules":
      return asArray(output.modules)
        .map((item, index) =>
          jsonArtifact({
            id: `review_modules:module:${item.module_uid || index}`,
            type: "module",
            kindLabel: "确认模块",
            title: item.module_name || item.plan_name || `模块 ${index + 1}`,
            subtitle: artifactMeta([item.business_goal, "保留"]),
            status: "succeeded",
            sourceStep: stepKey,
            promptText: item.planner_prompt || "",
            contentTitle: "审查后模块",
            contentObject: item,
          }),
        )
        .concat(
          asArray(output.decisions).map((item, index) =>
            jsonArtifact({
              id: `review_modules:decision:${item.module_uid || index}`,
              type: "decision",
              kindLabel: "审查决策",
              title: item.module_name || item.module_uid || `决策 ${index + 1}`,
              subtitle: artifactMeta([item.action, item.reason]),
              status: item.action === "delete" ? "excluded" : "succeeded",
              sourceStep: stepKey,
              contentTitle: "决策详情",
              contentObject: item,
            }),
          ),
        );
    case "generate_plans":
      return [
        ...asArray(output.plans).map((item, index) => planArtifact(item, index, stepKey)),
        ...asArray(output.skipped).map((item, index) => planArtifact(item, index, stepKey, "skipped")),
        ...pendingPlanArtifacts(step),
        ...failureArtifacts(stepKey, output.failures, "计划失败"),
      ];
    case "review_plans":
      return [
        ...asArray(output.plans).map((item, index) => planArtifact(item, index, stepKey)),
        ...asArray(output.decisions).map((item, index) =>
          jsonArtifact({
            id: `review_plans:decision:${item.module_name || ""}:${item.plan_filename || index}`,
            type: "decision",
            kindLabel: "计划决策",
            title: item.plan_filename || item.module_name || `决策 ${index + 1}`,
            subtitle: artifactMeta([item.module_name, item.action, item.reason]),
            status: item.action === "delete" ? "excluded" : "succeeded",
            sourceStep: stepKey,
            contentTitle: "审查详情",
            contentObject: item,
          }),
        ),
      ];
    case "generate_scripts":
      return uniqueArtifacts([
        ...asArray(output.scripts).map((item, index) => scriptArtifact(item, index, stepKey)),
        ...pendingScriptArtifacts(step),
        ...failureArtifacts(stepKey, output.resolved_failures, "已恢复失败", "resolved"),
        ...failureArtifacts(stepKey, output.failures, "脚本失败"),
        ...failureArtifacts(stepKey, output.retrying, "脚本重试", "retrying"),
      ]);
    case "execute_scripts":
      return uniqueArtifacts([
        ...asArray(output.scripts).map((item, index) =>
          jsonArtifact({
            id: `execute_scripts:passed:${item.module_name || ""}:${item.filename || index}`,
            type: "execution",
            kindLabel: "执行结果",
            title: item.filename || `通过脚本 ${index + 1}`,
            subtitle: artifactMeta([item.module_name, item.execution_run_id, "通过"]),
            status: "passed",
            sourceStep: stepKey,
            contentTitle: "执行结果",
            contentObject: item,
          }),
        ),
        ...failureArtifacts(stepKey, output.resolved_failures, "复验通过", "resolved"),
        ...failureArtifacts(stepKey, output.failures, "执行失败"),
        ...failureArtifacts(stepKey, output.retrying, "单项执行", "retrying"),
      ]);
    case "repair_scripts":
      return uniqueArtifacts([
        ...asArray(output.scripts).map((item, index) => scriptArtifact(item, index, stepKey, "repaired")),
        ...pendingRepairArtifacts(step),
        ...failureArtifacts(stepKey, output.resolved_failures, "修复并复验通过", "resolved"),
        ...failureArtifacts(stepKey, output.failures, "修复失败"),
        ...failureArtifacts(stepKey, output.retrying, "单项修复", "retrying"),
      ]);
    case "review_failed_scripts":
      return [
        ...asArray(output.scripts).map((item, index) => scriptArtifact(item, index, stepKey, "recovered")),
        ...asArray(output.decisions).map((item, index) =>
          jsonArtifact({
            id: `review_failed_scripts:decision:${item.module_name || ""}:${item.filename || index}`,
            type: "decision",
            kindLabel: "失败处理",
            title: item.filename || item.module_name || `处理 ${index + 1}`,
            subtitle: artifactMeta([item.module_name, item.action, item.reason]),
            status: item.action === "exclude" ? "excluded" : "succeeded",
            sourceStep: stepKey,
            contentTitle: "处理详情",
            contentObject: item,
          }),
        ),
      ];
    case "create_suite":
      return output.suite
        ? [
            jsonArtifact({
              id: `create_suite:suite:${output.suite.id || "suite"}`,
              type: "suite",
              kindLabel: "测试集",
              title: output.suite.name || output.suite.id || "测试集",
              subtitle: artifactMeta([output.suite.id, `${asArray(output.suite.items).length} 个脚本`]),
              status: "succeeded",
              sourceStep: stepKey,
              contentTitle: "测试集详情",
              contentObject: output.suite,
            }),
          ]
        : [];
    case "run_suite":
      return output.result || output.summary
        ? [
            jsonArtifact({
              id: "run_suite:result",
              type: "execution",
              kindLabel: "执行汇总",
              title: "测试集执行结果",
              subtitle: output.summary
                ? artifactMeta([`通过 ${output.summary.passed || 0}`, `失败 ${output.summary.failed || 0}`, `总数 ${output.summary.total || 0}`])
                : "",
              status: output.summary?.failed ? "failed" : "succeeded",
              sourceStep: stepKey,
              contentTitle: "执行汇总",
              contentObject: output,
            }),
          ]
        : [];
    default:
      return Object.keys(output).length
        ? [
            jsonArtifact({
              id: `${stepKey}:output`,
              type: "output",
              kindLabel: "步骤输出",
              title: `${agentStepLabel(stepKey)}输出`,
              subtitle: "",
              status: step?.status || "succeeded",
              sourceStep: stepKey,
              contentObject: output,
            }),
          ]
        : [];
  }
}

function renderArtifacts() {
  const run = state.selectedRun;
  const showScriptPreparation = Boolean(run && state.activeStepKey === "prepare_scripts");
  elements.artifactPanel.classList.toggle("hidden", showScriptPreparation);
  if (showScriptPreparation) {
    const preparationState = scriptPreparation.getState();
    if (preparationState.runId !== state.selectedRunId) {
      scriptPreparation.setRun(state.selectedRunId);
      void scriptPreparation.activate(state.selectedRunId);
    } else if (!scriptPreparation.getState().active) {
      void scriptPreparation.activate(state.selectedRunId);
    } else {
      scriptPreparation.render();
    }
    state.currentArtifacts = [];
    return;
  }
  if (scriptPreparation.getState().active) {
    scriptPreparation.deactivate();
  }
  const step = getStep(state.activeStepKey);
  const stepLabel = agentStepLabel(state.activeStepKey);
  const artifacts = artifactsForStep(state.activeStepKey);
  state.currentArtifacts = artifacts;

  if (!run) {
    elements.runSubtitle.textContent = "暂无运行任务";
    elements.runTitle.textContent = "选择左侧任务，或上传需求启动 Agent";
    elements.artifactStageSummary.textContent = "暂无阶段产物";
    elements.planGenerationDetails.classList.add("hidden");
    elements.planGenerationMeta.textContent = "";
    elements.planGenerationPrompt.textContent = "";
    setBadge(elements.runStatus, "idle");
  } else {
    const planGeneration = isPlainObject(run.plan_generation) ? run.plan_generation : {};
    elements.runSubtitle.textContent = `${run.run_id} · ${agentCoverageMeta(run)} · ${formatDateTime(run.created_at)}`;
    elements.runTitle.textContent = run.requirement_title || run.run_id;
    elements.artifactStageSummary.textContent = `${stepLabel} · ${statusText(step?.status || "queued")} · ${artifacts.length} 个生成物`;
    elements.planGenerationDetails.classList.remove("hidden");
    elements.planGenerationMeta.textContent = agentCoverageMeta(run);
    elements.planGenerationPrompt.textContent = planGeneration.coverage_prompt || "未保存策略文本。";
    setBadge(elements.runStatus, state.activeRetryFlows.length ? "retrying" : run.status);
  }

  elements.artifactList.innerHTML = artifacts.length
    ? artifacts
        .map(
          (item) => {
            const retryMeta = item.retryFlow ? retryFlowProgressText(item.retryFlow) : "";
            return `
            <button class="artifact-item ${escapeHtml(item.status || "")}" type="button" data-artifact-id="${escapeHtml(item.id)}">
              <span class="artifact-type">${escapeHtml(item.kindLabel || "产物")}</span>
              <span class="artifact-main">
                <span class="artifact-title-row">
                  <strong>${escapeHtml(item.title || "未命名产物")}</strong>
                  <span class="status-badge ${escapeHtml(item.status || "")}">${escapeHtml(statusText(item.status))}</span>
                </span>
                <span class="artifact-subtitle">${escapeHtml(artifactMeta([retryMeta, item.subtitle || item.contentTitle || ""]))}</span>
              </span>
              <span class="artifact-chevron">›</span>
            </button>
          `;
          },
        )
        .join("")
    : `<div class="empty-state">${escapeHtml(stepLabel)}暂无生成物</div>`;

  elements.artifactList.querySelectorAll("[data-artifact-id]").forEach((button) => {
    button.addEventListener("click", () => openArtifact(button.dataset.artifactId));
  });
  if (state.openArtifact) {
    const refreshedArtifact = artifacts.find((item) => item.id === state.openArtifact.id);
    if (refreshedArtifact) {
      state.openArtifact = { ...state.openArtifact, ...refreshedArtifact };
    }
    renderArtifactRetryState();
  }
  renderAgentExecutionResult();
}

function mergeAgentScriptResults(previousResults, nextResults) {
  const previous = isPlainObject(previousResults) ? previousResults : {};
  const next = isPlainObject(nextResults) ? nextResults : {};
  return { ...previous, ...next };
}

function getAgentExecutionResultContext() {
  const runSuiteStep = getStep("run_suite");
  const stepResult = isPlainObject(runSuiteStep?.output?.result) ? runSuiteStep.output.result : {};
  const latestStatusEvent = latestEvents(
    "run_suite",
    (event) => isPlainObject(event.payload) && Boolean(event.payload.run_id),
    1,
  )[0];
  const eventResult = isPlainObject(latestStatusEvent?.payload) ? latestStatusEvent.payload : {};
  const result = {
    ...eventResult,
    ...stepResult,
    script_results: mergeAgentScriptResults(eventResult.script_results, stepResult.script_results),
  };
  const suiteUid = state.selectedRun?.suite_uid || getStep("create_suite")?.output?.suite?.id || "";
  const executionRunId = result.run_id || "";
  return {
    agentRunId: state.selectedRun?.run_id || "",
    suiteUid,
    executionRunId,
    cacheKey: [state.selectedRun?.run_id || "", suiteUid, executionRunId].join(":"),
    result,
    step: runSuiteStep,
  };
}

function buildAgentLocalExecutionRecord(context) {
  const result = context.result || {};
  if (!context.executionRunId) {
    return null;
  }
  const suiteItems = asArray(getStep("create_suite")?.output?.suite?.items);
  const scriptResults = isPlainObject(result.script_results) ? result.script_results : {};
  const normalizedItems = suiteItems.length
    ? suiteItems.map((item) => ({
        ...item,
        key: item.key || getSuiteScriptKey(item.module_name, item.filename),
      }))
    : Object.keys(scriptResults).map((key) => {
        const separator = key.lastIndexOf("/");
        return {
          key,
          module_name: separator >= 0 ? key.slice(0, separator) : "",
          filename: separator >= 0 ? key.slice(separator + 1) : key,
        };
      });
  const running = ["queued", "running"].includes(result.status || context.step?.status);
  const results = normalizedItems.map((item, index) => {
    const status = Object.prototype.hasOwnProperty.call(scriptResults, item.key)
      ? normalizeExecutionResultStatus(scriptResults[item.key])
      : running
        ? "running"
        : "unknown";
    return {
      result_id: null,
      run_id: context.executionRunId,
      order_index: index + 1,
      module_name: item.module_name || "",
      filename: item.filename || "",
      script_key: item.key,
      script_path: item.path || item.script_path || "",
      script_name: item.display_name || stripSpecSuffix(item.filename || ""),
      status,
      error_message: status === "passed" ? "" : result.error || "",
      stdout_tail: "",
      started_at: context.step?.started_at || null,
      finished_at: context.step?.finished_at || null,
      updated_at: context.step?.updated_at || null,
      report: normalizeTestSuiteExecutionArtifact(result.report),
      video: null,
    };
  });
  const materializedResults = Object.fromEntries(
    results.filter((item) => item.script_key).map((item) => [item.script_key, item.status]),
  );
  return {
    run_id: context.executionRunId,
    run_type: "test_suite",
    status: result.status || context.step?.status || "running",
    execution_mode: result.execution_mode || EXECUTION_MODE.SERIAL_PER_FILE,
    suite_id: context.suiteUid,
    command: result.command || "",
    summary: buildExecutionSummaryFromResults(materializedResults),
    total_files: Number(result.total_files) || results.length,
    completed_files:
      Number(result.completed_files) || results.filter((item) => item.status !== "running").length,
    error: result.error || "",
    started_at: context.step?.started_at || null,
    finished_at: context.step?.finished_at || null,
    created_at: context.step?.started_at || null,
    updated_at: context.step?.updated_at || null,
    report: normalizeTestSuiteExecutionArtifact(result.report),
    results,
    logs: result.logs || result.output || "",
    report_error: result.report_error || "",
  };
}

function mergeAgentExecutionRecord(record, context) {
  const localRecord = buildAgentLocalExecutionRecord(context);
  if (!record) {
    return localRecord;
  }
  return {
    ...localRecord,
    ...record,
    report: record.report || localRecord?.report || null,
    results: record.results?.length ? record.results : localRecord?.results || [],
    logs: localRecord?.logs || "",
    report_error: localRecord?.report_error || "",
  };
}

function resetAgentExecutionResult() {
  state.executionResultRequestId += 1;
  state.executionResultKey = "";
  state.executionResultRecord = null;
  state.executionResultLoading = false;
  state.executionResultError = "";
}

function renderAgentExecutionResult() {
  const visible = state.activeStepKey === "run_suite";
  elements.executionResultPanel.classList.toggle("hidden", !visible);
  if (!visible) {
    return;
  }

  const context = getAgentExecutionResultContext();
  let emptyTitle = "等待执行结果";
  let emptyMessage = "测试集开始执行后，这里会逐条展示脚本状态。";
  let emptySummary = "等待测试集执行。";
  if (state.executionResultLoading && !state.executionResultRecord) {
    emptyTitle = "正在加载执行结果";
    emptyMessage = "正在读取本次测试集的逐脚本执行记录。";
    emptySummary = "正在加载逐脚本结果...";
  } else if (state.executionResultError && !state.executionResultRecord) {
    emptyTitle = "执行结果加载失败";
    emptyMessage = state.executionResultError;
    emptySummary = "无法读取逐脚本结果。";
  } else if (!context.suiteUid) {
    emptyMessage = "测试集创建完成后，这里会展示具体执行结果。";
  }

  renderExecutionResultPanel(
    state.executionResultRecord,
    {
      title: elements.executionResultTitle,
      summary: elements.executionResultSummary,
      reportLink: elements.executionResultReportLink,
      empty: elements.executionResultEmpty,
      resultWrap: elements.executionResultWrap,
      resultTableBody: elements.executionResultTableBody,
      logPanel: elements.executionResultLogPanel,
      logStatus: elements.executionResultLogStatus,
      log: elements.executionResultLog,
    },
    {
      titleText: "具体执行结果",
      emptyTitle,
      emptyMessage,
      emptySummary,
      noResultsMessage: state.executionResultError || "这次执行还没有可展示的脚本级结果。",
    },
  );
}

async function loadAgentExecutionResult(options = {}) {
  const { force = false } = options;
  const context = getAgentExecutionResultContext();
  if (state.executionResultKey && state.executionResultKey !== context.cacheKey) {
    resetAgentExecutionResult();
  }
  if (!context.suiteUid) {
    state.executionResultKey = context.cacheKey;
    state.executionResultRecord = buildAgentLocalExecutionRecord(context);
    state.executionResultLoading = false;
    state.executionResultError = "";
    renderAgentExecutionResult();
    return;
  }
  if (
    !force &&
    state.executionResultKey === context.cacheKey &&
    state.executionResultRecord &&
    !["queued", "running"].includes(state.executionResultRecord.status)
  ) {
    renderAgentExecutionResult();
    return;
  }

  const requestId = state.executionResultRequestId + 1;
  state.executionResultRequestId = requestId;
  state.executionResultKey = context.cacheKey;
  state.executionResultLoading = true;
  state.executionResultError = "";
  state.executionResultRecord = mergeAgentExecutionRecord(state.executionResultRecord, context);
  renderAgentExecutionResult();

  try {
    const data = await requestJson(
      `/api/test-suites/${encodePathPart(context.suiteUid)}/execution-records?limit=20`,
    );
    if (requestId !== state.executionResultRequestId || context.agentRunId !== state.selectedRun?.run_id) {
      return;
    }
    const records = normalizeTestSuiteExecutionRunList(data.records || []);
    const record =
      records.find((item) => context.executionRunId && item.run_id === context.executionRunId) ||
      records[0] ||
      null;
    state.executionResultRecord = mergeAgentExecutionRecord(record, context);
  } catch (error) {
    if (requestId !== state.executionResultRequestId || context.agentRunId !== state.selectedRun?.run_id) {
      return;
    }
    state.executionResultRecord = mergeAgentExecutionRecord(null, context);
    state.executionResultError = error.message || "读取执行结果失败。";
  } finally {
    if (requestId === state.executionResultRequestId) {
      state.executionResultLoading = false;
      renderAgentExecutionResult();
    }
  }
}

function renderEvents() {
  elements.eventSummary.textContent = `已加载 ${state.events.length} 条事件`;
  elements.eventLog.textContent = state.events
    .slice(-400)
    .map((event) => {
      const step = event.step_key ? `[${agentStepLabel(event.step_key)}]` : "";
      return `${formatDateTime(event.created_at)} ${step} ${event.event_type}: ${event.message}`;
    })
    .join("\n");
  elements.eventLog.scrollTop = elements.eventLog.scrollHeight;
}

function renderAll() {
  renderRunList();
  renderRequirements();
  renderRetryStatusBar();
  renderTimeline();
  renderArtifacts();
  renderEvents();
}

async function resolvePromptText(artifact) {
  if (artifact.jobId) {
    const cacheKey = `job:${artifact.jobId}`;
    try {
      if (!state.jobCache.has(cacheKey)) {
        state.jobCache.set(cacheKey, requestJson(`/api/jobs/${encodePathPart(artifact.jobId)}`));
      }
      const data = await state.jobCache.get(cacheKey);
      if (data.job?.prompt) {
        return data.job.prompt;
      }
    } catch (error) {
      if (artifact.promptText) {
        return artifact.promptText;
      }
      return `读取任务生成语句失败：${error.message}`;
    }
  }
  if (artifact.promptText) {
    return artifact.promptText;
  }
  if (artifact.promptObject && Object.keys(artifact.promptObject).length) {
    return formatJsonPreview(artifact.promptObject);
  }
  return "暂无生成语句。";
}

async function resolveContentText(artifact) {
  if (artifact.contentText) {
    return artifact.contentText;
  }
  const loader = artifact.contentLoader || {};
  if (loader.type === "plan" && loader.moduleName && loader.planFilename) {
    const cacheKey = `plan:${loader.moduleName}:${loader.planFilename}`;
    if (!state.contentCache.has(cacheKey)) {
      state.contentCache.set(
        cacheKey,
        requestJson(`/api/plans/${encodePathPart(loader.moduleName)}/${encodePathPart(loader.planFilename)}`),
      );
    }
    const data = await state.contentCache.get(cacheKey);
    return data.markdown || "计划文件暂无内容。";
  }
  if (loader.type === "script" && loader.moduleName && loader.filename) {
    const cacheKey = `script:${loader.moduleName}:${loader.filename}`;
    if (!state.contentCache.has(cacheKey)) {
      state.contentCache.set(
        cacheKey,
        requestJson(`/api/test-scripts/${encodePathPart(loader.moduleName)}/${encodePathPart(loader.filename)}`),
      );
    }
    const data = await state.contentCache.get(cacheKey);
    return data.content || "脚本文件暂无内容。";
  }
  if (artifact.contentObject !== undefined) {
    return formatJsonPreview(artifact.contentObject);
  }
  return "暂无产物文本。";
}

function retryPhaseState(flow, phase) {
  const currentIndex = RETRY_FLOW_PHASES.indexOf(flow?.current_phase);
  const phaseIndex = RETRY_FLOW_PHASES.indexOf(phase);
  const terminal = RETRY_FLOW_TERMINAL_STATUSES.has(flow?.status);
  const attemptFields = {
    generating: "generation_attempt_id",
    executing: "execution_attempt_id",
    repairing: "repair_attempt_id",
    verifying: "verification_attempt_id",
  };
  const hasAttempt = Boolean(flow?.[attemptFields[phase]]);
  if (phase === "repairing" && flow?.auto_repair === false && !hasAttempt) {
    return "skipped";
  }
  if (terminal && flow.status === "succeeded") {
    if (phase === "repairing" && !hasAttempt) {
      return "skipped";
    }
    if (phase === "verifying" && !hasAttempt) {
      return "skipped";
    }
    return "succeeded";
  }
  if (terminal && phase === flow.current_phase) {
    return flow.status === "cancelled" ? "cancelled" : "failed";
  }
  if (phaseIndex >= 0 && currentIndex >= 0 && phaseIndex < currentIndex) {
    return phase === "repairing" && !hasAttempt ? "skipped" : "succeeded";
  }
  if (phase === flow?.current_phase && isActiveRetryFlow(flow)) {
    return "running";
  }
  if (hasAttempt && phaseIndex <= currentIndex) {
    return "succeeded";
  }
  return "queued";
}

function retryPhaseStateText(stateValue) {
  return (
    {
      queued: "等待中",
      running: "进行中",
      succeeded: "完成",
      failed: "失败",
      cancelled: "已停止",
      skipped: "无需执行",
    }[stateValue] || stateValue
  );
}

function renderArtifactRetryState() {
  const artifact = state.openArtifact;
  if (!artifact) {
    return;
  }
  const flow =
    state.retryFlows.find((item) => item.retry_flow_id === state.openRetryFlowId) ||
    findRetryFlowForArtifact(artifact) ||
    null;
  if (flow) {
    state.openRetryFlowId = flow.retry_flow_id;
  }
  const isFailureArtifact = artifact.type === "failure" || artifact.originalStatus === "failed" || artifact.status === "failed";
  const isScriptGenerationFailure = isFailureArtifact && artifact.sourceStep === "generate_scripts";
  const flowActive = isActiveRetryFlow(flow);
  const retryResolved = flow?.status === "succeeded" || artifact.status === "resolved";
  const mainRunActive = Boolean(getActiveRun());
  const canDownloadDiagnostic = isFailureArtifact && Boolean(state.selectedRun?.run_id || state.selectedRunId);
  const canStartRetry = isScriptGenerationFailure && !flowActive && !retryResolved && !mainRunActive;
  const showActions = isFailureArtifact || isScriptGenerationFailure || Boolean(flow);

  const modalStatus = flowActive
    ? "retrying"
    : flow?.status === "succeeded"
      ? "resolved"
      : flow && RETRY_FLOW_TERMINAL_STATUSES.has(flow.status)
        ? flow.status
        : artifact.status;
  elements.artifactModalMeta.textContent = artifactMeta([artifact.kindLabel, statusText(modalStatus), artifact.subtitle]);

  elements.artifactDiagnosticActions.classList.toggle("hidden", !showActions);
  elements.artifactDiagnosticDownload.classList.toggle("hidden", !isFailureArtifact);
  elements.artifactDiagnosticDownload.disabled = !canDownloadDiagnostic;
  elements.artifactDiagnosticDownload.textContent = "下载诊断包";
  elements.artifactDiagnosticHint.classList.toggle("hidden", !isFailureArtifact);
  elements.artifactDiagnosticHint.textContent = artifact.attemptId
    ? "诊断包包含脱敏日志、Prompt、上下文、源码和小型执行产物。"
    : "历史失败会在首次操作时补建诊断记录；下载内容同样会自动脱敏。";

  elements.artifactRetryOption.classList.toggle("hidden", !canStartRetry);
  elements.artifactRetryAutoRepair.disabled = flowActive || state.retryRequestPending;
  if (flowActive) {
    elements.artifactRetryAutoRepair.checked = flow.auto_repair !== false;
  }
  elements.artifactRetryButton.classList.toggle("hidden", !isScriptGenerationFailure || retryResolved);
  elements.artifactRetryButton.disabled = !canStartRetry || state.retryRequestPending;
  elements.artifactRetryButton.textContent = state.retryRequestPending
    ? "正在启动…"
    : flowActive
      ? "重试验证中…"
      : flow && ["failed", "blocked", "cancelled"].includes(flow.status)
        ? "再次重试并验证"
        : "重试并验证";
  elements.artifactRetryCancelButton.classList.toggle("hidden", !flowActive);
  elements.artifactRetryCancelButton.disabled = !["queued", "running"].includes(flow?.status);
  elements.artifactRetryCancelButton.textContent =
    flow?.status === "cancelling" ? "正在停止…" : flow?.status === "finalizing" ? "正在收尾…" : "停止重试";

  elements.artifactRetryProgress.classList.toggle("hidden", !flow);
  if (!flow) {
    return;
  }
  elements.artifactRetryProgressTitle.textContent = `${retryFlowTitle(flow)} · 重试并验证`;
  elements.artifactRetryProgressStatus.textContent = retryFlowProgressText(flow);
  const badgeStatus = isActiveRetryFlow(flow) ? "running" : flow.status === "succeeded" ? "succeeded" : flow.status;
  setBadge(elements.artifactRetryProgressBadge, badgeStatus);
  elements.artifactRetryProgressSteps.innerHTML = [
    ["generating", "重新生成"],
    ["executing", "执行新脚本"],
    ["repairing", "自动修复（按需）"],
    ["verifying", "修复后复验"],
  ]
    .map(([phase, label]) => {
      const phaseState = retryPhaseState(flow, phase);
      const marker = phaseState === "succeeded" ? "✓" : phaseState === "running" ? "●" : phaseState === "failed" ? "!" : "○";
      return `<li class="${escapeHtml(phaseState)}"><span aria-hidden="true">${marker}</span><strong>${escapeHtml(label)}</strong><em>${escapeHtml(
        retryPhaseStateText(phaseState),
      )}</em></li>`;
    })
    .join("");
  elements.artifactRetryProgressNote.textContent = isActiveRetryFlow(flow)
    ? "重试在后台运行，关闭弹窗或切换页面不会停止任务。"
    : flow.status === "succeeded"
      ? "新脚本已完成执行验证；本次重试的历史记录和诊断信息会继续保留。"
      : flow.error || "本次流程已结束，可以查看失败详情后再次重试。";
}

async function openArtifact(artifactId) {
  const artifact = state.currentArtifacts.find((item) => item.id === artifactId);
  if (!artifact) {
    return;
  }
  state.openArtifact = artifact;
  state.artifactModalOpener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  elements.artifactModalTitle.textContent = artifact.title || "生成物";
  elements.artifactModalMeta.textContent = artifactMeta([artifact.kindLabel, statusText(artifact.status), artifact.subtitle]);
  elements.artifactContentTitle.textContent = artifact.contentTitle || "产物文本";
  elements.artifactPromptText.textContent = "正在加载...";
  elements.artifactContentText.textContent = "正在加载...";
  const initialFlow =
    state.retryFlows.find((item) => item.retry_flow_id === artifact.retryFlowId) || findRetryFlowForArtifact(artifact) || null;
  state.openRetryFlowId = initialFlow?.retry_flow_id || "";
  elements.artifactRetryAutoRepair.checked = initialFlow ? initialFlow.auto_repair !== false : true;
  renderArtifactRetryState();
  elements.artifactModal.classList.remove("hidden");
  document.body.classList.add("agent-modal-open");
  window.requestAnimationFrame(() => elements.artifactModalClose.focus());

  try {
    const [promptText, contentText] = await Promise.all([resolvePromptText(artifact), resolveContentText(artifact)]);
    elements.artifactPromptText.textContent = promptText;
    elements.artifactContentText.textContent = contentText;
  } catch (error) {
    elements.artifactContentText.textContent = `读取产物失败：${error.message}`;
  }
}

function artifactRetrySelector(artifact) {
  const source = isPlainObject(artifact?.contentObject) ? artifact.contentObject : {};
  return {
    step_key: artifact?.sourceStep || "generate_scripts",
    module_uid: source.module_uid || "",
    module_name: source.module_name || "",
    plan_filename: source.plan_filename || "",
    filename: source.filename || "",
    job_id: artifact?.jobId || source.job_id || "",
  };
}

async function ensureArtifactAttemptId(artifact, runId) {
  if (artifact.attemptId) {
    return artifact.attemptId;
  }
  const data = await requestJson(`/api/agent/runs/${encodePathPart(runId)}/legacy-failure-attempt`, {
    method: "POST",
    body: JSON.stringify(artifactRetrySelector(artifact)),
  });
  const attemptId = data.attempt?.attempt_id || data.attempt_id || "";
  if (!attemptId) {
    throw new Error("历史失败记录补建成功，但接口没有返回 attempt_id。");
  }
  artifact.attemptId = attemptId;
  if (isPlainObject(artifact.contentObject)) {
    artifact.contentObject.attempt_id = attemptId;
    artifact.contentObject.failure_id = artifact.contentObject.failure_id || attemptId;
  }
  return attemptId;
}

async function retryArtifactAndVerify() {
  const artifact = state.openArtifact;
  const runId = state.selectedRun?.run_id || state.selectedRunId;
  if (!artifact || artifact.sourceStep !== "generate_scripts" || !runId || state.retryRequestPending) {
    return;
  }
  state.retryRequestPending = true;
  renderArtifactRetryState();
  try {
    const attemptId = await ensureArtifactAttemptId(artifact, runId);
    const data = await requestJson(
      `/api/agent/runs/${encodePathPart(runId)}/attempts/${encodePathPart(attemptId)}/retry`,
      {
        method: "POST",
        body: JSON.stringify({ auto_repair: elements.artifactRetryAutoRepair.checked }),
      },
    );
    const flow = mergeRetryFlow(data.retry_flow);
    if (!flow) {
      throw new Error("重试任务已提交，但接口没有返回 retry_flow。");
    }
    markRetryFlowWatched(flow.retry_flow_id);
    state.openRetryFlowId = flow.retry_flow_id;
    renderAll();
    renderArtifactRetryState();
    if (state.isActive && shouldObserveSelectedRun() && !state.streamController) {
      startEventStream();
    }
    setNotice(data.idempotent ? "该脚本已有重试流程，已打开当前进度。" : "已启动重试并验证；关闭弹窗不会停止后台流程。", "success");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    state.retryRequestPending = false;
    renderArtifactRetryState();
  }
}

async function cancelArtifactRetry() {
  const flow = state.retryFlows.find((item) => item.retry_flow_id === state.openRetryFlowId);
  const runId = state.selectedRun?.run_id || state.selectedRunId;
  if (!flow || !runId || !["queued", "running"].includes(flow.status)) {
    return;
  }
  if (!window.confirm(`确定停止“${retryFlowTitle(flow)}”的重试并验证吗？已生成的中间结果会保留。`)) {
    return;
  }
  elements.artifactRetryCancelButton.disabled = true;
  try {
    const data = await requestJson(
      `/api/agent/runs/${encodePathPart(runId)}/retry-flows/${encodePathPart(flow.retry_flow_id)}/cancel`,
      { method: "POST", body: JSON.stringify({}) },
    );
    mergeRetryFlow(data.retry_flow);
    renderAll();
    renderArtifactRetryState();
    setNotice(data.cancel_requested ? "已请求停止本次重试。" : data.reason || "重试正在收尾或已经结束，无法再停止。", "success");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    renderArtifactRetryState();
  }
}

async function showRetryFlowDetails(flowId) {
  const flow = state.retryFlows.find((item) => item.retry_flow_id === flowId);
  if (!flow) {
    return;
  }
  state.activeStepKey = "generate_scripts";
  renderTimeline();
  renderArtifacts();
  let artifact = state.currentArtifacts.find((item) => findRetryFlowForArtifact(item)?.retry_flow_id === flow.retry_flow_id);
  if (!artifact) {
    artifact = decorateArtifactRetryStatus({
      id: `generate_scripts:retry-flow:${flow.retry_flow_id}`,
      type: "failure",
      kindLabel: "脚本重试",
      title: retryFlowTitle(flow),
      subtitle: artifactMeta([flow.module_name, retryFlowProgressText(flow)]),
      status: isActiveRetryFlow(flow) ? "retrying" : flow.status === "succeeded" ? "resolved" : "failed",
      originalStatus: "failed",
      sourceStep: "generate_scripts",
      attemptId: flow.root_attempt_id || "",
      contentTitle: "重试详情",
      contentObject: flow,
      retryFlowId: flow.retry_flow_id,
    });
    state.currentArtifacts.push(artifact);
  }
  state.openRetryFlowId = flow.retry_flow_id;
  await openArtifact(artifact.id);
}

async function downloadArtifactDiagnosticBundle() {
  const artifact = state.openArtifact;
  const runId = state.selectedRun?.run_id || state.selectedRunId;
  if (!artifact || (artifact.type !== "failure" && artifact.originalStatus !== "failed" && artifact.status !== "failed") || !runId) {
    return;
  }
  const button = elements.artifactDiagnosticDownload;
  button.disabled = true;
  button.textContent = "正在生成…";
  try {
    const response = artifact.attemptId
      ? await fetch(
          `/api/agent/runs/${encodePathPart(runId)}/attempts/${encodePathPart(artifact.attemptId)}/diagnostic-bundle`,
          { headers: getProjectHeaders() },
        )
      : await fetch(`/api/agent/runs/${encodePathPart(runId)}/legacy-diagnostic-bundle`, {
          method: "POST",
          headers: getProjectHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            step_key: artifact.sourceStep,
            module_uid: artifact.contentObject?.module_uid || "",
            module_name: artifact.contentObject?.module_name || "",
            plan_filename: artifact.contentObject?.plan_filename || "",
            filename: artifact.contentObject?.filename || "",
            job_id: artifact.jobId || "",
          }),
        });
    if (!response.ok) {
      let message = `生成诊断包失败：${response.status}`;
      try {
        const data = await response.json();
        message = data.error || message;
      } catch (_error) {
        // 非 JSON 错误响应沿用状态码提示。
      }
      throw new Error(message);
    }
    const blob = await response.blob();
    const fallback = `agent-diagnostic-${artifact.attemptId || "legacy-failure"}.zip`;
    const filename = getDownloadFilename(response, fallback);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setNotice("诊断包已下载。", "success");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "下载诊断包";
  }
}

function closeArtifactModal() {
  elements.artifactModal.classList.add("hidden");
  state.openArtifact = null;
  state.openRetryFlowId = "";
  document.body.classList.remove("agent-modal-open");
  if (state.artifactModalOpener?.isConnected) {
    state.artifactModalOpener.focus();
  }
  state.artifactModalOpener = null;
}

function openNewTaskModal() {
  if (getActiveRun()) {
    setNotice("当前项目已有任务运行中，停止或完成后才能新建任务。", "error");
    return;
  }
  if (state.runs.some(runHasActiveRetrySummary)) {
    setNotice("当前项目有脚本正在重试并验证，完成或取消后才能新建任务。", "error");
    return;
  }
  if (state.runs.some((run) => run.status === "awaiting_script_action")) {
    setNotice("当前项目有脚本等待人工处理，完成处理或终止任务后才能新建任务。", "error");
    return;
  }
  populateAgentCoveragePrompt(state.defaultCoverageProfile);
  elements.newTaskModal.classList.remove("hidden");
  window.WaterfallI18n?.localizeDom(elements.newTaskModal);
  document.body.classList.add("agent-modal-open");
  window.requestAnimationFrame(() => {
    if (elements.requirementSelect.value) {
      elements.requirementSelect.focus();
    } else {
      elements.requirementFile.focus();
    }
  });
}

function closeNewTaskModal() {
  elements.newTaskModal.classList.add("hidden");
  document.body.classList.remove("agent-modal-open");
}

async function loadRequirements() {
  const data = await requestJson("/api/requirements");
  state.requirements = data.requirements || [];
}

async function loadRuns() {
  const data = await requestJson("/api/agent/runs");
  state.runs = data.runs || [];
  const selectionExists = state.runs.some((run) => run.run_id === state.selectedRunId);
  if ((!state.selectedRunId || !selectionExists) && state.runs.length) {
    const activeRetryRun = state.runs.find(runHasActiveRetrySummary);
    const storedRunId = readStorageItem(selectedRunStorageKey()) || "";
    const storedRun = state.runs.find((run) => run.run_id === storedRunId);
    state.selectedRunId = activeRetryRun?.run_id || storedRun?.run_id || state.runs[0].run_id;
  }
}

async function loadRetryFlows(runId = state.selectedRunId) {
  if (!runId) {
    state.retryFlows = [];
    state.activeRetryFlows = [];
    return;
  }
  try {
    const data = await requestJson(`/api/agent/runs/${encodePathPart(runId)}/retry-flows`);
    applyRetryFlowData(data, { replace: true });
  } catch (_error) {
    // 兼容尚未部署单项重试接口的服务；主任务详情仍可正常查看。
  }
}

async function loadRunDetail(runId = state.selectedRunId, options = {}) {
  const { loadInitialEvents = true, keepActiveStep = false } = options;
  const detailRequestId = ++state.runDetailRequestId;
  if (!runId) {
    state.selectedRun = null;
    state.steps = [];
    state.events = [];
    state.lastEventId = 0;
    state.retryFlows = [];
    state.activeRetryFlows = [];
    resetAgentExecutionResult();
    return true;
  }
  const data = await requestJson(`/api/agent/runs/${encodeURIComponent(runId)}`);
  if (detailRequestId !== state.runDetailRequestId || runId !== state.selectedRunId) {
    return false;
  }
  state.selectedRun = data.run;
  state.steps = data.steps || [];
  state.selectedRunId = runId;
  persistSelectedRunId(runId);
  if (!applyRetryFlowData(data, { replace: true })) {
    await loadRetryFlows(runId);
  }
  if (!keepActiveStep) {
    state.activeStepKey = state.selectedRun?.current_step || "upload_requirement";
  }
  if (loadInitialEvents) {
    await loadEvents(true);
  }
  if (state.activeStepKey === "run_suite") {
    await loadAgentExecutionResult({ force: true });
  }
  return true;
}

async function loadEvents(reset = false) {
  if (!state.selectedRunId) {
    return;
  }
  const afterId = reset ? 0 : state.lastEventId;
  const params = new URLSearchParams({
    after_id: String(afterId),
    limit: String(INITIAL_EVENT_LIMIT),
  });
  if (reset) {
    params.set("tail", "1");
  }
  const data = await requestJson(`/api/agent/runs/${encodeURIComponent(state.selectedRunId)}/events?${params.toString()}`);
  if (reset) {
    state.events = [];
    state.lastEventId = 0;
  }
  mergeEvents(data.events || []);
}

function mergeEvents(events) {
  if (!events.length) {
    return;
  }
  const seen = new Set(state.events.map((event) => Number(event.event_id)));
  events.forEach((event) => {
    if (event.run_id && event.run_id !== state.selectedRunId) {
      return;
    }
    const eventId = Number(event.event_id) || 0;
    if (!seen.has(eventId)) {
      state.events.push(event);
      seen.add(eventId);
      state.lastEventId = Math.max(state.lastEventId, eventId);
      applyRetryFlowProgressEvent(event);
      applyAgentStepProgressEvent(event);
      if (event.step_key === "prepare_scripts") {
        scriptPreparation.applyEvent(event);
      }
    }
  });
}

function scheduleRetryTerminalRefresh(runId = state.selectedRunId) {
  if (!runId || state.retryTerminalRefreshPending) {
    return;
  }
  state.retryTerminalRefreshPending = true;
  window.setTimeout(async () => {
    try {
      if (state.isActive && state.selectedRunId === runId) {
        await refreshSelectedRun();
      }
    } catch (error) {
      if (state.isActive && state.selectedRunId === runId) {
        setNotice(error.message, "error");
      }
    } finally {
      state.retryTerminalRefreshPending = false;
    }
  }, 0);
}

function applyRetryFlowProgressEvent(event) {
  const payload = event?.payload || {};
  if (!payload.retry_flow_progress || !isPlainObject(payload.retry_flow)) {
    return;
  }
  const incoming = normalizeRetryFlow(payload.retry_flow);
  const previous = incoming
    ? state.retryFlows.find((flow) => flow.retry_flow_id === incoming.retry_flow_id)
    : null;
  const merged = mergeRetryFlow(payload.retry_flow);
  if (previous && isActiveRetryFlow(previous) && merged && RETRY_FLOW_TERMINAL_STATUSES.has(merged.status)) {
    scheduleRetryTerminalRefresh(event.run_id || state.selectedRunId);
  }
}

function applyAgentStepProgressEvent(event) {
  const payload = event?.payload || {};
  if (!payload.artifact_progress) {
    return;
  }
  const step = getStep(event.step_key);
  if (!step || ["succeeded", "failed", "cancelled", "skipped"].includes(step.status)) {
    return;
  }
  if (step.status !== "running" && isActiveStatus(state.selectedRun?.status)) {
    step.status = "running";
    if (state.selectedRun && event.step_key) {
      state.selectedRun.current_step = event.step_key;
    }
  }
  if (step.status !== "running") {
    return;
  }
  if (isPlainObject(payload.step_input)) {
    step.input = payload.step_input;
  }
  if (isPlainObject(payload.step_output)) {
    step.output = payload.step_output;
  }
  if (isPlainObject(payload.counts)) {
    step.counts = payload.counts;
  }
}

async function startEventStream() {
  if (!state.selectedRunId) {
    return;
  }
  const streamRunId = state.selectedRunId;
  if (state.streamController) {
    state.streamController.abort();
  }
  const controller = new AbortController();
  state.streamController = controller;
  try {
    const response = await fetch(
      `/api/agent/runs/${encodeURIComponent(streamRunId)}/events-stream?after_id=${state.lastEventId}`,
      {
        headers: getProjectHeaders(),
        signal: controller.signal,
      },
    );
    if (!response.ok || !response.body) {
      throw new Error(`事件流连接失败：${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (!state.isActive || state.selectedRunId !== streamRunId) {
        return;
      }
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      const pendingEvents = [];
      let shouldRefreshSelectedRun = false;
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const parsed = parseSseBlock(block);
        if (parsed?.event === "agent-event") {
          pendingEvents.push(parsed.data);
        }
        if (parsed?.event === "done") {
          shouldRefreshSelectedRun = true;
        }
        if (parsed?.event === "heartbeat") {
          const heartbeatActive = asArray(parsed.data?.active_retry_flows);
          if (heartbeatActive.length) {
            applyRetryFlowData({ active_retry_flows: heartbeatActive }, { replace: false });
          } else if (state.activeRetryFlows.length) {
            shouldRefreshSelectedRun = true;
          }
        }
        boundary = buffer.indexOf("\n\n");
      }
      if (pendingEvents.length) {
        if (state.selectedRunId !== streamRunId) {
          return;
        }
        mergeEvents(pendingEvents);
        renderRunList();
        renderRetryStatusBar();
        renderEvents();
        renderTimeline();
        renderArtifacts();
        notifyCompletedRetryFlows();
        if (
          state.activeStepKey === "run_suite" &&
          pendingEvents.some((event) => event.step_key === "run_suite" && event.event_type === "status")
        ) {
          await loadAgentExecutionResult({ force: true });
        }
      }
      if (shouldRefreshSelectedRun) {
        await refreshSelectedRun();
        return;
      }
    }
  } catch (error) {
    if (error.name !== "AbortError") {
      setNotice(error.message, "error");
    }
  } finally {
    if (state.streamController === controller) {
      state.streamController = null;
    }
  }
}

async function refreshSelectedRun() {
  await loadRuns();
  if (state.selectedRunId) {
    await loadRunDetail(state.selectedRunId, { loadInitialEvents: false, keepActiveStep: true });
  }
  renderAll();
  notifyCompletedRetryFlows();
  if (state.isActive && shouldObserveSelectedRun() && !state.streamController) {
    startEventStream();
  }
}

async function selectRun(runId) {
  stopEventStream();
  state.runMenuOpen = false;
  state.runSearchQuery = "";
  elements.runSearch.value = "";
  state.selectedRunId = runId;
  state.retryFlows = [];
  state.activeRetryFlows = [];
  state.openRetryFlowId = "";
  persistSelectedRunId(runId);
  state.jobCache.clear();
  state.contentCache.clear();
  resetAgentExecutionResult();
  const loaded = await loadRunDetail(runId, { keepActiveStep: false });
  if (!loaded || state.selectedRunId !== runId) {
    return;
  }
  renderAll();
  if (shouldObserveSelectedRun()) {
    startEventStream();
  }
}

async function submitRun(event) {
  event.preventDefault();
  setNotice("");
  elements.startButton.disabled = true;
  try {
    const coverageProfile = elements.coverageProfile.value || state.defaultCoverageProfile;
    const coveragePrompt = elements.coveragePrompt.value.trim();
    const promptCustomized = coveragePrompt !== String(agentCoverageProfile(coverageProfile)?.template_prompt || "").trim();
    const file = elements.requirementFile.files?.[0];
    let response;
    if (file) {
      const form = new FormData();
      form.append("file", file);
      form.append("coverage_profile", coverageProfile);
      form.append("coverage_prompt", coveragePrompt);
      form.append("prompt_customized", String(promptCustomized));
      response = await fetch("/api/agent/runs", {
        method: "POST",
        headers: getProjectHeaders(),
        body: form,
      });
    } else {
      const requirementUid = elements.requirementSelect.value;
      if (!requirementUid) {
        throw new Error("请选择需求 Markdown 文件，或选择已有需求。");
      }
      response = await fetch("/api/agent/runs", {
        method: "POST",
        headers: getProjectHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          requirement_uid: requirementUid,
          coverage_profile: coverageProfile,
          coverage_prompt: coveragePrompt,
          prompt_customized: promptCustomized,
        }),
      });
    }
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || `请求失败：${response.status}`);
    }
    state.selectedRunId = data.run.run_id;
    persistSelectedRunId(state.selectedRunId);
    state.selectedRun = data.run;
    state.steps = data.steps || [];
    state.events = data.events || [];
    state.activeStepKey = data.run.current_step || "upload_requirement";
    state.retryFlows = [];
    state.activeRetryFlows = [];
    state.lastEventId = Math.max(0, ...state.events.map((item) => Number(item.event_id) || 0));
    state.jobCache.clear();
    state.contentCache.clear();
    resetAgentExecutionResult();
    elements.requirementFile.value = "";
    elements.fileLabel.textContent = "选择需求 Markdown";
    await loadRuns();
    renderAll();
    closeNewTaskModal();
    startEventStream();
    setNotice("Agent 任务已启动。", "success");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    elements.startButton.disabled = false;
  }
}

async function cancelRun() {
  if (!state.selectedRunId || !["queued", "running", "awaiting_script_action"].includes(state.selectedRun?.status)) {
    return;
  }
  if (!window.confirm("确定停止当前任务吗？已生成的结果会保留。")) {
    return;
  }
  try {
    await requestJson(`/api/agent/runs/${encodeURIComponent(state.selectedRunId)}/cancel`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await refreshSelectedRun();
    setNotice("已请求取消 Agent 任务。");
  } catch (error) {
    setNotice(error.message, "error");
  }
}

async function resumeRun() {
  if (!state.selectedRunId || !isResumableStatus(state.selectedRun?.status)) {
    return;
  }
  elements.resumeButton.disabled = true;
  try {
    const fromStep = state.activeStepKey || state.selectedRun.current_step || "upload_requirement";
    const data = await requestJson(`/api/agent/runs/${encodeURIComponent(state.selectedRunId)}/resume`, {
      method: "POST",
      body: JSON.stringify({ from_step: fromStep }),
    });
    state.selectedRun = data.run;
    state.steps = data.steps || [];
    state.events = data.events || [];
    state.lastEventId = Math.max(0, ...state.events.map((item) => Number(item.event_id) || 0));
    state.jobCache.clear();
    state.contentCache.clear();
    resetAgentExecutionResult();
    renderAll();
    startEventStream();
    const actualFromStep = data.from_step || fromStep;
    const resumePrefix = actualFromStep === fromStep ? "" : `检测到上游存在失败生成物，`;
    setNotice(`${resumePrefix}已从“${agentStepLabel(actualFromStep)}”恢复 Agent 任务。`, "success");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    renderRunList();
  }
}

function resetAgentProjectState() {
  state.runDetailRequestId += 1;
  state.selectedRunId = "";
  state.selectedRun = null;
  state.steps = [];
  state.events = [];
  state.activeStepKey = "upload_requirement";
  state.lastEventId = 0;
  state.runMenuOpen = false;
  state.runSearchQuery = "";
  state.currentArtifacts = [];
  state.openArtifact = null;
  state.retryFlows = [];
  state.activeRetryFlows = [];
  state.openRetryFlowId = "";
  state.retryRequestPending = false;
  state.retryTerminalRefreshPending = false;
  state.artifactModalOpener = null;
  scriptPreparation.setRun("");
  resetAgentExecutionResult();
  state.jobCache.clear();
  state.contentCache.clear();
}

function stopEventStream() {
  if (state.streamController) {
    state.streamController.abort();
    state.streamController = null;
  }
}

function stopRefreshTimer() {
  if (state.refreshTimer) {
    window.clearInterval(state.refreshTimer);
    state.refreshTimer = null;
  }
}

function startRefreshTimer() {
  stopRefreshTimer();
  state.refreshTimer = window.setInterval(() => {
    if (state.isActive && shouldRefreshAgentProject()) {
      refreshSelectedRun();
    }
  }, 5000);
}

async function loadAgentData() {
  await Promise.all([loadRequirements(), loadRuns(), loadAgentCoverageDefaults()]);
  if (state.selectedRunId) {
    await loadRunDetail(state.selectedRunId);
  }
  state.hasLoaded = true;
  renderAll();
  notifyCompletedRetryFlows();
}

async function activate(projectKey = state.currentProjectKey) {
  state.isActive = true;
  if (projectKey && projectKey !== state.currentProjectKey) {
    state.currentProjectKey = projectKey;
    resetAgentProjectState();
  }
  try {
    await loadAgentData();
    if (state.isActive && shouldObserveSelectedRun()) {
      startEventStream();
    }
    startRefreshTimer();
  } catch (error) {
    setNotice(error.message, "error");
  }
}

function deactivate() {
  state.isActive = false;
  stopEventStream();
  stopRefreshTimer();
  scriptPreparation.deactivate();
  elements.artifactModal.classList.add("hidden");
  elements.newTaskModal.classList.add("hidden");
  state.openArtifact = null;
  state.openRetryFlowId = "";
  state.artifactModalOpener = null;
  document.body.classList.remove("agent-modal-open");
}

async function setProject(projectKey) {
  if (projectKey === state.currentProjectKey && state.hasLoaded) {
    return;
  }
  state.currentProjectKey = projectKey || "";
  resetAgentProjectState();
  if (state.isActive) {
    await activate(state.currentProjectKey);
  } else {
    renderAll();
  }
}

const handleCurrentRunClick = () => {
  state.runMenuOpen = !state.runMenuOpen;
  renderRunList();
  if (state.runMenuOpen) {
    window.requestAnimationFrame(() => elements.runSearch.focus());
  }
};
elements.currentRunMain.addEventListener("click", handleCurrentRunClick);
elements.runSearch.addEventListener("input", () => {
  state.runSearchQuery = elements.runSearch.value;
  renderRunList();
});
root.addEventListener("click", (event) => {
  if (state.runMenuOpen && !event.target.closest(".task-select-wrap")) {
    state.runMenuOpen = false;
    renderRunList();
  }
});

function trapAgentModalFocus(event, modal) {
  if (event.key !== "Tab") {
    return;
  }
  const focusable = Array.from(
    modal.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href]',
    ),
  ).filter((element) => !element.classList.contains("hidden") && element.getClientRects().length);
  if (!focusable.length) {
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

root.addEventListener("keydown", (event) => {
  if (!elements.artifactModal.classList.contains("hidden")) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeArtifactModal();
      return;
    }
    trapAgentModalFocus(event, elements.artifactModal);
  }
  if (event.key === "Escape" && state.runMenuOpen) {
    state.runMenuOpen = false;
    renderRunList();
    elements.currentRunMain.focus();
  }
});
elements.newRunButton.addEventListener("click", openNewTaskModal);
elements.launchForm.addEventListener("submit", submitRun);
elements.resumeButton.addEventListener("click", resumeRun);
elements.cancelButton.addEventListener("click", cancelRun);
elements.artifactModalClose.addEventListener("click", closeArtifactModal);
elements.artifactModalBackdrop.addEventListener("click", closeArtifactModal);
elements.artifactDiagnosticDownload.addEventListener("click", downloadArtifactDiagnosticBundle);
elements.artifactRetryCancelButton.addEventListener("click", cancelArtifactRetry);
elements.artifactRetryButton.addEventListener("click", retryArtifactAndVerify);
elements.retryStatusView.addEventListener("click", () => showRetryFlowDetails(elements.retryStatusView.dataset.retryFlowId));
elements.newTaskModalClose.addEventListener("click", closeNewTaskModal);
elements.newTaskModalBackdrop.addEventListener("click", closeNewTaskModal);
elements.newTaskCancelButton.addEventListener("click", closeNewTaskModal);
elements.coverageProfile.addEventListener("change", changeAgentCoverageProfile);
elements.coveragePrompt.addEventListener("input", renderAgentCoverageState);
elements.coverageReset.addEventListener("click", resetAgentCoveragePrompt);
const handleRequirementFileChange = () => {
  const file = elements.requirementFile.files?.[0];
  elements.fileLabel.textContent = file
    ? file.name
    : (window.WaterfallI18n?.source("选择需求 Markdown") || "选择需求 Markdown");
  if (file) {
    elements.requirementSelect.value = "";
  }
};
elements.requirementFile.addEventListener("change", handleRequirementFileChange);
const handleRequirementSelectChange = () => {
  if (elements.requirementSelect.value) {
    elements.requirementFile.value = "";
    elements.fileLabel.textContent = window.WaterfallI18n?.source("选择需求 Markdown") || "选择需求 Markdown";
  }
};
elements.requirementSelect.addEventListener("change", handleRequirementSelectChange);

const handleKeydown = (event) => {
  if (!state.isActive) {
    return;
  }
  if (event.key === "Escape" && !elements.artifactModal.classList.contains("hidden")) {
    closeArtifactModal();
  } else if (event.key === "Escape" && !elements.newTaskModal.classList.contains("hidden")) {
    closeNewTaskModal();
  }
};

const handleBeforeUnload = () => {
  deactivate();
};

window.addEventListener("keydown", handleKeydown);
window.addEventListener("beforeunload", handleBeforeUnload);

function destroy() {
  deactivate();
  scriptPreparation.destroy();
  window.removeEventListener("keydown", handleKeydown);
  window.removeEventListener("beforeunload", handleBeforeUnload);
}

renderAll();

return { activate, deactivate, setProject, destroy };

}

window.createAgentAutoTest = createAgentAutoTest;
