function normalizeModuleScriptPreparationRuns(value, legacyModule = "", legacyRunId = "") {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const runs = Object.fromEntries(Object.entries(source)
    .map(([moduleName, runId]) => [String(moduleName || "").trim(), String(runId || "").trim()])
    .filter(([moduleName, runId]) => moduleName && runId));
  const moduleName = String(legacyModule || "").trim();
  const runId = String(legacyRunId || "").trim();
  if (moduleName && runId && !runs[moduleName]) {
    runs[moduleName] = runId;
  }
  return runs;
}

function createModuleScriptPreparationFeature(options = {}) {
  const {
    root,
    state,
    SECTION,
    SCRIPT_VIEW_TAB,
    requestJson,
    encodePathPart = (value) => encodeURIComponent(String(value || "")),
    persistViewState = () => {},
    renderSideList = () => {},
    renderContent = () => {},
    refreshScriptTree = null,
    window: windowRef = window,
    document: documentRef = document,
  } = options;
  if (!root || !state || typeof requestJson !== "function") {
    throw new Error("模块脚本准备功能缺少挂载容器或请求依赖。");
  }
  state.scripts.preparationRuns = normalizeModuleScriptPreparationRuns(
    state.scripts.preparationRuns, state.scripts.preparationModule, state.scripts.preparationRunId,
  );

  const context = {
    scope: "module",
    moduleName: state.scripts.preparationModule || state.scripts.selectedModule || "",
    emptyMeta: "当前模块尚无脚本准备任务",
    footerHint: "执行通过的脚本会保留在当前模块；忽略只作用于本次准备任务",
    abandonLabel: "忽略本次准备",
    abandonNote: "忽略后仅结束该脚本在本次任务中的准备流程；已有脚本、Prompt、版本和处理历史都会保留。",
    abandonedStatusLabel: "已忽略",
    abandonedProgress: "已忽略本次准备",
    participationLabel: "本次准备可用",
    abandonItemMessage: ({ title }) => `确定忽略“${title}”的本次准备吗？已有脚本不会被删除。`,
    abandonBatchMessage: ({ count }) => `确定忽略选中的 ${count} 条脚本的本次准备吗？已有脚本不会被删除。`,
  };
  const api = {
    snapshotUrl: (runId) => `/api/script-preparation-runs/${encodePathPart(runId)}`,
    itemUrl: (runId, itemId) =>
      `/api/script-preparation-runs/${encodePathPart(runId)}/items/${encodePathPart(itemId)}`,
    batchActionsUrl: (runId) => `/api/script-preparation-runs/${encodePathPart(runId)}/items/batch-actions`,
  };

  let workbench = null;
  let treeSignature = "";
  let treeRefreshTimer = null;

  function runForModule(moduleName = state.scripts.selectedModule) {
    return String(state.scripts.preparationRuns?.[String(moduleName || "")] || "");
  }

  function syncSelectedRun(persist = false) {
    const moduleName = String(state.scripts.selectedModule || "");
    const runId = runForModule(moduleName);
    const changed = state.scripts.preparationModule !== moduleName || state.scripts.preparationRunId !== runId;
    state.scripts.preparationModule = moduleName;
    state.scripts.preparationRunId = runId;
    context.moduleName = moduleName;
    if (changed && persist) {
      persistViewState();
    }
    return runId;
  }

  function currentRunId() {
    return runForModule();
  }

  function isVisible() {
    return state.activeSection === SECTION.SCRIPTS &&
      !state.scripts.selectedFile &&
      state.scripts.activeTab === SCRIPT_VIEW_TAB.PREPARATION;
  }

  function clearTreeRefreshTimer() {
    if (treeRefreshTimer !== null) {
      (windowRef.clearTimeout || clearTimeout)(treeRefreshTimer);
      treeRefreshTimer = null;
    }
  }

  function maybeRefreshScriptTree(snapshot) {
    if (!isVisible()) {
      return;
    }
    const items = Array.isArray(snapshot?.items) ? snapshot.items : [];
    const signature = items.map((item) => [
      item.item_id || item.script_item_id || "",
      item.current_revision_id || item.current_script?.revision_id || "",
      item.filename || item.current_script?.filename || "",
    ].join(":")).sort().join("|");
    if (!signature || signature === treeSignature) {
      return;
    }
    if (typeof refreshScriptTree !== "function") {
      return;
    }
    clearTreeRefreshTimer();
    treeRefreshTimer = (windowRef.setTimeout || setTimeout)(async () => {
      treeRefreshTimer = null;
      if (!isVisible()) {
        return;
      }
      try {
        await refreshScriptTree();
        treeSignature = signature;
      } catch (error) {
        // The preparation workbench remains usable if the navigation tree refresh fails.
      }
    }, 250);
  }

  function handleWorkbenchState(nextState) {
    if (!isVisible()) {
      clearTreeRefreshTimer();
      root.classList.add("hidden");
      if (nextState.active) {
        workbench?.deactivate();
      }
      return;
    }
    maybeRefreshScriptTree(nextState);
  }

  workbench = windowRef.createScriptPreparationFeature(root, {
    runId: currentRunId(),
    getRunId: currentRunId,
    requestJson,
    api,
    context,
    pollIntervalMs: 3000,
    revealOnActivate: false,
    window: windowRef,
    document: documentRef,
    onStateChange: handleWorkbenchState,
  });

  function extractRunId(value) {
    const source = value && typeof value === "object" ? value : {};
    const run = source.run && typeof source.run === "object" ? source.run : {};
    const snapshot = source.snapshot && typeof source.snapshot === "object" ? source.snapshot : {};
    return String(source.run_id || source.id || run.run_id || run.id || snapshot.run_id || "");
  }

  function extractSnapshot(value) {
    const source = value && typeof value === "object" ? value : {};
    return source.snapshot || source.script_preparation || source.run?.snapshot || null;
  }

  async function openRun(result, moduleName) {
    const runId = extractRunId(result);
    if (!runId) {
      throw new Error("脚本准备任务创建成功，但响应中缺少 run_id。");
    }
    const targetModule = String(moduleName || result?.module_name || result?.run?.module_name || "");
    state.scripts.preparationRunId = runId;
    state.scripts.preparationModule = targetModule;
    state.scripts.preparationRuns = { ...state.scripts.preparationRuns, [targetModule]: runId };
    state.scripts.selectedModule = targetModule;
    state.scripts.selectedFile = null;
    state.scripts.activeTab = SCRIPT_VIEW_TAB.PREPARATION;
    state.scripts.bulkSelectionMode = false;
    state.scripts.selectedFiles.clear();
    if (targetModule) {
      state.scripts.expandedModules.add(targetModule);
    }
    state.activeSection = SECTION.SCRIPTS;
    context.moduleName = targetModule;
    workbench.setRun(runId);
    const snapshot = extractSnapshot(result);
    if (snapshot) {
      workbench.applyEvent({ payload: { script_preparation: snapshot } });
    }
    persistViewState();
    renderSideList();
    renderContent();
    if (!workbench.getState().active) {
      await workbench.activate(runId);
    }
    root.classList.remove("hidden");
    return runId;
  }

  function render() {
    const runId = syncSelectedRun(true);
    if (workbench.getState().runId !== runId) {
      workbench.setRun(runId);
      treeSignature = "";
    }
    const visible = isVisible();
    root.classList.toggle("hidden", !visible);
    if (visible) {
      if (!workbench.getState().active) {
        void workbench.activate(runId).finally(() => root.classList.toggle("hidden", !isVisible()));
      }
    } else {
      clearTreeRefreshTimer();
      if (workbench.getState().active) {
        workbench.deactivate();
      }
    }
  }

  async function refresh() {
    if (!currentRunId() || !isVisible()) {
      return null;
    }
    if (!workbench.getState().active) {
      return workbench.activate(currentRunId());
    }
    return workbench.refresh({ includeDetail: isVisible() });
  }

  function reset() {
    state.scripts.preparationRunId = "";
    state.scripts.preparationModule = "";
    state.scripts.preparationRuns = {};
    clearTreeRefreshTimer();
    workbench.setRun("");
    workbench.deactivate();
  }

  function destroy() {
    clearTreeRefreshTimer();
    workbench.destroy();
  }

  function withModulePlaceholders(modules) {
    const result = Array.isArray(modules) ? [...modules] : [];
    Object.entries(state.scripts.preparationRuns || {}).forEach(([moduleName, runId]) => {
      if (runId && !result.some((item) => item.name === moduleName)) {
        result.push({ name: moduleName, path: `tests/${moduleName}`, scripts: [], preparationPlaceholder: true });
      }
    });
    return result;
  }

  return { openRun, render, refresh, reset, destroy, withModulePlaceholders, getState: workbench.getState, extractRunId };
}

window.createModuleScriptPreparationFeature = createModuleScriptPreparationFeature;
window.normalizeModuleScriptPreparationRuns = normalizeModuleScriptPreparationRuns;
