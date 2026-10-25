const { test } = require("node:test"),
  assert = require("node:assert/strict"),
  fs = require("node:fs"),
  vm = require("node:vm"),
  ts = require("typescript"),
  path = require("node:path");
const api = {};
vm.runInNewContext(
  ts.transpileModule(
    fs.readFileSync(
      path.join(__dirname, "../src/utils/factionDraft.ts"),
      "utf8"
    ),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022
      }
    }
  ).outputText,
  { exports: api }
);
const base = {
  limitsVersion: 2,
  enabled: false,
  limits: { Lonestar: -1, Valkyra: -1, Manticore: -1 },
  balanceEnabled: true,
  maxDifference: 2,
  minimumPlayers: 0,
  stableSeconds: 0,
  cooldownSeconds: 5
};
function storage() {
  const map = new Map();
  return {
    getItem: key => map.get(key) ?? null,
    setItem: (key, value) => map.set(key, value),
    removeItem: key => map.delete(key)
  };
}
test("a two-team draft survives navigation without enabling or modifying saved settings", () => {
  const memory = storage(),
    draft = {
      ...base,
      limits: { Lonestar: 0, Valkyra: 50, Manticore: 50 },
      maxDifference: 1
    };
  api.rememberFactionDraft(memory, "server-A", base, draft);
  assert.equal(
    JSON.stringify(api.restoreFactionDraft(memory, "server-A", base)),
    JSON.stringify(draft)
  );
  assert.equal(base.limits.Lonestar, -1);
  assert.equal(base.enabled, false);
  assert.equal(api.restoreFactionDraft(memory, "server-B", base), null);
  assert.equal(
    api.restoreFactionDraft(memory, "server-A", { ...base, maxDifference: 3 }),
    null
  );
  api.rememberFactionDraft(memory, "server-A", draft, draft);
  assert.equal(api.restoreFactionDraft(memory, "server-A", draft), null);
});
test("blocked or unavailable browser storage does not prevent server-side saves", () => {
  assert.doesNotThrow(() =>
    api.rememberFactionDraft(null, "server", base, {
      ...base,
      maxDifference: 3
    })
  );
  assert.equal(api.restoreFactionDraft(null, "server", base), null);
});
