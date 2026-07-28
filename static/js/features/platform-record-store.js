function createPlatformRecordStore(deps = {}) {
  const {
    fetchImpl,
    getProjectHeaders,
    safeJsonParse,
    readStorageItem,
    writeStorageItem,
    normalizeTestSuiteItem,
    getSuiteScriptKey,
    finalizeTestSuiteScriptResults,
    isPlainObject,
    executionMode,
    storageKeys,
    logLimit = 120000,
    saveDebounceMs = 300,
    timerHost = window,
  } = deps;

  const pendingSaves = new Map();
  let persistenceEnabled = true;

  function cloneRecordForSave(record) {
    return safeJsonParse(JSON.stringify(record || {}), {});
  }

  function savePlatformRecord(bucket, recordKey, record) {
    return fetchImpl(`/api/platform-records/${encodeURIComponent(bucket)}/${encodeURIComponent(recordKey)}`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        ...getProjectHeaders(),
      },
      body: JSON.stringify({ record }),
    });
  }

  function queuePlatformRecordSave(bucket, recordKey, record) {
    if (!persistenceEnabled || !bucket || !recordKey || !record || typeof record !== "object") {
      return;
    }

    const pendingKey = `${bucket}\n${recordKey}`;
    const previous = pendingSaves.get(pendingKey);
    if (previous?.timer) {
      timerHost.clearTimeout(previous.timer);
    }

    const snapshot = cloneRecordForSave(record);
    const timer = timerHost.setTimeout(async () => {
      pendingSaves.delete(pendingKey);
      try {
        const response = await savePlatformRecord(bucket, recordKey, snapshot);
        if (!response.ok) {
          // localStorage remains the fallback when MySQL is unavailable.
          return;
        }
      } catch (error) {
        // Persistence is deliberately non-blocking; a later update will retry.
      }
    }, saveDebounceMs);

    pendingSaves.set(pendingKey, { timer, snapshot });
  }

  function setPersistenceEnabled(enabled) {
    persistenceEnabled = Boolean(enabled);
  }

  function normalizeExecutionModeValue(value) {
    return value === executionMode.SERIAL_PER_FILE ? executionMode.SERIAL_PER_FILE : executionMode.BATCH;
  }

  function getExecutionModeLabel(value) {
    return normalizeExecutionModeValue(value) === executionMode.SERIAL_PER_FILE
      ? "按文件串行执行"
      : "当前批量执行";
  }

  function normalizeLogs(record) {
    const logs =
      typeof record.logs === "string"
        ? record.logs
        : typeof record.output === "string"
          ? record.output
          : "";
    return logs.length > logLimit ? logs.slice(-logLimit) : logs;
  }

  function normalizeScriptRunRecord(record) {
    if (!record || typeof record !== "object") {
      return null;
    }

    return {
      status: record.status === "running" ? "failed" : record.status || "succeeded",
      command: typeof record.command === "string" ? record.command : "",
      logs: normalizeLogs(record),
      returncode: Object.prototype.hasOwnProperty.call(record, "returncode") ? record.returncode : undefined,
      video: record.video && typeof record.video === "object" ? record.video : null,
      report: record.report && typeof record.report === "object" ? record.report : null,
      video_error:
        record.status === "running"
          ? "页面刷新后实时连接已中断，请重新执行脚本。"
          : typeof record.video_error === "string"
            ? record.video_error
            : "",
      report_error: typeof record.report_error === "string" ? record.report_error : "",
      updated_at: Number(record.updated_at) || Date.now(),
    };
  }

  function normalizeScriptRepairRecord(record, staleRunning = false) {
    if (!record || typeof record !== "object") {
      return null;
    }

    const status = staleRunning && record.status === "running" ? "failed" : record.status || "idle";
    const finishedAt =
      staleRunning && record.status === "running"
        ? Number(record.finished_at) || Number(record.updated_at) || Date.now()
        : Number(record.finished_at) || null;
    const error =
      staleRunning && record.status === "running"
        ? "页面刷新后实时连接已中断，请重新修复脚本。"
        : typeof record.error === "string"
          ? record.error
          : "";

    return {
      status,
      prompt_fixed: typeof record.prompt_fixed === "string" ? record.prompt_fixed : "",
      prompt_note: typeof record.prompt_note === "string" ? record.prompt_note : "",
      prompt: typeof record.prompt === "string" ? record.prompt : "",
      logs: normalizeLogs(record),
      error,
      target_path: typeof record.target_path === "string" ? record.target_path : "",
      started_at: Number(record.started_at) || null,
      finished_at: finishedAt,
      updated_at: Number(record.updated_at) || Date.now(),
    };
  }

  function normalizeModuleExecutionRecord(record, staleRunning = false) {
    if (!record || typeof record !== "object") {
      return null;
    }

    const filenames = Array.isArray(record.filenames)
      ? record.filenames.filter((filename) => typeof filename === "string" && filename)
      : [];
    const scriptResults =
      record.script_results && typeof record.script_results === "object" && !Array.isArray(record.script_results)
        ? Object.entries(record.script_results).reduce((results, [filename, value]) => {
            if (typeof filename === "string" && filename) {
              results[filename] = typeof value === "string" ? value : "";
            }
            return results;
          }, {})
        : {};
    const status = staleRunning && record.status === "running" ? "failed" : record.status || "idle";
    const finishedAt =
      staleRunning && record.status === "running"
        ? Number(record.finished_at) || Number(record.updated_at) || Date.now()
        : Number(record.finished_at) || null;
    const error =
      staleRunning && record.status === "running"
        ? "页面刷新后批量执行连接已中断，请重新批量执行。"
        : typeof record.error === "string"
          ? record.error
          : "";

    return {
      status,
      module_name: typeof record.module_name === "string" ? record.module_name : "",
      filenames,
      execution_mode: normalizeExecutionModeValue(record.execution_mode),
      command: typeof record.command === "string" ? record.command : "",
      logs: normalizeLogs(record),
      returncode: Object.prototype.hasOwnProperty.call(record, "returncode") ? record.returncode : undefined,
      total_files: Number(record.total_files) || filenames.length || 0,
      completed_files: Number(record.completed_files) || 0,
      report: record.report && typeof record.report === "object" ? record.report : null,
      report_error: typeof record.report_error === "string" ? record.report_error : "",
      script_results: scriptResults,
      error,
      started_at: Number(record.started_at) || null,
      finished_at: finishedAt,
      updated_at: Number(record.updated_at) || Date.now(),
    };
  }

  function normalizeTestSuiteExecutionRecord(record, staleRunning = false) {
    if (!record || typeof record !== "object") {
      return null;
    }

    const items = Array.isArray(record.items)
      ? record.items
          .map(normalizeTestSuiteItem)
          .filter(Boolean)
          .map((item) => ({
            ...item,
            key: getSuiteScriptKey(item.module_name, item.filename),
          }))
      : [];
    let scriptResults =
      record.script_results && typeof record.script_results === "object" && !Array.isArray(record.script_results)
        ? Object.entries(record.script_results).reduce((results, [key, value]) => {
            if (typeof key === "string" && key) {
              results[key] = typeof value === "string" ? value : "";
            }
            return results;
          }, {})
        : {};
    if (staleRunning && record.status === "running") {
      scriptResults = finalizeTestSuiteScriptResults(items, scriptResults, "interrupted");
    }
    const status = staleRunning && record.status === "running" ? "failed" : record.status || "idle";
    const finishedAt =
      staleRunning && record.status === "running"
        ? Number(record.finished_at) || Number(record.updated_at) || Date.now()
        : Number(record.finished_at) || null;
    const error =
      staleRunning && record.status === "running"
        ? "页面刷新后测试集执行连接已中断，请重新执行。"
        : typeof record.error === "string"
          ? record.error
          : "";

    return {
      status,
      run_id: typeof record.run_id === "string" ? record.run_id : "",
      job_id: typeof record.job_id === "string" ? record.job_id : "",
      suite_id: typeof record.suite_id === "string" ? record.suite_id : "",
      suite_name: typeof record.suite_name === "string" ? record.suite_name : "",
      items,
      execution_mode: normalizeExecutionModeValue(record.execution_mode),
      command: typeof record.command === "string" ? record.command : "",
      logs: normalizeLogs(record),
      returncode: Object.prototype.hasOwnProperty.call(record, "returncode") ? record.returncode : undefined,
      total_files: Number(record.total_files) || items.length || 0,
      completed_files: Number(record.completed_files) || 0,
      report: record.report && typeof record.report === "object" ? record.report : null,
      report_error: typeof record.report_error === "string" ? record.report_error : "",
      script_results: scriptResults,
      error,
      started_at: Number(record.started_at) || null,
      finished_at: finishedAt,
      updated_at: Number(record.updated_at) || Date.now(),
    };
  }

  function normalizeBatchItems(rawItems, staleRunning, itemKey, extraFields = () => ({})) {
    const items = rawItems && typeof rawItems === "object" && !Array.isArray(rawItems) ? rawItems : {};
    return Object.entries(items).reduce((nextItems, [key, item]) => {
      if (!key || !item || typeof item !== "object") {
        return nextItems;
      }

      const wasInterrupted = staleRunning && (item.status === "running" || item.status === "queued");
      const logs = typeof item.logs === "string" ? item.logs : "";
      nextItems[key] = {
        status: wasInterrupted ? "failed" : item.status || "queued",
        logs: logs.length > logLimit ? logs.slice(-logLimit) : logs,
        error: wasInterrupted
          ? "页面刷新后连接已中断，请重新执行。"
          : typeof item.error === "string"
            ? item.error
            : "",
        ...extraFields(item, key, wasInterrupted),
        started_at: Number(item.started_at) || null,
        finished_at: wasInterrupted
          ? Number(item.finished_at) || Number(item.updated_at) || Date.now()
          : Number(item.finished_at) || null,
        updated_at: Number(item.updated_at) || Date.now(),
      };
      if (itemKey) {
        nextItems[key][itemKey] = typeof item[itemKey] === "string" ? item[itemKey] : key;
      }
      return nextItems;
    }, {});
  }

  function normalizeModuleRepairBatch(record, staleRunning = false) {
    if (!record || typeof record !== "object") {
      return null;
    }

    const filenames = Array.isArray(record.filenames)
      ? record.filenames.filter((filename) => typeof filename === "string" && filename)
      : [];
    const items = normalizeBatchItems(record.items, staleRunning, null, (item, _key, wasInterrupted) => ({
      error: wasInterrupted
        ? "页面刷新后批量修复连接已中断，请重新批量修复。"
        : typeof item.error === "string"
          ? item.error
          : "",
    }));

    return {
      status: staleRunning && record.status === "running" ? "failed" : record.status || "idle",
      module_name: typeof record.module_name === "string" ? record.module_name : "",
      filenames,
      active_filename: typeof record.active_filename === "string" ? record.active_filename : "",
      expanded_filename: typeof record.expanded_filename === "string" ? record.expanded_filename : "",
      items,
      started_at: Number(record.started_at) || null,
      finished_at:
        staleRunning && record.status === "running"
          ? Number(record.finished_at) || Number(record.updated_at) || Date.now()
          : Number(record.finished_at) || null,
      updated_at: Number(record.updated_at) || Date.now(),
    };
  }

  function normalizePlanScriptGenerationBatch(record, staleRunning = false) {
    if (!record || typeof record !== "object") {
      return null;
    }

    const planFilenames = Array.isArray(record.plan_filenames)
      ? record.plan_filenames.filter((filename) => typeof filename === "string" && filename)
      : [];
    const items = normalizeBatchItems(record.items, staleRunning, null, (item, _key, wasInterrupted) => ({
      error: wasInterrupted
        ? "页面刷新后批量生成脚本连接已中断，请重新批量生成。"
        : typeof item.error === "string"
          ? item.error
          : "",
      target_path: typeof item.target_path === "string" ? item.target_path : "",
      script_filename: typeof item.script_filename === "string" ? item.script_filename : "",
    }));

    return {
      status: staleRunning && record.status === "running" ? "failed" : record.status || "idle",
      module_name: typeof record.module_name === "string" ? record.module_name : "",
      plan_filenames: planFilenames,
      active_plan_filename: typeof record.active_plan_filename === "string" ? record.active_plan_filename : "",
      expanded_plan_filename:
        typeof record.expanded_plan_filename === "string" ? record.expanded_plan_filename : "",
      items,
      started_at: Number(record.started_at) || null,
      finished_at:
        staleRunning && record.status === "running"
          ? Number(record.finished_at) || Number(record.updated_at) || Date.now()
          : Number(record.finished_at) || null,
      updated_at: Number(record.updated_at) || Date.now(),
    };
  }

  function normalizeRequirementPlanGenerationBatch(record, staleRunning = false) {
    if (!record || typeof record !== "object") {
      return null;
    }

    const moduleUids = Array.isArray(record.module_uids)
      ? record.module_uids.filter((uid) => typeof uid === "string" && uid)
      : [];
    const items = normalizeBatchItems(record.items, staleRunning, "module_uid", (item, _key, wasInterrupted) => ({
      error: wasInterrupted
        ? "页面刷新后批量生成计划连接已中断，请重新批量生成。"
        : typeof item.error === "string"
          ? item.error
          : "",
      module_name: typeof item.module_name === "string" ? item.module_name : "",
      plan_name: typeof item.plan_name === "string" ? item.plan_name : "",
      plan_filename: typeof item.plan_filename === "string" ? item.plan_filename : "",
      prompt: typeof item.prompt === "string" ? item.prompt : "",
      target_path: typeof item.target_path === "string" ? item.target_path : "",
      generated_plan: isPlainObject(item.generated_plan) ? item.generated_plan : null,
    }));

    return {
      status: staleRunning && record.status === "running" ? "failed" : record.status || "idle",
      requirement_uid: typeof record.requirement_uid === "string" ? record.requirement_uid : "",
      requirement_title: typeof record.requirement_title === "string" ? record.requirement_title : "",
      module_uids: moduleUids,
      active_module_uid: typeof record.active_module_uid === "string" ? record.active_module_uid : "",
      expanded_module_uid: typeof record.expanded_module_uid === "string" ? record.expanded_module_uid : "",
      items,
      started_at: Number(record.started_at) || null,
      finished_at:
        staleRunning && record.status === "running"
          ? Number(record.finished_at) || Number(record.updated_at) || Date.now()
          : Number(record.finished_at) || null,
      updated_at: Number(record.updated_at) || Date.now(),
    };
  }

  function normalizePlanGenerationRecord(record, staleRunning = false) {
    if (!record || typeof record !== "object") {
      return null;
    }

    return {
      status: staleRunning && record.status === "running" ? "failed" : record.status || "idle",
      module_name: typeof record.module_name === "string" ? record.module_name : "",
      plan_filename: typeof record.plan_filename === "string" ? record.plan_filename : "",
      prompt: typeof record.prompt === "string" ? record.prompt : "",
      logs: normalizeLogs(record),
      error:
        staleRunning && record.status === "running"
          ? "页面刷新后实时连接已中断，请重新生成测试计划。"
          : typeof record.error === "string"
            ? record.error
            : "",
      target_path: typeof record.target_path === "string" ? record.target_path : "",
      started_at: Number(record.started_at) || null,
      finished_at:
        staleRunning && record.status === "running"
          ? Number(record.finished_at) || Number(record.updated_at) || Date.now()
          : Number(record.finished_at) || null,
      updated_at: Number(record.updated_at) || Date.now(),
    };
  }

  function normalizePlanScriptGenerationRecord(record, staleRunning = false) {
    if (!record || typeof record !== "object") {
      return null;
    }

    const promptFixed = typeof record.prompt_fixed === "string" ? record.prompt_fixed : "";
    const promptNote = typeof record.prompt_note === "string" ? record.prompt_note : "";
    return {
      status: staleRunning && record.status === "running" ? "failed" : record.status || "idle",
      module_name: typeof record.module_name === "string" ? record.module_name : "",
      plan_filename: typeof record.plan_filename === "string" ? record.plan_filename : "",
      prompt_fixed: promptFixed,
      prompt_note: promptNote,
      prompt:
        typeof record.prompt === "string" ? record.prompt : `${promptFixed.trim()}\n${promptNote.trim()}`.trim(),
      logs: normalizeLogs(record),
      error:
        staleRunning && record.status === "running"
          ? "页面刷新后实时连接已中断，请重新生成脚本。"
          : typeof record.error === "string"
            ? record.error
            : "",
      target_path: typeof record.target_path === "string" ? record.target_path : "",
      started_at: Number(record.started_at) || null,
      finished_at:
        staleRunning && record.status === "running"
          ? Number(record.finished_at) || Number(record.updated_at) || Date.now()
          : Number(record.finished_at) || null,
      updated_at: Number(record.updated_at) || Date.now(),
    };
  }

  function loadRecordMap(storageKey, normalize, transform = null) {
    const parsed = safeJsonParse(readStorageItem(storageKey), {});
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {};
    }

    return Object.entries(parsed).reduce((records, [key, record]) => {
      const normalized = normalize(record);
      if (normalized) {
        const next = typeof transform === "function" ? transform(key, normalized) : { key, record: normalized };
        records[next.key] = next.record;
      }
      return records;
    }, {});
  }

  function loadScriptRunRecordsFromStorage() {
    return loadRecordMap(storageKeys.SCRIPT_RUN, normalizeScriptRunRecord);
  }

  function loadScriptRepairRecordsFromStorage() {
    return loadRecordMap(storageKeys.SCRIPT_REPAIR, (record) => normalizeScriptRepairRecord(record, true));
  }

  function loadModuleExecutionRecordsFromStorage() {
    return loadRecordMap(storageKeys.MODULE_EXECUTION, (record) => normalizeModuleExecutionRecord(record, true));
  }

  function loadTestSuiteExecutionRecordsFromStorage() {
    return loadRecordMap(storageKeys.TEST_SUITE_EXECUTION, (record) =>
      normalizeTestSuiteExecutionRecord(record, true),
    );
  }

  function loadModuleRepairBatchesFromStorage() {
    return loadRecordMap(storageKeys.MODULE_REPAIR_BATCH, (record) => normalizeModuleRepairBatch(record, true));
  }

  function loadPlanScriptGenerationBatchesFromStorage() {
    return loadRecordMap(storageKeys.PLAN_SCRIPT_GENERATION_BATCH, (record) =>
      normalizePlanScriptGenerationBatch(record, true),
    );
  }

  function loadRequirementPlanGenerationBatchesFromStorage() {
    return loadRecordMap(storageKeys.REQUIREMENT_PLAN_GENERATION_BATCH, (record) =>
      normalizeRequirementPlanGenerationBatch(record, true),
    );
  }

  function normalizeLegacyPlanRecordKey(key, normalized) {
    const recordKey = key.includes("/") ? key : `${key}/${key}.md`;
    if (!normalized.plan_filename) {
      normalized.plan_filename = key.includes("/") ? key.slice(key.indexOf("/") + 1) : `${key}.md`;
    }
    return { key: recordKey, record: normalized };
  }

  function loadPlanGenerationRecordsFromStorage() {
    return loadRecordMap(
      storageKeys.PLAN_GENERATION,
      (record) => normalizePlanGenerationRecord(record, true),
      normalizeLegacyPlanRecordKey,
    );
  }

  function loadPlanScriptGenerationRecordsFromStorage() {
    return loadRecordMap(
      storageKeys.SCRIPT_GENERATION,
      (record) => normalizePlanScriptGenerationRecord(record, true),
      normalizeLegacyPlanRecordKey,
    );
  }

  function persistRecordMap({ records, normalize, storageKey, bucket, recordKey = null }) {
    const normalizedRecords = Object.entries(records).reduce((nextRecords, [key, record]) => {
      const normalized = normalize(record);
      if (normalized) {
        nextRecords[key] = normalized;
      }
      return nextRecords;
    }, {});

    writeStorageItem(storageKey, JSON.stringify(normalizedRecords));
    if (recordKey && normalizedRecords[recordKey]) {
      queuePlatformRecordSave(bucket, recordKey, normalizedRecords[recordKey]);
    }
    return normalizedRecords;
  }

  async function hydrate({ requestJson, descriptors }) {
    let data;
    try {
      data = await requestJson("/api/platform-records");
    } catch (error) {
      return false;
    }

    if (data.enabled === false) {
      setPersistenceEnabled(false);
      return false;
    }
    setPersistenceEnabled(true);

    const records = data.records || {};
    descriptors.forEach(({ remoteKey, storageKey, apply }) => {
      const record = records[remoteKey];
      if (!isPlainObject(record) || !Object.keys(record).length) {
        return;
      }
      writeStorageItem(storageKey, JSON.stringify(record));
      apply(record);
    });
    return true;
  }

  return {
    queuePlatformRecordSave,
    setPersistenceEnabled,
    normalizeExecutionModeValue,
    getExecutionModeLabel,
    normalizeScriptRunRecord,
    normalizeScriptRepairRecord,
    normalizeModuleExecutionRecord,
    normalizeTestSuiteExecutionRecord,
    normalizeModuleRepairBatch,
    normalizePlanScriptGenerationBatch,
    normalizeRequirementPlanGenerationBatch,
    normalizePlanGenerationRecord,
    normalizePlanScriptGenerationRecord,
    loadScriptRunRecordsFromStorage,
    loadScriptRepairRecordsFromStorage,
    loadModuleExecutionRecordsFromStorage,
    loadTestSuiteExecutionRecordsFromStorage,
    loadModuleRepairBatchesFromStorage,
    loadPlanScriptGenerationBatchesFromStorage,
    loadRequirementPlanGenerationBatchesFromStorage,
    loadPlanGenerationRecordsFromStorage,
    loadPlanScriptGenerationRecordsFromStorage,
    persistRecordMap,
    hydrate,
  };
}

window.createPlatformRecordStore = createPlatformRecordStore;
