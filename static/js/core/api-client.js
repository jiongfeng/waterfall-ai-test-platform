function readCsrfToken() {
  if (typeof document === "undefined") {
    return "";
  }
  return (
    document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") ||
    ""
  );
}

function createApiClient({ getProjectKey, onUnauthorized } = {}) {
  const resolveProjectKey = typeof getProjectKey === "function" ? getProjectKey : () => "";
  const handleUnauthorized = typeof onUnauthorized === "function" ? onUnauthorized : () => {};

  function getProjectHeaders(extraHeaders = {}) {
    const projectKey = resolveProjectKey() || "";
    const csrfToken = readCsrfToken();
    return {
      ...(projectKey ? { "X-Project-Key": projectKey } : {}),
      ...extraHeaders,
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
    };
  }

  async function requestJson(url, options = {}) {
    const { headers = {}, ...restOptions } = options;
    const response = await fetch(url, {
      ...restOptions,
      headers: {
        "Content-Type": "application/json",
        ...getProjectHeaders(headers),
      },
    });

    let data;
    try {
      data = await response.json();
    } catch (error) {
      data = { error: `接口返回不是 JSON: ${error}` };
    }

    if (!response.ok) {
      if (response.status === 401) {
        handleUnauthorized(data, response);
        throw new Error(data.error || "请先登录。");
      }
      throw new Error(data.error || `请求失败: ${response.status}`);
    }

    return data;
  }

  async function readFetchError(response, fallbackMessage) {
    try {
      const data = await response.json();
      return data.error || fallbackMessage;
    } catch (error) {
      return fallbackMessage;
    }
  }

  function getDownloadFilename(response, fallback) {
    const disposition = response.headers.get("Content-Disposition") || "";
    const encodedMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (encodedMatch) {
      try {
        return decodeURIComponent(encodedMatch[1].replace(/"/g, ""));
      } catch (error) {
        return fallback;
      }
    }
    const plainMatch = disposition.match(/filename="?([^";]+)"?/i);
    return plainMatch ? plainMatch[1] : fallback;
  }

  return {
    getProjectHeaders,
    requestJson,
    readFetchError,
    getDownloadFilename,
  };
}

window.createApiClient = createApiClient;
