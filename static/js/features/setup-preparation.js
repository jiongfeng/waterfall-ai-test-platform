function createSetupPreparation(deps) {
  const {
    setupState,
    root,
    getProject,
    getProjectKey,
    getTestSuites,
    getScriptModules,
    isActive,
    requestJson,
    encodePathPart,
    isPlainObject,
    escapeHtml,
    stripSpecSuffix,
    renderHost,
  } = deps;

function setupValue(value, ...keys) {
  for (const key of keys) {
    if (value && Object.prototype.hasOwnProperty.call(value, key)) {
      return value[key];
    }
  }
  return undefined;
}

function setupCollection(data, key) {
  if (Array.isArray(data)) {
    return data;
  }
  if (Array.isArray(data?.[key])) {
    return data[key];
  }
  return Array.isArray(data?.items) ? data.items : [];
}

function setSetupNotice(message, type = "") {
  setupState.notice = message || "";
  setupState.noticeType = type;
}

function setupStatusInfo(status) {
  if (["succeeded", "passed"].includes(status)) {
    return { label: "成功", className: "success" };
  }
  if (status === "succeeded_with_warnings") {
    return { label: "完成（有警告）", className: "cancelled" };
  }
  if (["failed", "timed_out"].includes(status)) {
    return { label: "失败", className: "error" };
  }
  if (["running", "queued"].includes(status)) {
    return { label: status === "running" ? "运行中" : "等待中", className: "running" };
  }
  if (["skipped", "cancelled", "interrupted"].includes(status)) {
    return { label: status === "interrupted" ? "已中断" : status === "cancelled" ? "已取消" : "已跳过", className: "cancelled" };
  }
  return { label: status || "未运行", className: "" };
}

function formatSetupTimestamp(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function formatSetupDuration(durationMs) {
  if (!durationMs) {
    return "-";
  }
  return durationMs < 1000 ? `${durationMs}ms` : `${Math.round(durationMs / 100) / 10}s`;
}

function normalizeSetupScriptBinding(value) {
  const binding = isPlainObject(value) ? value : {};
  const scopeType = ["project", "test_suite", "script"].includes(binding.scope_type)
    ? binding.scope_type
    : "project";
  return {
    uid: String(setupValue(binding, "uid", "binding_uid", "id") || ""),
    script_uid: String(setupValue(binding, "script_uid", "setup_script_uid", "script_id") || ""),
    scope_type: scopeType,
    scope_key: String(binding.scope_key || ""),
    scope_label: String(binding.scope_label || binding.scope_key || ""),
    priority: Number(binding.priority) || 0,
    enabled: binding.enabled !== false,
    updated_at: binding.updated_at || null,
  };
}

function normalizeSetupScriptRun(value) {
  const run = isPlainObject(value) ? value : {};
  const startedAt = run.started_at || run.created_at || null;
  const finishedAt = run.finished_at || run.completed_at || null;
  let durationMs = Number(run.duration_ms) || 0;
  if (!durationMs && startedAt && finishedAt) {
    const elapsed = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
    durationMs = Number.isFinite(elapsed) && elapsed > 0 ? elapsed : 0;
  }
  return {
    uid: String(setupValue(run, "uid", "run_uid", "id") || ""),
    script_uid: String(setupValue(run, "script_uid", "setup_script_uid", "script_id") || ""),
    script_name: String(run.script_name || run.name || ""),
    parent_run_id: String(run.parent_run_id || ""),
    target_type: String(run.target_type || "project"),
    target_key: String(run.target_key || ""),
    status: String(run.status || "queued"),
    exit_code: run.exit_code === null || run.exit_code === undefined ? null : Number(run.exit_code),
    output_summary: String(run.output_summary || run.output || run.stdout || run.logs || ""),
    error: String(run.error || run.stderr || ""),
    started_at: startedAt,
    finished_at: finishedAt,
    duration_ms: durationMs,
  };
}

function normalizeSetupScript(value) {
  const script = isPlainObject(value) ? value : {};
  const environment = isPlainObject(script.environment_overrides)
    ? script.environment_overrides
    : isPlainObject(script.environment)
      ? script.environment
      : {};
  return {
    uid: String(setupValue(script, "uid", "script_uid", "id") || ""),
    name: String(script.name || "未命名准备脚本"),
    description: String(script.description || ""),
    script_content: String(script.script_content || script.content || ""),
    working_directory: String(script.working_directory || ""),
    environment_overrides: { ...environment },
    timeout_seconds: Math.max(1, Number(script.timeout_seconds) || 300),
    concurrency_key: String(script.concurrency_key || ""),
    enabled: script.enabled !== false,
    bindings: (Array.isArray(script.bindings) ? script.bindings : []).map(normalizeSetupScriptBinding),
    latest_run: script.latest_run ? normalizeSetupScriptRun(script.latest_run) : null,
    created_at: script.created_at || null,
    updated_at: script.updated_at || null,
  };
}

function getSetupScript(scriptUid) {
  return setupState.scripts.find((script) => script.uid === scriptUid) || null;
}

function getSetupScriptBindings(scriptUid) {
  const setup = setupState;
  const loaded = setup.bindings.filter((binding) => binding.script_uid === scriptUid);
  if (loaded.length) {
    return loaded;
  }
  return getSetupScript(scriptUid)?.bindings || [];
}

function getSetupScriptRuns(scriptUid) {
  return setupState.runs
    .filter((run) => run.script_uid === scriptUid)
    .sort((left, right) => new Date(right.started_at || 0).getTime() - new Date(left.started_at || 0).getTime());
}

function getLatestSetupScriptRun(scriptUid) {
  return getSetupScriptRuns(scriptUid)[0] || getSetupScript(scriptUid)?.latest_run || null;
}

function setupScopeLabel(scopeType) {
  return { project: "项目", test_suite: "测试集", script: "脚本" }[scopeType] || "项目";
}

function currentSetupProjectKey() {
  return getProjectKey() || getProject()?.project_key || "";
}

function currentSetupProjectName() {
  return getProject()?.name || currentSetupProjectKey() || "当前项目";
}

function defaultSetupScriptBinding(scriptUid = "") {
  return normalizeSetupScriptBinding({
    script_uid: scriptUid,
    scope_type: "project",
    scope_key: currentSetupProjectKey(),
    scope_label: currentSetupProjectName(),
    priority: 0,
    enabled: true,
  });
}

function syncSelectedSetupScriptRecords() {
  const setup = setupState;
  const script = getSetupScript(setup.selectedScriptUid) || setup.scripts[0] || null;
  setup.selectedScriptUid = script?.uid || "";
  const runs = script ? getSetupScriptRuns(script.uid) : [];
  if (!runs.some((run) => run.uid === setup.selectedRunUid)) {
    setup.selectedRunUid = runs[0]?.uid || "";
  }
}

async function loadSetupPreparationRuns(scriptUid = "") {
  const suffix = scriptUid ? `?script_uid=${encodeURIComponent(scriptUid)}` : "";
  const data = await requestJson(`/api/setup-runs${suffix}`);
  const loadedRuns = setupCollection(data, "runs").map(normalizeSetupScriptRun);
  const setup = setupState;
  if (scriptUid) {
    setup.runs = [
      ...setup.runs.filter((run) => run.script_uid !== scriptUid),
      ...loadedRuns.map((run) => ({ ...run, script_uid: run.script_uid || scriptUid })),
    ];
  } else {
    setup.runs = loadedRuns;
  }
  syncSelectedSetupScriptRecords();
}

async function loadSetupPreparation(options = {}) {
  const setup = setupState;
  if (setup.isLoading) {
    return;
  }
  setup.isLoading = true;
  setup.error = "";
  if (!options.silent) {
    renderHost();
  }
  try {
    const [scriptsData, bindingsData, runsData] = await Promise.all([
      requestJson("/api/setup-scripts"),
      requestJson("/api/setup-bindings"),
      requestJson("/api/setup-runs"),
    ]);
    setup.scripts = setupCollection(scriptsData, "scripts").map(normalizeSetupScript);
    setup.bindings = setupCollection(bindingsData, "bindings").map(normalizeSetupScriptBinding);
    setup.runs = setupCollection(runsData, "runs").map(normalizeSetupScriptRun);
    setup.loaded = true;
    syncSelectedSetupScriptRecords();
  } catch (error) {
    setup.error = error.message;
  } finally {
    setup.isLoading = false;
    if (isActive()) {
      renderHost();
    }
  }
}

function setupScriptEnvironmentRows(environment) {
  return Object.entries(environment || {}).map(([key, value], index) => ({
    uid: `environment-${Date.now()}-${index}`,
    key,
    value: String(value ?? ""),
  }));
}

function cloneSetupScript(script) {
  return normalizeSetupScript(JSON.parse(JSON.stringify(script || {})));
}

function openSetupScriptModal(scriptUid = "") {
  const setup = setupState;
  const existing = getSetupScript(scriptUid);
  const draft = existing
    ? cloneSetupScript(existing)
    : normalizeSetupScript({
        uid: "",
        name: "",
        script_content: "#!/usr/bin/env bash\nset -euo pipefail\n\n",
        timeout_seconds: 300,
        enabled: true,
      });
  if (!existing) {
    draft.uid = "";
    draft.name = "";
  }
  setup.selectedScriptUid = existing?.uid || "";
  setup.scriptDraftSourceUid = existing?.uid || "";
  setup.scriptDraft = draft;
  setup.draftBinding = existing ? { ...(getSetupScriptBindings(existing.uid)[0] || defaultSetupScriptBinding(existing.uid)) } : defaultSetupScriptBinding();
  setup.draftEnvironmentRows = setupScriptEnvironmentRows(draft.environment_overrides);
  setup.scriptModalOpen = true;
  setup.runDetailModalOpen = false;
  setSetupNotice("", "");
  renderHost();
  window.requestAnimationFrame(() => root.querySelector("#setupScriptName")?.focus());
}

function closeSetupScriptModal() {
  const setup = setupState;
  setup.scriptModalOpen = false;
  setup.scriptDraft = null;
  setup.scriptDraftSourceUid = "";
  setup.draftBinding = null;
  setup.draftEnvironmentRows = [];
  renderHost();
}

function readSetupEnvironmentRows(strict = true) {
  const rows = [...root.querySelectorAll("[data-setup-environment-row]")].map((row) => ({
    uid: row.dataset.setupEnvironmentRow,
    key: row.querySelector("[data-setup-environment-key]")?.value.trim() || "",
    value: row.querySelector("[data-setup-environment-value]")?.value || "",
  }));
  const environment = {};
  rows.forEach((row) => {
    if (!row.key && !row.value) {
      return;
    }
    if (!row.key) {
      if (strict) {
        throw new Error("环境变量名称不能为空。");
      }
      return;
    }
    if (Object.prototype.hasOwnProperty.call(environment, row.key) && strict) {
      throw new Error(`环境变量“${row.key}”重复。`);
    }
    environment[row.key] = row.value;
  });
  return { rows, environment };
}

function syncSetupScriptDraftFromForm(options = {}) {
  const setup = setupState;
  const draft = setup.scriptDraft;
  const form = root.querySelector("#setupScriptForm");
  if (!draft || !form) {
    return;
  }
  draft.name = form.elements.namedItem("name")?.value || "";
  draft.description = form.elements.namedItem("description")?.value || "";
  draft.script_content = form.elements.namedItem("script_content")?.value || "";
  draft.working_directory = form.elements.namedItem("working_directory")?.value.trim() || "";
  draft.timeout_seconds = Math.max(1, Number(form.elements.namedItem("timeout_seconds")?.value) || 300);
  draft.concurrency_key = form.elements.namedItem("concurrency_key")?.value.trim() || "";
  draft.enabled = Boolean(form.elements.namedItem("enabled")?.checked);
  const parsedEnvironment = readSetupEnvironmentRows(options.strict !== false);
  setup.draftEnvironmentRows = parsedEnvironment.rows;
  draft.environment_overrides = parsedEnvironment.environment;
  const scopeType = form.elements.namedItem("scope_type")?.value || "project";
  const targetSelect = form.elements.namedItem("scope_key");
  setup.draftBinding = {
    ...(setup.draftBinding || defaultSetupScriptBinding(draft.uid)),
    script_uid: draft.uid || setup.scriptDraftSourceUid,
    scope_type: scopeType,
    scope_key: targetSelect?.value || (scopeType === "project" ? currentSetupProjectKey() : ""),
    scope_label: targetSelect?.selectedOptions?.[0]?.dataset.label || targetSelect?.selectedOptions?.[0]?.textContent || "",
    enabled: true,
  };
}

function setupScriptPayload() {
  const setup = setupState;
  syncSetupScriptDraftFromForm();
  const draft = setup.scriptDraft;
  if (!draft?.name.trim()) {
    throw new Error("请输入脚本名称。");
  }
  if (!draft.script_content.trim()) {
    throw new Error("请输入 Bash 脚本内容。");
  }
  if (!setup.draftBinding?.scope_key) {
    throw new Error(`请选择目标${setupScopeLabel(setup.draftBinding?.scope_type)}。`);
  }
  return {
    name: draft.name.trim(),
    description: draft.description.trim(),
    script_content: draft.script_content,
    working_directory: draft.working_directory,
    environment_overrides: draft.environment_overrides,
    timeout_seconds: draft.timeout_seconds,
    concurrency_key: draft.concurrency_key,
    enabled: draft.enabled,
  };
}

async function persistSetupScriptBinding(scriptUid) {
  const setup = setupState;
  const draft = { ...(setup.draftBinding || defaultSetupScriptBinding(scriptUid)), script_uid: scriptUid };
  const existing = getSetupScriptBindings(scriptUid)[0] || null;
  const payload = {
    script_uid: scriptUid,
    scope_type: draft.scope_type,
    scope_key: draft.scope_key,
    scope_label: draft.scope_label,
    priority: Number(draft.priority) || 0,
    enabled: draft.enabled !== false,
  };
  const data = await requestJson(existing ? `/api/setup-bindings/${encodePathPart(existing.uid)}` : "/api/setup-bindings", {
    method: existing ? "PUT" : "POST",
    body: JSON.stringify(payload),
  });
  const saved = normalizeSetupScriptBinding(data.binding || data);
  saved.script_uid = saved.script_uid || scriptUid;
  setup.bindings = existing
    ? setup.bindings.map((binding) => (binding.uid === existing.uid ? saved : binding))
    : [...setup.bindings, saved];
  return saved;
}

async function saveSetupScript(event, options = {}) {
  event?.preventDefault?.();
  const setup = setupState;
  let payload;
  try {
    payload = setupScriptPayload();
  } catch (error) {
    setSetupNotice(error.message, "error");
    renderHost();
    return null;
  }
  const sourceUid = setup.scriptDraftSourceUid;
  setup.isSaving = true;
  renderHost();
  try {
    const data = await requestJson(sourceUid ? `/api/setup-scripts/${encodePathPart(sourceUid)}` : "/api/setup-scripts", {
      method: sourceUid ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    const saved = normalizeSetupScript(data.script || data);
    setup.scripts = sourceUid
      ? setup.scripts.map((script) => (script.uid === sourceUid ? saved : script))
      : [...setup.scripts, saved];
    setup.selectedScriptUid = saved.uid;
    setup.scriptDraftSourceUid = saved.uid;
    setup.scriptDraft.uid = saved.uid;
    const savedBinding = await persistSetupScriptBinding(saved.uid);
    saved.bindings = [savedBinding];
    setup.scripts = setup.scripts.map((script) => (script.uid === saved.uid ? saved : script));
    setup.scriptModalOpen = false;
    setup.scriptDraft = null;
    setup.scriptDraftSourceUid = "";
    setup.draftBinding = null;
    setup.draftEnvironmentRows = [];
    setSetupNotice(sourceUid ? "准备脚本已保存。" : "准备脚本已创建。", "success");
    setup.isSaving = false;
    if (options.trial === true) {
      openSetupRunDetail(saved.uid);
      await trialRunSetupScript(saved.uid);
      return saved;
    }
    renderHost();
    return saved;
  } catch (error) {
    setSetupNotice(error.message, "error");
    return null;
  } finally {
    setup.isSaving = false;
    renderHost();
  }
}

async function deleteSetupScript(scriptUid) {
  const setup = setupState;
  const script = getSetupScript(scriptUid);
  if (!script || !window.confirm(`删除准备脚本“${script.name}”？历史执行记录仍会保留。`)) {
    return;
  }
  setup.isSaving = true;
  renderHost();
  try {
    await requestJson(`/api/setup-scripts/${encodePathPart(script.uid)}`, { method: "DELETE" });
    setup.scripts = setup.scripts.filter((item) => item.uid !== script.uid);
    setup.bindings = setup.bindings.filter((binding) => binding.script_uid !== script.uid);
    setup.selectedScriptUid = setup.scripts[0]?.uid || "";
    setSetupNotice("准备脚本已删除。", "success");
  } catch (error) {
    setSetupNotice(error.message, "error");
  } finally {
    setup.isSaving = false;
    renderHost();
  }
}

function openSetupRunDetail(scriptUid) {
  const setup = setupState;
  const script = getSetupScript(scriptUid);
  if (!script) {
    return;
  }
  setup.selectedScriptUid = script.uid;
  setup.runDetailScriptUid = script.uid;
  setup.selectedRunUid = getSetupScriptRuns(script.uid)[0]?.uid || "";
  setup.scriptModalOpen = false;
  setup.runDetailModalOpen = true;
  setSetupNotice("", "");
  renderHost();
}

function closeSetupRunDetail() {
  const setup = setupState;
  setup.runDetailModalOpen = false;
  setup.runDetailScriptUid = "";
  renderHost();
}

async function trialRunSetupScript(scriptUid = setupState.runDetailScriptUid) {
  const setup = setupState;
  const script = getSetupScript(scriptUid);
  if (!script || setup.isRunning) {
    return;
  }
  const binding = getSetupScriptBindings(script.uid)[0] || defaultSetupScriptBinding(script.uid);
  setup.runDetailScriptUid = script.uid;
  setup.runDetailModalOpen = true;
  setup.isRunning = true;
  setSetupNotice("正在执行准备脚本，请稍候…", "");
  renderHost();
  try {
    const data = await requestJson(`/api/setup-scripts/${encodePathPart(script.uid)}/trial-run`, {
      method: "POST",
      body: JSON.stringify({ target_type: binding.scope_type, target_key: binding.scope_key }),
    });
    const returnedRun = data?.run || (data?.uid || data?.run_uid ? data : null);
    if (returnedRun) {
      const normalized = normalizeSetupScriptRun({ ...returnedRun, script_uid: returnedRun.script_uid || script.uid });
      setup.runs = [normalized, ...setup.runs.filter((run) => run.uid !== normalized.uid)];
      setup.selectedRunUid = normalized.uid;
    }
    await loadSetupPreparationRuns(script.uid);
    setSetupNotice("试运行已完成。", "success");
  } catch (error) {
    try {
      await loadSetupPreparationRuns(script.uid);
      setup.selectedRunUid = getSetupScriptRuns(script.uid)[0]?.uid || "";
    } catch (_refreshError) {
      // Keep the original trial-run error as the user-facing failure.
    }
    setSetupNotice(error.message, "error");
  } finally {
    setup.isRunning = false;
    renderHost();
  }
}

function renderSetupTargetOptions(scopeType, selectedKey) {
  let targets = [];
  if (scopeType === "test_suite") {
    targets = getTestSuites().map((suite) => ({ key: suite.suite_uid || suite.id, label: suite.name }));
  } else if (scopeType === "script") {
    targets = getScriptModules().flatMap((moduleItem) =>
      (moduleItem.scripts || []).map((script) => ({
        key: `${moduleItem.name}/${script.name}`,
        label: `${moduleItem.name} / ${script.display_name || stripSpecSuffix(script.name)}`,
      })),
    );
  } else {
    targets = [{ key: currentSetupProjectKey(), label: currentSetupProjectName() }];
  }
  if (selectedKey && !targets.some((target) => target.key === selectedKey)) {
    targets.unshift({ key: selectedKey, label: selectedKey });
  }
  if (!targets.length) {
    return `<option value="">暂无可选${escapeHtml(setupScopeLabel(scopeType))}</option>`;
  }
  return targets
    .map((target) => `<option value="${escapeHtml(target.key)}" data-label="${escapeHtml(target.label)}" ${target.key === selectedKey ? "selected" : ""}>${escapeHtml(target.label)}</option>`)
    .join("");
}

function renderSetupBindingSummary(scriptUid) {
  const bindings = getSetupScriptBindings(scriptUid);
  if (!bindings.length) {
    return '<span class="setup-script-binding-empty">未绑定</span>';
  }
  const visible = bindings.slice(0, 2);
  return `<div class="setup-script-binding-summary">${visible
    .map((binding) => `<span class="setup-scope-chip">${escapeHtml(setupScopeLabel(binding.scope_type))}</span><span title="${escapeHtml(binding.scope_key)}">${escapeHtml(binding.scope_label || binding.scope_key || "当前项目")}</span>`)
    .join("")}${bindings.length > visible.length ? `<small>+${bindings.length - visible.length}</small>` : ""}</div>`;
}

function renderSetupCodeLineNumbers(scriptContent) {
  const lineCount = Math.max(1, String(scriptContent || "").split("\n").length);
  return Array.from({ length: lineCount }, (_, index) => `<span>${index + 1}</span>`).join("");
}

function renderSetupScriptModal() {
  const setup = setupState;
  const draft = setup.scriptDraft;
  if (!setup.scriptModalOpen || !draft) {
    return "";
  }
  const editing = Boolean(setup.scriptDraftSourceUid);
  const binding = setup.draftBinding || defaultSetupScriptBinding(draft.uid);
  return `
    <div class="setup-modal-backdrop" data-setup-backdrop="script">
      <section class="setup-modal setup-script-modal" role="dialog" aria-modal="true" aria-labelledby="setupScriptModalTitle">
        <header class="setup-modal-header"><div><h3 id="setupScriptModalTitle">${editing ? "编辑准备脚本" : "新建准备脚本"}</h3></div><button class="setup-close-button" id="setupScriptModalClose" type="button" aria-label="关闭">关闭</button></header>
        ${setup.notice ? `<div class="setup-modal-notice ${escapeHtml(setup.noticeType)}">${escapeHtml(setup.notice)}</div>` : ""}
        <form class="setup-script-form" id="setupScriptForm">
          <div class="setup-script-form-layout">
            <section class="setup-script-detail-column">
              <h4>脚本详情</h4>
              <label class="setup-script-field" for="setupScriptDescription"><span>脚本描述（可选）</span><textarea id="setupScriptDescription" name="description" maxlength="200" placeholder="可选，简要描述脚本的用途、前置条件和注意事项">${escapeHtml(draft.description)}</textarea><small><span data-setup-description-count>${escapeHtml(draft.description.length)}</span>/200</small></label>
              <label class="setup-script-field setup-script-content-field" for="setupScriptContent"><span>脚本内容 <b>*</b></span><div class="setup-code-editor"><div class="setup-code-toolbar"><span>Bash</span><button type="button" id="setupCodeExpand">全屏编辑</button></div><div class="setup-code-body"><div class="setup-code-gutter" id="setupCodeGutter" aria-hidden="true">${renderSetupCodeLineNumbers(draft.script_content)}</div><textarea id="setupScriptContent" name="script_content" spellcheck="false" aria-label="Bash 脚本内容">${escapeHtml(draft.script_content)}</textarea></div></div></label>
              <div class="setup-script-contract"><strong>执行契约</strong><span>退出码 0 表示成功，非 0 将停止测试；标准输出与错误输出会脱敏后保留到执行详情。</span></div>
            </section>
            <aside class="setup-script-settings-column">
              <label class="setup-script-field" for="setupScriptName"><span>脚本名称 <b>*</b></span><input id="setupScriptName" name="name" type="text" maxlength="64" value="${escapeHtml(draft.name)}" placeholder="请输入脚本名称，便于识别和复用" /><small><span data-setup-name-count>${escapeHtml(draft.name.length)}</span>/64</small></label>
              <section class="setup-settings-section"><h4>运行设置</h4><label class="setup-script-field" for="setupWorkingDirectory"><span>工作目录</span><input id="setupWorkingDirectory" name="working_directory" type="text" value="${escapeHtml(draft.working_directory)}" placeholder="默认使用仓库根目录" /></label><label class="setup-script-field" for="setupTimeoutSeconds"><span>超时时间</span><div class="setup-input-suffix"><input id="setupTimeoutSeconds" name="timeout_seconds" type="number" min="1" value="${escapeHtml(draft.timeout_seconds)}" /><span>秒</span></div></label><label class="setup-script-field" for="setupConcurrencyKey"><span>并发键（可选）</span><input id="setupConcurrencyKey" name="concurrency_key" type="text" value="${escapeHtml(draft.concurrency_key)}" placeholder="用于串行化运行的资源锁标识" /></label><div class="setup-environment-heading"><span>环境变量（可选）</span><button class="secondary-button compact-button" id="setupAddEnvironment" type="button">添加变量</button></div><div class="setup-environment-list">${setup.draftEnvironmentRows.length ? setup.draftEnvironmentRows.map((row) => `<div class="setup-environment-row" data-setup-environment-row="${escapeHtml(row.uid)}"><input data-setup-environment-key type="text" value="${escapeHtml(row.key)}" placeholder="变量名" aria-label="环境变量名" /><input data-setup-environment-value type="text" value="${escapeHtml(row.value)}" placeholder="变量值" aria-label="环境变量值" /><button type="button" data-setup-remove-environment="${escapeHtml(row.uid)}">删除</button></div>`).join("") : '<div class="setup-environment-empty">当前未配置环境变量</div>'}</div><label class="setup-script-enabled"><span><strong>启用脚本</strong><small>停用后不会在 OpenCode 探索和测试执行前自动运行</small></span><input id="setupScriptEnabled" name="enabled" type="checkbox" ${draft.enabled ? "checked" : ""} /></label></section>
              <section class="setup-settings-section setup-binding-section"><h4>绑定范围</h4><span class="setup-settings-label">绑定对象</span><div class="setup-scope-selector">${["project", "test_suite", "script"].map((scopeType) => `<label class="${binding.scope_type === scopeType ? "active" : ""}"><input type="radio" name="scope_type" value="${scopeType}" ${binding.scope_type === scopeType ? "checked" : ""} /><span>${escapeHtml(setupScopeLabel(scopeType))}</span></label>`).join("")}</div><label class="setup-script-field" for="setupScopeKey"><span>目标${escapeHtml(setupScopeLabel(binding.scope_type))} <b>*</b></span><select id="setupScopeKey" name="scope_key">${renderSetupTargetOptions(binding.scope_type, binding.scope_key)}</select></label><div class="setup-binding-help">生效优先级：脚本 &gt; 测试集 &gt; 项目。试运行日志会在独立的执行详情弹窗中展示。</div></section>
            </aside>
          </div>
          <footer class="setup-modal-footer"><span class="setup-modal-footer-spacer"></span><button class="secondary-button" id="setupScriptCancel" type="button">取消</button><button class="secondary-button setup-trial-button" id="setupScriptSaveTrial" type="button" ${setup.isSaving ? "disabled" : ""}>试运行</button><button class="primary-button" type="submit" ${setup.isSaving ? "disabled" : ""}>${setup.isSaving ? "保存中" : "保存脚本"}</button></footer>
        </form>
      </section>
    </div>`;
}

function renderSetupScriptRunDetailModal() {
  const setup = setupState;
  if (!setup.runDetailModalOpen) {
    return "";
  }
  const script = getSetupScript(setup.runDetailScriptUid || setup.selectedScriptUid);
  const runs = script ? getSetupScriptRuns(script.uid) : [];
  const run = runs.find((item) => item.uid === setup.selectedRunUid) || runs[0] || null;
  const runStatus = setupStatusInfo(run?.status);
  const output = run?.output_summary || "暂无标准输出。";
  return `
    <div class="setup-modal-backdrop" data-setup-backdrop="runs">
      <section class="setup-modal setup-run-modal setup-script-run-modal" role="dialog" aria-modal="true" aria-labelledby="setupRunModalTitle">
        <header class="setup-modal-header"><div><h3 id="setupRunModalTitle">执行详情</h3><p>${escapeHtml(script?.name || "准备脚本")} · 试运行历史与 Shell 日志</p></div><div class="setup-run-header-actions"><button class="secondary-button" id="setupRefreshRuns" type="button" ${setup.isRunning ? "disabled" : ""}>刷新</button><button class="primary-button" id="setupTrialRun" type="button" ${!script || setup.isRunning ? "disabled" : ""}>${setup.isRunning ? "试运行中" : "试运行"}</button><button class="setup-close-button" id="setupRunModalClose" type="button">关闭</button></div></header>
        ${setup.notice ? `<div class="setup-modal-notice ${escapeHtml(setup.noticeType)}">${escapeHtml(setup.notice)}</div>` : ""}
        <div class="setup-run-layout"><aside class="setup-run-history"><header><strong>执行历史</strong><span>${runs.length} 次</span></header><div class="setup-history-list">${runs.length ? runs.map((item) => { const status = setupStatusInfo(item.status); return `<button type="button" class="${item.uid === run?.uid ? "active" : ""}" data-setup-run="${escapeHtml(item.uid)}"><span><strong>${escapeHtml(formatSetupTimestamp(item.started_at))}</strong><small>${escapeHtml(setupScopeLabel(item.target_type))} · ${escapeHtml(item.target_key || "当前项目")}</small></span><span class="status-badge ${status.className}">${escapeHtml(status.label)}</span></button>`; }).join("") : '<div class="setup-list-empty">暂无执行记录</div>'}</div></aside><main class="setup-run-detail">${run ? `<div class="setup-run-summary-grid"><div><span>执行状态</span><strong><span class="status-badge ${runStatus.className}">${escapeHtml(runStatus.label)}</span></strong></div><div><span>退出码</span><strong>${run.exit_code === null ? "-" : escapeHtml(run.exit_code)}</strong></div><div><span>执行目标</span><strong>${escapeHtml(setupScopeLabel(run.target_type))} · ${escapeHtml(run.target_key || "当前项目")}</strong></div><div><span>开始时间</span><strong>${escapeHtml(formatSetupTimestamp(run.started_at))}</strong></div><div><span>总耗时</span><strong>${escapeHtml(formatSetupDuration(run.duration_ms))}</strong></div></div><section class="setup-run-output setup-script-run-output"><header><strong>Shell 输出</strong><span>${escapeHtml(formatSetupTimestamp(run.finished_at || run.started_at))}</span></header><div><label>stdout / stderr</label><pre>${escapeHtml(output)}</pre></div>${run.error ? `<div class="error"><label>错误信息</label><pre>${escapeHtml(run.error)}</pre></div>` : ""}</section>` : '<div class="setup-panel-empty"><strong>暂无试运行</strong><span>点击右上角“试运行”验证当前准备脚本。</span></div>'}</main></div>
      </section>
    </div>`;
}

function renderSetupScriptsPanel() {
  const setup = setupState;
  const query = setup.scriptQuery.trim().toLowerCase();
  const scripts = setup.scripts.filter((script) => {
    const matchesQuery = [script.name, script.description, script.script_content].some((value) => String(value || "").toLowerCase().includes(query));
    const matchesStatus = setup.scriptStatusFilter === "all" || (setup.scriptStatusFilter === "enabled" ? script.enabled : !script.enabled);
    return matchesQuery && matchesStatus;
  });
  return `
    <section class="setup-management-panel setup-scripts-panel">
      <div class="setup-management-header"><div><h3>准备脚本</h3><p>准备脚本在 OpenCode 页面探索和测试执行前运行，用于环境初始化、数据准备、服务启动等操作。</p></div><button class="primary-button" id="setupNewScript" type="button">新建准备脚本</button></div>
      <div class="setup-list-toolbar setup-script-list-toolbar"><label class="setup-toolbar-search" for="setupScriptSearch"><span>搜索</span><input id="setupScriptSearch" type="search" value="${escapeHtml(setup.scriptQuery)}" placeholder="搜索脚本名称" autocomplete="off" /></label><label for="setupScriptStatusFilter"><span>状态</span><select id="setupScriptStatusFilter"><option value="all">全部</option><option value="enabled" ${setup.scriptStatusFilter === "enabled" ? "selected" : ""}>已启用</option><option value="disabled" ${setup.scriptStatusFilter === "disabled" ? "selected" : ""}>已停用</option></select></label><span class="setup-toolbar-fill"></span><button class="secondary-button" id="setupRefreshAll" type="button">刷新</button></div>
      <div class="setup-table-wrap"><table class="setup-data-table setup-scripts-table"><thead><tr><th>脚本名称</th><th>绑定范围</th><th>超时时间</th><th>最近执行</th><th>状态</th><th class="setup-actions-column">操作</th></tr></thead><tbody>${scripts.length ? scripts.map((script) => { const latestRun = getLatestSetupScriptRun(script.uid); const latestStatus = setupStatusInfo(latestRun?.status); return `<tr><td><strong>${escapeHtml(script.name)}</strong><small>${escapeHtml(script.description || "暂无说明")}</small></td><td>${renderSetupBindingSummary(script.uid)}</td><td>${escapeHtml(script.timeout_seconds)} 秒</td><td>${latestRun ? `<span class="setup-run-cell"><span class="status-badge ${latestStatus.className}">${escapeHtml(latestStatus.label)}</span><small>${escapeHtml(formatSetupTimestamp(latestRun.started_at))}</small></span>` : "-"}</td><td><span class="status-badge ${script.enabled ? "success" : "cancelled"}">${script.enabled ? "已启用" : "已停用"}</span></td><td><div class="setup-row-actions"><button type="button" data-setup-trial-script="${escapeHtml(script.uid)}">试运行</button><button type="button" data-setup-open-runs="${escapeHtml(script.uid)}">执行详情</button><button type="button" data-setup-edit-script="${escapeHtml(script.uid)}">编辑</button><button class="danger" type="button" data-setup-delete-script="${escapeHtml(script.uid)}">删除</button></div></td></tr>`; }).join("") : '<tr><td class="setup-table-empty" colspan="6">暂无匹配的准备脚本，请新建脚本或调整筛选条件。</td></tr>'}</tbody></table></div><div class="setup-table-footer">共 ${scripts.length} 条准备脚本</div>
    </section>`;
}

function renderSetupPreparationMarkup() {
  const setup = setupState;
  if (setup.isLoading && !setup.loaded) {
    return '<div class="project-settings-empty"><h3>正在读取准备脚本</h3></div>';
  }
  if (setup.error && !setup.loaded) {
    return `<div class="project-settings-empty setup-load-error"><h3>准备脚本加载失败</h3><p>${escapeHtml(setup.error)}</p><button class="secondary-button" id="setupRetryLoad" type="button">重新加载</button></div>`;
  }
  return `
    <div class="setup-preparation-shell setup-script-preparation-shell">
      ${setup.notice && !setup.scriptModalOpen && !setup.runDetailModalOpen ? `<div class="setup-notice ${escapeHtml(setup.noticeType)}" role="${setup.noticeType === "error" ? "alert" : "status"}">${escapeHtml(setup.notice)}</div>` : ""}
      ${renderSetupScriptsPanel()}
    </div>
    ${renderSetupScriptModal()}
    ${renderSetupScriptRunDetailModal()}`;
}

function updateSetupCodeGutter(value) {
  const gutter = root.querySelector("#setupCodeGutter");
  if (gutter) {
    gutter.innerHTML = renderSetupCodeLineNumbers(value);
  }
}

function bindSetupPreparationEvents() {
  const setup = setupState;
  root.querySelector("#setupRetryLoad")?.addEventListener("click", () => loadSetupPreparation());
  root.querySelector("#setupRefreshAll")?.addEventListener("click", () => loadSetupPreparation({ silent: true }));
  root.querySelector("#setupScriptSearch")?.addEventListener("input", (event) => {
    setup.scriptQuery = event.target.value;
    renderHost();
    window.requestAnimationFrame(() => {
      const input = root.querySelector("#setupScriptSearch");
      input?.focus();
      input?.setSelectionRange(input.value.length, input.value.length);
    });
  });
  root.querySelector("#setupScriptStatusFilter")?.addEventListener("change", (event) => {
    setup.scriptStatusFilter = event.target.value;
    renderHost();
  });
  root.querySelector("#setupNewScript")?.addEventListener("click", () => openSetupScriptModal());
  root.querySelectorAll("[data-setup-edit-script]").forEach((button) => button.addEventListener("click", () => openSetupScriptModal(button.dataset.setupEditScript)));
  root.querySelectorAll("[data-setup-delete-script]").forEach((button) => button.addEventListener("click", () => deleteSetupScript(button.dataset.setupDeleteScript)));
  root.querySelectorAll("[data-setup-open-runs]").forEach((button) => button.addEventListener("click", () => openSetupRunDetail(button.dataset.setupOpenRuns)));
  root.querySelectorAll("[data-setup-trial-script]").forEach((button) => button.addEventListener("click", async () => {
    openSetupRunDetail(button.dataset.setupTrialScript);
    await trialRunSetupScript(button.dataset.setupTrialScript);
  }));

  root.querySelector("#setupScriptModalClose")?.addEventListener("click", closeSetupScriptModal);
  root.querySelector("#setupScriptCancel")?.addEventListener("click", closeSetupScriptModal);
  root.querySelector("#setupScriptForm")?.addEventListener("submit", saveSetupScript);
  root.querySelector("#setupScriptSaveTrial")?.addEventListener("click", (event) => saveSetupScript(event, { trial: true }));
  root.querySelector("#setupScriptName")?.addEventListener("input", (event) => {
    const count = root.querySelector("[data-setup-name-count]");
    if (count) count.textContent = event.target.value.length;
  });
  root.querySelector("#setupScriptDescription")?.addEventListener("input", (event) => {
    const count = root.querySelector("[data-setup-description-count]");
    if (count) count.textContent = event.target.value.length;
  });
  root.querySelector("#setupScriptContent")?.addEventListener("input", (event) => updateSetupCodeGutter(event.target.value));
  root.querySelector("#setupScriptContent")?.addEventListener("scroll", (event) => {
    const gutter = root.querySelector("#setupCodeGutter");
    if (gutter) {
      gutter.scrollTop = event.target.scrollTop;
    }
  });
  root.querySelector("#setupCodeExpand")?.addEventListener("click", () => root.querySelector(".setup-script-modal")?.classList.toggle("setup-script-editor-expanded"));
  root.querySelector("#setupAddEnvironment")?.addEventListener("click", () => {
    syncSetupScriptDraftFromForm({ strict: false });
    setup.draftEnvironmentRows.push({ uid: `environment-${Date.now()}`, key: "", value: "" });
    renderHost();
    window.requestAnimationFrame(() => root.querySelector("[data-setup-environment-row]:last-child [data-setup-environment-key]")?.focus());
  });
  root.querySelectorAll("[data-setup-remove-environment]").forEach((button) => button.addEventListener("click", () => {
    syncSetupScriptDraftFromForm({ strict: false });
    setup.draftEnvironmentRows = setup.draftEnvironmentRows.filter((row) => row.uid !== button.dataset.setupRemoveEnvironment);
    renderHost();
  }));
  root.querySelectorAll('input[name="scope_type"]').forEach((input) => input.addEventListener("change", () => {
    syncSetupScriptDraftFromForm({ strict: false });
    setup.draftBinding.scope_type = input.value;
    setup.draftBinding.scope_key = input.value === "project" ? currentSetupProjectKey() : "";
    setup.draftBinding.scope_label = input.value === "project" ? currentSetupProjectName() : "";
    renderHost();
  }));

  root.querySelector("#setupRunModalClose")?.addEventListener("click", closeSetupRunDetail);
  root.querySelector("#setupTrialRun")?.addEventListener("click", () => trialRunSetupScript());
  root.querySelector("#setupRefreshRuns")?.addEventListener("click", async () => {
    try {
      await loadSetupPreparationRuns(setup.runDetailScriptUid);
      setSetupNotice("执行历史已刷新。", "success");
    } catch (error) {
      setSetupNotice(error.message, "error");
    }
    renderHost();
  });
  root.querySelectorAll("[data-setup-run]").forEach((button) => button.addEventListener("click", () => {
    setup.selectedRunUid = button.dataset.setupRun;
    renderHost();
  }));

  root.querySelectorAll("[data-setup-backdrop]").forEach((backdrop) => backdrop.addEventListener("click", (event) => {
    if (event.target !== backdrop) {
      return;
    }
    if (backdrop.dataset.setupBackdrop === "script") {
      closeSetupScriptModal();
    } else {
      closeSetupRunDetail();
    }
  }));
  root.onkeydown = (event) => {
    if (event.key !== "Escape") {
      return;
    }
    if (setup.runDetailModalOpen) {
      closeSetupRunDetail();
    } else if (setup.scriptModalOpen) {
      closeSetupScriptModal();
    }
  };
}

return {
  load: loadSetupPreparation,
  renderMarkup: renderSetupPreparationMarkup,
  bindEvents: bindSetupPreparationEvents,
};

}

window.createSetupPreparation = createSetupPreparation;
