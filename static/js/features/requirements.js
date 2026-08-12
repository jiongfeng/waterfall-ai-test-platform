function createRequirementsFeature(deps) {
  const {
    state,
    elements,
    SECTION,
    REQUIREMENT_VIEW_TAB,
    PLAN_GENERATION_MODE,
    PLAN_VIEW_TAB,
    document,
    window,
    fetch,
    TextDecoder,
    FormData,
    CSS,
    renderContent,
    renderSideList,
    getSearchQuery,
    escapeHtml,
    formatTimestampMs,
    isPlainObject,
    getPlanFilenameForProjectLanguage,
    normalizeRequirementPlanGenerationBatch,
    persistRequirementPlanGenerationBatches,
    normalizeRequirementModule,
    isAnyScriptJobRunning,
    requestJson,
    encodePathPart,
    setNotice,
    parseSseBlock,
    getCoverageProfile,
    ensureGenerationDefaults,
    populateCoverageSelect,
    composeCoveragePrompt,
    getProjectRequestHeaders,
    persistViewState,
    formatModuleRepairDuration,
    createStatusBadge,
    getGenerationStatusInfo,
    openRequirementPlanGenerationModal,
    loadPlanModules,
    selectPlan,
    confirmDiscardEdit,
    setLoading,
    normalizeRequirement,
  } = deps;

const localizeRequirementLog = (value) => window.WaterfallI18n?.log(value) || value;
const requirementText = (key, params, fallback) => {
  const translated = window.WaterfallI18n?.t?.(key, params);
  return translated && translated !== key ? translated : fallback;
};

function switchRequirementViewTab(nextTab) {
  if (
    state.activeSection !== SECTION.REQUIREMENTS ||
    !Object.values(REQUIREMENT_VIEW_TAB).includes(nextTab) ||
    state.requirements.activeTab === nextTab
  ) {
    return;
  }

  state.requirements.activeTab = nextTab;
  renderContent();
}

function filteredRequirements() {
  const query = getSearchQuery();
  if (!query) {
    return state.requirements.items;
  }
  return state.requirements.items.filter((item) => {
    const haystack = [item.title, item.filename, item.requirement_uid].join(" ").toLowerCase();
    return haystack.includes(query);
  });
}

function renderRequirementList() {
  const requirements = filteredRequirements();
  if (!requirements.length) {
    const empty = document.createElement("div");
    empty.className = "module-item";
    empty.textContent = state.requirements.items.length ? "没有匹配的需求" : "未上传需求";
    elements.moduleList.appendChild(empty);
    return;
  }

  requirements.forEach((requirement) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "module-item";
    button.classList.toggle("active", requirement.requirement_uid === state.requirements.selectedUid);
    button.title = requirement.file_path || requirement.filename || requirement.title;
    window.WaterfallI18n?.markDynamicAttributes?.(button);
    button.innerHTML = `
      <span class="requirement-list-title"></span>
      <span class="requirement-list-meta">${escapeHtml(requirementText("requirements.candidateMeta", {
        count: requirement.module_count || 0,
        timestamp: formatTimestampMs(requirement.updated_at),
      }, `${requirement.module_count || 0} 个候选 · ${formatTimestampMs(requirement.updated_at)}`))}</span>
    `;
    const title = button.querySelector(".requirement-list-title");
    window.WaterfallI18n?.markDynamic?.(title);
    title.textContent = requirement.title;
    button.addEventListener("click", () => selectRequirement(requirement.requirement_uid));
    elements.moduleList.appendChild(button);
  });
}

function getRequirementModuleStatusInfo(status) {
  if (status === "generated") {
    return { label: "已生成计划", className: "success" };
  }
  if (status === "confirmed") {
    return { label: "已确认", className: "running" };
  }
  if (status === "superseded") {
    return { label: "已替换", className: "cancelled" };
  }
  return { label: "候选", className: "" };
}

function formatRequirementList(items) {
  return (Array.isArray(items) ? items : []).filter(Boolean).join("\n");
}

function parseTextareaList(value) {
  return String(value || "")
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

function summarizeMatchedInventory(value) {
  if (Array.isArray(value)) {
    return value
      .map((item) => (typeof item === "string" ? item : item?.page_name || item?.url || ""))
      .filter(Boolean)
      .join("；");
  }
  if (!isPlainObject(value)) {
    return "";
  }
  const parts = [];
  if (value.page_name) {
    parts.push(`页面：${value.page_name}`);
  }
  if (value.url) {
    parts.push(`路径：${value.url}`);
  }
  if (Array.isArray(value.roles) && value.roles.length) {
    parts.push(`角色：${value.roles.join("、")}`);
  }
  if (Array.isArray(value.stable_selectors) && value.stable_selectors.length) {
    parts.push(`控件：${value.stable_selectors.slice(0, 6).join("、")}`);
  }
  return parts.join("；");
}

function getRequirementPlanGenerationBatchKey(requirementUid = state.requirements.selectedUid) {
  return requirementUid || "";
}

function getRequirementModulePlanTargetPath(moduleItem) {
  if (!moduleItem?.module_name) {
    return "";
  }
  return `specs/${moduleItem.module_name}/${getPlanFilenameForProjectLanguage(
    moduleItem.plan_name,
    moduleItem.module_name,
    moduleItem.module_name || "测试计划",
  )}`;
}

function setRequirementPlanGenerationBatch(requirementUid, updates) {
  const key = getRequirementPlanGenerationBatchKey(requirementUid);
  if (!key) {
    return null;
  }

  const previous = state.requirements.planGenerationBatches[key] || {
    status: "idle",
    requirement_uid: requirementUid,
    requirement_title: state.requirements.current?.title || state.requirements.current?.filename || "",
    module_uids: [],
    active_module_uid: "",
    expanded_module_uid: "",
    items: {},
    started_at: null,
    finished_at: null,
  };
  const next = normalizeRequirementPlanGenerationBatch({
    ...previous,
    ...updates,
    requirement_uid: updates.requirement_uid || previous.requirement_uid || requirementUid,
    requirement_title: updates.requirement_title || previous.requirement_title || state.requirements.current?.filename || "",
    updated_at: Date.now(),
  });
  state.requirements.planGenerationBatches[key] = next;
  persistRequirementPlanGenerationBatches(key);
  if (
    state.activeSection === SECTION.REQUIREMENTS &&
    state.requirements.selectedUid === requirementUid &&
    state.requirements.activeTab === REQUIREMENT_VIEW_TAB.PLAN_GENERATION_BATCH
  ) {
    renderRequirementPlanGenerationBatchRecord();
  }
  return next;
}

function setRequirementPlanGenerationBatchItem(requirementUid, moduleUid, updates) {
  const batch =
    state.requirements.planGenerationBatches[getRequirementPlanGenerationBatchKey(requirementUid)] ||
    setRequirementPlanGenerationBatch(requirementUid, { requirement_uid: requirementUid, module_uids: [moduleUid], items: {} });
  const previousItem = batch.items?.[moduleUid] || {
    status: "queued",
    module_uid: moduleUid,
    module_name: "",
    plan_name: "",
    plan_filename: "",
    prompt: "",
    logs: "",
    error: "",
    target_path: "",
    generated_plan: null,
    started_at: null,
    finished_at: null,
    updated_at: Date.now(),
  };
  const nextItems = {
    ...(batch.items || {}),
    [moduleUid]: {
      ...previousItem,
      ...updates,
      module_uid: updates.module_uid || previousItem.module_uid || moduleUid,
      logs: Object.prototype.hasOwnProperty.call(updates, "logs") ? updates.logs : previousItem.logs || "",
      updated_at: Date.now(),
    },
  };
  return setRequirementPlanGenerationBatch(requirementUid, { items: nextItems });
}

function appendRequirementPlanGenerationBatchLog(requirementUid, moduleUid, text) {
  if (!text) {
    return;
  }
  const batch = state.requirements.planGenerationBatches[getRequirementPlanGenerationBatchKey(requirementUid)] || {};
  const item = batch.items?.[moduleUid] || {};
  setRequirementPlanGenerationBatchItem(requirementUid, moduleUid, {
    logs: `${item.logs || ""}${text}`,
  });
}

function pruneSelectedRequirementModuleUids() {
  const validUids = new Set(state.requirements.modules.map((moduleItem) => moduleItem.module_uid));
  Array.from(state.requirements.selectedModuleUids).forEach((moduleUid) => {
    if (!validUids.has(moduleUid)) {
      state.requirements.selectedModuleUids.delete(moduleUid);
    }
  });
}

function enterRequirementModuleBulkMode() {
  if (isAnyScriptJobRunning()) {
    return;
  }
  state.requirements.bulkSelectionMode = true;
  state.requirements.selectedModuleUids.clear();
  renderRequirementModules();
}

function cancelRequirementModuleBulkMode() {
  state.requirements.bulkSelectionMode = false;
  state.requirements.selectedModuleUids.clear();
  renderRequirementModules();
}

function toggleRequirementModuleSelectAll() {
  if (!state.requirements.bulkSelectionMode || isAnyScriptJobRunning()) {
    return;
  }
  const modules = state.requirements.modules;
  const shouldSelectAll = state.requirements.selectedModuleUids.size !== modules.length;
  state.requirements.selectedModuleUids.clear();
  if (shouldSelectAll) {
    modules.forEach((moduleItem) => state.requirements.selectedModuleUids.add(moduleItem.module_uid));
  }
  renderRequirementModules();
}

async function deleteSelectedRequirementModules() {
  const requirementUid = state.requirements.selectedUid;
  const moduleUids = Array.from(state.requirements.selectedModuleUids);
  if (!requirementUid || !moduleUids.length || state.requirements.bulkDeletingModules || isAnyScriptJobRunning()) {
    return;
  }

  if (!window.confirm(`确认删除选中的 ${moduleUids.length} 个候选模块？`)) {
    return;
  }

  state.requirements.bulkDeletingModules = true;
  renderRequirementModules();
  const failures = [];
  const deletedUids = new Set();

  try {
    for (const moduleUid of moduleUids) {
      try {
        await requestJson(`/api/requirements/${encodePathPart(requirementUid)}/modules/${encodePathPart(moduleUid)}`, {
          method: "DELETE",
        });
        deletedUids.add(moduleUid);
      } catch (error) {
        const moduleItem = getRequirementModuleByUid(moduleUid);
        failures.push(`${moduleItem?.module_name || moduleUid}：${error.message}`);
      }
    }

    state.requirements.modules = state.requirements.modules.filter((item) => !deletedUids.has(item.module_uid));
    const selectedRequirement = state.requirements.items.find((item) => item.requirement_uid === requirementUid);
    if (selectedRequirement) {
      selectedRequirement.module_count = state.requirements.modules.length;
    }
    state.requirements.selectedModuleUids.clear();
    state.requirements.bulkSelectionMode = false;
    if (deletedUids.has(state.requirements.detailModuleUid)) {
      closeRequirementModuleDetail();
    }
    renderSideList();
    renderContent();
    if (failures.length) {
      setNotice(`批量删除完成，失败 ${failures.length} 个：${failures.join("；")}`, "error");
    } else {
      setNotice(`已删除 ${deletedUids.size} 个候选模块。`, "success");
    }
  } finally {
    state.requirements.bulkDeletingModules = false;
    renderContent();
  }
}

async function splitRequirementBatchGeneratedPlanCases(requirementUid, moduleUid, moduleName, planFilename) {
  appendRequirementPlanGenerationBatchLog(requirementUid, moduleUid, "正在拆分单用例计划。\n");
  const result = await requestJson(
    `/api/plans/${encodePathPart(moduleName)}/${encodePathPart(planFilename)}/split-cases`,
    {
      method: "POST",
      body: JSON.stringify({ overwrite: false }),
    },
  );
  const created = result.created || [];
  const skipped = result.skipped || [];
  const message = [
    `拆分完成：新增 ${created.length} 个单用例计划。`,
    skipped.length ? `跳过 ${skipped.length} 个已存在或无效计划。` : "",
  ]
    .filter(Boolean)
    .join("");
  appendRequirementPlanGenerationBatchLog(requirementUid, moduleUid, `${message}\n`);
  return result;
}

async function readRequirementBatchPlanGenerationStream(response, requirementUid, moduleItem) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let result = {
    status: "running",
    module_uid: moduleItem.module_uid,
    module_name: moduleItem.module_name,
    plan_name: moduleItem.plan_name,
    logs: "",
  };

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
        result = handleRequirementBatchPlanGenerationEvent(event, result, requirementUid, moduleItem);
      }
      boundaryIndex = buffer.indexOf("\n\n");
    }
  }

  buffer += decoder.decode();
  const trailingEvent = parseSseBlock(buffer.trim());
  if (trailingEvent) {
    result = handleRequirementBatchPlanGenerationEvent(trailingEvent, result, requirementUid, moduleItem);
  }

  return result;
}

