(() => {
  const PING_EVENT = "ctyun-manager:console-bridge-ping";
  const PONG_EVENT = "ctyun-manager:console-bridge-pong";
  const OPEN_EVENT = "ctyun-manager:console-bridge-open";
  const RESULT_EVENT = "ctyun-manager:console-bridge-result";

  function emit(type, detail) {
    window.dispatchEvent(new CustomEvent(type, { detail }));
  }

  function isBridgeEvent(event) {
    return event.target === window && event.detail && typeof event.detail === "object";
  }

  document.documentElement.setAttribute("data-ctyun-console-bridge", "1");

  window.addEventListener(PING_EVENT, (event) => {
    if (!isBridgeEvent(event)) return;
    emit(PONG_EVENT, {
      requestId: event.detail.requestId || "",
      ok: true,
      version: chrome.runtime.getManifest().version,
    });
  });

  window.addEventListener(OPEN_EVENT, (event) => {
    if (!isBridgeEvent(event)) return;
    const detail = event.detail;
    chrome.runtime.sendMessage(
      {
        type: "ctyun-open-console",
        requestId: detail.requestId || "",
        payload: detail.payload || {},
      },
      (response) => {
        if (chrome.runtime.lastError) {
          emit(RESULT_EVENT, {
            requestId: detail.requestId || "",
            ok: false,
            message: chrome.runtime.lastError.message || "扩展后台未响应",
          });
          return;
        }
        emit(RESULT_EVENT, {
          requestId: detail.requestId || "",
          ...(response || { ok: false, message: "扩展未返回结果" }),
        });
      },
    );
  });
})();
