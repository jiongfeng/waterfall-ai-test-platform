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
    return catalog[value] || value;
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