function handleRequirementBatchPlanGenerationEvent({ event, data }, previousResult, requirementUid, moduleItem) {
  const moduleUid = moduleItem.module_uid;
  if (event === "status") {
    const nextResult = {
      ...previousResult,
      status: data.status || previousResult.status,
      module_name: data.module_name || previousResult.module_name || moduleItem.module_name,
      target_path: data.target_path || previousResult.target_path,
      error: data.error || previousResult.error,
    };
    setRequirementPlanGenerationBatchItem(requirementUid, moduleUid, {
      status: nextResult.status,
      module_name: nextResult.module_name,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
      target_path: nextResult.target_path || "",
    });
    return nextResult;
  }

  if (event === "log") {
    const text = data.message ? `${data.message}\n` : "";
    appendRequirementPlanGenerationBatchLog(requirementUid, moduleUid, text);
    return { ...previousResult, logs: `${previousResult.logs || ""}${text}` };
  }

  if (event === "delta") {
    const text = data.text || "";
    appendRequirementPlanGenerationBatchLog(requirementUid, moduleUid, text);
    return { ...previousResult, logs: `${previousResult.logs || ""}${text}` };
  }

  if (event === "done") {
    const updated = normalizeRequirementModule(data.requirement_module);
    if (updated) {
      mergeRequirementModuleUpdate(updated);
    }
    const status = data.ok === false ? "failed" : previousResult.status === "running" ? "succeeded" : previousResult.status;
    const nextResult = {
      ...previousResult,
      status,
      error: data.error || previousResult.error || "",
      plan_filename: data.plan_filename || previousResult.plan_filename || "",
      generation_mode: data.generation_mode || previousResult.generation_mode || "",
      coverage_profile: data.coverage_profile || previousResult.coverage_profile || "",
      prompt_customized:
        typeof data.prompt_customized === "boolean" ? data.prompt_customized : Boolean(previousResult.prompt_customized),
      job_id: data.job_id || previousResult.job_id || "",
      prompt: data.job?.prompt || previousResult.prompt || "",
      requirement_module: updated || previousResult.requirement_module,
      generated_plan: updated?.generated_plan || previousResult.generated_plan || null,
      plans: Array.isArray(data.plans) ? data.plans : previousResult.plans,
      created: Array.isArray(data.created) ? data.created : previousResult.created,
      reused: Array.isArray(data.reused) ? data.reused : previousResult.reused,
      split: data.split || previousResult.split,
      deleted_source: data.deleted_source || previousResult.deleted_source,
    };
    setRequirementPlanGenerationBatchItem(requirementUid, moduleUid, {
      status: nextResult.status,
      error: nextResult.error || "",
      logs: nextResult.logs || "",
      plan_filename: nextResult.plan_filename || "",
      generated_plan: nextResult.generated_plan || null,
      coverage_profile: nextResult.coverage_profile || "",
      prompt_customized: Boolean(nextResult.prompt_customized),
      job_id: nextResult.job_id || "",
      prompt: nextResult.prompt || "",
      target_path: nextResult.target_path || updated?.generated_plan?.path || "",
      finished_at: Date.now(),
    });
    return nextResult;
  }

  return previousResult;
}

function renderRequirementBatchPromptState() {
  const profile = getCoverageProfile(elements.requirementBatchCoverageProfile.value);
  const customized = elements.requirementBatchCoveragePrompt.value.trim() !== String(profile?.template_prompt || "").trim();
  elements.requirementBatchPromptCustomized.textContent = customized ? "· 已自定义" : "";
}

async function openRequirementBatchPlanModal() {
  if (!state.requirements.selectedModuleUids.size) {
    return;
  }
  await ensureGenerationDefaults();
  const profileKey = state.generation.defaultCoverageProfile;
  populateCoverageSelect(elements.requirementBatchCoverageProfile, profileKey);
  elements.requirementBatchCoverageProfile.dataset.previous = profileKey;
  elements.requirementBatchCoveragePrompt.value = getCoverageProfile(profileKey)?.template_prompt || "";
  elements.requirementBatchPlanSummary.textContent = `将为选中的 ${state.requirements.selectedModuleUids.size} 个模块生成计划。`;
  renderRequirementBatchPromptState();
  elements.requirementBatchPlanModal.classList.remove("hidden");
}

function closeRequirementBatchPlanModal() {
  elements.requirementBatchPlanModal.classList.add("hidden");
}

