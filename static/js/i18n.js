(() => {
  "use strict";

  const ENGLISH = "en";
  const CHINESE = "zh-CN";
  const DYNAMIC_CONTENT_ATTRIBUTE = "data-i18n-dynamic";
  const DYNAMIC_ATTRIBUTES_ATTRIBUTE = "data-i18n-dynamic-attributes";
  const LOCALIZABLE_ATTRIBUTES = ["title", "placeholder", "aria-label", "aria-description"];
  const TEXT_SKIPPED_SELECTOR = [
    "pre",
    "code",
    "textarea",
    "input",
    "select",
    "option",
    "[contenteditable='true']",
    "[data-i18n-skip]",
    `[${DYNAMIC_CONTENT_ATTRIBUTE}]`,
    ".markdown-preview",
    ".execution-log",
    ".event-log",
  ].join(",");
  const ATTRIBUTE_SKIPPED_SELECTOR = [
    "pre",
    "code",
    "[data-i18n-skip]",
    `[${DYNAMIC_CONTENT_ATTRIBUTE}]`,
    `[${DYNAMIC_ATTRIBUTES_ATTRIBUTE}]`,
    ".markdown-preview",
    ".execution-log",
    ".event-log",
  ].join(",");
  const DIALOG_SELECTOR = "[role='dialog'], .modal-backdrop, .task-modal";
  const LEGACY_COUNT_PATTERNS = [
    // Agent execution pages compose these snippets with run ids, timestamps
    // and profile labels, so an exact source-string catalogue cannot see them.
    // Keep the patterns deliberately structural: requirement titles, event
    // messages and other user-authored content are never translated here.
    [/^耗时\s+(\d+)h\s+(\d+)m$/, (hours, minutes) => `Duration ${hours}h ${minutes}m`],
    [/^耗时\s+(\d+)m\s+(\d+)s$/, (minutes, seconds) => `Duration ${minutes}m ${seconds}s`],
    [/^耗时\s+(\d+)s$/, (seconds) => `Duration ${seconds}s`],
    [/^模板来源：(.+?)(\s*·\s*已自定义)?$/, (label, customized) => `Template source: ${label}${customized ? " · Customized" : ""}`],
    [/^(.+?)\s*·\s*模板来源：(.+?)(\s*·\s*已自定义)?\s*·\s*(.+)$/, (runId, label, customized, timestamp) => `${runId} · Template source: ${label}${customized ? " · Customized" : ""} · ${timestamp}`],
    [/^(.*?)\s*·\s*(.*?)\s*·\s*(\d+)\s*个生成物$/, (step, status, count) => `${localizeSourceText(step)} · ${localizeSourceText(status)} · ${count} artifacts`],
    [/^已加载\s*(\d+)\s*条事件$/, (count) => `${count} events loaded`],
    [/^(\d+)\s*个生成物$/, (count) => `${count} artifacts`],
    [/^(\d+)\s*个脚本$/, (count) => `${count} scripts`],
    [/^通过\s*(\d+)\s*·\s*失败\s*(\d+)\s*·\s*总数\s*(\d+)$/, (passed, failed, total) => `${passed} passed · ${failed} failed · ${total} total`],
    [/^置信度\s*([\d.]+%?)$/, (value) => `Confidence ${value}`],
    [/^项目目录将自动创建为：(.+?)\/<项目标识>$/, (root) => `The project directory will be created as: ${root}/<project-key>`],
    [/^(\d+)\s*个候选(?:\s*·\s*(.+))?$/, (count, suffix) => `${count} candidates${suffix ? ` · ${suffix}` : ""}`],
    [/^共\s*(\d+)\s*个候选模块$/, (count) => `${count} candidate modules`],
    [/^已选择\s*(\d+)\s*个候选模块$/, (count) => `${count} candidate modules selected`],
    [/^(\d+)\s*\/\s*(\d+)\s*个模块$/, (complete, total) => `${complete} / ${total} modules`],
    [/^计划\s*(\d+)$/, (index) => `Plan ${index}`],
    [/^脚本\s*(\d+)$/, (index) => `Script ${index}`],
    [/^模块\s*(\d+)$/, (index) => `Module ${index}`],
    [/^失败项\s*(\d+)$/, (index) => `Failure ${index}`],
    [/^决策\s*(\d+)$/, (index) => `Decision ${index}`],
    [/^处理\s*(\d+)$/, (index) => `Action ${index}`],
    [/^(需求|需求解析|模块审查|计划生成|脚本准备|测试集|执行)输出$/, (step) => `${localizeSourceText(step)} output`],
    [/^(.+?)暂无生成物$/, (step) => `No artifacts for ${localizeSourceText(step)}`],
    [/^共\s*(\d+)\s*条脚本$/, (count) => `${count} scripts total`],
    [/^共\s*(\d+)\s*条计划$/, (count) => `${count} plans total`],
    [/^共\s*(\d+)\s*个测试集$/, (count) => `${count} test suites total`],
    [/^共\s*(\d+)\s*个用户$/, (count) => `${count} users total`],
    [/^共\s*(\d+)\s*个角色$/, (count) => `${count} roles total`],
    [/^共\s*(\d+)\s*条相关脚本$/, (count) => `${count} related scripts total`],
    [/^已选择\s*(\d+)\s*[个条]$/, (count) => `${count} selected`],
    [/^已导出\s*(\d+)\s*条测试计划。$/, (count) => `${count} test plans exported.`],
    [/^测试计划导入完成：新增\s*(\d+)\s*条，覆盖\s*(\d+)\s*条，跳过\s*(\d+)\s*条。$/, (created, overwritten, skipped) => `Test-plan import completed: ${created} created, ${overwritten} overwritten, ${skipped} skipped.`],
    [/^导出测试计划失败：(\d+)$/, (status) => `Could not export test plans: ${status}`],
    [/^导入测试计划失败：(\d+)$/, (status) => `Could not import test plans: ${status}`],
    [/^拆分完成：新增\s*(\d+)\s*个单用例计划。$/, (count) => `Split complete: ${count} single-case plans created.`],
    [/^跳过\s*(\d+)\s*个已存在或无效计划。$/, (count) => `Skipped ${count} existing or invalid plans.`],
    [/^任务失败：?\s*(.*)$/, (error) => error ? `Task failed: ${error}` : "Task failed"],
    [/^已删除\s*(\d+)\s*条测试脚本。$/, (count) => `${count} test scripts deleted.`],
    [/^已删除\s*(\d+)\s*条测试脚本，失败\s*(\d+)\s*条：(.+)$/, (deleted, failed, details) => `${deleted} test scripts deleted; ${failed} failed: ${details}`],
    [/^确认删除选中的\s*(\d+)\s*条测试脚本？$/, (count) => `Delete the selected ${count} test scripts?`],
    [/^全部\s*(\d+)$/, (count) => `All ${count}`],
    [/^处理中\s*(\d+)$/, (count) => `Processing ${count}`],
    [/^已通过\s*(\d+)$/, (count) => `Passed ${count}`],
    [/^待人工\s*(\d+)$/, (count) => `Manual action ${count}`],
    [/^已放弃\s*(\d+)$/, (count) => `Abandoned ${count}`],
    [/^已选择\s*(\d+)\s*条脚本$/, (count) => `${count} scripts selected`],
    [/^共\s*(\d+)\s*条脚本\s*·\s*当前显示\s*(\d+)\s*条$/, (total, shown) => `${total} scripts total · ${shown} shown`],
    [/^批量修复记录：成功\s*(\d+)，失败\s*(\d+)，已取消\s*(\d+)$/, (succeeded, failed, cancelled) => `Bulk repair record: ${succeeded} succeeded, ${failed} failed, ${cancelled} cancelled`],
    [/^工具完成：\s*(.+)$/, (tool) => `Tool completed: ${tool}`],
    [/^工具元数据：\s*(.+)$/, (tool) => `Tool metadata: ${tool}`],
    [/^工具输出：\s*(.+)$/, (tool) => `Tool output: ${tool}`],
    [/^拆分计划失败：\s*(.+)$/, (error) => `Plan splitting failed: ${localizeSourceText(error)}`],
    [
      /^多计划拆分检测到已有文件内容冲突；未写入、登记或删除任何计划文件：\s*(.+)$/,
      (files) => `Existing file-content conflicts were found while splitting the multi-case plan; no plan files were written, registered, or deleted: ${files}`,
    ],
    [/^仍有\s*(\d+)\s*个模块计划生成失败：\s*(.+)$/, (count, error) => `Plan generation failed for ${count} module${count === "1" ? "" : "s"}: ${localizeSourceText(error)}`],
    [/^模块计划生成失败：\s*(.+?)，\s*(.+)$/, (moduleName, error) => `Plan generation failed for ${moduleName}: ${localizeSourceText(error)}`],
    [/^确认删除选中的\s*(\d+)\s*个候选模块？$/, (count) => `Delete the selected ${count} candidate modules?`],
    [/^确定停止“(.+)”的重试并验证吗？已生成的中间结果会保留。$/, (name) => `Stop retrying and validating “${name}”? Generated intermediate results will be kept.`],
    [/^确认删除测试集“(.+)”？$/, (name) => `Delete test suite “${name}”?`],
    [/^删除准备脚本“(.+)”？历史执行记录仍会保留。$/, (name) => `Delete setup script “${name}”? Historical execution records will be kept.`],
    [/^共\s*(\d+)\s*条单用例计划$/, (count) => `${count} single-case plans`],
    [/^确认删除选中的\s*(\d+)\s*条测试计划？$/, (count) => `Delete the selected ${count} test plans?`],
    [/^确定删除“(.+)”吗？脚本会被归档，失败历史仍会保留。$/, (name) => `Delete “${name}”? The script will be archived and its failure history will be kept.`],
    [/^“(.+)”没有可删除脚本，确定保留失败记录并忽略该项吗？$/, (name) => `“${name}” has no script to delete. Keep the failure record and ignore this item?`],
    [/^页面：\s*(.+)$/, (value) => `Page: ${value}`],
    [/^路径：\s*(.+)$/, (value) => `Path: ${value}`],
    [/^角色：\s*(.+)$/, (value) => `Roles: ${value}`],
    [/^控件：\s*(.+)$/, (value) => `Controls: ${value}`],
    [/^解析失败(?:：\s*(.*))?$/, (error) => error ? `Analysis failed: ${localizeSourceText(error)}` : "Analysis failed"],
    [/^批量删除完成，失败\s*(\d+)\s*个：\s*(.+)$/, (count, details) => `Bulk deletion completed with ${count} failure${count === "1" ? "" : "s"}: ${details}`],
    [/^已删除\s*(\d+)\s*个候选模块。$/, (count) => `${count} candidate modules deleted.`],
    [/^将为选中的\s*(\d+)\s*个模块生成计划。$/, (count) => `Plans will be generated for the selected ${count} modules.`],
    [/^批量生成计划记录：成功\s*(\d+)，失败\s*(\d+)$/, (succeeded, failed) => `Bulk plan-generation record: ${succeeded} succeeded, ${failed} failed`],
    [/^批量生成脚本记录：成功\s*(\d+)，失败\s*(\d+)$/, (succeeded, failed) => `Bulk script-generation record: ${succeeded} succeeded, ${failed} failed`],
    [/^计划生成语句\s*·\s*模板来源：(.+?)(\s*·\s*已自定义)?$/, (label, customized) => `Plan-generation prompt · Template source: ${label}${customized ? " · Customized" : ""}`],
    [/^已导入\s*(\d+)\s*条页面 inventory。$/, (count) => `${count} page-inventory entries imported.`],
    [/^导入项目目录将创建在：(.+?)\/<项目标识>$/, (root) => `The imported project directory will be created at: ${root}/<project-key>`],
    [/^项目导入成功：(\d+)\s*个模块，(\d+)\s*个计划，(\d+)\s*个脚本，(\d+)\s*个测试集。$/, (modules, plans, scripts, suites) => `Project imported: ${modules} modules, ${plans} plans, ${scripts} scripts, ${suites} test suites.`],
    [/^请求失败[:：]\s*(\d+)$/, (status) => `Request failed: ${status}`],
    [/^导出项目失败：\s*(\d+)$/, (status) => `Project export failed: ${status}`],
    [/^导入项目失败：\s*(\d+)$/, (status) => `Project import failed: ${status}`],
    [/^上传失败[:：]\s*(\d+)$/, (status) => `Upload failed: ${status}`],
    [/^接口返回不是 JSON:\s*(.+)$/, (error) => `The API response is not JSON: ${error}`],
    [/^(\d+)\s*项$/, (count) => `${count} items`],
    [/^证据\s*(\d+)$/, (index) => `Evidence ${index}`],
    [/^分类：\s*(.+)$/, (category) => `Category: ${category}`],
    [/^置信度：\s*(.+)$/, (confidence) => `Confidence: ${confidence}`],
    [/^失败详情\s*·\s*(.+)$/, (title) => `Failure details · ${title}`],
    [/^分析和建议\s*·\s*(.+)$/, (title) => `Analysis and suggestions · ${title}`],
    [/^编辑(候选稿|脚本)\s*·\s*(.+)$/, (kind, title) => `Edit ${localizeSourceText(kind)} · ${title}`],
    [/^读取脚本失败：\s*(.+)$/, (error) => `Could not load script: ${localizeSourceText(error)}`],
    [/^仍有\s*(\d+)\s*个未解决项目$/, (count) => `${count} unresolved items remain`],
    [/^其中脚本生成失败\s*(\d+)\s*项、脚本修复失败\s*(\d+)\s*项。继续后它们不会进入本次测试集，但失败证据、分析和历史记录会继续保留。$/, (generation, repair) => `${generation} script-generation failures and ${repair} script-repair failures. They will be excluded from this test suite if you continue, while their evidence, analysis, and history are retained.`],
    [/^已加载\s*(\d+)\s*条事件，内存保留\s*(\d+)\s*条$/, (loaded, retained) => `${loaded} events loaded; ${retained} retained in memory`],
    [/^(\d+)\s*项重试生成中$/, (count) => `${count} items regenerating`],
    [/^(\d+)\s*项已重新生成$/, (count) => `${count} items regenerated`],
    [/^(\d+)\s*项单独执行中$/, (count) => `${count} items running individually`],
    [/^(\d+)\s*项执行后待处理$/, (count) => `${count} items awaiting action after execution`],
    [/^(\d+)\s*项自动修复中$/, (count) => `${count} items being repaired automatically`],
    [/^(\d+)\s*项修复后复验中$/, (count) => `${count} items being verified after repair`],
    [/^正在重试并验证\s*(\d+)\s*个脚本$/, (count) => `Retrying and validating ${count} scripts`],
    [/^另有\s*(\d+)\s*项$/, (count) => `${count} more items`],
    [/^(\d+)\s*个脚本的重试并验证已结束：(\d+)\s*个通过，(\d+)\s*个失败或受阻。$/, (total, passed, failed) => `Retry and validation finished for ${total} scripts: ${passed} passed; ${failed} failed or blocked.`],
    [/^读取任务生成语句失败：\s*(.+)$/, (error) => `Could not load the task generation prompt: ${localizeSourceText(error)}`],
    [/^读取产物失败：\s*(.+)$/, (error) => `Could not load the artifact: ${localizeSourceText(error)}`],
    [/^生成诊断包失败：\s*(\d+)$/, (status) => `Could not generate the diagnostic bundle: ${status}`],
    [/^事件流连接失败：\s*(\d+)$/, (status) => `Event-stream connection failed: ${status}`],
    [/^检测到上游存在失败生成物，已从“(.+)”恢复 Agent 任务。$/, (step) => `Upstream failed artifacts were detected; the Agent task resumed from “${localizeSourceText(step)}”.`],
    [/^已从“(.+)”恢复 Agent 任务。$/, (step) => `Agent task resumed from “${localizeSourceText(step)}”.`],
    [/^通过脚本\s*(\d+)$/, (index) => `Passed script ${index}`],
  ];

  function locale() {
    return document.documentElement.dataset.locale === CHINESE ? CHINESE : ENGLISH;
  }

  function dictionary(language = locale()) {
    return window.WaterfallTranslations?.[language] || {};
  }

  function interpolate(value, params = {}) {
    return Object.entries(params).reduce(
      (result, [name, replacement]) => result.replaceAll(`{${name}}`, String(replacement)),
      value,
    );
  }

  function translate(key, params = {}) {
    const value = dictionary()[key] || dictionary(ENGLISH)[key] || key;
    return interpolate(value, params);
  }

  function isTextExcluded(node) {
    const parent = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    return !parent || Boolean(parent.closest(TEXT_SKIPPED_SELECTOR));
  }

  function isAttributeExcluded(element) {
    return !element || Boolean(element.closest(ATTRIBUTE_SKIPPED_SELECTOR));
  }

  function localizeSourceText(value) {
    if (locale() !== ENGLISH || typeof value !== "string" || !/[\u3400-\u9fff]/.test(value)) {
      return value;
    }
    const catalog = dictionary(ENGLISH).source || {};
    const whitespace = value.match(/^(\s*)([\s\S]*?)(\s*)$/);
    const prefix = whitespace?.[1] || "";
    const source = whitespace?.[2] || value;
    const suffix = whitespace?.[3] || "";
    if (catalog[source]) return `${prefix}${catalog[source]}${suffix}`;
    for (const [pattern, format] of LEGACY_COUNT_PATTERNS) {
      const match = source.match(pattern);
      if (match) return `${prefix}${format(...match.slice(1))}${suffix}`;
    }
    return value;
  }

  function localizeLogText(value) {
    if (locale() !== ENGLISH || typeof value !== "string" || !/[\u3400-\u9fff]/.test(value)) {
      return value;
    }
    return value
      .split(/(\r?\n)/)
      .map((line) => (/^\r?\n$/.test(line) ? line : localizeSourceText(line)))
      .join("");
  }

  function localizePlatformFailure(stepKey, value) {
    if (locale() !== ENGLISH || stepKey !== "generate_plans" || typeof value !== "string") return value;
    return /^拆分计划失败：\s*多计划拆分检测到已有文件内容冲突；未写入、登记或删除任何计划文件：/.test(value)
      ? localizeLogText(value)
      : value;
  }

  function localizeTextNode(node) {
    if (!node?.nodeValue || isTextExcluded(node)) return;
    const translated = localizeSourceText(node.nodeValue);
    if (translated !== node.nodeValue) node.nodeValue = translated;
  }

  function localizeElement(element) {
    if (isAttributeExcluded(element)) return;
    LOCALIZABLE_ATTRIBUTES.forEach((attribute) => {
      if (!element.hasAttribute(attribute)) return;
      const value = element.getAttribute(attribute);
      const translated = localizeSourceText(value);
      if (translated !== value) element.setAttribute(attribute, translated);
    });
  }

  function localizeDom(root = document.body) {
    if (locale() !== ENGLISH || !root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);
    textNodes.forEach(localizeTextNode);
    if (root.nodeType === Node.ELEMENT_NODE) localizeElement(root);
    root.querySelectorAll?.("[title], [placeholder], [aria-label], [aria-description]").forEach(localizeElement);
  }

  function setDynamicContent(element, enabled = true) {
    if (!element?.setAttribute) return element;
    if (enabled) element.setAttribute(DYNAMIC_CONTENT_ATTRIBUTE, "");
    else element.removeAttribute?.(DYNAMIC_CONTENT_ATTRIBUTE);
    return element;
  }

  function setDynamicAttributes(element, enabled = true) {
    if (!element?.setAttribute) return element;
    if (enabled) element.setAttribute(DYNAMIC_ATTRIBUTES_ATTRIBUTE, "");
    else element.removeAttribute?.(DYNAMIC_ATTRIBUTES_ATTRIBUTE);
    return element;
  }

  let observer = null;
  function observeDom() {
    observer?.disconnect();
    if (locale() !== ENGLISH || !document.body) return;
    observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.type === "characterData") {
          localizeTextNode(mutation.target);
          return;
        }
        if (mutation.type === "attributes") {
          // Dialog bodies are server-rendered while hidden.  A class change
          // does not add text nodes, so translate the complete dialog when it
          // is opened instead of leaving its source-language copy visible.
          if (mutation.attributeName === "class" && mutation.target.matches?.(DIALOG_SELECTOR)) {
            localizeDom(mutation.target);
          }
          localizeElement(mutation.target);
          return;
        }
        mutation.addedNodes.forEach((node) => {
          if (node.nodeType === Node.TEXT_NODE) localizeTextNode(node);
          if (node.nodeType === Node.ELEMENT_NODE) localizeDom(node);
        });
      });
    });
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: [...LOCALIZABLE_ATTRIBUTES, "class"],
      childList: true,
      subtree: true,
      characterData: true,
    });
  }

  function installNativeDialogBridge() {
    const nativeConfirm = window.confirm;
    if (typeof nativeConfirm !== "function" || nativeConfirm.__waterfallI18nBridge) return;
    const localizedConfirm = function localizedConfirm(message) {
      return nativeConfirm.call(this, localizeSourceText(String(message ?? "")));
    };
    localizedConfirm.__waterfallI18nBridge = true;
    window.confirm = localizedConfirm;
  }

  window.WaterfallI18n = {
    t: translate,
    // Use this only while migrating legacy feature modules. New UI must use a
    // semantic t(key) entry; source() preserves Chinese-mode copy and safely
    // localizes known first-party legacy literals in English mode.
    source(value) {
      return locale() === ENGLISH ? localizeSourceText(value) : value;
    },
    // Log and event bodies can contain user-authored text or generated code.
    // Translate only complete, known first-party lines and leave every other
    // line byte-for-byte unchanged.
    log(value) {
      return locale() === ENGLISH ? localizeLogText(value) : value;
    },
    platformFailure: localizePlatformFailure,
    formatDate(value, options) {
      return new Intl.DateTimeFormat(locale(), options).format(new Date(value));
    },
    getLocale: locale,
    markDynamic: setDynamicContent,
    markDynamicAttributes: setDynamicAttributes,
    localizeDom,
    setLocale(value) {
      const next = value === CHINESE ? CHINESE : ENGLISH;
      document.documentElement.dataset.locale = next;
      document.documentElement.lang = next;
      localizeDom();
      observeDom();
    },
  };
  installNativeDialogBridge();
})();
