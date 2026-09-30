const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const ts = require("typescript");

const exportsObject = {};
vm.runInNewContext(
  ts.transpileModule(
    fs.readFileSync(
      require("node:path").join(__dirname, "../src/utils/configFields.ts"),
      "utf8"
    ),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2020
      }
    }
  ).outputText,
  { exports: exportsObject }
);
const {
  discoverConfigFieldGroups,
  readConfigField,
  writeConfigField,
  captureConfigPreset,
  applyConfigPreset
} = exportsObject;

test("only actual scalar assignments become fields; empty and unusual values stay real", () => {
  const text =
    "\uFEFF[/Script/WDGame.WDGameSession]\r\nServerName=Real\r\nServerPassword=\r\n" +
    ".DefaultReservedPlayerIds=76561198000000001\r\n[Custom]\r\nCash=-3\r\nFlag=unknown\r\nEnabled=False\r\nObject=(X=1)\r\n";
  const fields = discoverConfigFieldGroups(text).flatMap(g => g.fields);
  assert.deepEqual(
    Array.from(fields, f => f.key),
    ["ServerName", "ServerPassword", "Cash", "Flag", "Enabled"]
  );
  assert.equal(readConfigField(text, fields[1]), "");
  assert.equal(fields[2].kind, "text");
  assert.equal(readConfigField(text, fields[2]), "-3");
  assert.equal(fields[3].kind, "text");
  assert.equal(fields[4].kind, "boolean");
});

test("last repeated assignment is edited without changing unknown lines or adding keys", () => {
  const text =
    "[Custom]\r\nvalue=old\r\n[Other]\r\n# keep me\r\nUnknown=untouched\r\n" +
    "[custom]\r\nVALUE=new\r\n";
  const field = discoverConfigFieldGroups(text)[0].fields[0];
  assert.equal(readConfigField(text, field), "new");
  assert.equal(
    writeConfigField(text, field, "edited"),
    text.replace("VALUE=new", "VALUE=edited")
  );
  assert.throws(() =>
    writeConfigField(
      text,
      { section: "Custom", key: "Missing", kind: "text" },
      "0"
    )
  );
});

test("presets omit absent fields and cannot introduce a stale template key", () => {
  const text = "[/Script/WDGame.WDGameSession]\nServerName=Real\n";
  const preset = captureConfigPreset("test", text);
  assert.deepEqual(Object.keys(preset.values), [
    "/Script/WDGame.WDGameSession|ServerName"
  ]);
  preset.values["/Script/Engine.GameSession|MaxPlayers"] = "100";
  assert.equal(applyConfigPreset(text, preset), text);
});

test("localized labels distinguish join password, RCON and rotation without changing document values", () => {
  const text =
    "[/Script/WDGame.WDGameSession]\nServerPassword=\n" +
    "[/Script/WDGame.WDServerMapRotationSettings]\nbEnabled=true\nRotationMode=Ordered\n" +
    "[/Script/WDRCON.WDRCONSettings]\nbEnabled=true\nPassword=__WD_REDACTED_rev1__\nBindAddress=127.0.0.1\nPort=47990\n";
  const groups = discoverConfigFieldGroups(text);
  assert.deepEqual(
    Array.from(groups, g => g.title),
    ["服务器与玩家", "地图轮换设置", "RCON 连接设置"]
  );
  assert.equal(groups[0].fields[0].label, "加入密码");
  assert.equal(groups[1].fields[0].label, "启用地图轮换");
  assert.equal(groups[2].fields[0].label, "启用 RCON");
  assert.equal(groups[2].fields[1].label, "RCON 密码");
  const mode = groups[1].fields[1];
  assert.equal(readConfigField(text, mode), "Ordered");
  assert.equal(
    exportsObject.configValueLabel(mode, "Ordered"),
    "按列表顺序轮换"
  );
  assert.equal(exportsObject.configValueLabel(mode, "FutureMode"), null);
  assert.equal(writeConfigField(text, mode, "Ordered"), text);
});