function changeRequirementBatchCoverageProfile() {
  const current = elements.requirementBatchCoveragePrompt.value.trim();
  const previousKey = elements.requirementBatchCoverageProfile.dataset.previous || state.generation.defaultCoverageProfile;
  const previousTemplate = getCoverageProfile(previousKey)?.template_prompt || "";
  if (current && current !== previousTemplate && !window.confirm("批量策略语句已被编辑，切换档位将替换这些修改。是否继续？")) {
    elements.requirementBatchCoverageProfile.value = previousKey;
    return;
  }
  const nextKey = elements.requirementBatchCoverageProfile.value;
  elements.requirementBatchCoverageProfile.dataset.previous = nextKey;
  elements.requirementBatchCoveragePrompt.value = getCoverageProfile(nextKey)?.template_prompt || "";
  renderRequirementBatchPromptState();
}

function resetRequirementBatchCoveragePrompt() {
  const profile = getCoverageProfile(elements.requirementBatchCoverageProfile.value);
  const template = profile?.template_prompt || "";
  if (elements.requirementBatchCoveragePrompt.value.trim() !== template.trim() && !window.confirm("恢复模板将丢弃当前批量策略修改。是否继续？")) {
    return;
  }
  elements.requirementBatchCoveragePrompt.value = template;
  renderRequirementBatchPromptState();
}

async function generateSelectedRequirementModulePlans() {
  const requirementUid = state.requirements.selectedUid;
  const moduleUids = Array.from(state.requirements.selectedModuleUids);
  if (!requirementUid || !moduleUids.length || isAnyScriptJobRunning()) {
    return;
  }

  const modules = moduleUids.map(getRequirementModuleByUid).filter(Boolean);
  if (!modules.length) {
    return;
  }

  const coverageProfile = elements.requirementBatchCoverageProfile.value || state.generation.defaultCoverageProfile;
  const coveragePrompt = elements.requirementBatchCoveragePrompt.value.trim();
  const promptCustomized = coveragePrompt !== String(getCoverageProfile(coverageProfile)?.template_prompt || "").trim();
  const coverageLabel = getCoverageProfile(coverageProfile)?.label || "核心回归";
  closeRequirementBatchPlanModal();

  const startedAt = Date.now();
  const initialItems = Object.fromEntries(
    modules.map((moduleItem) => [
      moduleItem.module_uid,
      {
        status: "queued",
        module_uid: moduleItem.module_uid,
        module_name: moduleItem.module_name,
        plan_name: moduleItem.generated_plans?.length
          ? `${moduleItem.plan_name || moduleItem.module_name}-${coverageLabel}`
          : moduleItem.plan_name || moduleItem.module_name,
        plan_filename: "",
        prompt: composeCoveragePrompt(moduleItem.planner_prompt || "", coveragePrompt),
        coverage_profile: coverageProfile,
        prompt_customized: promptCustomized,
        logs: "",
        error: "",
        target_path: getRequirementModulePlanTargetPath({
          ...moduleItem,
          plan_name: moduleItem.generated_plans?.length
            ? `${moduleItem.plan_name || moduleItem.module_name}-${coverageLabel}`
            : moduleItem.plan_name,
        }),
        generated_plan: moduleItem.generated_plan || null,
        started_at: null,
        finished_at: null,
        updated_at: Date.now(),
      },
    ]),
  );

  state.requirements.planGenerationRunning = true;
  state.requirements.bulkSelectionMode = false;
  state.requirements.selectedModuleUids.clear();
  state.requirements.activeTab = REQUIREMENT_VIEW_TAB.PLAN_GENERATION_BATCH;
  setRequirementPlanGenerationBatch(requirementUid, {
    status: "running",
    requirement_uid: requirementUid,
    requirement_title: state.requirements.current?.title || state.requirements.current?.filename || "",
    module_uids: modules.map((moduleItem) => moduleItem.module_uid),
    active_module_uid: "",
    expanded_module_uid: modules[0]?.module_uid || "",
    items: initialItems,
    started_at: startedAt,
    finished_at: null,
  });
  renderContent();
  setNotice("正在批量生成计划，请稍候。");

  let hasFailure = false;
  const renderTimer = window.setInterval(() => renderRequirementPlanGenerationBatchRecord(), 1000);

  try {
    for (const moduleItem of modules) {
      const moduleUid = moduleItem.module_uid;
      const prompt = composeCoveragePrompt(moduleItem.planner_prompt || "", coveragePrompt);
      const itemStartedAt = Date.now();
      setRequirementPlanGenerationBatch(requirementUid, {
        active_module_uid: moduleUid,
        expanded_module_uid: moduleUid,
      });
      setRequirementPlanGenerationBatchItem(requirementUid, moduleUid, {
        status: "running",
        prompt,
        logs: "",
        error: "",
        started_at: itemStartedAt,
        finished_at: null,
      });
      renderRequirementModules();

      try {
        const generationMode = PLAN_GENERATION_MODE.MULTIPLE;
        const response = await fetch(
          `/api/requirements/${encodePathPart(requirementUid)}/modules/${encodePathPart(moduleUid)}/generate-plan-stream`,
          {
            method: "POST",
            headers: getProjectRequestHeaders({
              "Content-Type": "application/json",
            }),
            body: JSON.stringify({
              module_name: moduleItem.module_name,
              plan_name: moduleItem.generated_plans?.length
                ? `${moduleItem.plan_name || moduleItem.module_name}-${coverageLabel}`
                : moduleItem.plan_name || moduleItem.module_name,
              prompt,
              generation_mode: generationMode,
              coverage_profile: coverageProfile,
              coverage_prompt: coveragePrompt,
              prompt_customized: promptCustomized,
            }),
          },
        );

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

        const result = await readRequirementBatchPlanGenerationStream(response, requirementUid, moduleItem);
        const finishedAt = Date.now();
        if (result.status !== "succeeded" && result.status !== "failed") {
          result.status = "failed";
          result.error = "流式响应提前结束。";
        }
        if (result.status === "succeeded" && result.plan_filename && generationMode !== PLAN_GENERATION_MODE.MULTIPLE) {
          try {
            await splitRequirementBatchGeneratedPlanCases(
              requirementUid,
              moduleUid,
              result.module_name || moduleItem.module_name,
              result.plan_filename,
            );
          } catch (error) {
            result.status = "failed";
            result.error = error.message;
            appendRequirementPlanGenerationBatchLog(requirementUid, moduleUid, `${error.message}\n`);
          }
        }
        setRequirementPlanGenerationBatchItem(requirementUid, moduleUid, {
          status: result.status,
          error: result.error || "",
          logs:
            state.requirements.planGenerationBatches[getRequirementPlanGenerationBatchKey(requirementUid)]?.items?.[
              moduleUid
            ]?.logs ||
            result.logs ||
            "",
          plan_filename: result.plan_filename || "",
          generated_plan: result.generated_plan || null,
          target_path: result.target_path || "",
          finished_at: finishedAt,
        });
        if (result.status === "failed") {
          hasFailure = true;
        }
      } catch (error) {
        hasFailure = true;
        const finishedAt = Date.now();
        const batch = state.requirements.planGenerationBatches[getRequirementPlanGenerationBatchKey(requirementUid)] || {};
        const item = batch.items?.[moduleUid] || {};
        const prefix = item.logs && !item.logs.endsWith("\n") ? "\n" : "";
        const logs = `${item.logs || ""}${prefix}${error.message}\n`;
        setRequirementPlanGenerationBatchItem(requirementUid, moduleUid, {
          status: "failed",
          error: error.message,
          logs,
          finished_at: finishedAt,
        });
      }
    }

    setRequirementPlanGenerationBatch(requirementUid, {
      status: hasFailure ? "failed" : "succeeded",
      active_module_uid: "",
      finished_at: Date.now(),
    });
    await refreshRequirementModules();
    state.activeSection = SECTION.REQUIREMENTS;
    state.requirements.selectedUid = requirementUid;
    state.requirements.activeTab = REQUIREMENT_VIEW_TAB.PLAN_GENERATION_BATCH;
    persistViewState();
    renderSideList();
    setNotice(hasFailure ? "批量生成计划完成，存在失败模块。" : "批量生成计划完成。", hasFailure ? "error" : "success");
  } finally {
    window.clearInterval(renderTimer);
    state.requirements.planGenerationRunning = false;
    renderContent();
  }
}

