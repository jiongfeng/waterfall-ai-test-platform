function createAgentScriptPreparationFeature(root, options = {}) {
  if (!root) {
    throw new Error("脚本准备挂载容器不存在。");
  }

  const documentRef = options.document || document;
  const windowRef = options.window || window;
  const apiClient = options.apiClient || null;
  const requestJson = options.requestJson || apiClient?.requestJson;
  if (typeof requestJson !== "function") {
    throw new Error("脚本准备功能缺少 requestJson 依赖。");
  }

  const getRunId = typeof options.getRunId === "function" ? options.getRunId : () => "";
  const notify = typeof options.setNotice === "function" ? options.setNotice : setLocalNotice;
  const onStateChange = typeof options.onStateChange === "function" ? options.onStateChange : () => {};
  const confirmAction = typeof options.confirm === "function" ? options.confirm : (message) => windowRef.confirm(message);
  const encodePathPart = options.encodePathPart || ((value) => encodeURIComponent(String(value || "")));

  const preparationElement = (name) => {
    const element = root.querySelector(`[data-script-preparation-id="${name}"]`);
    if (!element) {
      throw new Error(`脚本准备页面元素不存在：${name}`);
    }
    return element;
  };

  const elements = {
    stageMeta: preparationElement("stageMeta"),
    stageTitle: preparationElement("stageTitle"),
    stageSummary: preparationElement("stageSummary"),
    stageStatus: preparationElement("stageStatus"),
    bulkToggle: preparationElement("bulkToggle"),
    bulkExit: preparationElement("bulkExit"),
    progressValue: preparationElement("progressValue"),
    progressBar: preparationElement("progressBar"),
    processingCount: preparationElement("processingCount"),
    readyCount: preparationElement("readyCount"),
    awaitingCount: preparationElement("awaitingCount"),
    abandonedCount: preparationElement("abandonedCount"),
    filterBar: preparationElement("filterBar"),
    searchInput: preparationElement("searchInput"),
    batchBar: preparationElement("batchBar"),
    selectedCount: preparationElement("selectedCount"),
    batchHint: preparationElement("batchHint"),
    clearSelection: preparationElement("clearSelection"),
    batchMenuToggle: preparationElement("batchMenuToggle"),
    batchMenu: preparationElement("batchMenu"),
    batchExecute: preparationElement("batchExecute"),
    batchRepair: preparationElement("batchRepair"),
    selectAll: preparationElement("selectAll"),
    tableBody: preparationElement("tableBody"),
    tableEmpty: preparationElement("tableEmpty"),
    tableFooterTotal: preparationElement("tableFooterTotal"),
    tableFooterHint: preparationElement("tableFooterHint"),
    detailModal: preparationElement("detailModal"),
    detailBackdrop: preparationElement("detailBackdrop"),
    detailClose: preparationElement("detailClose"),
    detailMeta: preparationElement("detailMeta"),
    detailTitle: preparationElement("detailTitle"),
    detailBadges: preparationElement("detailBadges"),
    historyList: preparationElement("historyList"),
    detailContent: preparationElement("detailContent"),
    actionPanel: preparationElement("actionPanel"),
    editorModal: preparationElement("editorModal"),
    editorBackdrop: preparationElement("editorBackdrop"),
    editorClose: preparationElement("editorClose"),
    editorMeta: preparationElement("editorMeta"),
    editorTitle: preparationElement("editorTitle"),
    editorDescription: preparationElement("editorDescription"),
    editSection: preparationElement("editSection"),
    promptSection: preparationElement("promptSection"),
    scriptEditor: preparationElement("scriptEditor"),
    originalPrompt: preparationElement("originalPrompt"),
    supplementalPrompt: preparationElement("supplementalPrompt"),
    editorBaseline: preparationElement("editorBaseline"),
    editorTarget: preparationElement("editorTarget"),
    editorCancel: preparationElement("editorCancel"),
    editorSave: preparationElement("editorSave"),
    editorSaveExecute: preparationElement("editorSaveExecute"),
    editorConfirm: preparationElement("editorConfirm"),
    localNotice: preparationElement("localNotice"),
  };

  const PROCESSING_STATUSES = new Set([
    "queued",
    "running",
    "generating",
    "executing",
    "repairing",
    "analyzing",
    "retrying",
    "editing",
    "finalizing",
  ]);
  const READY_STATUSES = new Set(["ready", "passed", "resolved", "succeeded", "verified"]);
  const HUMAN_STATUSES = new Set([
    "awaiting_human",
    "awaiting_action",
    "waiting_for_action",
    "pending_verification",
    "unresolved",
  ]);
  const ABANDONED_STATUSES = new Set([
    "abandoned",
    "ignored",
    "excluded",
    "deleted",
    "kept_unresolved",
  ]);
  const HUMAN_HISTORY_TYPES = new Set(["awaiting_human", "awaiting_action", "human_review", "manual_review"]);

  const state = {
    active: false,
    runId: String(options.runId || getRunId() || ""),
    status: "queued",
    summary: "系统依次完成生成、执行和一次自动修复；待人工脚本不阻塞后续处理。",
    items: [],
    counts: {},
    filter: "all",
    search: "",
    bulkMode: false,
    batchMenuOpen: false,
    selectedIds: new Set(),
    openItem: null,
    selectedHistoryId: "",
    followLatestHistory: true,
    editorMode: "",
    editorDraft: null,
    loading: false,
    detailLoading: false,
    actionPending: false,
    snapshotRequestId: 0,
    detailRequestId: 0,
    actionRequestId: 0,
    detailOpener: null,
    editorOpener: null,
  };

  function setLocalNotice(message, type = "") {
    elements.localNotice.textContent = message || "";
    elements.localNotice.className = `notice script-preparation-notice ${type || ""}`.trim();
    elements.localNotice.classList.toggle("hidden", !message);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function isPlainObject(value) {
    return Boolean(value && typeof value === "object" && !Array.isArray(value));
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function firstText(...values) {
    return values.map((value) => String(value ?? "").trim()).find(Boolean) || "";
  }

  function firstNumber(...values) {
    for (const value of values) {
      if (value === null || value === undefined || value === "") {
        continue;
      }
      const number = Number(value);
      if (Number.isFinite(number)) {
        return number;
      }
    }
    return 0;
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
      hour12: false,
    }).format(new Date(value));
  }

  function formatClock(timestamp) {
    const value = Number(timestamp);
    if (!value) {
      return "-";
    }
    return new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(new Date(value));
  }

  function formatDuration(startedAt, finishedAt) {
    const start = Number(startedAt);
    const finish = Number(finishedAt);
    if (!start || !finish || finish < start) {
      return "";
    }
    const seconds = Math.max(0, Math.floor((finish - start) / 1000));
    if (seconds >= 60) {
      return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
    }
    return `${seconds} 秒`;
  }

  function normalizeStatus(value) {
    return String(value || "queued").trim().toLowerCase();
  }

  function hasCurrentScript(item) {
    return Boolean(isPlainObject(item?.current_script) || firstText(item?.script_content));
  }

  function statusGroup(value) {
    const status = normalizeStatus(value);
    if (READY_STATUSES.has(status)) {
      return "ready";
    }
    if (HUMAN_STATUSES.has(status)) {
      return "awaiting_human";
    }
    if (ABANDONED_STATUSES.has(status)) {
      return "abandoned";
    }
    if (PROCESSING_STATUSES.has(status)) {
      return "processing";
    }
    if (["failed", "blocked"].includes(status)) {
      return "awaiting_human";
    }
    return "processing";
  }

  function statusInfo(value) {
    const status = normalizeStatus(value);
    const group = statusGroup(status);
    if (group === "ready") {
      return { label: "已通过", className: "success" };
    }
    if (group === "awaiting_human") {
      return { label: "待人工", className: "script-preparation-warning" };
    }
    if (group === "abandoned") {
      return { label: "已放弃", className: "script-preparation-muted" };
    }
    const labels = {
      queued: "等待中",
      generating: "生成中",
      executing: "执行中",
      repairing: "修复中",
      analyzing: "分析中",
      editing: "保存中",
      finalizing: "收尾中",
    };
    return { label: labels[status] || "处理中", className: "running" };
  }

  function normalizeHistory(history, index) {
    const raw = isPlainObject(history) ? history : {};
    const historyId = firstText(
      raw.history_id,
      raw.stage_id,
      raw.event_id,
      raw.attempt_id,
      raw.id,
      `history-${index + 1}`,
    );
    return {
      ...raw,
      history_id: historyId,
      stage_type: normalizeStatus(raw.stage_type || raw.action || raw.type || raw.phase || "unknown"),
      status: normalizeStatus(raw.status || raw.stage_status || "queued"),
      sequence_no: firstNumber(raw.sequence_no, raw.sequence, index + 1),
      started_at: firstNumber(raw.started_at, raw.created_at),
      finished_at: firstNumber(raw.finished_at, raw.updated_at),
      base_revision_id: raw.base_revision_id ?? raw.input_revision_id ?? null,
      output_revision_id: raw.output_revision_id ?? raw.revision_id ?? null,
      error: firstText(raw.error, raw.error_message, raw.detail?.error, raw.result?.error),
    };
  }

  function normalizeItem(value, index = 0) {
    const raw = isPlainObject(value) ? value : {};
    const itemId = firstText(raw.script_item_id, raw.item_id, raw.id, `script-item-${index + 1}`);
    const history = asArray(raw.history || raw.stages || raw.timeline)
      .map(normalizeHistory)
      .sort((left, right) => left.sequence_no - right.sequence_no || left.started_at - right.started_at);
    const analysis = isPlainObject(raw.latest_analysis)
      ? raw.latest_analysis
      : isPlainObject(raw.analysis)
        ? raw.analysis
        : isPlainObject(raw.ai_analysis)
          ? raw.ai_analysis
          : {};
    const revision = isPlainObject(raw.current_revision) ? raw.current_revision : {};
    const script = isPlainObject(raw.current_script) ? raw.current_script : isPlainObject(raw.script) ? raw.script : {};
    const scriptAsset = isPlainObject(script.asset) ? script.asset : {};
    const promptDefaults = isPlainObject(raw.prompt_defaults) ? raw.prompt_defaults : {};
    const promptOptions = isPlainObject(analysis.prompt_options) ? analysis.prompt_options : {};
    const recommendedPrompt = isPlainObject(promptOptions[analysis.recommended_action])
      ? promptOptions[analysis.recommended_action]
      : {};
    const currentRevisionId =
      raw.current_revision_id ?? revision.revision_id ?? script.revision_id ?? scriptAsset.current_revision_id ?? null;
    const revisionVersions = {};
    history.forEach((stage) => {
      const revisionId = stage.output_revision_id;
      if (revisionId !== null && revisionId !== undefined && revisionVersions[revisionId] === undefined) {
        revisionVersions[revisionId] = Object.keys(revisionVersions).length + 1;
      }
    });
    const currentVersion = firstNumber(
      raw.current_version,
      raw.version_no,
      revision.version_no,
      script.version_no,
      scriptAsset.version_no,
      revisionVersions[currentRevisionId],
    );
    const lastVerifiedStage = [...history].reverse().find((stage) => {
      const type = normalizeStatus(stage.stage_type);
      const status = normalizeStatus(stage.status);
      return ["execute", "execution", "verify", "verification"].includes(type) &&
        (READY_STATUSES.has(status) || ["completed", "passed"].includes(status));
    });
    const lastVerifiedRevisionId = lastVerifiedStage?.input_revision_id ?? lastVerifiedStage?.output_revision_id ?? null;
    const lastVerifiedVersion = firstNumber(
      raw.last_verified_version,
      raw.verified_version_no,
      revisionVersions[lastVerifiedRevisionId],
      lastVerifiedRevisionId === currentRevisionId ? currentVersion : 0,
    );
    return {
      ...raw,
      script_item_id: itemId,
      item_key: firstText(raw.item_key, `${raw.module_name || ""}/${raw.plan_filename || raw.filename || itemId}`),
      display_name: firstText(raw.display_name, raw.case_name, raw.title, raw.plan_filename, raw.filename, itemId),
      case_id: firstText(raw.case_id, raw.case_uid, raw.plan_case_id),
      module_name: firstText(raw.module_name, raw.module),
      plan_filename: firstText(raw.plan_filename, raw.plan_name),
      filename: firstText(raw.filename, raw.script_filename, script.filename),
      path: firstText(raw.path, raw.script_path, script.path),
      status: normalizeStatus(raw.status || raw.state || "queued"),
      progress_message: firstText(raw.progress_message, raw.current_progress, raw.status_message),
      current_revision_id: currentRevisionId,
      current_version: currentVersion,
      last_verified_version: lastVerifiedVersion,
      original_prompt: firstText(
        raw.original_prompt,
        raw.prompt_original,
        raw.prompt,
        script.original_prompt,
        recommendedPrompt.original_prompt,
        promptDefaults.regenerate,
      ),
      supplemental_prompt: firstText(
        raw.supplemental_prompt,
        raw.prompt_supplement,
        raw.ai_supplemental_prompt,
        recommendedPrompt.supplemental_prompt,
        analysis.prompt_patch,
        analysis.suggestion,
      ),
      script_content: firstText(raw.script_content, raw.content, script.content, scriptAsset.content),
      included_in_suite: Boolean(raw.included_in_suite),
      analysis,
      prompt_defaults: promptDefaults,
      history,
      revision_versions: {
        ...revisionVersions,
        ...(isPlainObject(raw.revision_versions) ? raw.revision_versions : {}),
      },
      updated_at: firstNumber(raw.updated_at, history.at(-1)?.finished_at, history.at(-1)?.started_at),
      capabilities: isPlainObject(raw.capabilities) ? raw.capabilities : {},
      error: firstText(raw.error, [...history].reverse().find((stage) => stage.error)?.error),
    };
  }

  function deriveCounts(items, supplied = {}) {
    const counts = {
      total: items.length,
      processing: 0,
      ready: 0,
      awaiting_human: 0,
      abandoned: 0,
    };
    items.forEach((item) => {
      counts[statusGroup(item.status)] += 1;
    });
    const suppliedCounts = isPlainObject(supplied) ? supplied : {};
    return {
      total: firstNumber(suppliedCounts.total, suppliedCounts.scripts, counts.total),
      processing: firstNumber(suppliedCounts.processing, suppliedCounts.running, counts.processing),
      ready: firstNumber(suppliedCounts.ready, suppliedCounts.passed, suppliedCounts.succeeded, counts.ready),
      awaiting_human: firstNumber(
        suppliedCounts.awaiting_human,
        suppliedCounts.awaiting_action,
        suppliedCounts.unresolved,
        counts.awaiting_human,
      ),
      abandoned: firstNumber(suppliedCounts.abandoned, suppliedCounts.ignored, suppliedCounts.excluded, counts.abandoned),
    };
  }

  function normalizeSnapshot(data) {
    const source = isPlainObject(data?.snapshot)
      ? data.snapshot
      : isPlainObject(data?.script_preparation)
        ? data.script_preparation
        : isPlainObject(data)
          ? data
          : {};
    const items = asArray(source.items || data?.items).map(normalizeItem);
    const counts = deriveCounts(items, source.counts || data?.counts);
    const suppliedStatus = firstText(source.status, source.state, data?.status);
    const inferredStatus = counts.processing
      ? "running"
      : counts.awaiting_human
        ? "awaiting_action"
        : counts.total && counts.ready + counts.abandoned === counts.total
          ? "succeeded"
          : "queued";
    return {
      status: normalizeStatus(suppliedStatus || inferredStatus),
      summary: firstText(source.summary, source.description, data?.summary, state.summary),
      counts,
      items,
      updated_at: firstNumber(source.updated_at, data?.updated_at),
    };
  }

  function applySnapshot(data) {
    const snapshot = normalizeSnapshot(data);
    state.status = snapshot.status;
    state.summary = snapshot.summary;
    state.items = snapshot.items;
    state.counts = snapshot.counts;
    const visibleIds = new Set(snapshot.items.map((item) => item.script_item_id));
    state.selectedIds = new Set(Array.from(state.selectedIds).filter((itemId) => visibleIds.has(itemId)));
    if (state.openItem) {
      const summaryItem = snapshot.items.find((item) => item.script_item_id === state.openItem.script_item_id);
      if (summaryItem) {
        const currentScript = isPlainObject(state.openItem.current_script) && isPlainObject(summaryItem.current_script)
          ? { ...state.openItem.current_script, ...summaryItem.current_script }
          : summaryItem.current_script || state.openItem.current_script;
        state.openItem = normalizeItem({ ...state.openItem, ...summaryItem, current_script: currentScript });
        ensureSelectedHistory(false, { forceLatest: state.followLatestHistory });
      }
    }
  }

  function mergeItem(value) {
    const incoming = normalizeItem(value);
    const index = state.items.findIndex((item) => item.script_item_id === incoming.script_item_id);
    if (index >= 0) {
      state.items[index] = normalizeItem({ ...state.items[index], ...incoming }, index);
    } else {
      state.items.push(incoming);
    }
    state.counts = deriveCounts(state.items);
    if (state.openItem?.script_item_id === incoming.script_item_id) {
      const followLatest = state.followLatestHistory;
      const currentScript = isPlainObject(state.openItem.current_script) && isPlainObject(incoming.current_script)
        ? { ...state.openItem.current_script, ...incoming.current_script }
        : incoming.current_script || state.openItem.current_script;
      state.openItem = normalizeItem({ ...state.openItem, ...incoming, current_script: currentScript });
      ensureSelectedHistory(false, { forceLatest: followLatest });
    }
  }

  function versionLabel(item, revisionId = null) {
    const version = firstNumber(
      revisionId && item?.revision_versions?.[revisionId],
      revisionId && revisionId === item?.current_revision_id ? item?.current_version : null,
      item?.current_version,
    );
    if (version) {
      return `v${version}`;
    }
    if (revisionId) {
      return `revision ${revisionId}`;
    }
    return "暂无候选";
  }

  function itemDisplayTitle(item) {
    return [item.case_id, item.display_name].filter(Boolean).join(" ") || item.filename || item.script_item_id;
  }

  function itemProgressText(item) {
    if (item.progress_message) {
      return item.progress_message;
    }
    const group = statusGroup(item.status);
    if (group === "ready") {
      return "执行通过";
    }
    if (group === "abandoned") {
      return "不进入测试集";
    }
    if (group === "awaiting_human") {
      const action = recommendedAction(item);
      return action === "regenerate" ? "建议重新生成" : action === "repair" ? "建议重新修复" : "等待人工处理";
    }
    return statusInfo(item.status).label;
  }

  function recommendedAction(item) {
    const action = normalizeStatus(
      item?.analysis?.recommended_action || item?.recommended_action || item?.analysis?.action || "",
    );
    return ["regenerate", "repair"].includes(action) ? action : "";
  }

  function analysisFailed(item) {
    const analysis = isPlainObject(item?.analysis) ? item.analysis : {};
    return Boolean(firstText(analysis.analysis_error, analysis.error) || !recommendedAction(item));
  }

  function actionLabel(action) {
    return (
      {
        edit: "人工编辑脚本",
        execute: "重新执行当前版本",
        abandon: "忽略脚本",
        regenerate: "重新生成",
        repair: "重新修复",
      }[action] || action
    );
  }

  function promptValues(item, action) {
    const analysis = isPlainObject(item?.analysis) ? item.analysis : {};
    const options = isPlainObject(analysis.prompt_options) ? analysis.prompt_options : {};
    const option = isPlainObject(options[action]) ? options[action] : {};
    const defaults = isPlainObject(item?.prompt_defaults) ? item.prompt_defaults : {};
    const fallbackKey = action === "regenerate" ? "regenerate" : "repair";
    const recommended = recommendedAction(item) === action;
    return {
      originalPrompt: firstText(option.original_prompt, defaults[fallbackKey], item?.original_prompt),
      supplementalPrompt: firstText(
        option.supplemental_prompt,
        recommended ? analysis.prompt_patch : "",
        recommended ? item?.supplemental_prompt : "",
      ),
      enabled: option.enabled !== false && (action !== "repair" || hasCurrentScript(item)),
    };
  }

  function actionAvailability(item, action) {
    const capability = item?.capabilities?.[action];
    if (capability === false || capability?.enabled === false) {
      return { enabled: false, reason: firstText(capability?.reason, "当前状态不支持此操作。") };
    }
    if (["edit", "execute", "repair"].includes(action) && !hasCurrentScript(item)) {
      return { enabled: false, reason: "尚未生成候选脚本，当前只能重新生成或忽略脚本。" };
    }
    if (action === "repair" && !promptValues(item, action).enabled) {
      return { enabled: false, reason: "当前没有可用的修复 Prompt。" };
    }
    return { enabled: true, reason: "" };
  }

  function stageLabel(stage, item) {
    const type = normalizeStatus(stage?.stage_type);
    const inputVersion = firstNumber(
      stage?.input_version,
      stage?.base_version_no,
      item?.revision_versions?.[stage?.input_revision_id],
    );
    const outputVersion = firstNumber(
      stage?.output_version,
      stage?.version_no,
      item?.revision_versions?.[stage?.output_revision_id],
    );
    const labels = {
      generate: "生成脚本",
      generation: "生成脚本",
      regenerate: "重新生成",
      execute: "执行脚本",
      execution: "执行脚本",
      verify: "执行验证",
      verification: "执行验证",
      repair: "自动修复",
      auto_repair: "自动修复",
      rerepair: "重新修复",
      analyze: "AI 分析",
      analysis: "AI 分析",
      awaiting_human: "待人工处理",
      awaiting_action: "待人工处理",
      human_review: "待人工处理",
      manual_review: "待人工处理",
      edit: "人工编辑脚本",
      manual_edit: "人工编辑脚本",
      abandon: "忽略脚本",
      ignored: "忽略脚本",
    };
    let label = labels[type] || stage?.stage_name || stage?.title || stage?.name || "处理记录";
    if (["execute", "execution", "verify", "verification"].includes(type) && inputVersion) {
      label = `执行 v${inputVersion}`;
    }
    if (["repair", "auto_repair", "rerepair"].includes(type) && inputVersion && outputVersion) {
      label = `${type === "rerepair" ? "重新修复" : "自动修复"} v${inputVersion} → v${outputVersion}`;
    }
    if (["generate", "generation", "regenerate"].includes(type) && outputVersion) {
      label = `${type === "regenerate" ? "重新生成" : "生成脚本"} v${outputVersion}`;
    }
    return label || itemDisplayTitle(item);
  }

  function historyStatusInfo(stage) {
    const status = normalizeStatus(stage?.status);
    if (READY_STATUSES.has(status) || ["completed", "repaired", "generated"].includes(status)) {
      return { label: "成功", className: "success", marker: "完成" };
    }
    if (["failed", "blocked", "error"].includes(status)) {
      return { label: "失败", className: "failed", marker: "失败" };
    }
    if (HUMAN_STATUSES.has(status) || HUMAN_HISTORY_TYPES.has(stage?.stage_type)) {
      return { label: "待人工", className: "pending", marker: "待处理" };
    }
    if (ABANDONED_STATUSES.has(status)) {
      return { label: "已放弃", className: "abandoned", marker: "放弃" };
    }
    return { label: "进行中", className: "running", marker: "进行中" };
  }

  function visibleItems() {
    const query = state.search.trim().toLocaleLowerCase("zh-CN");
    return state.items.filter((item) => {
      if (state.filter !== "all" && statusGroup(item.status) !== state.filter) {
        return false;
      }
      if (!query) {
        return true;
      }
      return [itemDisplayTitle(item), item.module_name, item.filename, item.path, item.plan_filename]
        .filter(Boolean)
        .some((value) => String(value).toLocaleLowerCase("zh-CN").includes(query));
    });
  }

  function renderStageHeader() {
    const counts = state.counts;
    elements.stageTitle.textContent = "脚本准备";
    elements.stageSummary.textContent = state.summary;
    elements.stageMeta.textContent = state.runId ? `${state.runId} · 脚本准备` : "尚未选择 Agent 任务";
    const info = statusInfo(state.status);
    elements.stageStatus.textContent = info.label;
    elements.stageStatus.className = `status-badge ${info.className}`;
    const complete = firstNumber(counts.ready) + firstNumber(counts.abandoned);
    const total = firstNumber(counts.total);
    elements.progressValue.textContent = `${complete} / ${total}`;
    elements.progressBar.style.width = `${total ? Math.min(100, Math.round((complete / total) * 100)) : 0}%`;
    elements.processingCount.textContent = String(firstNumber(counts.processing));
    elements.readyCount.textContent = String(firstNumber(counts.ready));
    elements.awaitingCount.textContent = String(firstNumber(counts.awaiting_human));
    elements.abandonedCount.textContent = String(firstNumber(counts.abandoned));
  }

  function renderFilters() {
    const counts = state.counts;
    const labels = {
      all: `全部 ${firstNumber(counts.total)}`,
      processing: `处理中 ${firstNumber(counts.processing)}`,
      ready: `已通过 ${firstNumber(counts.ready)}`,
      awaiting_human: `待人工 ${firstNumber(counts.awaiting_human)}`,
      abandoned: `已放弃 ${firstNumber(counts.abandoned)}`,
    };
    elements.filterBar.querySelectorAll("[data-script-preparation-filter]").forEach((button) => {
      const filter = button.dataset.scriptPreparationFilter || "all";
      button.textContent = labels[filter] || filter;
      button.classList.toggle("active", filter === state.filter);
      button.setAttribute("aria-pressed", String(filter === state.filter));
    });
    if (elements.searchInput.value !== state.search) {
      elements.searchInput.value = state.search;
    }
  }

  function renderBatchControls(items) {
    root.classList.toggle("is-bulk-mode", state.bulkMode);
    elements.bulkToggle.classList.toggle("hidden", state.bulkMode);
    elements.bulkExit.classList.toggle("hidden", !state.bulkMode);
    elements.batchBar.classList.toggle("hidden", !state.bulkMode);
    elements.batchMenu.classList.toggle("hidden", !state.batchMenuOpen || !state.bulkMode);
    elements.batchMenuToggle.setAttribute("aria-expanded", String(state.batchMenuOpen && state.bulkMode));
    elements.selectedCount.textContent = `已选择 ${state.selectedIds.size} 条脚本`;
    const selectedItems = Array.from(state.selectedIds)
      .map((itemId) => state.items.find((entry) => entry.script_item_id === itemId))
      .filter(Boolean);
    const awaitingSelected = selectedItems.filter((item) => statusGroup(item.status) === "awaiting_human").length;
    const missingCurrentScript = selectedItems.filter((item) => !hasCurrentScript(item)).length;
    elements.batchHint.textContent = state.selectedIds.size
      ? missingCurrentScript
        ? `其中 ${missingCurrentScript} 条尚无候选脚本，只能重新生成或忽略`
        : awaitingSelected === state.selectedIds.size
          ? "仅选中待人工脚本"
          : `其中待人工 ${awaitingSelected} 条`
      : "选择脚本后执行批量操作";
    elements.batchMenuToggle.disabled = !state.selectedIds.size || state.actionPending;
    elements.clearSelection.disabled = !state.selectedIds.size || state.actionPending;
    [elements.batchExecute, elements.batchRepair].forEach((button) => {
      button.disabled = !state.selectedIds.size || state.actionPending || Boolean(missingCurrentScript);
      button.title = missingCurrentScript ? "选中项包含尚未生成候选脚本的记录。" : "";
    });
    const visibleIds = items.map((item) => item.script_item_id);
    const selectedVisible = visibleIds.filter((itemId) => state.selectedIds.has(itemId)).length;
    elements.selectAll.checked = Boolean(visibleIds.length && selectedVisible === visibleIds.length);
    elements.selectAll.indeterminate = Boolean(selectedVisible && selectedVisible < visibleIds.length);
  }

  function renderTable() {
    const items = visibleItems();
    elements.tableBody.innerHTML = items
      .map((item) => {
        const info = statusInfo(item.status);
        const selected = state.selectedIds.has(item.script_item_id);
        return `
          <tr class="${selected ? "selected" : ""}" data-script-item-id="${escapeHtml(item.script_item_id)}">
            <td class="script-preparation-select-cell">
              <input
                type="checkbox"
                data-script-preparation-action="select-item"
                data-item-id="${escapeHtml(item.script_item_id)}"
                aria-label="选择 ${escapeHtml(itemDisplayTitle(item))}"
                ${selected ? "checked" : ""}
                ${state.actionPending ? "disabled" : ""}
              />
            </td>
            <td>
              <button class="script-preparation-name" type="button" data-script-preparation-action="open-detail" data-item-id="${escapeHtml(
                item.script_item_id,
              )}">${escapeHtml(itemDisplayTitle(item))}</button>
              <small title="${escapeHtml(item.path || item.filename || item.plan_filename)}">${escapeHtml(
                item.path || item.filename || item.plan_filename || "自动准备队列",
              )}</small>
            </td>
            <td title="${escapeHtml(item.module_name || "-")}">${escapeHtml(item.module_name || "-")}</td>
            <td><span class="status-badge ${escapeHtml(info.className)}">${escapeHtml(info.label)}</span></td>
            <td>${escapeHtml(versionLabel(item))}</td>
            <td title="${escapeHtml(itemProgressText(item))}">${escapeHtml(itemProgressText(item))}</td>
            <td>${escapeHtml(formatDateTime(item.updated_at))}</td>
            <td><button class="script-preparation-detail-link" type="button" data-script-preparation-action="open-detail" data-item-id="${escapeHtml(
              item.script_item_id,
            )}">查看详情</button></td>
          </tr>
        `;
      })
      .join("");
    elements.tableEmpty.classList.toggle("hidden", Boolean(items.length));
    elements.tableFooterTotal.textContent = `共 ${state.items.length} 条脚本 · 当前显示 ${items.length} 条`;
    elements.tableFooterHint.textContent = "只有执行成功的脚本会进入测试集";
    renderBatchControls(items);
  }

  function ensureSelectedHistory(preferPending = false, { forceLatest = false } = {}) {
    const item = state.openItem;
    if (!item?.history?.length) {
      state.selectedHistoryId = "";
      state.followLatestHistory = true;
      return null;
    }
    const selected = item.history.find((stage) => stage.history_id === state.selectedHistoryId);
    if (selected && !preferPending && !forceLatest) {
      return selected;
    }
    const pending = [...item.history]
      .reverse()
      .find((stage) => HUMAN_HISTORY_TYPES.has(stage.stage_type) || HUMAN_STATUSES.has(stage.status));
    const fallback = preferPending && statusGroup(item.status) === "awaiting_human" ? pending : null;
    const next = forceLatest ? item.history.at(-1) : fallback || item.history.at(-1);
    state.selectedHistoryId = next?.history_id || "";
    state.followLatestHistory = next?.history_id === item.history.at(-1)?.history_id;
    return next;
  }

  function selectedHistory() {
    return state.openItem?.history?.find((stage) => stage.history_id === state.selectedHistoryId) || null;
  }

  function renderHistory() {
    const item = state.openItem;
    if (!item?.history?.length) {
      elements.historyList.innerHTML = '<div class="script-preparation-history-empty">暂无处理历史</div>';
      return;
    }
    elements.historyList.innerHTML = item.history
      .map((stage) => {
        const info = historyStatusInfo(stage);
        const active = stage.history_id === state.selectedHistoryId;
        const duration = formatDuration(stage.started_at, stage.finished_at);
        const version = stage.output_version || stage.version_no;
        const meta = [
          formatClock(stage.finished_at || stage.started_at),
          info.label,
          version ? `产出 v${version}` : "",
          duration,
        ]
          .filter((entry) => entry && entry !== "-")
          .join(" · ");
        return `
          <button
            class="script-preparation-history-item ${escapeHtml(info.className)} ${active ? "active" : ""}"
            type="button"
            data-script-preparation-action="select-history"
            data-history-id="${escapeHtml(stage.history_id)}"
            aria-pressed="${active}"
          >
            <span class="script-preparation-history-rail" aria-hidden="true"><span></span></span>
            <span class="script-preparation-history-copy">
              <strong>${escapeHtml(stageLabel(stage, item))}</strong>
              <small>${escapeHtml(meta || info.label)}</small>
            </span>
          </button>
        `;
      })
      .join("");
  }

  function analysisList(value) {
    return asArray(value).map((entry) => (typeof entry === "string" ? entry : JSON.stringify(entry))).filter(Boolean);
  }

  function analysisMarkup(item) {
    const analysis = item.analysis || {};
    const action = recommendedAction(item);
    const failed = analysisFailed(item);
    const actionText = action === "regenerate"
      ? "重新生成完整脚本"
      : action === "repair"
        ? `重新修复当前候选 ${versionLabel(item)}`
        : "暂无有效推荐";
    const summary = firstText(
      analysis.summary,
      analysis.root_cause,
      analysis.failure_reason,
      item.error,
      failed ? "AI 未能形成有效建议，请结合失败详情人工选择下一步。" : "AI 已完成失败分析，请结合证据选择下一步。",
    );
    const facts = analysisList(analysis.facts || analysis.confirmed_facts);
    const evidence = analysisList(analysis.evidence_refs || analysis.evidence_references);
    const risks = analysisList(analysis.risks);
    return `
      <h3 class="script-preparation-section-title">AI 分析 <span class="status-badge ${failed ? "failed" : "success"}">${failed ? "分析失败" : "分析完成"}</span></h3>
      <div class="script-preparation-analysis-callout ${failed ? "failed" : ""}">
        <span class="script-preparation-analysis-label">AI</span>
        <div><strong>${failed ? "未形成自动推荐" : `推荐：${escapeHtml(actionText)}`}</strong><p>${escapeHtml(summary)}</p></div>
      </div>
      <section class="script-preparation-content-card">
        <h4>分析结论</h4>
        <p>${escapeHtml(firstText(
          analysis.suggestion,
          typeof analysis.recommendation === "string" ? analysis.recommendation : "",
          analysis.analysis_error,
          summary,
        ))}</p>
      </section>
      <section class="script-preparation-content-card">
        <h4>关键证据</h4>
        ${
          facts.length || evidence.length
            ? `<ul>${[...facts, ...evidence].slice(0, 8).map((entry) => `<li>${escapeHtml(entry)}</li>`).join("")}</ul>`
            : `<p>${escapeHtml(firstText(item.error, "详细证据会随执行记录一并保留。"))}</p>`
        }
      </section>
      <section class="script-preparation-content-card">
        <h4>版本依据</h4>
        <p>分析基于候选 ${escapeHtml(versionLabel(item))} 与最近一次失败执行。脚本或 Prompt 更新后，本建议应重新计算。</p>
        ${risks.length ? `<ul>${risks.map((entry) => `<li>${escapeHtml(entry)}</li>`).join("")}</ul>` : ""}
      </section>
    `;
  }

  function stageDetailObject(stage) {
    for (const value of [stage?.detail, stage?.result, stage?.output_summary, stage?.output, stage?.input_snapshot]) {
      if (isPlainObject(value) && Object.keys(value).length) {
        return value;
      }
    }
    return {};
  }

  function previewJson(value) {
    const text = JSON.stringify(value, null, 2);
    return text.length > 6000 ? `${text.slice(0, 6000)}\n... 已截断` : text;
  }

  function failureMarkup(item, stage) {
    const detail = stageDetailObject(stage);
    const error = firstText(stage.error, detail.error, detail.error_message, item.error, "本次处理失败。");
    const inputVersion = firstNumber(
      stage.input_version,
      stage.base_version_no,
      item.revision_versions?.[stage.input_revision_id],
      item.current_version,
    );
    return `
      <div class="script-preparation-history-notice">
        <span>你正在查看 ${escapeHtml(formatClock(stage.finished_at || stage.started_at))} 的历史记录，内容只读</span>
        <button class="secondary-button" type="button" data-script-preparation-action="return-latest">返回最新</button>
      </div>
      <h3 class="script-preparation-section-title">${escapeHtml(stageLabel(stage, item))} <span class="status-badge failed">失败</span></h3>
      <div class="script-preparation-error-callout"><strong>${escapeHtml(firstText(detail.title, detail.failure_step, "脚本处理失败"))}</strong><span>${escapeHtml(
        error,
      )}</span></div>
      <div class="script-preparation-input-grid">
        <div><span>脚本版本</span><strong>${escapeHtml(inputVersion ? `v${inputVersion}` : versionLabel(item))}</strong></div>
        <div><span>开始时间</span><strong>${escapeHtml(formatDateTime(stage.started_at))}</strong></div>
        <div><span>总耗时</span><strong>${escapeHtml(formatDuration(stage.started_at, stage.finished_at) || "-")}</strong></div>
      </div>
      <section class="script-preparation-content-card">
        <h4>失败详情</h4>
        <pre class="script-preparation-code-box">${escapeHtml(Object.keys(detail).length ? previewJson(detail) : error)}</pre>
      </section>
    `;
  }

  function genericStageMarkup(item, stage) {
    if (!stage) {
      return '<div class="script-preparation-detail-empty">选择左侧历史阶段查看详情</div>';
    }
    const info = historyStatusInfo(stage);
    const detail = stageDetailObject(stage);
    const inputVersion = firstNumber(
      stage.input_version,
      stage.base_version_no,
      item.revision_versions?.[stage.input_revision_id],
    );
    const outputVersion = firstNumber(
      stage.output_version,
      stage.version_no,
      item.revision_versions?.[stage.output_revision_id],
    );
    return `
      <h3 class="script-preparation-section-title">${escapeHtml(stageLabel(stage, item))} <span class="status-badge ${escapeHtml(
        info.className === "failed" ? "failed" : info.className === "success" ? "success" : "running",
      )}">${escapeHtml(info.label)}</span></h3>
      <div class="script-preparation-input-grid">
        <div><span>输入版本</span><strong>${escapeHtml(inputVersion ? `v${inputVersion}` : "-")}</strong></div>
        <div><span>输出版本</span><strong>${escapeHtml(outputVersion ? `v${outputVersion}` : "-")}</strong></div>
        <div><span>触发来源</span><strong>${escapeHtml(firstText(stage.trigger_source, stage.created_by, "自动流程"))}</strong></div>
      </div>
      <section class="script-preparation-content-card">
        <h4>阶段详情</h4>
        ${
          Object.keys(detail).length
            ? `<pre class="script-preparation-code-box">${escapeHtml(previewJson(detail))}</pre>`
            : `<p>${escapeHtml(firstText(stage.message, stage.summary, `${stageLabel(stage, item)}已记录。`))}</p>`
        }
      </section>
      ${
        firstText(stage.original_prompt, stage.supplemental_prompt)
          ? `<section class="script-preparation-content-card"><h4>Prompt 快照</h4><p>${escapeHtml(
              [stage.original_prompt, stage.supplemental_prompt].filter(Boolean).join("\n\n补充要求：\n"),
            )}</p></section>`
          : ""
      }
    `;
  }

  function renderDetailContent() {
    const item = state.openItem;
    const stage = selectedHistory();
    if (!item) {
      elements.detailContent.innerHTML = '<div class="script-preparation-detail-empty">脚本详情尚未加载</div>';
      return;
    }
    const stageStatus = normalizeStatus(stage?.status);
    const showAnalysis =
      (stage && HUMAN_HISTORY_TYPES.has(stage.stage_type)) ||
      ["analyze", "analysis"].includes(stage?.stage_type) ||
      (!stage && statusGroup(item.status) === "awaiting_human");
    if (showAnalysis && Object.keys(item.analysis || {}).length) {
      elements.detailContent.innerHTML = analysisMarkup(item);
    } else if (["failed", "blocked", "error"].includes(stageStatus) || stage?.error) {
      elements.detailContent.innerHTML = failureMarkup(item, stage);
    } else {
      elements.detailContent.innerHTML = genericStageMarkup(item, stage);
    }
  }

  function renderActionPanel() {
    const item = state.openItem;
    const stage = selectedHistory();
    if (!item) {
      elements.actionPanel.innerHTML = "";
      return;
    }
    const latest = item.history.at(-1) || null;
    const viewingLatest = !stage || !latest || stage.history_id === latest.history_id;
    if (!viewingLatest) {
      const info = historyStatusInfo(stage);
      const inputVersion = firstNumber(
        stage?.input_version,
        stage?.base_version_no,
        item.revision_versions?.[stage?.input_revision_id],
      );
      elements.actionPanel.innerHTML = `
        <h3>当时的处理结果</h3>
        <p>这是历史快照，人工操作不会作用于该阶段。</p>
        <div class="script-preparation-readonly-summary">
          <div><span>处理结果</span><strong>${escapeHtml(info.label)}</strong></div>
          <div><span>输入版本</span><strong>${escapeHtml(inputVersion ? `v${inputVersion}` : "-")}</strong></div>
          <div><span>触发来源</span><strong>${escapeHtml(firstText(stage?.trigger_source, "自动流程"))}</strong></div>
        </div>
        <div class="script-preparation-version-note">只有最新的待人工阶段可以执行人工处理。</div>
        <button class="primary-button script-preparation-block" type="button" data-script-preparation-action="return-latest">返回最新状态</button>
      `;
      return;
    }
    if (statusGroup(item.status) !== "awaiting_human") {
      const info = statusInfo(item.status);
      elements.actionPanel.innerHTML = `
        <h3>当前处理状态</h3>
        <p>此脚本当前无需人工选择。</p>
        <div class="script-preparation-readonly-summary">
          <div><span>状态</span><strong>${escapeHtml(info.label)}</strong></div>
          <div><span>当前候选</span><strong>${escapeHtml(versionLabel(item))}</strong></div>
          <div><span>进入测试集</span><strong>${item.included_in_suite ? "是" : "否"}</strong></div>
        </div>
      `;
      return;
    }
    const recommended = recommendedAction(item);
    const alternate = recommended === "repair" ? "regenerate" : recommended === "regenerate" ? "repair" : "";
    const prompts = recommended ? promptValues(item, recommended) : { supplementalPrompt: "" };
    const supplemental = firstText(prompts.supplementalPrompt, item.analysis?.suggestion, "暂无补充 Prompt。");
    const controlsPending = state.actionPending || state.detailLoading;
    const availability = Object.fromEntries(
      ["edit", "execute", "regenerate", "repair", "abandon"].map((action) => [action, actionAvailability(item, action)]),
    );
    const buttonAttributes = (action) => {
      const reason = controlsPending ? "脚本详情或操作仍在处理中。" : availability[action]?.reason || "";
      return `${controlsPending || !availability[action]?.enabled ? "disabled" : ""}${reason ? ` title="${escapeHtml(reason)}"` : ""}`;
    };
    const primaryDecisionButton = recommended
      ? `
        <button class="primary-button script-preparation-block script-preparation-large" type="button" data-script-preparation-action="item-${escapeHtml(
          recommended,
        )}" ${buttonAttributes(recommended)}>按建议${escapeHtml(actionLabel(recommended))}</button>
      `
      : "";
    const alternativeDecisionButtons = recommended
      ? `<button class="secondary-button script-preparation-block" type="button" data-script-preparation-action="item-${escapeHtml(
          alternate,
        )}" ${buttonAttributes(alternate)}>${alternate === "repair" ? "改为重新修复" : "改为重新生成"}</button>`
      : `
        <button class="secondary-button script-preparation-block" type="button" data-script-preparation-action="item-regenerate" ${buttonAttributes("regenerate")}>重新生成</button>
        <button class="secondary-button script-preparation-block" type="button" data-script-preparation-action="item-repair" ${buttonAttributes("repair")}>重新修复</button>
      `;
    elements.actionPanel.innerHTML = `
      <h3>处理此脚本</h3>
      <p>请选择下一步。操作会追加到左侧历史，不会覆盖已有版本。</p>
      ${
        recommended
          ? `<section class="script-preparation-prompt-card">
              <header><span>${recommended === "repair" ? "重新修复" : "重新生成"}补充 Prompt</span><span class="status-badge running">AI 预生成</span></header>
              <p>${escapeHtml(supplemental)}</p>
            </section>`
          : `<div class="script-preparation-version-note">AI 未形成有效推荐，请根据失败详情人工选择操作。</div>`
      }
      <div class="script-preparation-action-list">
        ${primaryDecisionButton}
        <button class="secondary-button script-preparation-block" type="button" data-script-preparation-action="item-edit" ${buttonAttributes("edit")}>人工编辑脚本</button>
        <button class="secondary-button script-preparation-block" type="button" data-script-preparation-action="item-execute" ${buttonAttributes("execute")}>重新执行当前版本</button>
        ${alternativeDecisionButtons}
        <button class="secondary-button danger-button script-preparation-block" type="button" data-script-preparation-action="item-abandon" ${buttonAttributes("abandon")}>忽略脚本</button>
      </div>
      ${!hasCurrentScript(item) ? '<div class="script-preparation-version-note">尚未生成候选脚本，人工编辑、重新执行和重新修复暂不可用；请重新生成或忽略脚本。</div>' : ""}
      <div class="script-preparation-version-note">忽略后脚本标记为已放弃，不进入测试集；Prompt、脚本版本和处理历史仍会保留。</div>
    `;
  }

  function renderDetail() {
    const item = state.openItem;
    const open = Boolean(item);
    elements.detailModal.classList.toggle("hidden", !open);
    elements.detailModal.setAttribute("aria-hidden", String(!open));
    if (!item) {
      return;
    }
    const info = statusInfo(item.status);
    elements.detailMeta.textContent = ["脚本准备", item.case_id, item.module_name].filter(Boolean).join(" · ");
    elements.detailTitle.textContent = item.display_name || item.filename || "脚本详情";
    elements.detailBadges.innerHTML = `
      <span class="status-badge ${escapeHtml(info.className)}">${escapeHtml(info.label)}</span>
      <span class="status-badge script-preparation-muted">当前候选 ${escapeHtml(versionLabel(item))}</span>
      <span class="status-badge script-preparation-muted">最后验证成功版本：${escapeHtml(
        item.last_verified_version ? `v${item.last_verified_version}` : "无",
      )}</span>
    `;
    renderHistory();
    renderDetailContent();
    renderActionPanel();
  }

  function renderEditor() {
    const item = state.openItem;
    const mode = state.editorMode;
    const open = Boolean(mode && item);
    elements.editorModal.classList.toggle("hidden", !open);
    elements.editorModal.setAttribute("aria-hidden", String(!open));
    elements.detailModal.inert = open;
    elements.detailModal.setAttribute("aria-hidden", String(open || !item));
    elements.detailModal.setAttribute("aria-modal", String(!open));
    if (!open) {
      return;
    }
    const editingScript = mode === "edit";
    elements.editSection.classList.toggle("hidden", !editingScript);
    elements.promptSection.classList.toggle("hidden", editingScript);
    elements.editorSave.classList.toggle("hidden", !editingScript);
    elements.editorSaveExecute.classList.toggle("hidden", !editingScript);
    elements.editorConfirm.classList.toggle("hidden", editingScript);
    elements.editorMeta.textContent = `脚本准备 · ${itemDisplayTitle(item)}`;
    elements.editorBaseline.textContent = `候选 ${versionLabel(item)}`;
    elements.editorTarget.textContent = item.current_version ? `候选 v${item.current_version + 1}` : "新候选版本";
    if (editingScript) {
      elements.editorTitle.textContent = "人工编辑脚本";
      elements.editorDescription.textContent = "保存会创建新候选版本；选择保存并执行后重新进入自动验证流程。";
      elements.editorSave.textContent = state.actionPending ? "正在保存…" : "仅保存";
      elements.editorSaveExecute.textContent = state.actionPending ? "正在保存…" : "保存并执行";
    } else {
      elements.editorTitle.textContent = mode === "repair" ? "重新修复" : "重新生成";
      elements.editorDescription.textContent =
        mode === "repair"
          ? "基于当前候选创建修复版本；原 Prompt 与 AI 补充 Prompt 均可编辑。"
          : "不继承当前代码，根据更新后的 Prompt 生成全新候选。";
      elements.editorConfirm.textContent = state.actionPending
        ? "正在提交…"
        : mode === "repair"
          ? "开始重新修复"
          : "开始重新生成";
    }
    [elements.editorSave, elements.editorSaveExecute, elements.editorConfirm].forEach((button) => {
      button.disabled = state.actionPending;
    });
  }

  function render() {
    renderStageHeader();
    renderFilters();
    renderTable();
    renderDetail();
    renderEditor();
    onStateChange(getState());
  }

  function getState() {
    return {
      active: state.active,
      runId: state.runId,
      status: state.status,
      counts: { ...state.counts },
      filter: state.filter,
      search: state.search,
      bulkMode: state.bulkMode,
      batchMenuOpen: state.batchMenuOpen,
      selectedIds: Array.from(state.selectedIds),
      openItemId: state.openItem?.script_item_id || "",
      selectedHistoryId: state.selectedHistoryId,
      editorMode: state.editorMode,
      editorDraft: state.editorDraft ? { ...state.editorDraft } : null,
      loading: state.loading,
      detailLoading: state.detailLoading,
      actionPending: state.actionPending,
      items: state.items.map((item) => ({ ...item, history: [...item.history] })),
    };
  }

  function snapshotUrl() {
    return `/api/agent/runs/${encodePathPart(state.runId)}/script-preparation`;
  }

  function itemUrl(itemId) {
    return `/api/agent/runs/${encodePathPart(state.runId)}/script-items/${encodePathPart(itemId)}`;
  }

  async function loadSnapshot() {
    if (!state.runId) {
      state.items = [];
      state.counts = deriveCounts([]);
      render();
      return null;
    }
    const requestId = ++state.snapshotRequestId;
    const requestRunId = state.runId;
    state.loading = true;
    render();
    try {
      const data = await requestJson(snapshotUrl());
      if (requestId !== state.snapshotRequestId || requestRunId !== state.runId || !state.active) {
        return null;
      }
      applySnapshot(data);
      return data;
    } finally {
      if (requestId === state.snapshotRequestId) {
        state.loading = false;
        render();
      }
    }
  }

  async function loadItem(itemId, { preferPending = false } = {}) {
    if (!state.runId || !itemId) {
      return null;
    }
    const requestId = ++state.detailRequestId;
    const requestRunId = state.runId;
    state.detailLoading = true;
    render();
    try {
      const data = await requestJson(itemUrl(itemId));
      if (requestId !== state.detailRequestId || requestRunId !== state.runId || !state.active) {
        return null;
      }
      const rawItem = data?.item || data?.script_item || data;
      const summary = state.items.find((item) => item.script_item_id === itemId) || {};
      state.openItem = normalizeItem({ ...summary, ...(isPlainObject(rawItem) ? rawItem : {}) });
      mergeItem(state.openItem);
      ensureSelectedHistory(preferPending);
      return state.openItem;
    } finally {
      if (requestId === state.detailRequestId) {
        state.detailLoading = false;
        render();
      }
    }
  }

  async function refresh({ includeDetail = Boolean(state.openItem) } = {}) {
    const openItemId = state.openItem?.script_item_id || "";
    await loadSnapshot();
    if (includeDetail && openItemId && state.active) {
      await loadItem(openItemId);
    }
  }

  async function openDetail(itemId, opener = null) {
    if (!itemId) {
      return;
    }
    state.detailOpener = opener || documentRef.activeElement || null;
    state.openItem = state.items.find((item) => item.script_item_id === itemId) || normalizeItem({ script_item_id: itemId });
    state.selectedHistoryId = "";
    state.followLatestHistory = true;
    documentRef.body?.classList?.add("agent-modal-open");
    render();
    try {
      await loadItem(itemId, { preferPending: true });
      if (state.openItem?.script_item_id === itemId) {
        windowRef.requestAnimationFrame?.(() => elements.detailClose.focus());
      }
    } catch (error) {
      notify(error.message || "读取脚本详情失败。", "error");
    }
  }

  function closeEditor() {
    state.editorMode = "";
    state.editorDraft = null;
    renderEditor();
    if (state.editorOpener?.isConnected) {
      state.editorOpener.focus();
    }
    state.editorOpener = null;
  }

  function closeDetail() {
    const hadOpenModal = Boolean(
      state.openItem || state.editorMode || !elements.detailModal.classList.contains("hidden"),
    );
    state.detailRequestId += 1;
    state.detailLoading = false;
    closeEditor();
    state.openItem = null;
    state.selectedHistoryId = "";
    elements.detailModal.classList.add("hidden");
    elements.detailModal.inert = false;
    elements.detailModal.setAttribute("aria-hidden", "true");
    elements.detailModal.setAttribute("aria-modal", "true");
    if (hadOpenModal) {
      documentRef.body?.classList?.remove("agent-modal-open");
    }
    if (state.detailOpener?.isConnected) {
      state.detailOpener.focus();
    }
    state.detailOpener = null;
  }

  function openEditor(mode, opener = null) {
    if (!state.openItem || !["edit", "regenerate", "repair"].includes(mode)) {
      return;
    }
    const availability = actionAvailability(state.openItem, mode);
    if (!availability.enabled) {
      notify(availability.reason, "error");
      return;
    }
    const prompts = promptValues(state.openItem, mode);
    state.editorDraft = {
      mode,
      itemId: state.openItem.script_item_id,
      scriptContent: state.openItem.script_content || "",
      originalPrompt: prompts.originalPrompt,
      supplementalPrompt: prompts.supplementalPrompt,
    };
    state.editorMode = mode;
    state.editorOpener = opener || documentRef.activeElement || null;
    elements.scriptEditor.value = state.editorDraft.scriptContent;
    elements.originalPrompt.value = state.editorDraft.originalPrompt;
    elements.supplementalPrompt.value = state.editorDraft.supplementalPrompt;
    renderEditor();
    windowRef.requestAnimationFrame?.(() => {
      if (mode === "edit") {
        elements.scriptEditor.focus();
      } else {
        elements.originalPrompt.focus();
      }
    });
  }

  function setFilter(filter) {
    const allowed = new Set(["all", "processing", "ready", "awaiting_human", "abandoned"]);
    state.filter = allowed.has(filter) ? filter : "all";
    render();
  }

  function setSearch(value) {
    state.search = String(value || "");
    render();
  }

  function toggleBatchMode(force) {
    state.bulkMode = typeof force === "boolean" ? force : !state.bulkMode;
    state.batchMenuOpen = false;
    if (!state.bulkMode) {
      state.selectedIds.clear();
    }
    render();
  }

  function setSelectedItems(itemIds) {
    const valid = new Set(state.items.map((item) => item.script_item_id));
    state.selectedIds = new Set(asArray(itemIds).map(String).filter((itemId) => valid.has(itemId)));
    render();
  }

  function selectItem(itemId, selected) {
    if (selected) {
      state.selectedIds.add(itemId);
    } else {
      state.selectedIds.delete(itemId);
    }
    render();
  }

  function applyActionResponse(data) {
    if (data?.snapshot || data?.script_preparation || Array.isArray(data?.items)) {
      applySnapshot(data);
    }
    if (isPlainObject(data?.item || data?.script_item)) {
      mergeItem(data.item || data.script_item);
    }
  }

  async function performItemAction(action, payload = {}) {
    const item = state.openItem;
    if (!item || state.actionPending) {
      return null;
    }
    if (!["edit", "execute", "abandon", "regenerate", "repair"].includes(action)) {
      throw new Error("不支持的脚本准备操作。");
    }
    if (action === "abandon" && !confirmAction(`确定忽略“${itemDisplayTitle(item)}”吗？该脚本不会进入测试集。`)) {
      return null;
    }
    const actionCapability = actionAvailability(item, action);
    if (!actionCapability.enabled) {
      notify(actionCapability.reason, "error");
      return null;
    }
    const previousHistoryId = state.selectedHistoryId;
    const previousFollowLatest = state.followLatestHistory;
    state.selectedHistoryId = "";
    state.followLatestHistory = true;
    const requestId = ++state.actionRequestId;
    const requestRunId = state.runId;
    state.actionPending = true;
    render();
    try {
      const body = {
        action,
        expected_revision_id: item.current_revision_id,
        ...payload,
      };
      const data = await requestJson(`${itemUrl(item.script_item_id)}/actions`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (requestId !== state.actionRequestId || requestRunId !== state.runId) {
        return data;
      }
      applyActionResponse(data);
      notify(data?.message || `${actionLabel(action)}已提交。`, "success");
      if (state.editorMode) {
        closeEditor();
      }
      await refresh({ includeDetail: true });
      return data;
    } catch (error) {
      if (requestId !== state.actionRequestId || requestRunId !== state.runId) {
        return null;
      }
      state.selectedHistoryId = previousHistoryId;
      state.followLatestHistory = previousFollowLatest;
      notify(error.message || `${actionLabel(action)}失败。`, "error");
      throw error;
    } finally {
      if (requestId === state.actionRequestId && requestRunId === state.runId) {
        state.actionPending = false;
        render();
      }
    }
  }

  async function submitEditor(executeAfterSave = false) {
    const mode = state.editorMode;
    if (mode === "edit") {
      const content = elements.scriptEditor.value;
      if (!content.trim()) {
        notify("脚本内容不能为空。", "error");
        return null;
      }
      return performItemAction("edit", {
        content,
        execute_after_save: Boolean(executeAfterSave),
      });
    }
    if (["regenerate", "repair"].includes(mode)) {
      const originalPrompt = elements.originalPrompt.value.trim();
      const supplementalPrompt = elements.supplementalPrompt.value.trim();
      if (!originalPrompt) {
        notify("原 Prompt 不能为空。", "error");
        return null;
      }
      return performItemAction(mode, {
        original_prompt: originalPrompt,
        supplemental_prompt: supplementalPrompt,
      });
    }
    return null;
  }

  async function performBatchAction(action) {
    const itemIds = Array.from(state.selectedIds);
    if (!itemIds.length || state.actionPending) {
      return null;
    }
    if (!["execute", "abandon", "regenerate", "repair"].includes(action)) {
      throw new Error("不支持的批量脚本准备操作。");
    }
    if (action === "abandon" && !confirmAction(`确定忽略选中的 ${itemIds.length} 条脚本吗？它们不会进入测试集。`)) {
      return null;
    }
    const requestId = ++state.actionRequestId;
    const requestRunId = state.runId;
    state.actionPending = true;
    state.batchMenuOpen = false;
    render();
    try {
      const items = itemIds.map((itemId) => {
        const item = state.items.find((entry) => entry.script_item_id === itemId);
        if (!["regenerate", "repair"].includes(action) || !item) {
          return itemId;
        }
        const prompts = promptValues(item, action);
        return {
          item_id: itemId,
          expected_revision_id: item.current_revision_id,
          original_prompt: prompts.originalPrompt,
          supplemental_prompt: prompts.supplementalPrompt,
        };
      });
      const data = await requestJson(
        `/api/agent/runs/${encodePathPart(state.runId)}/script-items/batch-actions`,
        {
          method: "POST",
          body: JSON.stringify({ items, action }),
        },
      );
      if (requestId !== state.actionRequestId || requestRunId !== state.runId) {
        return data;
      }
      applyActionResponse(data);
      const accepted = firstNumber(data?.accepted_count, data?.accepted?.length, itemIds.length);
      const rejected = firstNumber(data?.rejected_count, data?.rejected?.length);
      notify(
        data?.message || `批量${actionLabel(action)}已提交：接受 ${accepted} 条${rejected ? `，拒绝 ${rejected} 条` : ""}。`,
        rejected ? "error" : "success",
      );
      state.selectedIds.clear();
      await refresh({ includeDetail: false });
      return data;
    } catch (error) {
      if (requestId !== state.actionRequestId || requestRunId !== state.runId) {
        return null;
      }
      notify(error.message || `批量${actionLabel(action)}失败。`, "error");
      throw error;
    } finally {
      if (requestId === state.actionRequestId && requestRunId === state.runId) {
        state.actionPending = false;
        render();
      }
    }
  }

  function applyEvent(event) {
    const payload = isPlainObject(event?.payload) ? event.payload : isPlainObject(event) ? event : {};
    const preparation =
      payload.script_preparation ||
      payload.script_preparation_progress ||
      payload.snapshot ||
      payload.step_output;
    let changed = false;
    if (isPlainObject(preparation)) {
      if (Array.isArray(preparation.items) || preparation.counts || preparation.status) {
        applySnapshot(preparation);
        changed = true;
      }
      if (isPlainObject(preparation.item)) {
        mergeItem(preparation.item);
        changed = true;
      }
    }
    if (isPlainObject(payload.script_item || payload.item) && (payload.script_item_id || payload.script_item || payload.script_preparation_progress)) {
      mergeItem(payload.script_item || payload.item);
      changed = true;
    }
    if (changed) {
      render();
    }
    return changed;
  }

  async function activate(runId = state.runId || getRunId()) {
    state.active = true;
    root.classList.remove("hidden");
    if (runId && runId !== state.runId) {
      setRun(runId);
      state.active = true;
      root.classList.remove("hidden");
    }
    try {
      await loadSnapshot();
    } catch (error) {
      notify(error.message || "读取脚本准备状态失败。", "error");
    }
  }

  function deactivate() {
    state.active = false;
    state.snapshotRequestId += 1;
    state.detailRequestId += 1;
    state.actionRequestId += 1;
    state.loading = false;
    state.detailLoading = false;
    state.actionPending = false;
    state.batchMenuOpen = false;
    state.bulkMode = false;
    state.selectedIds.clear();
    closeDetail();
    root.classList.add("hidden");
  }

  function setRun(runId) {
    const nextRunId = String(runId || "");
    if (nextRunId === state.runId) {
      return false;
    }
    state.snapshotRequestId += 1;
    state.detailRequestId += 1;
    state.actionRequestId += 1;
    closeDetail();
    state.runId = nextRunId;
    state.status = "queued";
    state.items = [];
    state.counts = deriveCounts([]);
    state.selectedIds.clear();
    state.bulkMode = false;
    state.batchMenuOpen = false;
    state.filter = "all";
    state.search = "";
    state.openItem = null;
    state.selectedHistoryId = "";
    state.followLatestHistory = true;
    state.editorMode = "";
    state.editorDraft = null;
    state.loading = false;
    state.detailLoading = false;
    state.actionPending = false;
    render();
    return true;
  }

  function selectHistory(historyId) {
    if (!state.openItem?.history?.some((stage) => stage.history_id === historyId)) {
      return;
    }
    state.selectedHistoryId = historyId;
    state.followLatestHistory = historyId === state.openItem.history.at(-1)?.history_id;
    renderDetail();
  }

  function returnToLatest() {
    state.selectedHistoryId = state.openItem?.history?.at(-1)?.history_id || "";
    state.followLatestHistory = true;
    renderDetail();
  }

  function trapFocus(event, modal) {
    if (event.key !== "Tab") {
      return;
    }
    const focusable = Array.from(
      modal.querySelectorAll('button:not([disabled]):not([tabindex="-1"]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href]'),
    ).filter((element) => !element.classList.contains("hidden") && (!element.getClientRects || element.getClientRects().length));
    if (!focusable.length) {
      return;
    }
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && documentRef.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && documentRef.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  const handleClick = (event) => {
    const target = event.target.closest?.("[data-script-preparation-action]");
    if (!target) {
      if (state.batchMenuOpen && !event.target.closest?.(".script-preparation-batch-menu-wrap")) {
        state.batchMenuOpen = false;
        renderBatchControls(visibleItems());
      }
      return;
    }
    const action = target.dataset.scriptPreparationAction || "";
    if (action === "bulk-toggle") {
      toggleBatchMode();
    } else if (action === "bulk-exit") {
      toggleBatchMode(false);
    } else if (action === "clear-selection") {
      state.selectedIds.clear();
      render();
    } else if (action === "batch-menu-toggle") {
      state.batchMenuOpen = !state.batchMenuOpen;
      renderBatchControls(visibleItems());
    } else if (action.startsWith("batch-")) {
      void performBatchAction(action.slice("batch-".length)).catch(() => {});
    } else if (action === "open-detail") {
      void openDetail(target.dataset.itemId, target);
    } else if (["detail-close", "detail-backdrop"].includes(action)) {
      closeDetail();
    } else if (action === "select-history") {
      selectHistory(target.dataset.historyId);
    } else if (action === "return-latest") {
      returnToLatest();
    } else if (action === "item-edit") {
      openEditor("edit", target);
    } else if (action === "item-regenerate") {
      openEditor("regenerate", target);
    } else if (action === "item-repair") {
      openEditor("repair", target);
    } else if (action === "item-execute") {
      void performItemAction("execute").catch(() => {});
    } else if (action === "item-abandon") {
      void performItemAction("abandon").catch(() => {});
    } else if (["editor-close", "editor-cancel", "editor-backdrop"].includes(action)) {
      if (!state.actionPending) {
        closeEditor();
      }
    } else if (action === "editor-save") {
      void submitEditor(false).catch(() => {});
    } else if (action === "editor-save-execute") {
      void submitEditor(true).catch(() => {});
    } else if (action === "editor-confirm") {
      void submitEditor(false).catch(() => {});
    }
  };

  const handleChange = (event) => {
    const target = event.target;
    const action = target.dataset?.scriptPreparationAction;
    if (action === "select-item") {
      selectItem(target.dataset.itemId, target.checked);
    } else if (action === "select-all") {
      visibleItems().forEach((item) => {
        if (target.checked) {
          state.selectedIds.add(item.script_item_id);
        } else {
          state.selectedIds.delete(item.script_item_id);
        }
      });
      render();
    }
  };

  const handleInput = (event) => {
    if (event.target === elements.searchInput) {
      setSearch(elements.searchInput.value);
    } else if (state.editorDraft && event.target === elements.scriptEditor) {
      state.editorDraft.scriptContent = elements.scriptEditor.value;
    } else if (state.editorDraft && event.target === elements.originalPrompt) {
      state.editorDraft.originalPrompt = elements.originalPrompt.value;
    } else if (state.editorDraft && event.target === elements.supplementalPrompt) {
      state.editorDraft.supplementalPrompt = elements.supplementalPrompt.value;
    }
  };

  const handleFilterClick = (event) => {
    const button = event.target.closest?.("[data-script-preparation-filter]");
    if (button) {
      setFilter(button.dataset.scriptPreparationFilter);
    }
  };

  const handleKeydown = (event) => {
    if (!elements.editorModal.classList.contains("hidden")) {
      if (event.key === "Escape" && !state.actionPending) {
        event.preventDefault();
        closeEditor();
        return;
      }
      trapFocus(event, elements.editorModal);
      return;
    }
    if (!elements.detailModal.classList.contains("hidden")) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDetail();
        return;
      }
      trapFocus(event, elements.detailModal);
      return;
    }
    if (event.key === "Escape" && state.batchMenuOpen) {
      state.batchMenuOpen = false;
      renderBatchControls(visibleItems());
    }
  };

  root.addEventListener("click", handleClick);
  root.addEventListener("change", handleChange);
  root.addEventListener("input", handleInput);
  root.addEventListener("keydown", handleKeydown);
  elements.filterBar.addEventListener("click", handleFilterClick);

  function destroy() {
    deactivate();
    root.removeEventListener("click", handleClick);
    root.removeEventListener("change", handleChange);
    root.removeEventListener("input", handleInput);
    root.removeEventListener("keydown", handleKeydown);
    elements.filterBar.removeEventListener("click", handleFilterClick);
  }

  state.counts = deriveCounts([]);
  render();

  return {
    activate,
    deactivate,
    setRun,
    refresh,
    applyEvent,
    render,
    openDetail,
    closeDetail,
    openEditor,
    closeEditor,
    selectHistory,
    returnToLatest,
    setFilter,
    setSearch,
    toggleBatchMode,
    setSelectedItems,
    performItemAction,
    performBatchAction,
    submitEditor,
    getState,
    destroy,
  };
}

window.createAgentScriptPreparationFeature = createAgentScriptPreparationFeature;
