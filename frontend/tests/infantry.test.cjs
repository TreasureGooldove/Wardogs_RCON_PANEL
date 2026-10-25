const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");
const helper = {};
vm.runInNewContext(
  ts.transpileModule(
    fs.readFileSync(
      path.join(__dirname, "../src/utils/rotationConfig.ts"),
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
const section = "[/Script/WDGame.WDServerMapRotationSettings]";
const entry = (map, experiences, extras = "") =>
  `.RotationEntries=(Map="${map}",Experience="${experiences}",Lighting="DayLateRain",ZoneAlternator="Zone.X"${extras})`;

test("native infantry modifier edits only experiences, preserving the actual file and repeated entries", () => {
  const line = entry(
    "Kavkazi",
    "Bakurani_KOTH_02",
    ',Unknown="keep,Experience=untouched",Nested=(X=1,Y=2)'
  );
  const text =
    "\uFEFF[Private]\r\nPassword=fictional-only\r\n" +
    section +
    "\r\n" +
    "bEnabled=False\r\nRotationMode=Random\r\n!RotationEntries=ClearArray\r\n" +
    line +
    "\r\n; keep comment\r\n" +
    line.replace(".Rotation", "+Rotation") +
    "\r\n[Other]\r\nValue=3\r\n";
  const out = helper.setInfantryOnRotation(text, true, () => true);
  assert.equal(
    out.text,
    text.replaceAll(
      'Experience="Bakurani_KOTH_02"',
      'Experiences="Bakurani_KOTH_02+KOTH_InfantryOnly"'
    )
  );
  assert.equal(out.changed, 2);
  assert.equal(out.skipped, 0);
  assert.equal(
    helper.setInfantryOnRotation(out.text, true, () => true).text,
    out.text
  );
  assert.equal(
    helper.setInfantryOnRotation(out.text, false, () => false).text,
    text
  );
});

test("unsupported maps and missing base modes are skipped without creating defaults", () => {
  const text =
    section +
    "\n" +
    entry("Europe", "Madrid_KOTH_01") +
    "\n" +
    entry("Unsupported", "ExampleMode") +
    '\n.RotationEntries=(Map="MissingMode",Lighting="Unusual")\n' +
    entry("ModifierOnly", "KOTH_Hardcore") +
    "\n";
  const out = helper.setInfantryOnRotation(text, true, map => map === "Europe");
  assert.equal(out.changed, 1);
  assert.equal(out.skipped, 3);
  assert.equal(
    out.text,
    text.replace(
      'Experience="Madrid_KOTH_01"',
      'Experiences="Madrid_KOTH_01+KOTH_InfantryOnly"'
    )
  );
  assert.ok(out.text.includes('Lighting="Unusual"'));
});

test("removing infantry is case insensitive, retains other modifiers and never empties the only experience", () => {
  const text =
    section +
    "\n" +
    entry("Europe", "Madrid_KOTH_01+koth_infantryonly+KOTH_Hardcore") +
    "\n" +
    entry("Kavkazi", "KOTH_InfantryOnly");
  const out = helper.setInfantryOnRotation(text, false, () => {
    throw new Error("removal needs no catalog read");
  });
  assert.equal(out.changed, 1);
  assert.equal(out.skipped, 1);
  assert.ok(out.text.includes('Experiences="Madrid_KOTH_01+KOTH_Hardcore"'));
  assert.ok(out.text.endsWith(entry("Kavkazi", "KOTH_InfantryOnly")));
  assert.equal(helper.hasInfantryOnly(" KOTH_InfantryOnly "), true);
  assert.equal(helper.hasInfantryOnly(null), false);
});

test("ambiguous or broken documents fail without producing a rewritten file", () => {
  assert.throws(() =>
    helper.setInfantryOnRotation("[Other]\nValue=1", true, () => true)
  );
  for (const line of [
    '.RotationEntries=(Map="Europe",Experience="Madrid_KOTH_01",Experiences="Other")',
    '.RotationEntries=(Map="Europe",Experience="Madrid_KOTH_01)'
  ])
    assert.throws(() =>
      helper.setInfantryOnRotation(section + "\n" + line, true, () => true)
    );
  assert.throws(() =>
    helper.setInfantryOnRotation(
      section + "\n" + entry("Europe", "Madrid_KOTH_01") + "\n" + section,
      true,
      () => true
    )
  );
});