function renderRequirementsPanel() {
  const requirement = state.requirements.current;
  if (!requirement) {
    return;
  }

  const activeTab = Object.values(REQUIREMENT_VIEW_TAB).includes(state.requirements.activeTab)
    ? state.requirements.activeTab
    : REQUIREMENT_VIEW_TAB.PREVIEW;
  window.WaterfallI18n?.markDynamic?.(elements.requirementMeta);
  elements.requirementMeta.textContent = `${requirement.filename || "-"} · ${formatTimestampMs(requirement.updated_at)}`;
  elements.requirementDownloadLink.href = `/api/requirements/${encodePathPart(requirement.requirement_uid)}/download`;
  elements.requirementDeleteButton.disabled =
    state.requirements.isDeleting ||
    state.requirements.analysisRunning ||
    state.requirements.planGenerationRunning ||
    state.requirements.bulkDeletingModules ||
    isAnyScriptJobRunning();
  elements.requirementDeleteButton.textContent =
    window.WaterfallI18n?.source?.(
      state.requirements.isDeleting ? "删除中" : "删除",
    ) || (state.requirements.isDeleting ? "删除中" : "删除");
  elements.requirementPreview.innerHTML = state.requirements.html || "";
  elements.requirementPreviewTab.classList.toggle("active", activeTab === REQUIREMENT_VIEW_TAB.PREVIEW);
  elements.requirementPreviewTab.setAttribute(
    "aria-selected",
    activeTab === REQUIREMENT_VIEW_TAB.PREVIEW ? "true" : "false",
  );
  elements.requirementModulesTab.classList.toggle("active", activeTab === REQUIREMENT_VIEW_TAB.MODULES);
  elements.requirementModulesTab.setAttribute(
    "aria-selected",
    activeTab === REQUIREMENT_VIEW_TAB.MODULES ? "true" : "false",
  );
  elements.requirementPlanGenerationBatchTab?.classList.toggle(
    "active",
    activeTab === REQUIREMENT_VIEW_TAB.PLAN_GENERATION_BATCH,
  );
  elements.requirementPlanGenerationBatchTab?.setAttribute(
    "aria-selected",
    activeTab === REQUIREMENT_VIEW_TAB.PLAN_GENERATION_BATCH ? "true" : "false",
  );
  elements.requirementPreviewTabPanel.classList.toggle("hidden", activeTab !== REQUIREMENT_VIEW_TAB.PREVIEW);
  elements.requirementModulesTabPanel.classList.toggle("hidden", activeTab !== REQUIREMENT_VIEW_TAB.MODULES);
  elements.requirementPlanGenerationBatchTabPanel?.classList.toggle(
    "hidden",
    activeTab !== REQUIREMENT_VIEW_TAB.PLAN_GENERATION_BATCH,
  );
  elements.analyzeRequirementButton.disabled =
    state.requirements.analysisRunning || state.requirements.planGenerationRunning || state.requirements.bulkDeletingModules;
  elements.analyzeRequirementButton.textContent = state.requirements.analysisRunning ? "解析中" : "解析需求";
  elements.importInventoryButton.disabled =
    state.requirements.analysisRunning || state.requirements.planGenerationRunning || state.requirements.bulkDeletingModules;

  const hasAnalysisLogs = Boolean(state.requirements.analysisLogs || state.requirements.analysisStatus);
  elements.requirementAnalysisOutput.classList.toggle("hidden", !hasAnalysisLogs);
  elements.requirementAnalysisLogs.textContent = localizeRequirementLog(state.requirements.analysisLogs || "");
  elements.requirementAnalysisLogs.scrollTop = elements.requirementAnalysisLogs.scrollHeight;
  elements.requirementAnalysisStatus.className = "job-status";
  if (state.requirements.analysisStatus === "succeeded") {
    elements.requirementAnalysisStatus.textContent = "解析完成";
    elements.requirementAnalysisStatus.classList.add("success");
  } else if (state.requirements.analysisStatus === "failed") {
    elements.requirementAnalysisStatus.textContent = `解析失败${state.requirements.analysisError ? `：${state.requirements.analysisError}` : ""}`;
    elements.requirementAnalysisStatus.classList.add("error");
  } else {
    elements.requirementAnalysisStatus.textContent = state.requirements.analysisRunning ? "解析进行中" : "解析日志";
  }

  renderRequirementModules();
  renderRequirementPlanGenerationBatchRecord();
  renderRequirementModuleDetailModal();
}

function renderRequirementModuleCounts() {
  const moduleCount = state.requirements.modules.length;
  const selectedCount = state.requirements.selectedModuleUids.size;
  if (elements.requirementModuleSummary) {
    elements.requirementModuleSummary.textContent = requirementText("requirements.moduleTabCount", { count: moduleCount }, `共 ${moduleCount} 个`);
  }
  if (elements.requirementModuleListSummary) {
    elements.requirementModuleListSummary.textContent = requirementText("requirements.candidateModuleCount", { count: moduleCount }, `共 ${moduleCount} 个候选模块`);
  }
  if (elements.requirementModuleSelectionCount) {
    elements.requirementModuleSelectionCount.textContent = requirementText("requirements.selectedModuleCount", { count: selectedCount }, `已选择 ${selectedCount} 个`);
  }
}

function renderRequirementModules() {
  const modules = state.requirements.modules;
  pruneSelectedRequirementModuleUids();
  const isBulkMode = state.requirements.bulkSelectionMode;
  const selectedCount = state.requirements.selectedModuleUids.size;
  const isBusy = isAnyScriptJobRunning();
  const hasBulkToolbar = Boolean(
    elements.requirementModuleListSummary &&
      elements.requirementModuleActions &&
      elements.requirementModuleBulkActions &&
      elements.requirementModuleSelectionCount &&
      elements.requirementModuleBulkToggle &&
      elements.requirementModuleBulkCancel &&
      elements.requirementModuleBulkDelete &&
      elements.requirementModuleBulkGenerate,
  );
  renderRequirementModuleCounts();

  if (hasBulkToolbar) {
    elements.requirementModuleActions.classList.toggle("hidden", isBulkMode);
    elements.requirementModuleBulkActions.classList.toggle("hidden", !isBulkMode);
    elements.requirementModuleBulkToggle.disabled = !modules.length || isBusy;
    elements.requirementModuleBulkCancel.disabled =
      state.requirements.bulkDeletingModules || state.requirements.planGenerationRunning;
    elements.requirementModuleBulkDelete.disabled = selectedCount === 0 || isBusy;
    elements.requirementModuleBulkGenerate.disabled = selectedCount === 0 || isBusy;
  }

  if (!modules.length) {
    state.requirements.selectedModuleUids.clear();
    state.requirements.bulkSelectionMode = false;
    if (hasBulkToolbar) {
      elements.requirementModuleActions.classList.remove("hidden");
      elements.requirementModuleBulkActions.classList.add("hidden");
      elements.requirementModuleBulkToggle.disabled = true;
    }
    elements.requirementModulesList.innerHTML = `
      <div class="requirement-modules-empty">
        <h3>暂无模块候选</h3>
        <p>上传需求后点击“解析需求”，这里会显示 OpenCode 识别出的模块、测试点和 planner prompt。</p>
      </div>
    `;
    return;
  }

  elements.requirementModulesList.innerHTML = `
    <div class="requirement-module-table-wrap">
      <table class="requirement-module-table">
        <thead>
          <tr>
            <th class="module-select-cell ${isBulkMode ? "" : "hidden"}">
              <input
                type="checkbox"
                data-action="select-all"
                aria-label="选择全部候选模块"
                ${modules.length && selectedCount === modules.length ? "checked" : ""}
                ${selectedCount > 0 && selectedCount < modules.length ? "data-indeterminate=\"true\"" : ""}
                ${isBusy ? "disabled" : ""}
              />
            </th>
            <th>模块名</th>
            <th>操作按钮</th>
          </tr>
        </thead>
        <tbody>
          ${modules.map((moduleItem) => renderRequirementModuleRow(moduleItem, { isBulkMode, isBusy })).join("")}
        </tbody>
      </table>
    </div>
  `;

  const selectAll = elements.requirementModulesList.querySelector('[data-action="select-all"]');
  if (selectAll) {
    selectAll.indeterminate = selectAll.dataset.indeterminate === "true";
    selectAll.addEventListener("change", toggleRequirementModuleSelectAll);
  }
  elements.requirementModulesList.querySelectorAll('[data-action="select-module"]').forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const moduleUid = checkbox.closest("[data-module-uid]")?.dataset.moduleUid || "";
      if (!moduleUid) {
        return;
      }
      if (checkbox.checked) {
        state.requirements.selectedModuleUids.add(moduleUid);
      } else {
        state.requirements.selectedModuleUids.delete(moduleUid);
      }
      renderRequirementModules();
    });
  });
  elements.requirementModulesList.querySelectorAll("button[data-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const row = button.closest("[data-module-uid]");
      const moduleUid = row?.dataset.moduleUid || "";
      const action = button.dataset.action;
      if (action === "open-detail") {
        openRequirementModuleDetail(moduleUid);
      } else if (action === "delete") {
        deleteRequirementModuleAction(moduleUid);
      } else if (action === "generate") {
        openRequirementPlanGenerationModal(moduleUid);
      } else if (action === "open-plan") {
        openRequirementGeneratedPlan(moduleUid);
      }
    });
  });
}

