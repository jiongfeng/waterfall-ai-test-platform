(() => {
  "use strict";

  const ENGLISH = "en";
  const CHINESE = "zh-CN";
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
    ".markdown-preview",
    ".execution-log",
    ".event-log",
  ].join(",");
  const ATTRIBUTE_SKIPPED_SELECTOR = [
    "pre",
    "code",
    "[data-i18n-skip]",
    ".markdown-preview",
    ".execution-log",
    ".event-log",
  ].join(",");
  const LEGACY_COUNT_PATTERNS = [
    [/^共\s*(\d+)\s*条脚本$/, (count) => `${count} scripts total`],
    [/^共\s*(\d+)\s*条计划$/, (count) => `${count} plans total`],
    [/^共\s*(\d+)\s*个测试集$/, (count) => `${count} test suites total`],
    [/^共\s*(\d+)\s*个用户$/, (count) => `${count} users total`],
    [/^共\s*(\d+)\s*个角色$/, (count) => `${count} roles total`],
    [/^共\s*(\d+)\s*条相关脚本$/, (count) => `${count} related scripts total`],
    [/^已选择\s*(\d+)\s*[个条]$/, (count) => `${count} selected`],
    [/^拆分完成：新增\s*(\d+)\s*个单用例计划。$/, (count) => `Split complete: ${count} single-case plans created.`],
    [/^跳过\s*(\d+)\s*个已存在或无效计划。$/, (count) => `Skipped ${count} existing or invalid plans.`],
    [/^任务失败：?\s*(.*)$/, (error) => error ? `Task failed: ${error}` : "Task failed"],
    [/^已删除\s*(\d+)\s*条测试脚本。$/, (count) => `${count} test scripts deleted.`],
    [/^已删除\s*(\d+)\s*条测试脚本，失败\s*(\d+)\s*条：(.+)$/, (deleted, failed, details) => `${deleted} test scripts deleted; ${failed} failed: ${details}`],
    [/^确认删除选中的\s*(\d+)\s*条测试脚本？$/, (count) => `Delete the selected ${count} test scripts?`],
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
    if (catalog[value]) return catalog[value];
    for (const [pattern, format] of LEGACY_COUNT_PATTERNS) {
      const match = value.match(pattern);
      if (match) return format(match[1]);
    }
    return value;
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
      attributeFilter: LOCALIZABLE_ATTRIBUTES,
      childList: true,
      subtree: true,
      characterData: true,
    });
  }

  window.WaterfallI18n = {
    t: translate,
    // Use this only while migrating legacy feature modules. New UI must use a
    // semantic t(key) entry; source() preserves Chinese-mode copy and safely
    // localizes known first-party legacy literals in English mode.
    source(value) {
      return locale() === ENGLISH ? localizeSourceText(value) : value;
    },
    formatDate(value, options) {
      return new Intl.DateTimeFormat(locale(), options).format(new Date(value));
    },
    getLocale: locale,
    localizeDom,
    setLocale(value) {
      const next = value === CHINESE ? CHINESE : ENGLISH;
      document.documentElement.dataset.locale = next;
      document.documentElement.lang = next;
      localizeDom();
      observeDom();
    },
  };
})();
