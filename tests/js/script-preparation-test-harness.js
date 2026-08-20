function createClassList(initial = []) {
  const classes = new Set(initial);
  return {
    add: (...names) => names.forEach((name) => classes.add(name)),
    remove: (...names) => names.forEach((name) => classes.delete(name)),
    contains: (name) => classes.has(name),
    toggle(name, force) {
      const enabled = typeof force === "boolean" ? force : !classes.has(name);
      if (enabled) {
        classes.add(name);
      } else {
        classes.delete(name);
      }
      return enabled;
    },
    replaceFrom(value) {
      classes.clear();
      String(value || "")
        .split(/\s+/)
        .filter(Boolean)
        .forEach((name) => classes.add(name));
    },
    value: () => Array.from(classes).join(" "),
  };
}

function createElement(documentRef, { classes = [], dataset = {} } = {}) {
  const listeners = new Map();
  const attributes = new Map();
  const classList = createClassList(classes);
  const element = {
    innerHTML: "",
    textContent: "",
    value: "",
    disabled: false,
    checked: false,
    indeterminate: false,
    inert: false,
    isConnected: true,
    dataset: { ...dataset },
    style: {},
    classList,
    setAttribute(name, value) {
      attributes.set(name, String(value));
    },
    getAttribute(name) {
      return attributes.has(name) ? attributes.get(name) : null;
    },
    removeAttribute(name) {
      attributes.delete(name);
    },
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    removeEventListener(type) {
      listeners.delete(type);
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    focus() {
      documentRef.activeElement = element;
    },
    getClientRects() {
      return [1];
    },
  };
  Object.defineProperty(element, "className", {
    get: () => classList.value(),
    set: (value) => classList.replaceFrom(value),
  });
  return element;
}

const HOOK_NAMES = [
  "stageMeta",
  "stageTitle",
  "stageSummary",
  "stageStatus",
  "bulkToggle",
  "bulkExit",
  "progressValue",
  "progressBar",
  "processingCount",
  "readyCount",
  "awaitingCount",
  "abandonedCount",
  "filterBar",
  "searchInput",
  "batchBar",
  "selectedCount",
  "batchHint",
  "clearSelection",
  "batchMenuToggle",
  "batchMenu",
  "batchExecute",
  "batchRepair",
  "selectAll",
  "tableBody",
  "tableEmpty",
  "tableFooterTotal",
  "tableFooterHint",
  "detailModal",
  "detailBackdrop",
  "detailClose",
  "detailMeta",
  "detailTitle",
  "detailBadges",
  "historyList",
  "detailContent",
  "actionPanel",
  "editorModal",
  "editorBackdrop",
  "editorClose",
  "editorMeta",
  "editorTitle",
  "editorDescription",
  "editSection",
  "promptSection",
  "scriptEditor",
  "originalPrompt",
  "supplementalPrompt",
  "editorBaseline",
  "editorTarget",
  "editorCancel",
  "editorSave",
  "editorSaveExecute",
  "editorConfirm",
  "localNotice",
];

function createScriptPreparationHarness() {
  const documentRef = {
    activeElement: null,
    body: null,
  };
  const element = (options) => createElement(documentRef, options);
  const elements = Object.fromEntries(
    HOOK_NAMES.map((name) => [name, element()]),
  );
  [
    "bulkExit",
    "batchBar",
    "batchMenu",
    "tableEmpty",
    "detailModal",
    "editorModal",
    "promptSection",
    "editorConfirm",
    "localNotice",
  ].forEach((name) => elements[name].classList.add("hidden"));

  const filters = [
    "all",
    "processing",
    "ready",
    "awaiting_human",
    "abandoned",
  ].map((filter) => element({ dataset: { scriptPreparationFilter: filter } }));
  elements.filterBar.querySelectorAll = (selector) =>
    selector === "[data-script-preparation-filter]" ? filters : [];

  const root = element({ classes: ["hidden"] });
  root.querySelector = (selector) => {
    const match = selector.match(
      /^\[data-script-preparation-id="([^"]+)"\]$/,
    );
    return match ? elements[match[1]] || null : null;
  };
  documentRef.body = element();
  documentRef.activeElement = element();
  return { documentRef, elements, root };
}

function createTimerWindow() {
  let nextIntervalId = 0;
  const intervals = new Map();
  const windowRef = {
    confirm: () => true,
    requestAnimationFrame(callback) {
      callback();
    },
    setTimeout,
    clearTimeout,
    setInterval(callback, delay) {
      nextIntervalId += 1;
      intervals.set(nextIntervalId, { callback, delay });
      return nextIntervalId;
    },
    clearInterval(intervalId) {
      intervals.delete(intervalId);
    },
    WaterfallI18n: {
      t(key) {
        return key;
      },
      source(value) {
        return value;
      },
      getLocale() {
        return "zh-CN";
      },
      markDynamic(element, enabled = true) {
        if (enabled) {
          element?.setAttribute?.("data-i18n-dynamic", "");
        } else {
          element?.removeAttribute?.("data-i18n-dynamic");
        }
      },
      markDynamicAttributes(element, enabled = true) {
        if (enabled) {
          element?.setAttribute?.("data-i18n-dynamic-attributes", "");
        } else {
          element?.removeAttribute?.("data-i18n-dynamic-attributes");
        }
      },
    },
  };
  return {
    intervals,
    windowRef,
  };
}

module.exports = {
  createScriptPreparationHarness,
  createTimerWindow,
};