function renderRequirementModuleRow(moduleItem, { isBulkMode = false, isBusy = false } = {}) {
  const statusInfo = getRequirementModuleStatusInfo(moduleItem.status);
  const isGenerating = state.requirements.generatingModuleUid === moduleItem.module_uid;
  const disableActions = isGenerating || isBusy;
  const logs = state.requirements.modulePlanLogs[moduleItem.module_uid] || "";
  const generatedPlan = moduleItem.generated_plan || {};
  const checked = state.requirements.selectedModuleUids.has(moduleItem.module_uid);
  const confidenceBadge =
    moduleItem.confidence === null ? "" : `<span class="status-badge">置信度 ${(moduleItem.confidence * 100).toFixed(0)}%</span>`;
  const generatedSummary = generatedPlan.plan_filename
    ? `<span class="requirement-module-plan-path" data-i18n-dynamic>${escapeHtml(generatedPlan.module_name || moduleItem.module_name)}/${escapeHtml(
        generatedPlan.plan_filename,
      )}</span>`
    : "";
  const logSummary = logs
    ? `<div class="requirement-module-row-log ${moduleItem.generation_status === "failed" ? "error" : ""}" ${
        moduleItem.generation_error ? "data-i18n-dynamic" : ""
      }>${escapeHtml(
        moduleItem.generation_error || (moduleItem.generation_status === "succeeded" ? "生成完成" : "生成中"),
      )}</div>`
    : "";

  return `
    <tr data-module-uid="${escapeHtml(moduleItem.module_uid)}">
      <td class="module-select-cell ${isBulkMode ? "" : "hidden"}">
        <input
          type="checkbox"
          data-action="select-module"
          data-i18n-dynamic-attributes
          aria-label="${window.WaterfallI18n?.t?.("action.select") || "Select"} ${escapeHtml(moduleItem.module_name)}"
          ${checked ? "checked" : ""}
          ${isBusy ? "disabled" : ""}
        />
      </td>
      <td>
        <button class="module-name-button" type="button" data-action="open-detail" data-i18n-dynamic>${escapeHtml(moduleItem.module_name)}</button>
        <div class="requirement-module-row-meta">
          <span class="status-badge ${escapeHtml(statusInfo.className)}">${escapeHtml(statusInfo.label)}</span>
          ${confidenceBadge}
          ${moduleItem.write_risk ? '<span class="status-badge error">写库风险</span>' : ""}
          ${moduleItem.baseline_required ? '<span class="status-badge running">需要基线</span>' : ""}
        </div>
        ${moduleItem.business_goal ? `<p data-i18n-dynamic>${escapeHtml(moduleItem.business_goal)}</p>` : ""}
        ${generatedSummary}
        ${logSummary}
      </td>
      <td>
        <div class="module-row-actions">
          ${
            generatedPlan.plan_filename
              ? `<button class="secondary-button" type="button" data-action="open-plan">打开计划</button>`
              : ""
          }
          <button class="primary-button" type="button" data-action="generate" ${disableActions ? "disabled" : ""}>${
            isGenerating ? "生成中" : "生成计划"
          }</button>
          <button class="secondary-button danger-button" type="button" data-action="delete" ${
            disableActions ? "disabled" : ""
          }>删除</button>
        </div>
      </td>
    </tr>
  `;
}

function renderRequirementPlanGenerationBatchRecord() {
  if (!elements.requirementPlanGenerationBatchList) {
    return;
  }

  const requirementUid = state.requirements.selectedUid;
  const batch = state.requirements.planGenerationBatches[getRequirementPlanGenerationBatchKey(requirementUid)];
  const moduleUids = batch?.module_uids || [];
  const hasBatch = Boolean(batch && moduleUids.length);
  const isRunningBatch = hasBatch && batch.status === "running" && state.requirements.planGenerationRunning;
  const statusCounts = moduleUids.reduce(
    (counts, moduleUid) => {
      const status = batch?.items?.[moduleUid]?.status || "queued";
      counts[status] = (counts[status] || 0) + 1;
      return counts;
    },
    {},
  );

  elements.requirementPlanGenerationBatchEmpty.classList.toggle("hidden", hasBatch);
  elements.requirementPlanGenerationBatchList.classList.toggle("hidden", !hasBatch);
  elements.requirementPlanGenerationBatchHeader.classList.toggle("hidden", !hasBatch);
  elements.requirementPlanGenerationBatchSummary.textContent =
    batch?.status === "running" || isRunningBatch
      ? requirementText("requirements.batchRunningSummary", {
          succeeded: statusCounts.succeeded || 0,
          failed: statusCounts.failed || 0,
          running: statusCounts.running || 0,
          queued: statusCounts.queued || 0,
        }, `批量生成计划进行中：成功 ${statusCounts.succeeded || 0}，失败 ${statusCounts.failed || 0}，进行中 ${statusCounts.running || 0}，排队 ${statusCounts.queued || 0}`)
      : requirementText("requirements.batchRecordSummary", {
          succeeded: statusCounts.succeeded || 0,
          failed: statusCounts.failed || 0,
        }, `批量生成计划记录：成功 ${statusCounts.succeeded || 0}，失败 ${statusCounts.failed || 0}`);

  elements.requirementPlanGenerationBatchList.replaceChildren();
  if (!hasBatch) {
    return;
  }

  moduleUids.forEach((moduleUid) => {
    const item = batch.items?.[moduleUid] || { status: "queued", logs: "", error: "" };
    const isExpanded = batch.expanded_module_uid === moduleUid || (!batch.expanded_module_uid && item.status === "running");
    const wrapper = document.createElement("div");
    wrapper.className = "module-repair-item";

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "module-repair-toggle";
    toggle.setAttribute("aria-expanded", String(isExpanded));
    toggle.addEventListener("click", () => {
      setRequirementPlanGenerationBatch(requirementUid, {
        expanded_module_uid: isExpanded ? "" : moduleUid,
      });
    });

    const title = document.createElement("span");
    title.className = "module-repair-title";
    window.WaterfallI18n?.markDynamic?.(title);
    title.textContent = item.module_name || moduleUid;
    const duration = document.createElement("span");
    duration.className = "module-repair-duration";
    duration.textContent = formatModuleRepairDuration(item);
    const badge = createStatusBadge(getGenerationStatusInfo(item));
    toggle.append(title, duration, badge);
    wrapper.appendChild(toggle);

    if (isExpanded) {
      const output = document.createElement("div");
      output.className = "requirement-plan-batch-output";

      const promptLabel = document.createElement("div");
      promptLabel.className = "requirement-plan-batch-label";
      const profileLabel = getCoverageProfile(item.coverage_profile)?.label || "核心回归";
      promptLabel.textContent = `计划生成语句 · 模板来源：${profileLabel}${item.prompt_customized ? " · 已自定义" : ""}`;
      const promptPre = document.createElement("pre");
      promptPre.className = "requirement-plan-batch-prompt";
      promptPre.textContent = item.prompt || "暂无计划生成语句。";

      const logLabel = document.createElement("div");
      logLabel.className = "requirement-plan-batch-label";
      logLabel.textContent = "生成日志";
      const logPre = document.createElement("pre");
      logPre.className = "requirement-plan-batch-log";
      logPre.textContent = localizeRequirementLog(item.logs || item.error || "暂无实时输出。");

      output.append(promptLabel, promptPre, logLabel, logPre);
      wrapper.appendChild(output);
      window.requestAnimationFrame(() => {
        logPre.scrollTop = logPre.scrollHeight;
      });
    }

    elements.requirementPlanGenerationBatchList.appendChild(wrapper);
  });
}

function getRequirementModuleByUid(moduleUid) {
  return state.requirements.modules.find((item) => item.module_uid === moduleUid) || null;
}

function mergeRequirementModuleUpdate(moduleItem) {
  const updated = normalizeRequirementModule(moduleItem);
  if (!updated) {
    return null;
  }
  state.requirements.modules = state.requirements.modules.map((item) =>
    item.module_uid === updated.module_uid ? { ...item, ...updated } : item,
  );
  const selected = state.requirements.items.find((item) => item.requirement_uid === state.requirements.selectedUid);
  if (selected) {
    selected.module_count = state.requirements.modules.length;
  }
  return updated;
}

function openRequirementModuleDetail(moduleUid) {
  if (!getRequirementModuleByUid(moduleUid)) {
    return;
  }
  state.requirements.detailModuleUid = moduleUid;
  renderRequirementModuleDetailModal();
}

function closeRequirementModuleDetail() {
  state.requirements.detailModuleUid = "";
  elements.requirementModuleDetailModal.classList.add("hidden");
  elements.requirementModuleDetailBody.innerHTML = "";
}

function updateRequirementDeleteSubmitState() {
  const requirement = state.requirements.current;
  const isSelectedRequirement = Boolean(
    requirement &&
      requirement.requirement_uid === state.requirements.deletingUid,
  );
  elements.requirementDeleteSubmit.disabled =
    !isSelectedRequirement ||
    state.requirements.isDeleting ||
    elements.requirementDeleteConfirmation.value.trim() !==
      String(requirement?.title || "").trim();
}

