const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const vm = require("node:vm");
const helper = {};
vm.runInNewContext(
  ts.transpileModule(
    fs.readFileSync(
      path.join(__dirname, "../src/utils/factionControl.ts"),
      "utf8"
    ),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022
      }
    }
  ).outputText,
  { exports: helper }
);

test("50V50 drafts can close any faction without enabling automation or mutating saved settings", () => {
  const current = {
    limitsVersion: 2,
    enabled: false,
    limits: { Lonestar: -1, Valkyra: -1, Manticore: -1 },
    balanceEnabled: false,
    maxDifference: 10,
    minimumPlayers: 80,
    stableSeconds: 30,
    cooldownSeconds: 15
  };
  for (const closed of Object.keys(current.limits)) {
    const draft = helper.fiftyVsFiftyDraft(current, closed);
    assert.equal(draft.enabled, false);
    assert.equal(draft.limitsVersion, 2);
    assert.equal(draft.minimumPlayers, 0);
    assert.equal(draft.maxDifference, 1);
    assert.equal(draft.balanceEnabled, true);
    assert.equal(draft.stableSeconds, 30);
    assert.equal(draft.cooldownSeconds, 15);
    assert.equal(draft.limits[closed], 0);
    assert.equal(
      Object.values(draft.limits).filter(value => value === 50).length,
      2
    );
  }
  assert.deepEqual(current.limits, {
    Lonestar: -1,
    Valkyra: -1,
    Manticore: -1
  });
  assert.throws(() => helper.fiftyVsFiftyDraft(current, "Unknown"));
});
