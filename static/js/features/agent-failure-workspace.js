function createAgentFailureWorkspace(deps) {
  const {
    state,
    elements,
    asArray,
    isPlainObject,
    formatJsonPreview,
    escapeHtml,
    artifactMeta,
    statusText,
    encodePathPart,
    getStepOutput,
    isFailureCheckpointRun,
    requestJson,
    refreshSelectedRun,
    renderRunList,
    renderArtifacts,
    setNotice,
    shouldObserveSelectedRun,
    startEventStream,
    window,
    document,
  } = deps;

function hasOwn(object, key) {
  return Boolean(isPlainObject(object) && Object.prototype.hasOwnProperty.call(object, key));
}

function firstBoolean(object, keys, fallback = false) {
  for (const key of keys) {
    if (typeof object?.[key] === "boolean") {
      return object[key];
    }
  }
  return fallback;
}

function failureItemSourceType(item, fallbackType = "") {
  const value = String(item?.source_type || item?.failure_type || item?.source_step || fallbackType || "").toLowerCase();
  return value.includes("repair") ? "repair" : "generation";
}

function normalizeFailureItem(item, index, fallbackType = "") {
  const sourceType = failureItemSourceType(item, fallbackType);
  const itemId = String(
    item?.item_id ||
      item?.failure_item_id ||
      item?.attempt_id ||
      item?.failure_id ||
      `${sourceType}:${item?.module_name || ""}:${item?.plan_filename || item?.filename || index}`,
  );
  const resolution = String(item?.resolution || "").toLowerCase();
  let status = String(item?.status || item?.item_status || "failed").toLowerCase();
  if (["passed", "verified", "resolved", "succeeded"].includes(resolution)) {
    status = "resolved";
  } else if (["deleted", "excluded", "ignored", "kept_unresolved"].includes(resolution)) {
    status = resolution;
  }
  const scriptExists = firstBoolean(item, ["script_exists", "formal_script_exists"], sourceType === "repair" && Boolean(item?.filename));
  const candidateExists = firstBoolean(item, ["candidate_exists", "partial_script_exists"], Boolean(item?.candidate_path));
  const capabilities = isPlainObject(item?.capabilities) ? item.capabilities : {};
  const canEdit = firstBoolean(
    { ...capabilities, ...item },
    ["can_edit"],
    scriptExists || candidateExists,
  );
  const canExecute = firstBoolean(
    { ...capabilities, ...item },
    ["can_execute"],
    scriptExists,
  );
  const canDelete = firstBoolean(
    { ...capabilities, ...item },
    ["can_delete"],
    scriptExists || candidateExists,
  );
  const latestAttempt = isPlainObject(item?.latest_attempt) ? item.latest_attempt : {};
  return {
    ...item,
    item_id: itemId,
    source_type: sourceType,
    source_step: item?.source_step || (sourceType === "repair" ? "repair_scripts" : "generate_scripts"),
    status,
    resolution,
    module_name: item?.module_name || item?.module || "",
    plan_filename: item?.plan_filename || item?.plan_name || "",
    filename: item?.filename || item?.script_filename || "",
    error: item?.error || item?.error_message || item?.reason || latestAttempt.error || "",
    script_exists: scriptExists,
    candidate_exists: candidateExists,
    can_edit: canEdit,
    can_execute: canExecute,
    can_delete: canDelete,
    editable_artifact_kind:
      item?.editable_artifact_kind || (scriptExists ? "formal_script" : candidateExists ? "candidate" : "none"),
    latest_attempt: latestAttempt,
    _index: index,
  };
}

function failureWorkspaceData() {
  const output = getStepOutput("review_failed_scripts");
  const hasModernData =
    hasOwn(output, "failure_items") ||
    hasOwn(output, "generation_failures") ||
    hasOwn(output, "repair_failures") ||
    hasOwn(output, "script_generation_failures") ||
    hasOwn(output, "script_repair_failures") ||
    output.failure_checkpoint_version;
  if (!hasModernData && !isFailureCheckpointRun()) {
    return { modern: false, generation: [], repair: [], items: [] };
  }

  const consolidated = asArray(output.failure_items || output.items);
  const generationSource = [
    ...consolidated.filter((item) => failureItemSourceType(item) === "generation"),
    ...asArray(output.generation_failures || output.script_generation_failures),
  ];
  const repairSource = [
    ...consolidated.filter((item) => failureItemSourceType(item) === "repair"),
    ...asArray(output.repair_failures || output.script_repair_failures),
  ];
  if (!consolidated.length && !generationSource.length && !repairSource.length) {
    generationSource.push(...asArray(getStepOutput("generate_scripts").failures));
    repairSource.push(...asArray(getStepOutput("repair_scripts").failures));
  }

  const unique = new Map();
  generationSource.forEach((item, index) => {
    const normalized = normalizeFailureItem(item, index, "generation");
    unique.set(normalized.item_id, normalized);
  });
  repairSource.forEach((item, index) => {
    const normalized = normalizeFailureItem(item, index, "repair");
    const previous = unique.get(normalized.item_id);
    unique.set(normalized.item_id, previous ? { ...previous, ...normalized } : normalized);
  });
  const items = Array.from(unique.values());
  return {
    modern: true,
    generation: items.filter((item) => item.source_type === "generation"),
    repair: items.filter((item) => item.source_type === "repair"),
    items,
  };
}

function isFailureItemResolved(item) {
  return ["resolved", "passed", "verified", "succeeded", "recovered", "deleted", "excluded", "ignored", "kept_unresolved"].includes(
    String(item?.status || "").toLowerCase(),
  );
}

function isFailureItemBusy(item) {
  const latestStatus = String(item?.latest_attempt?.status || "").toLowerCase();
  return (
    state.failureActionPending && state.failureActionItemId === item?.item_id ||
    ["queued", "running", "retrying", "executing", "editing", "deleting", "analyzing", "repairing", "generating", "verifying"].includes(
      String(item?.status || "").toLowerCase(),
    ) ||
    ["queued", "running", "finalizing"].includes(latestStatus)
  );
}

function failureStatusText(item) {
  if (["resolved", "passed", "verified", "succeeded"].includes(item?.status)) {
    return "验证通过";
  }
  if (item?.status === "ignored") {
    return "保留未解决";
  }
  if (item?.status === "kept_unresolved") {
    return "保留未解决";
  }
  if (item?.status === "pending_verification") {
    return "待执行验证";
  }
  return statusText(item?.status || "failed");
}

function failureItemTitle(item) {
  return item?.filename || item?.plan_filename || item?.module_name || "未命名失败项";
}

function failureItemActionButton(item, action, label, { hidden = false, kind = "", disabled = false } = {}) {
  if (hidden) {
    return "";
  }
  const itemTitle = failureItemTitle(item);
  const localizedLabel = window.WaterfallI18n?.source?.(label) || label;
  return `<button
    class="failure-action-button ${escapeHtml(kind)}"
    type="button"
    data-failure-action="${escapeHtml(action)}"
    data-failure-item-id="${escapeHtml(item.item_id)}"
    data-i18n-dynamic-attributes
    aria-label="${escapeHtml(`${localizedLabel}: ${itemTitle}`)}"
    ${disabled ? "disabled" : ""}
  >${escapeHtml(label)}</button>`;
}

function failureTableMarkup(items, emptyMessage) {
  if (!items.length) {
    return `<div class="failure-list-empty">${escapeHtml(emptyMessage)}</div>`;
  }
  return `
    <table class="failure-table">
      <thead>
        <tr>
          <th>脚本 / 计划</th>
          <th>模块</th>
          <th>失败摘要</th>
          <th>状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        ${items
          .map((item) => {
            const busy = isFailureItemBusy(item);
            const resolved = isFailureItemResolved(item);
            const canMutate = isFailureCheckpointRun();
            const hasAnalysis = Boolean(normalizedFailureAnalysis(item));
            const deleteLabel = item.can_delete ? "删除" : "忽略";
            return `
              <tr>
                <td>
                  <span class="failure-item-name">
                    <strong data-i18n-dynamic title="${escapeHtml(failureItemTitle(item))}">${escapeHtml(failureItemTitle(item))}</strong>
                    <span>${escapeHtml(item.source_type === "repair" ? "脚本修复失败" : "脚本生成失败")}</span>
                  </span>
                </td>
                <td>
                  <span class="failure-item-context">
                    <span data-i18n-dynamic title="${escapeHtml(item.module_name || "-")}">${escapeHtml(item.module_name || "-")}</span>
                    <span data-i18n-dynamic title="${escapeHtml(item.plan_filename || "")}">${escapeHtml(item.plan_filename || "")}</span>
                  </span>
                </td>
                <td><span class="failure-error-summary" ${item.error ? "data-i18n-dynamic" : ""} title="${escapeHtml(item.error || "暂无失败摘要")}">${escapeHtml(item.error || "暂无失败摘要")}</span></td>
                <td><span class="status-badge ${escapeHtml(busy ? "running" : item.status)}">${escapeHtml(
                  busy ? "处理中" : failureStatusText(item),
                )}</span></td>
                <td>
                  <span class="failure-row-actions">
                    ${failureItemActionButton(item, "details", "失败详情")}
                    ${failureItemActionButton(item, "analysis", "分析和建议", { disabled: busy || (!canMutate && !hasAnalysis) })}
                    ${failureItemActionButton(item, "retry", "重试", { kind: "primary", disabled: busy || resolved || !canMutate })}
                    ${failureItemActionButton(item, "edit", "编辑", { hidden: !item.can_edit || resolved, disabled: busy || !canMutate })}
                    ${failureItemActionButton(item, "execute", "执行", { hidden: !item.can_execute || resolved, disabled: busy || !canMutate })}
                    ${failureItemActionButton(item, item.can_delete ? "delete" : "ignore", deleteLabel, {
                      kind: "danger",
                      hidden: resolved,
                      disabled: busy || !canMutate,
                    })}
                  </span>
                </td>
              </tr>
            `;
          })
          .join("")}
      </tbody>
    </table>
  `;
}

function renderFailureWorkspace(data = failureWorkspaceData()) {
  state.failureItems = data.items;
  const unresolved = data.items.filter((item) => !isFailureItemResolved(item));
  const resolved = data.items.length - unresolved.length;
  elements.failureSummary.innerHTML = `
    <span class="count-pill">总计 ${escapeHtml(data.items.length)}</span>
    <span class="count-pill unresolved">未解决 ${escapeHtml(unresolved.length)}</span>
    <span class="count-pill resolved">已处理 ${escapeHtml(resolved)}</span>
  `;
  elements.generationFailureCount.textContent = `${data.generation.length} 项`;
  elements.repairFailureCount.textContent = `${data.repair.length} 项`;
  elements.generationFailureList.innerHTML = failureTableMarkup(data.generation, "没有脚本生成失败项");
  elements.repairFailureList.innerHTML = failureTableMarkup(data.repair, "没有脚本修复失败项");
  elements.failureWorkspace.querySelectorAll("[data-failure-action][data-failure-item-id]").forEach((button) => {
    button.addEventListener("click", () => handleFailureItemAction(button.dataset.failureAction, button.dataset.failureItemId));
  });
}

function getFailureItem(itemId = state.failureActionItemId) {
  return state.failureItems.find((item) => item.item_id === itemId) || failureWorkspaceData().items.find((item) => item.item_id === itemId) || null;
}

function failureEvidenceEntries(item) {
  const snapshot = item?.evidence_snapshot;
  const entries = [];
  const addEntry = (title, value, type = "") => {
    if (value === null || value === undefined || value === "") {
      return;
    }
    entries.push({
      title: title || `证据 ${entries.length + 1}`,
      type,
      value,
    });
  };

  const appendList = (list) => {
    asArray(list).forEach((entry, index) => {
      if (isPlainObject(entry)) {
        addEntry(
          entry.title || entry.label || entry.name || `证据 ${index + 1}`,
          entry.content ?? entry.text ?? entry.value ?? entry.data ?? entry,
          entry.type || entry.kind || "",
        );
      } else {
        addEntry(`证据 ${index + 1}`, entry);
      }
    });
  };

  if (Array.isArray(snapshot)) {
    appendList(snapshot);
  } else if (isPlainObject(snapshot)) {
    const snapshotEntries = snapshot.evidence || snapshot.items || snapshot.entries;
    if (Array.isArray(snapshotEntries)) {
      appendList(snapshotEntries);
    }
    Object.entries(snapshot).forEach(([key, value]) => {
      if (["evidence", "items", "entries", "created_at", "version"].includes(key)) {
        return;
      }
      const labels = {
        error: "失败错误",
        error_message: "失败错误",
        stdout: "标准输出",
        stderr: "错误输出",
        logs: "运行日志",
        prompt: "生成或修复 Prompt",
        model_response: "模型响应",
        execution: "执行结果",
        trace: "Playwright Trace",
        screenshots: "截图",
        videos: "视频",
        diff: "脚本差异",
        partial_artifacts: "部分产物",
      };
      addEntry(labels[key] || key, value, key);
    });
  }
  appendList(item?.evidence);
  appendList(item?.partial_artifacts);
  if (!entries.some((entry) => ["error", "error_message"].includes(entry.type))) {
    addEntry("失败错误", item?.error, "error");
  }
  if (!entries.length) {
    addEntry("失败记录", {
      source_step: item?.source_step,
      module_name: item?.module_name,
      plan_filename: item?.plan_filename,
      filename: item?.filename,
      error: item?.error || "当前历史记录没有保存更多失败证据。",
    });
  }
  return entries;
}

function failureEvidenceText(value) {
  if (typeof value === "string") {
    return value;
  }
  return formatJsonPreview(value);
}

function renderFailureEvidence(item) {
  elements.failureContextCard.innerHTML = [
    ["失败来源", item.source_type === "repair" ? "脚本修复" : "脚本生成"],
    ["模块", item.module_name || "-"],
    ["脚本 / 计划", failureItemTitle(item)],
  ]
    .map(
      ([label, value]) =>
        `<span class="failure-context-field"><span>${escapeHtml(label)}</span><strong ${
          label === "失败来源" ? "" : "data-i18n-dynamic"
        } title="${escapeHtml(value)}">${escapeHtml(value)}</strong></span>`,
    )
    .join("");
  const evidence = failureEvidenceEntries(item);
  elements.failureEvidenceList.innerHTML = evidence
    .map(
      (entry, index) => `
        <details class="failure-evidence-item" ${index === 0 ? "open" : ""}>
          <summary>
            <span data-i18n-dynamic>${escapeHtml(entry.title)}</span>
            <small ${entry.type ? "data-i18n-dynamic" : ""}>${escapeHtml(entry.type || `证据 ${index + 1}`)}</small>
          </summary>
          <pre>${escapeHtml(failureEvidenceText(entry.value))}</pre>
        </details>
      `,
    )
    .join("");
}

function analysisValue(value) {
  if (Array.isArray(value)) {
    return value.map((entry) => (typeof entry === "string" ? entry : formatJsonPreview(entry)));
  }
  if (value === null || value === undefined || value === "") {
    return [];
  }
  return [typeof value === "string" ? value : formatJsonPreview(value)];
}

function analysisSection(title, value) {
  const lines = analysisValue(value);
  if (!lines.length) {
    return "";
  }
  const content =
    lines.length > 1
      ? `<ul>${lines.map((line) => `<li data-i18n-dynamic>${escapeHtml(line)}</li>`).join("")}</ul>`
      : `<p data-i18n-dynamic>${escapeHtml(lines[0])}</p>`;
  return `<section class="failure-analysis-section"><h3>${escapeHtml(title)}</h3>${content}</section>`;
}

function normalizedFailureAnalysis(item) {
  const raw = item?.analysis;
  if (!isPlainObject(raw)) {
    return raw ? { summary: String(raw) } : null;
  }
  return isPlainObject(raw.analysis) ? { ...raw, ...raw.analysis } : raw;
}

function failureAnalysisSuggestion(item) {
  const analysis = normalizedFailureAnalysis(item);
  if (!analysis) {
    return "";
  }
  return (
    analysis.prompt_patch ||
    analysis.suggestion ||
    analysis.suggested_prompt_patch ||
    analysis.retry_instructions ||
    analysis.regeneration_suggestion ||
    analysis.repair_suggestion ||
    analysis.recommendation ||
    analysis.recommended_action ||
    ""
  );
}

function renderFailureAnalysis(item, errorMessage = "") {
  const analysis = normalizedFailureAnalysis(item);
  elements.failureAnalysisLoading.classList.add("hidden");
  if (errorMessage) {
    elements.failureAnalysisContent.innerHTML = `
      <div class="failure-analysis-alert" data-i18n-dynamic>${escapeHtml(errorMessage)}</div>
      <div class="failure-analysis-empty">可以关闭弹窗后再次点击“分析和建议”重试。</div>
    `;
    return;
  }
  if (!analysis) {
    elements.failureAnalysisContent.innerHTML = '<div class="failure-analysis-empty">当前还没有分析结果。</div>';
    return;
  }
  const rootCause = analysis.root_cause || analysis.failure_reason || analysis.cause;
  const category = analysis.root_cause_category || analysis.category;
  const confidence = analysis.confidence;
  const summaryMeta = [category ? `分类：${category}` : "", confidence !== undefined ? `置信度：${confidence}` : ""]
    .filter(Boolean)
    .join(" · ");
  elements.failureAnalysisContent.innerHTML = `
    ${item.analysis_stale ? '<div class="failure-analysis-alert">失败证据已经更新，本分析已过期，打开弹窗时会尝试重新分析。</div>' : ""}
    ${analysisSection("分析摘要", analysis.summary || summaryMeta)}
    ${analysisSection("失败原因", rootCause)}
    ${analysisSection("已确认事实", analysis.facts || analysis.confirmed_facts)}
    ${analysisSection("可能原因", analysis.hypotheses || analysis.possible_causes)}
    ${analysisSection("引用证据", analysis.evidence_refs || analysis.evidence_references)}
    ${analysisSection("缺失信息", analysis.missing_evidence || analysis.missing_information)}
    ${analysisSection(
      item.source_type === "repair" ? "重新修复建议" : "重新生成建议",
      analysis.suggestion || analysis.recommendations || analysis.recommendation || analysis.recommended_action,
    )}
    ${analysisSection("建议补充的 Prompt", failureAnalysisSuggestion(item))}
    ${analysisSection("风险提示", analysis.risks)}
  `;
}

function resetFailureActionViews() {
  elements.failureEvidenceView.classList.add("hidden");
  elements.failureAnalysisView.classList.add("hidden");
  elements.failureRetryView.classList.add("hidden");
  elements.failureEditView.classList.add("hidden");
  elements.failureAnalysisLoading.classList.add("hidden");
  elements.failureAnalysisContent.innerHTML = "";
  elements.failureActionConfirm.classList.add("hidden");
  elements.failureActionConfirm.disabled = false;
  elements.failureActionConfirm.textContent = "确认";
}

function showFailureActionModal(item, mode) {
  state.failureActionItemId = item.item_id;
  state.failureActionMode = mode;
  state.failureActionOpener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  resetFailureActionViews();
  elements.failureActionModalMeta.textContent = artifactMeta([
    window.WaterfallI18n?.source?.(item.source_type === "repair" ? "脚本修复失败" : "脚本生成失败") ||
      (item.source_type === "repair" ? "脚本修复失败" : "脚本生成失败"),
    item.module_name,
    failureStatusText(item),
  ]);
  window.WaterfallI18n?.markDynamic?.(elements.failureActionModalMeta);
  elements.failureActionModal.classList.remove("hidden");
  document.body.classList.add("agent-modal-open");
  window.requestAnimationFrame(() => elements.failureActionModalClose.focus());
}

async function openFailureDetails(item) {
  showFailureActionModal(item, "details");
  elements.failureActionModalTitle.textContent = `${window.WaterfallI18n?.source?.("失败详情") || "失败详情"} · ${failureItemTitle(item)}`;
  window.WaterfallI18n?.markDynamic?.(elements.failureActionModalTitle);
  elements.failureEvidenceView.classList.remove("hidden");
  renderFailureEvidence(item);
}

async function loadFailureAnalysis(item) {
  elements.failureAnalysisLoading.classList.remove("hidden");
  elements.failureAnalysisContent.innerHTML = "";
  state.failureActionPending = true;
  renderRunList();
  try {
    await requestJson(
      `/api/agent/runs/${encodePathPart(state.selectedRunId)}/failure-items/${encodePathPart(item.item_id)}/analyze`,
      { method: "POST", body: JSON.stringify({}) },
    );
    await refreshSelectedRun();
    renderFailureAnalysis(getFailureItem(item.item_id) || item);
  } catch (error) {
    try {
      await refreshSelectedRun();
    } catch (_refreshError) {
      // 分析错误已经是本次交互的主要反馈，刷新失败由下一次轮询恢复。
    }
    renderFailureAnalysis(getFailureItem(item.item_id) || item, error.message);
  } finally {
    state.failureActionPending = false;
    renderRunList();
  }
}

async function openFailureAnalysis(item) {
  showFailureActionModal(item, "analysis");
  elements.failureActionModalTitle.textContent = `${window.WaterfallI18n?.source?.("分析和建议") || "分析和建议"} · ${failureItemTitle(item)}`;
  window.WaterfallI18n?.markDynamic?.(elements.failureActionModalTitle);
  elements.failureAnalysisView.classList.remove("hidden");
  const analysis = normalizedFailureAnalysis(item);
  if (analysis && !item.analysis_stale) {
    renderFailureAnalysis(item);
    return;
  }
  await loadFailureAnalysis(item);
}

function openFailureRetry(item) {
  showFailureActionModal(item, "retry");
  const isRepair = item.source_type === "repair";
  const retryLabel = isRepair ? "重新修复" : "重新生成";
  elements.failureActionModalTitle.textContent = `${window.WaterfallI18n?.source?.(retryLabel) || retryLabel} · ${failureItemTitle(item)}`;
  window.WaterfallI18n?.markDynamic?.(elements.failureActionModalTitle);
  elements.failureRetryView.classList.remove("hidden");
  elements.failureRetryKind.textContent = isRepair ? "重新修复" : "重新生成";
  elements.failureRetryName.textContent = failureItemTitle(item);
  window.WaterfallI18n?.markDynamic?.(elements.failureRetryName);
  elements.failureRetryReason.value = item.error || "暂无失败摘要";
  elements.failureRetryInstructions.value = failureAnalysisSuggestion(item);
  elements.failureRetryTarget.value = item.path || item.script_path || item.candidate_path || artifactMeta([item.module_name, item.filename || item.plan_filename]);
  elements.failureActionConfirm.textContent = isRepair ? "开始重新修复" : "开始重新生成";
  elements.failureActionConfirm.classList.remove("hidden");
  window.requestAnimationFrame(() => elements.failureRetryInstructions.focus());
}

async function loadFailureScriptContent(item) {
  if (typeof item.script_content === "string") {
    return { content: item.script_content, content_sha256: item.content_sha256 || "" };
  }
  if (typeof item.candidate_content === "string") {
    return { content: item.candidate_content, content_sha256: item.content_sha256 || "" };
  }
  if (state.selectedRunId && item.item_id) {
    const data = await requestJson(
      `/api/agent/runs/${encodePathPart(state.selectedRunId)}/failure-items/${encodePathPart(item.item_id)}/script`,
    );
    return data.script || { content: "", content_sha256: "" };
  }
  return { content: "", content_sha256: "" };
}

async function openFailureEdit(item) {
  showFailureActionModal(item, "edit");
  const candidate = item.editable_artifact_kind === "candidate";
  const editLabel = `编辑${candidate ? "候选稿" : "脚本"}`;
  elements.failureActionModalTitle.textContent = `${window.WaterfallI18n?.source?.(editLabel) || editLabel} · ${failureItemTitle(item)}`;
  window.WaterfallI18n?.markDynamic?.(elements.failureActionModalTitle);
  elements.failureEditView.classList.remove("hidden");
  elements.failureEditKind.textContent = candidate ? "候选脚本" : "正式脚本";
  elements.failureEditName.textContent = failureItemTitle(item);
  window.WaterfallI18n?.markDynamic?.(elements.failureEditName);
  elements.failureScriptEditor.value = "正在读取脚本内容…";
  elements.failureScriptEditor.disabled = true;
  elements.failureActionConfirm.textContent = "保存脚本";
  elements.failureActionConfirm.classList.remove("hidden");
  elements.failureActionConfirm.disabled = true;
  try {
    const script = await loadFailureScriptContent(item);
    elements.failureScriptEditor.value = script.content || "";
    state.failureEditContentSha256 = script.content_sha256 || "";
    elements.failureScriptEditor.disabled = false;
    elements.failureActionConfirm.disabled = false;
    window.requestAnimationFrame(() => elements.failureScriptEditor.focus());
  } catch (error) {
    elements.failureScriptEditor.value = `读取脚本失败：${error.message}`;
    setNotice(error.message, "error");
  }
}

function closeFailureActionModal() {
  elements.failureActionModal.classList.add("hidden");
  state.failureActionItemId = "";
  state.failureActionMode = "";
  state.failureEditContentSha256 = "";
  document.body.classList.remove("agent-modal-open");
  if (state.failureActionOpener?.isConnected) {
    state.failureActionOpener.focus();
  }
  state.failureActionOpener = null;
}

async function runFailureItemRequest(item, action, options = {}) {
  if (!item || state.failureActionPending) {
    return;
  }
  state.failureActionPending = true;
  state.failureActionItemId = item.item_id;
  const keepModalSelection = !elements.failureActionModal.classList.contains("hidden");
  const pendingMessages = {
    retry: item.source_type === "repair" ? "正在重新修复脚本…" : "正在重新生成脚本…",
    edit: "正在保存脚本…",
    execute: "正在执行脚本验证…",
    delete: "正在删除脚本…",
    ignore: "正在保留失败记录…",
  };
  setNotice(pendingMessages[action] || "正在处理失败项…");
  renderRunList();
  renderArtifacts();
  try {
    const baseUrl = `/api/agent/runs/${encodePathPart(state.selectedRunId)}/failure-items/${encodePathPart(item.item_id)}`;
    let data;
    if (action === "retry") {
      data = await requestJson(`${baseUrl}/retry`, {
        method: "POST",
        body: JSON.stringify({
          action: item.source_type === "repair" ? "repair" : "regenerate",
          instructions: options.instructions || "",
        }),
      });
    } else if (action === "edit") {
      data = await requestJson(`${baseUrl}/script`, {
        method: "PATCH",
        body: JSON.stringify({
          content: options.content || "",
          artifact_kind: item.editable_artifact_kind,
          expected_content_sha256: state.failureEditContentSha256 || "",
        }),
      });
    } else if (action === "execute") {
      data = await requestJson(`${baseUrl}/execute`, { method: "POST", body: JSON.stringify({}) });
    } else if (action === "delete") {
      data = await requestJson(baseUrl, { method: "DELETE" });
    } else if (action === "ignore") {
      data = await requestJson(`${baseUrl}/ignore`, { method: "POST", body: JSON.stringify({}) });
    }
    await refreshSelectedRun();
    const actionResult = data?.item?.latest_action || data?.result || {};
    const actionFailed = actionResult.status === "failed";
    const notices = {
      retry: item.source_type === "repair" ? "重新修复已完成，请执行脚本验证。" : "重新生成已完成，请执行脚本验证。",
      edit: "脚本已保存，分析结果会在证据更新后标记为过期。",
      execute: "脚本执行验证通过，已标记为成功。",
      delete: "脚本已删除，失败历史和分析记录仍然保留。",
      ignore: "失败项已标记为保留未解决。",
    };
    const failureMessage =
      actionResult.error ||
      data?.item?.error ||
      (action === "execute" ? "脚本执行仍然失败，已更新失败证据。" : "本次处理仍然失败，已更新失败证据。");
    setNotice(data?.message || (actionFailed ? failureMessage : notices[action]) || "操作已提交。", actionFailed ? "error" : "success");
    return data;
  } catch (error) {
    setNotice(error.message, "error");
    throw error;
  } finally {
    state.failureActionPending = false;
    state.failureActionItemId = keepModalSelection ? item.item_id : "";
    renderRunList();
    renderArtifacts();
  }
}

async function confirmFailureAction() {
  const item = getFailureItem();
  if (!item || state.failureActionPending) {
    return;
  }
  elements.failureActionConfirm.disabled = true;
  const mode = state.failureActionMode;
  try {
    if (mode === "retry") {
      elements.failureActionConfirm.textContent = item.source_type === "repair" ? "正在重新修复…" : "正在重新生成…";
      const data = await runFailureItemRequest(item, "retry", { instructions: elements.failureRetryInstructions.value.trim() });
      if (data?.item?.latest_action?.status === "failed") {
        elements.failureRetryReason.value = data.item.latest_action.error || data.item.error || "本次重试仍然失败。";
        elements.failureActionConfirm.textContent = item.source_type === "repair" ? "再次重新修复" : "再次重新生成";
        elements.failureActionConfirm.disabled = false;
        return;
      }
    } else if (mode === "edit") {
      elements.failureActionConfirm.textContent = "正在保存…";
      const content = elements.failureScriptEditor.value;
      if (!content.trim()) {
        throw new Error("脚本内容不能为空。");
      }
      await runFailureItemRequest(item, "edit", { content });
    }
    closeFailureActionModal();
  } catch (error) {
    if (error.message.includes("脚本内容不能为空")) {
      setNotice(error.message, "error");
    }
    elements.failureActionConfirm.textContent =
      mode === "edit" ? "保存脚本" : item.source_type === "repair" ? "再次重新修复" : "再次重新生成";
    elements.failureActionConfirm.disabled = false;
  }
}

async function handleFailureItemAction(action, itemId) {
  const item = getFailureItem(itemId);
  if (!item || (action !== "details" && isFailureItemBusy(item))) {
    return;
  }
  if (action === "details") {
    await openFailureDetails(item);
  } else if (action === "analysis") {
    await openFailureAnalysis(item);
  } else if (action === "retry") {
    openFailureRetry(item);
  } else if (action === "edit") {
    await openFailureEdit(item);
  } else if (action === "execute") {
    await runFailureItemRequest(item, "execute");
  } else if (action === "delete") {
    if (window.confirm(`确定删除“${failureItemTitle(item)}”吗？脚本会被归档，失败历史仍会保留。`)) {
      await runFailureItemRequest(item, "delete");
    }
  } else if (action === "ignore") {
    if (window.confirm(`“${failureItemTitle(item)}”没有可删除脚本，确定保留失败记录并忽略该项吗？`)) {
      await runFailureItemRequest(item, "ignore");
    }
  }
}


function closeContinueTaskModal() {
  elements.continueTaskModal.classList.add("hidden");
  document.body.classList.remove("agent-modal-open");
  if (state.continueTaskOpener?.isConnected) {
    state.continueTaskOpener.focus();
  }
  state.continueTaskOpener = null;
}

async function continueFailureTask({ keepUnresolved = false } = {}) {
  if (!state.selectedRunId || !isFailureCheckpointRun() || state.continueTaskPending) {
    return;
  }
  state.continueTaskPending = true;
  elements.continueTaskConfirm.disabled = true;
  elements.continueTaskConfirm.textContent = "正在继续…";
  renderRunList();
  try {
    const data = await requestJson(`/api/agent/runs/${encodePathPart(state.selectedRunId)}/continue`, {
      method: "POST",
      body: JSON.stringify({ keep_unresolved: keepUnresolved }),
    });
    if (isPlainObject(data.run)) {
      state.selectedRun = data.run;
      state.activeStepKey = data.run.current_step || state.activeStepKey;
    }
    if (Array.isArray(data.steps)) {
      state.steps = data.steps;
    }
    closeContinueTaskModal();
    await refreshSelectedRun();
    if (state.isActive && shouldObserveSelectedRun() && !state.streamController) {
      startEventStream();
    }
    setNotice(
      keepUnresolved ? "已保留未解决项目，任务将继续创建测试集。" : "失败项已处理完成，任务将继续创建测试集。",
      "success",
    );
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    state.continueTaskPending = false;
    elements.continueTaskConfirm.disabled = false;
    elements.continueTaskConfirm.textContent = "保留并继续";
    renderRunList();
  }
}

function openContinueTaskModal() {
  if (!isFailureCheckpointRun()) {
    return;
  }
  const unresolved = failureWorkspaceData().items.filter((item) => !isFailureItemResolved(item));
  if (!unresolved.length) {
    continueFailureTask({ keepUnresolved: false });
    return;
  }
  const generationCount = unresolved.filter((item) => item.source_type === "generation").length;
  const repairCount = unresolved.length - generationCount;
  state.continueTaskOpener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  elements.continueTaskSummary.textContent = `仍有 ${unresolved.length} 个未解决项目`;
  elements.continueTaskDescription.textContent = `其中脚本生成失败 ${generationCount} 项、脚本修复失败 ${repairCount} 项。继续后它们不会进入本次测试集，但失败证据、分析和历史记录会继续保留。`;
  elements.continueTaskModal.classList.remove("hidden");
  document.body.classList.add("agent-modal-open");
  window.requestAnimationFrame(() => elements.continueTaskCancel.focus());
}


  return {
    getData: failureWorkspaceData,
    isHandled: isFailureItemResolved,
    render: renderFailureWorkspace,
    handleAction: handleFailureItemAction,
    closeActionModal: closeFailureActionModal,
    confirmAction: confirmFailureAction,
    openContinue: openContinueTaskModal,
    closeContinue: closeContinueTaskModal,
    continueTask: continueFailureTask,
    reset() {
      state.failureItems = [];
      state.failureActionItemId = "";
      state.failureActionMode = "";
      state.failureActionPending = false;
      state.failureActionOpener = null;
      state.failureEditContentSha256 = "";
      state.continueTaskPending = false;
      state.continueTaskOpener = null;
    },
  };
}

window.createAgentFailureWorkspace = createAgentFailureWorkspace;
