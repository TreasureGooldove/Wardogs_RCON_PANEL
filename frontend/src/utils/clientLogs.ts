import type { App } from "vue";
let nextSend = 0,
  suppressed = 0;
export function reportPanelError(
  source: "vue" | "window" | "promise" | "save_readback",
  reason: unknown
) {
  if (Date.now() < nextSend) {
    suppressed++;
    return;
  }
  nextSend = Date.now() + 5000;
  const name = (reason as { name?: string } | null)?.name ?? "UnknownError";
  const errorType = [
    "Error",
    "TypeError",
    "ReferenceError",
    "RangeError",
    "SyntaxError",
    "URIError",
    "EvalError",
    "AggregateError",
    "AbortError"
  ].includes(name)
    ? name
    : "UnknownError";
  // Error text, component state, stack URLs and form values are deliberately excluded.
  void fetch("/api/panel/logs/client", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source,
      errorType,
      version: __PANEL_VERSION__,
      suppressed
    })
  }).catch(() => {});
  suppressed = 0;
}
export function installClientLogs(app: App) {
  const prior = app.config.errorHandler;
  app.config.errorHandler = (reason, instance, info) => {
    reportPanelError("vue", reason);
    prior?.(reason, instance, info);
  };
  window.addEventListener("error", event =>
    reportPanelError("window", event.error)
  );
  window.addEventListener("unhandledrejection", event =>
    reportPanelError("promise", event.reason)
  );
}