function openRequirementDeleteModal() {
  const requirement = state.requirements.current;
  if (!requirement || state.requirements.isDeleting) {
    return;
  }
  state.requirements.deletingUid = requirement.requirement_uid;
  elements.requirementDeleteName.textContent = requirement.title;
  elements.requirementDeleteConfirmation.value = "";
  elements.requirementDeleteConfirmation.setCustomValidity("");
  updateRequirementDeleteSubmitState();
  elements.requirementDeleteModal.classList.remove("hidden");
  window.WaterfallI18n?.markDynamic?.(
    elements.requirementDeleteName,
  );
  window.WaterfallI18n?.localizeDom?.(
    elements.requirementDeleteModal,
  );
  window.requestAnimationFrame(() =>
    elements.requirementDeleteConfirmation.focus(),
  );
}

function closeRequirementDeleteModal() {
  if (state.requirements.isDeleting) {
    return;
  }
  elements.requirementDeleteModal.classList.add("hidden");
  elements.requirementDeleteConfirmation.value = "";
  elements.requirementDeleteConfirmation.setCustomValidity("");
  state.requirements.deletingUid = "";
  elements.requirementDeleteButton.focus();
}

async function submitRequirementDelete() {
  const requirement = state.requirements.current;
  if (
    !requirement ||
    state.requirements.isDeleting ||
    requirement.requirement_uid !== state.requirements.deletingUid
  ) {
    return;
  }
  const confirmationName =
    elements.requirementDeleteConfirmation.value.trim();
  if (confirmationName !== String(requirement.title || "").trim()) {
    elements.requirementDeleteConfirmation.setCustomValidity(
      window.WaterfallI18n?.source?.("输入的需求名称不匹配。") ||
        "输入的需求名称不匹配。",
    );
    elements.requirementDeleteConfirmation.reportValidity();
    return;
  }

  state.requirements.isDeleting = true;
  updateRequirementDeleteSubmitState();
  renderContent();
  try {
    await requestJson(
      `/api/requirements/${encodePathPart(requirement.requirement_uid)}`,
      {
        method: "DELETE",
        body: JSON.stringify({
          confirmation_name: confirmationName,
        }),
      },
    );
    elements.requirementDeleteModal.classList.add("hidden");
    elements.requirementDeleteConfirmation.value = "";
    state.requirements.deletingUid = "";
    await loadRequirements();
    setNotice(
      window.WaterfallI18n?.source?.(
        "需求已删除，已生成的计划、脚本、测试集和执行记录已保留。",
      ) || "需求已删除，已生成的计划、脚本、测试集和执行记录已保留。",
      "success",
    );
  } catch (error) {
    const rawMessage =
      error.message ||
      "需求删除失败。";
    const message =
      window.WaterfallI18n?.source?.(rawMessage) || rawMessage;
    elements.requirementDeleteConfirmation.setCustomValidity(message);
    elements.requirementDeleteConfirmation.reportValidity();
    setNotice(message, "error");
  } finally {
    state.requirements.isDeleting = false;
    updateRequirementDeleteSubmitState();
    renderContent();
  }
}

function renderRequirementModuleDetailModal() {
  const moduleUid = state.requirements.detailModuleUid;
  const moduleItem = moduleUid ? getRequirementModuleByUid(moduleUid) : null;
  if (!moduleItem) {
    closeRequirementModuleDetail();
    return;
  }

  const statusInfo = getRequirementModuleStatusInfo(moduleItem.status);
  const generatedPlan = moduleItem.generated_plan || {};
  const logs = state.requirements.modulePlanLogs[moduleItem.module_uid] || "";
  const inventorySummary = summarizeMatchedInventory(moduleItem.matched_inventory) || "未匹配到页面 inventory";
  const questions = moduleItem.open_questions.length ? moduleItem.open_questions.join("；") : "无";
  const isGenerating = state.requirements.generatingModuleUid === moduleItem.module_uid;
  const disableActions = isGenerating || isAnyScriptJobRunning();

  window.WaterfallI18n?.markDynamic?.(elements.requirementModuleDetailTitle);
  elements.requirementModuleDetailTitle.textContent = moduleItem.module_name;
  window.WaterfallI18n?.markDynamic?.(
    elements.requirementModuleDetailSubtitle,
    Boolean(moduleItem.business_goal),
  );
  elements.requirementModuleDetailSubtitle.textContent = moduleItem.business_goal || "未填写业务目标";
  elements.requirementModuleDetailBody.innerHTML = `
    <div class="requirement-module-detail-editor" data-module-uid="${escapeHtml(moduleItem.module_uid)}">
      <div class="requirement-module-detail-status">
        <span class="status-badge ${escapeHtml(statusInfo.className)}">${escapeHtml(statusInfo.label)}</span>
        ${
          moduleItem.confidence === null
            ? ""
            : `<span class="status-badge">置信度 ${(moduleItem.confidence * 100).toFixed(0)}%</span>`
        }
        ${moduleItem.write_risk ? '<span class="status-badge error">写库风险</span>' : ""}
        ${moduleItem.baseline_required ? '<span class="status-badge running">需要基线</span>' : ""}
      </div>
      <div class="requirement-module-grid">
        <label class="form-field">
          <span>模块名</span>
          <input name="module_name" value="${escapeHtml(moduleItem.module_name)}" autocomplete="off" />
        </label>
        <label class="form-field">
          <span>计划名</span>
          <input name="plan_name" value="${escapeHtml(moduleItem.plan_name)}" autocomplete="off" />
        </label>
      </div>
      <label class="form-field">
        <span>业务目标</span>
        <textarea name="business_goal">${escapeHtml(moduleItem.business_goal)}</textarea>
      </label>
      <label class="form-field">
        <span>关键测试点</span>
        <textarea name="test_points">${escapeHtml(formatRequirementList(moduleItem.test_points))}</textarea>
      </label>
      <label class="form-field">
        <span>模块基础语句（不含覆盖模板）</span>
        <textarea name="planner_prompt" class="requirement-prompt-editor">${escapeHtml(moduleItem.planner_prompt)}</textarea>
      </label>
      <div class="requirement-module-detail-actions">
        <button class="secondary-button" type="button" data-action="reset-prompt">重置为中立基础语句</button>
      </div>
      <div class="requirement-module-meta">
        <div><strong>关联需求</strong><span data-i18n-dynamic>${escapeHtml(formatRequirementList(moduleItem.requirement_refs) || "-")}</span></div>
        <div><strong>匹配 inventory</strong><span data-i18n-dynamic>${escapeHtml(inventorySummary)}</span></div>
        <div><strong>不确定点</strong><span data-i18n-dynamic>${escapeHtml(questions)}</span></div>
        ${
          moduleItem.generated_plans.length
            ? `<div><strong>生成计划</strong><span>${moduleItem.generated_plans
                .map((item, index) => {
                  const profile = getCoverageProfile(item.coverage_profile);
                  const templateLabel = window.WaterfallI18n?.t?.("common.templateSource") || "Template source:";
                  const profileLabel = window.WaterfallI18n?.source?.(profile?.label || "核心回归") || profile?.label || "核心回归";
                  const customizedLabel = item.prompt_customized
                    ? ` · ${window.WaterfallI18n?.t?.("common.customized") || "Customized"}`
                    : "";
                  return `<button class="inline-link-button" type="button" data-action="open-generated-plan" data-plan-index="${index}"><span data-i18n-dynamic>${escapeHtml(
                    item.module_name || moduleItem.module_name,
                  )}/${escapeHtml(item.plan_filename || "")}</span> · ${templateLabel} ${escapeHtml(profileLabel)}${customizedLabel}</button>`;
                })
                .join("<br>")}</span></div>`
            : ""
        }
      </div>
      ${
        logs
          ? `<div class="job-output requirement-module-job-output">
              <div class="job-status ${moduleItem.generation_status === "failed" ? "error" : moduleItem.generation_status === "succeeded" ? "success" : ""}">
                ${escapeHtml(moduleItem.generation_error || (moduleItem.generation_status === "succeeded" ? "生成完成" : "生成日志"))}
              </div>
              <pre>${escapeHtml(logs)}</pre>
            </div>`
          : ""
      }
      <div class="requirement-module-detail-actions">
        ${
          generatedPlan.plan_filename
            ? `<button class="secondary-button" type="button" data-action="open-plan">打开计划</button>`
            : ""
        }
        <button class="secondary-button" type="button" data-action="save">保存</button>
        <button class="primary-button" type="button" data-action="generate" ${disableActions ? "disabled" : ""}>${
          isGenerating ? "生成中" : "生成计划"
        }</button>
        <button class="secondary-button danger-button" type="button" data-action="delete" ${disableActions ? "disabled" : ""}>删除</button>
      </div>
    </div>
  `;

  elements.requirementModuleDetailBody.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.dataset.action;
      if (action === "save") {
        saveRequirementModule(moduleUid);
      } else if (action === "delete") {
        deleteRequirementModuleAction(moduleUid);
      } else if (action === "generate") {
        openRequirementPlanGenerationModal(moduleUid);
      } else if (action === "reset-prompt") {
        resetRequirementModuleBasePrompt(moduleUid);
      } else if (action === "open-plan") {
        openRequirementGeneratedPlan(moduleUid);
      } else if (action === "open-generated-plan") {
        openRequirementGeneratedPlan(moduleUid, Number(button.dataset.planIndex) || 0);
      }
    });
  });

  elements.requirementModuleDetailModal.classList.remove("hidden");
}

