const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const ts = require("typescript");
const api = {};
vm.runInNewContext(
  ts.transpileModule(
    fs.readFileSync(
      path.join(__dirname, "../src/utils/updateStatus.ts"),
      "utf8"
    ),
    { compilerOptions: { module: ts.ModuleKind.CommonJS } }
  ).outputText,
  { exports: api }
);

test("loading and request failure do not claim that the host updater is absent", () => {
  assert.equal(api.updaterNotice(null).type, "info");
  for (const state of [null, { agentAvailable: true }]) {
    const failed = api.updaterNotice(state, true);
    assert.equal(failed.type, "error");
    assert.equal(failed.title, "无法读取更新服务状态");
  }
});

test("heartbeat diagnoses have distinct guidance and old servers have an honest fallback", () => {
  const titles = new Set();
  for (const status of [
    "missing",
    "stale",
    "unreadable",
    "invalid",
    "clock_skew"
  ]) {
    const notice = api.updaterNotice({
      agentAvailable: false,
      agent: { status }
    });
    assert.equal(notice.type, "warning");
    titles.add(notice.title);
    assert.ok(notice.detail.length);
  }
  assert.equal(titles.size, 5);
  assert.equal(
    api.updaterNotice({ agentAvailable: false }).title,
    "宿主机更新服务暂不可用"
  );
  assert.equal(api.updaterNotice({ agentAvailable: true }).type, "success");
});
