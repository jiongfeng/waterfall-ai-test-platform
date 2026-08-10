function getAgentPlanModuleProgress(step) {
  const asArray = (value) => (Array.isArray(value) ? value : []);
  const counts = step?.counts || {};
  const output = step?.output || {};
  const inputModules = asArray(step?.input?.modules);
  const moduleAliases = new Map();

  inputModules.forEach((item) => {
    const moduleUid = String(item?.module_uid || "").trim();
    const moduleName = String(item?.module_name || "").trim();
    const canonical = moduleUid || moduleName;
    if (moduleUid) moduleAliases.set(moduleUid, canonical);
    if (moduleName) moduleAliases.set(moduleName, canonical);
  });

  const moduleKey = (item) => {
    const moduleUid = String(item?.module_uid || "").trim();
    const moduleName = String(item?.module_name || "").trim();
    return moduleAliases.get(moduleUid) || moduleAliases.get(moduleName) || moduleUid || moduleName;
  };
  const completedModules = new Set(
    [...asArray(output.plans), ...asArray(output.failures), ...asArray(output.skipped)]
      .map(moduleKey)
      .filter(Boolean),
  );
  const total = Math.max(Number(counts.modules) || inputModules.length, completedModules.size);
  if (!total) return null;

  let complete = completedModules.size;
  if (step?.status === "succeeded") {
    complete = total;
  } else if (!complete) {
    const generated = Number(counts.generated) || 0;
    const failed = Number(counts.failed) || 0;
    const skipped = Number(counts.skipped) || 0;
    const generatedModules = generated <= total ? generated : 0;
    complete = Math.min(total, generatedModules + failed + skipped);
  }
  return { complete, total };
}

function localizeAgentText(value) {
  return window.WaterfallI18n?.source(value) || value;
}

function localizeAgentLog(value) {
  return window.WaterfallI18n?.log(value) || value;
}

function localizedAgentCount(label, count) {
  if (window.WaterfallI18n?.getLocale?.() !== "en") return `${label} ${count}`;
  return { 通过: `${count} passed`, 失败: `${count} failed`, 总数: `${count} total` }[label] || `${label} ${count}`;
}

function agentPlanModuleProgressText(step) {
  const progress = getAgentPlanModuleProgress(step);
  return progress ? localizeAgentText(`${progress.complete} / ${progress.total} 个模块`) : "";
}

function setAgentDynamicText(element, value, dynamic = true) {
  window.WaterfallI18n?.markDynamic?.(element, dynamic);
  element.textContent = value;
}

window.getAgentPlanModuleProgress = getAgentPlanModuleProgress;