function getRequirementModulePayloadFromItem(moduleItem) {
  return {
    module_name: moduleItem.module_name || "",
    plan_name: moduleItem.plan_name || moduleItem.module_name || "",
    business_goal: moduleItem.business_goal || "",
    test_points: Array.isArray(moduleItem.test_points) ? moduleItem.test_points : [],
    requirement_refs: moduleItem.requirement_refs || [],
    matched_inventory: moduleItem.matched_inventory || {},
    open_questions: moduleItem.open_questions || [],
    write_risk: Boolean(moduleItem.write_risk),
    baseline_required: Boolean(moduleItem.baseline_required),
    confidence: moduleItem.confidence,
    planner_prompt: moduleItem.planner_prompt || "",
    status: moduleItem.status === "generated" ? "generated" : "confirmed",
  };
}

function getRequirementModuleFormPayload(moduleUid) {
  const existing = getRequirementModuleByUid(moduleUid);
  if (!existing) {
    throw new Error("候选模块不存在。");
  }

  const editor = elements.requirementModuleDetailBody.querySelector(
    `.requirement-module-detail-editor[data-module-uid="${CSS.escape(moduleUid)}"]`,
  );
  if (!editor) {
    return getRequirementModulePayloadFromItem(existing);
  }
  return {
    module_name: editor.querySelector('[name="module_name"]')?.value.trim() || "",
    plan_name: editor.querySelector('[name="plan_name"]')?.value.trim() || "",
    business_goal: editor.querySelector('[name="business_goal"]')?.value.trim() || "",
    test_points: parseTextareaList(editor.querySelector('[name="test_points"]')?.value || ""),
    requirement_refs: existing.requirement_refs || [],
    matched_inventory: existing.matched_inventory || {},
    open_questions: existing.open_questions || [],
    write_risk: Boolean(existing.write_risk),
    baseline_required: Boolean(existing.baseline_required),
    confidence: existing.confidence,
    planner_prompt: editor.querySelector('[name="planner_prompt"]')?.value.trim() || "",
    status: existing.status === "generated" ? "generated" : "confirmed",
  };
}

async function resetRequirementModuleBasePrompt(moduleUid) {
  const requirementUid = state.requirements.selectedUid;
  if (!requirementUid || !moduleUid) {
    return;
  }
  if (!window.confirm("将根据当前模块字段重建覆盖中立的基础语句，现有基础语句会被替换。是否继续？")) {
    return;
  }
  try {
    const data = await requestJson(
      `/api/requirements/${encodePathPart(requirementUid)}/modules/${encodePathPart(moduleUid)}`,
      {
        method: "PUT",
        body: JSON.stringify({ ...getRequirementModuleFormPayload(moduleUid), reset_planner_prompt: true }),
      },
    );
    const updated = normalizeRequirementModule(data.module);
    if (updated) {
      mergeRequirementModuleUpdate(updated);
      state.requirements.detailModuleUid = updated.module_uid;
    }
    renderRequirementModuleDetailModal();
    setNotice("已重置为覆盖中立的模块基础语句。", "success");
  } catch (error) {
    setNotice(error.message, "error");
  }
}

async function saveRequirementModule(moduleUid, { silent = false } = {}) {
  const requirementUid = state.requirements.selectedUid;
  if (!requirementUid || !moduleUid) {
    return null;
  }
  const payload = getRequirementModuleFormPayload(moduleUid);
  if (!payload.module_name) {
    setNotice("模块名不能为空。", "error");
    return null;
  }
  if (!payload.planner_prompt) {
    setNotice("planner prompt 不能为空。", "error");
    return null;
  }

  const data = await requestJson(
    `/api/requirements/${encodePathPart(requirementUid)}/modules/${encodePathPart(moduleUid)}`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    },
  );
  const updated = mergeRequirementModuleUpdate(data.module);
  if (updated) {
    renderContent();
  }
  if (!silent) {
    setNotice("候选模块已保存。", "success");
  }
  return updated;
}

async function deleteRequirementModuleAction(moduleUid) {
  const requirementUid = state.requirements.selectedUid;
  if (!requirementUid || !moduleUid) {
    return;
  }
  if (!window.confirm("确认删除这个候选模块？")) {
    return;
  }
  try {
    await requestJson(`/api/requirements/${encodePathPart(requirementUid)}/modules/${encodePathPart(moduleUid)}`, {
      method: "DELETE",
    });
    state.requirements.modules = state.requirements.modules.filter((item) => item.module_uid !== moduleUid);
    if (state.requirements.detailModuleUid === moduleUid) {
      closeRequirementModuleDetail();
    }
    renderSideList();
    renderContent();
    setNotice("候选模块已删除。", "success");
  } catch (error) {
    setNotice(error.message, "error");
  }
}

async function analyzeSelectedRequirement() {
  const requirementUid = state.requirements.selectedUid;
  if (!requirementUid || state.requirements.analysisRunning) {
    return;
  }
  state.requirements.analysisRunning = true;
  state.requirements.analysisLogs = "";
  state.requirements.analysisStatus = "running";
  state.requirements.analysisError = "";
  renderContent();
  try {
    const response = await fetch(`/api/requirements/${encodePathPart(requirementUid)}/analysis-stream`, {
      method: "POST",
      headers: getProjectRequestHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({}),
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
    await readRequirementAnalysisStream(response);
    await loadRequirements();
    await selectRequirement(requirementUid, true);
  } catch (error) {
    state.requirements.analysisStatus = "failed";
    state.requirements.analysisError = error.message;
    state.requirements.analysisLogs += `${state.requirements.analysisLogs.endsWith("\n") ? "" : "\n"}${error.message}\n`;
    setNotice(error.message, "error");
  } finally {
    state.requirements.analysisRunning = false;
    renderContent();
  }
}

async function readRequirementAnalysisStream(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
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
      handleRequirementAnalysisEvent(parseSseBlock(block));
      boundaryIndex = buffer.indexOf("\n\n");
    }
  }
  buffer += decoder.decode();
  handleRequirementAnalysisEvent(parseSseBlock(buffer.trim()));
}

function handleRequirementAnalysisEvent(event) {
  if (!event) {
    return;
  }
  const data = event.data || {};
  if (event.event === "status") {
    state.requirements.analysisStatus = data.status || state.requirements.analysisStatus;
    state.requirements.analysisError = data.error || "";
    if (Array.isArray(data.modules)) {
      state.requirements.modules = data.modules.map(normalizeRequirementModule).filter(Boolean);
    }
  } else if (event.event === "log") {
    state.requirements.analysisLogs += data.message ? `${data.message}\n` : "";
  } else if (event.event === "delta") {
    state.requirements.analysisLogs += data.text ? `${data.text}\n` : "";
  } else if (event.event === "done") {
    state.requirements.analysisStatus = data.ok === false ? "failed" : "succeeded";
    state.requirements.analysisError = data.error || "";
    if (Array.isArray(data.modules)) {
      state.requirements.modules = data.modules.map(normalizeRequirementModule).filter(Boolean);
    }
    if (state.requirements.analysisStatus === "succeeded") {
      state.requirements.activeTab = REQUIREMENT_VIEW_TAB.MODULES;
    }
  }
  renderContent();
}

async function importInventoryFromDefaultDoc() {
  try {
    elements.importInventoryButton.disabled = true;
    const data = await requestJson("/api/page-inventory/import-from-doc", {
      method: "POST",
      body: JSON.stringify({}),
    });
    setNotice(`已导入 ${data.count || 0} 条页面 inventory。`, "success");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    elements.importInventoryButton.disabled = false;
  }
}

function appendRequirementModulePlanLog(moduleUid, text) {
  if (!text) {
    return;
  }
  state.requirements.modulePlanLogs[moduleUid] = `${state.requirements.modulePlanLogs[moduleUid] || ""}${text}`;
}

async function generateRequirementModulePlan(moduleUid) {
  await openRequirementPlanGenerationModal(moduleUid);
}

