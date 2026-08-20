function createPlanTransferFeature(deps) {
  const {
    state,
    elements,
    document,
    window,
    fetch,
    FormData,
    getProjectRequestHeaders,
    readFetchError,
    getDownloadFilename,
    loadPlanModules,
    setNotice,
    stripMarkdownSuffix,
  } = deps;
  const selected = new Set();
  let exporting = false;
  let importing = false;

  function planKey(moduleName, planFilename) {
    return `${moduleName}\u0000${planFilename}`;
  }

  function allPlans() {
    return (state.plans.modules || []).flatMap((moduleItem) =>
      (moduleItem.plans || []).map((plan) => ({
        moduleName: moduleItem.name,
        modulePath: moduleItem.path || "",
        planFilename: plan.filename,
        planName: plan.name || stripMarkdownSuffix(plan.filename),
        path: plan.path || "",
      })),
    );
  }

  function exportModalOpen() {
    return !elements.planExportModal.classList.contains("hidden");
  }

  function importModalOpen() {
    return !elements.planImportModal.classList.contains("hidden");
  }

  function renderExportTree() {
    const plans = allPlans();
    const validKeys = new Set(plans.map((plan) => planKey(plan.moduleName, plan.planFilename)));
    Array.from(selected).forEach((key) => {
      if (!validKeys.has(key)) selected.delete(key);
    });
    const query = elements.planExportSearch.value.trim().toLowerCase();
    const visible = plans.filter((plan) =>
      !query || [plan.moduleName, plan.planName, plan.planFilename]
        .some((value) => String(value).toLowerCase().includes(query)),
    );
    const groups = new Map();
    visible.forEach((plan) => {
      if (!groups.has(plan.moduleName)) groups.set(plan.moduleName, []);
      groups.get(plan.moduleName).push(plan);
    });

    elements.planExportTree.replaceChildren();
    if (!visible.length) {
      const empty = document.createElement("p");
      empty.className = "plan-export-empty";
      empty.textContent = plans.length ? "没有匹配的测试计划。" : "当前项目没有可导出的测试计划。";
      elements.planExportTree.appendChild(empty);
    }
    groups.forEach((modulePlans, moduleName) => {
      const section = document.createElement("section");
      section.className = "plan-export-module";
      const header = document.createElement("label");
      header.className = "plan-export-module-header";
      const moduleCheckbox = document.createElement("input");
      moduleCheckbox.type = "checkbox";
      const selectedCount = modulePlans.filter((plan) => selected.has(planKey(moduleName, plan.planFilename))).length;
      moduleCheckbox.checked = selectedCount === modulePlans.length;
      moduleCheckbox.indeterminate = selectedCount > 0 && selectedCount < modulePlans.length;
      moduleCheckbox.disabled = exporting;
      moduleCheckbox.addEventListener("change", () => {
        modulePlans.forEach((plan) => {
          const key = planKey(moduleName, plan.planFilename);
          if (moduleCheckbox.checked) selected.add(key);
          else selected.delete(key);
        });
        renderExportTree();
      });
      const moduleLabel = document.createElement("span");
      const countLabel = window.WaterfallI18n?.getLocale?.() === "en"
        ? ` (${modulePlans.length})`
        : `（${modulePlans.length}）`;
      moduleLabel.textContent = `${moduleName}${countLabel}`;
      window.WaterfallI18n?.markDynamic?.(moduleLabel);
      header.append(moduleCheckbox, moduleLabel);
      section.appendChild(header);
      const rows = document.createElement("div");
      rows.className = "plan-export-module-plans";
      modulePlans.forEach((plan) => {
        const label = document.createElement("label");
        label.className = "plan-export-plan-row";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        const key = planKey(moduleName, plan.planFilename);
        checkbox.checked = selected.has(key);
        checkbox.disabled = exporting;
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) selected.add(key);
          else selected.delete(key);
          renderExportTree();
        });
        const name = document.createElement("span");
        name.textContent = plan.planName;
        window.WaterfallI18n?.markDynamic?.(name);
        const filename = document.createElement("small");
        filename.textContent = plan.planFilename;
        window.WaterfallI18n?.markDynamic?.(filename);
        label.append(checkbox, name, filename);
        rows.appendChild(label);
      });
      section.appendChild(rows);
      elements.planExportTree.appendChild(section);
    });

    elements.planExportSelectionCount.textContent = `已选择 ${selected.size} 条`;
    elements.planExportSubmit.disabled = selected.size === 0 || exporting;
    elements.planExportSubmit.textContent = exporting ? "导出中" : "确定导出";
    elements.planExportSelectAll.checked = Boolean(plans.length && selected.size === plans.length);
    elements.planExportSelectAll.indeterminate = selected.size > 0 && selected.size < plans.length;
    elements.planExportSelectAll.disabled = !plans.length || exporting;
  }

  function openExportModal() {
    if (!state.project.currentKey || state.isEditing || exporting || importing) return;
    selected.clear();
    if (state.plans.selectedModule && state.plans.selectedPlanFile) {
      selected.add(planKey(state.plans.selectedModule, state.plans.selectedPlanFile));
    }
    elements.planExportSearch.value = "";
    elements.planExportModal.classList.remove("hidden");
    renderExportTree();
    window.requestAnimationFrame(() => elements.planExportSearch.focus());
  }

  function closeExportModal() {
    if (exporting) return;
    elements.planExportModal.classList.add("hidden");
    selected.clear();
  }

  async function submitExport() {
    if (!selected.size || exporting) return;
    const plans = allPlans()
      .filter((plan) => selected.has(planKey(plan.moduleName, plan.planFilename)))
      .map((plan) => ({ module_name: plan.moduleName, plan_filename: plan.planFilename }));
    exporting = true;
    renderExportTree();
    try {
      const response = await fetch("/api/plans/export-xlsx", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getProjectRequestHeaders(),
        },
        body: JSON.stringify({ plans }),
      });
      if (!response.ok) {
        throw new Error(await readFetchError(response, `导出测试计划失败：${response.status}`));
      }
      const blob = await response.blob();
      const filename = getDownloadFilename(response, "测试计划.xlsx");
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      exporting = false;
      closeExportModal();
      setNotice(`已导出 ${plans.length} 条测试计划。`, "success");
    } catch (error) {
      setNotice(error.message || "导出测试计划失败。", "error");
    } finally {
      exporting = false;
      if (exportModalOpen()) renderExportTree();
    }
  }

  function openImportModal() {
    if (!state.project.currentKey || state.isEditing || exporting || importing) return;
    elements.planImportFile.value = "";
    elements.planImportFile.setCustomValidity("");
    elements.planImportConflictPolicy.value = "reject";
    elements.planImportModal.classList.remove("hidden");
    window.requestAnimationFrame(() => elements.planImportFile.focus());
  }

  function closeImportModal() {
    if (importing) return;
    elements.planImportModal.classList.add("hidden");
    elements.planImportFile.setCustomValidity("");
  }

  async function submitImport() {
    const file = elements.planImportFile.files?.[0];
    if (!file) {
      elements.planImportFile.setCustomValidity("请选择测试计划 Excel 文件。");
      elements.planImportFile.reportValidity();
      return;
    }
    elements.planImportFile.setCustomValidity("");
    const formData = new FormData();
    formData.append("file", file);
    formData.append("conflict_policy", elements.planImportConflictPolicy.value || "reject");
    importing = true;
    elements.planImportSubmit.disabled = true;
    elements.planImportSubmit.textContent = "导入中";
    try {
      const response = await fetch("/api/plans/import-xlsx", {
        method: "POST",
        headers: getProjectRequestHeaders(),
        body: formData,
      });
      let data = {};
      try {
        data = await response.json();
      } catch (error) {
        data = { error: `接口返回不是 JSON: ${error}` };
      }
      if (!response.ok) throw new Error(data.error || `导入测试计划失败：${response.status}`);
      importing = false;
      closeImportModal();
      await loadPlanModules();
      setNotice(
        `测试计划导入完成：新增 ${data.created || 0} 条，覆盖 ${data.overwritten || 0} 条，跳过 ${data.skipped || 0} 条。`,
        "success",
      );
    } catch (error) {
      setNotice(error.message || "导入测试计划失败。", "error");
    } finally {
      importing = false;
      elements.planImportSubmit.disabled = false;
      elements.planImportSubmit.textContent = "确定导入";
    }
  }

  function bind() {
    elements.exportPlansButton.addEventListener("click", openExportModal);
    elements.importPlansButton.addEventListener("click", openImportModal);
    elements.planExportClose.addEventListener("click", closeExportModal);
    elements.planExportCancel.addEventListener("click", closeExportModal);
    elements.planExportSubmit.addEventListener("click", submitExport);
    elements.planExportSearch.addEventListener("input", renderExportTree);
    elements.planExportSelectAll.addEventListener("change", () => {
      selected.clear();
      if (elements.planExportSelectAll.checked) {
        allPlans().forEach((plan) => selected.add(planKey(plan.moduleName, plan.planFilename)));
      }
      renderExportTree();
    });
    elements.planImportClose.addEventListener("click", closeImportModal);
    elements.planImportCancel.addEventListener("click", closeImportModal);
    elements.planImportSubmit.addEventListener("click", submitImport);
    elements.planImportFile.addEventListener("change", () => elements.planImportFile.setCustomValidity(""));
    elements.planExportModal.addEventListener("click", (event) => {
      if (event.target === elements.planExportModal) closeExportModal();
    });
    elements.planImportModal.addEventListener("click", (event) => {
      if (event.target === elements.planImportModal) closeImportModal();
    });
    window.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (exportModalOpen()) closeExportModal();
      else if (importModalOpen()) closeImportModal();
    });
  }

  return { bind, openExportModal, openImportModal, renderExportTree, submitExport, submitImport };
}

window.createPlanTransferFeature = createPlanTransferFeature;
