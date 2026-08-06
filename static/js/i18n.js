(() => {
  "use strict";

  function translate(locale, key, params = {}) {
    const dictionary = window.WaterfallTranslations?.[locale] || {};
    const fallback = window.WaterfallTranslations?.["zh-CN"] || {};
    let value = dictionary[key] || fallback[key] || key;
    Object.entries(params).forEach(([name, replacement]) => {
      value = value.replaceAll(`{${name}}`, String(replacement));
    });
    return value;
  }

  window.WaterfallI18n = {
    t(key, params) {
      return translate(document.documentElement.dataset.locale || "zh-CN", key, params);
    },
    setLocale(locale) {
      document.documentElement.dataset.locale = locale === "en" ? "en" : "zh-CN";
      document.documentElement.lang = document.documentElement.dataset.locale;
    },
  };
})();