async function readRequirementPlanStream(response, moduleUid) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
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
      handleRequirementPlanStreamEvent(parseSseBlock(block), moduleUid);
      boundaryIndex = buffer.indexOf("\n\n");
    }
  }
  buffer += decoder.decode();
  handleRequirementPlanStreamEvent(parseSseBlock(buffer.trim()), moduleUid);
}

function handleRequirementPlanStreamEvent(event, moduleUid) {
  if (!event) {
    return;
  }
  const moduleItem = state.requirements.modules.find((item) => item.module_uid === moduleUid);
  const data = event.data || {};
  if (event.event === "status") {
    if (moduleItem) {
      moduleItem.generation_status = data.status || moduleItem.generation_status || "running";
      moduleItem.generation_error = data.error || "";
    }
  } else if (event.event === "log") {
    appendRequirementModulePlanLog(moduleUid, data.message ? `${data.message}\n` : "");
  } else if (event.event === "delta") {
    appendRequirementModulePlanLog(moduleUid, data.text || "");
  } else if (event.event === "done") {
    if (moduleItem) {
      moduleItem.generation_status = data.ok === false ? "failed" : "succeeded";
      moduleItem.generation_error = data.error || "";
      const updated = normalizeRequirementModule(data.requirement_module);
      if (updated) {
        Object.assign(moduleItem, updated);
      }
    }
  }
  renderContent();
}

async function openRequirementGeneratedPlan(moduleUid, planIndex = 0) {
  const moduleItem = state.requirements.modules.find((item) => item.module_uid === moduleUid);
  const generatedPlan = moduleItem?.generated_plans?.[planIndex] || moduleItem?.generated_plan;
  if (!generatedPlan?.module_name || !generatedPlan?.plan_filename) {
    setNotice("该候选模块还没有生成测试计划。", "error");
    return;
  }
  closeRequirementModuleDetail();
  state.activeSection = SECTION.PLANS;
  state.plans.selectedModule = generatedPlan.module_name;
  state.plans.selectedPlanFile = generatedPlan.plan_filename;
  state.plans.expandedModules.add(generatedPlan.module_name);
  state.plans.activeTab = PLAN_VIEW_TAB.CONTENT;
  persistViewState();
  renderSideList();
  await loadPlanModules();
  await selectPlan(generatedPlan.module_name, generatedPlan.plan_filename, true);
}

async function loadRequirements() {
  setNotice("");
  setLoading(true);

  try {
    const data = await requestJson("/api/requirements");
    state.requirements.items = (data.requirements || []).map(normalizeRequirement).filter(Boolean);
    if (!state.requirements.items.length) {
      state.requirements.selectedUid = null;
      state.requirements.current = null;
      state.requirements.markdown = "";
      state.requirements.html = "";
      state.requirements.modules = [];
      state.requirements.activeTab = REQUIREMENT_VIEW_TAB.PREVIEW;
      state.requirements.detailModuleUid = "";
      state.requirements.bulkSelectionMode = false;
      state.requirements.selectedModuleUids.clear();
      renderSideList();
      renderContent();
      return;
    }

    const selected =
      state.requirements.items.find((item) => item.requirement_uid === state.requirements.selectedUid) ||
      state.requirements.items[0];
    renderSideList();
    await selectRequirement(selected.requirement_uid, true);
  } catch (error) {
    state.requirements.items = [];
    state.requirements.selectedUid = null;
    state.requirements.current = null;
    state.requirements.markdown = "";
    state.requirements.html = "";
    state.requirements.modules = [];
    state.requirements.activeTab = REQUIREMENT_VIEW_TAB.PREVIEW;
    state.requirements.detailModuleUid = "";
    state.requirements.bulkSelectionMode = false;
    state.requirements.selectedModuleUids.clear();
    renderSideList();
    renderContent();
    setNotice(error.message, "error");
  } finally {
    setLoading(false);
  }
}

async function selectRequirement(requirementUid, skipConfirm = false) {
  const sameSelection = state.requirements.selectedUid === requirementUid;
  if (!skipConfirm && sameSelection) {
    return;
  }
  if (!skipConfirm && !confirmDiscardEdit()) {
    return;
  }

  state.activeSection = SECTION.REQUIREMENTS;
  state.requirements.selectedUid = requirementUid;
  if (!sameSelection) {
    state.requirements.analysisLogs = "";
    state.requirements.analysisStatus = "";
    state.requirements.analysisError = "";
    state.requirements.activeTab = REQUIREMENT_VIEW_TAB.PREVIEW;
    state.requirements.detailModuleUid = "";
    state.requirements.bulkSelectionMode = false;
    state.requirements.selectedModuleUids.clear();
  }
  state.isEditing = false;
  persistViewState();
  setNotice("");
  setLoading(true);
  renderSideList();

  try {
    const data = await requestJson(`/api/requirements/${encodePathPart(requirementUid)}`);
    const requirement = normalizeRequirement(data.requirement);
    state.requirements.current = requirement;
    state.requirements.markdown = requirement?.markdown || "";
    state.requirements.html = requirement?.html || "";
    state.requirements.modules = (data.modules || []).map(normalizeRequirementModule).filter(Boolean);
    renderSideList();
    renderContent();
  } catch (error) {
    state.requirements.current = null;
    state.requirements.markdown = "";
    state.requirements.html = "";
    state.requirements.modules = [];
    state.requirements.detailModuleUid = "";
    state.requirements.bulkSelectionMode = false;
    state.requirements.selectedModuleUids.clear();
    renderContent();
    setNotice(error.message, "error");
  } finally {
    setLoading(false);
  }
}

async function refreshRequirementModules() {
  const requirementUid = state.requirements.selectedUid;
  if (!requirementUid) {
    return;
  }
  const data = await requestJson(`/api/requirements/${encodePathPart(requirementUid)}/modules`);
  state.requirements.modules = (data.modules || []).map(normalizeRequirementModule).filter(Boolean);
  const selected = state.requirements.items.find((item) => item.requirement_uid === requirementUid);
  if (selected) {
    selected.module_count = state.requirements.modules.length;
  }
  renderSideList();
  renderContent();
}

async function uploadRequirementFile(file) {
  if (!file) {
    return;
  }
  if (!file.name.toLowerCase().endsWith(".md")) {
    setNotice("第一阶段只支持上传 Markdown .md 文件。", "error");
    return;
  }

  const formData = new FormData();
  formData.append("file", file);
  setLoading(true);
  setNotice("");
  try {
    const response = await fetch("/api/requirements/upload", {
      method: "POST",
      headers: getProjectRequestHeaders(),
      body: formData,
    });
    let data;
    try {
      data = await response.json();
    } catch (error) {
      data = { error: `接口返回不是 JSON: ${error}` };
    }
    if (!response.ok) {
      throw new Error(data.error || `上传失败: ${response.status}`);
    }
    const requirement = normalizeRequirement(data.requirement);
    await loadRequirements();
    if (requirement?.requirement_uid) {
      await selectRequirement(requirement.requirement_uid, true);
    }
    setNotice("需求上传成功。", "success");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    elements.requirementFileInput.value = "";
    setLoading(false);
  }
}

return {
  switchRequirementViewTab,
  renderRequirementList,
  enterRequirementModuleBulkMode,
  cancelRequirementModuleBulkMode,
  toggleRequirementModuleSelectAll,
  deleteSelectedRequirementModules,
  renderRequirementBatchPromptState,
  openRequirementBatchPlanModal,
  closeRequirementBatchPlanModal,
  changeRequirementBatchCoverageProfile,
  resetRequirementBatchCoveragePrompt,
  generateSelectedRequirementModulePlans,
  renderRequirementsPanel,
  getRequirementModuleByUid,
  mergeRequirementModuleUpdate,
  closeRequirementModuleDetail,
  openRequirementDeleteModal,
  closeRequirementDeleteModal,
  updateRequirementDeleteSubmitState,
  submitRequirementDelete,
  saveRequirementModule,
  analyzeSelectedRequirement,
  importInventoryFromDefaultDoc,
  loadRequirements,
  selectRequirement,
  refreshRequirementModules,
  uploadRequirementFile,
  // State and payload operations are exposed for focused VM regression tests.
  getRequirementModuleStatusInfo,
  formatRequirementList,
  parseTextareaList,
  summarizeMatchedInventory,
  getRequirementPlanGenerationBatchKey,
  getRequirementModulePlanTargetPath,
  setRequirementPlanGenerationBatch,
  setRequirementPlanGenerationBatchItem,
  pruneSelectedRequirementModuleUids,
  handleRequirementBatchPlanGenerationEvent,
  getRequirementModulePayloadFromItem,
  getRequirementModuleFormPayload,
  handleRequirementAnalysisEvent,
  handleRequirementPlanStreamEvent,
  requirementText,
  renderRequirementModuleCounts,
};
}

window.createRequirementsFeature = createRequirementsFeature;
