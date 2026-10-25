const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

const root = path.join(__dirname, "../src/i18n");
const core = {};
vm.runInNewContext(
  ts.transpileModule(fs.readFileSync(path.join(root, "core.ts"), "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022
    }
  }).outputText,
  { exports: core }
);
const dictionaries = Object.fromEntries(
  core.LOCALE_CODES.map(code => [
    code,
    JSON.parse(fs.readFileSync(path.join(root, code + ".json"), "utf8"))
  ])
);
const tokens = text => (text.match(/\{[A-Za-z][A-Za-z0-9_]*\}/g) || []).sort();

for (const file of [
  "faction-control.json",
  "faction-shuffle.json",
  "infantry.json"
])
  test(`${file} messages cover every supported language`, () => {
    const messages = JSON.parse(fs.readFileSync(path.join(root, file), "utf8"));
    for (const code of core.LOCALE_CODES) {
      assert.deepEqual(
        Object.keys(messages[code]).sort(),
        Object.keys(messages["zh-CN"]).sort()
      );
      for (const [key, value] of Object.entries(messages[code])) {
        assert.ok(value.trim());
        assert.deepEqual(tokens(value), tokens(key));
      }
    }
  });

test("delayed awards messages cover all supported locales", () => {
  const awards = JSON.parse(
    fs.readFileSync(path.join(root, "awards.json"), "utf8")
  );
  const keys = Object.keys(awards["zh-CN"]).sort();
  for (const code of core.LOCALE_CODES) {
    assert.deepEqual(Object.keys(awards[code]).sort(), keys, code);
    for (const key of keys) {
      assert.ok(awards[code][key].trim());
      assert.deepEqual(tokens(awards[code][key]), tokens(key));
    }
  }
});

test("all locales cover the same messages and retain dynamic placeholders", () => {
  const keys = Object.keys(dictionaries["zh-CN"]).sort();
  for (const [code, messages] of Object.entries(dictionaries)) {
    assert.deepEqual(Object.keys(messages).sort(), keys, code);
    for (const key of keys) {
      assert.ok(messages[key].trim(), `${code}: empty message`);
      assert.deepEqual(
        tokens(messages[key]),
        tokens(key),
        `${code}: placeholder mismatch`
      );
      assert.ok(
        !messages[key].includes("[[I18N_"),
        `${code}: translation marker leaked`
      );
    }
  }
  assert.ok(
    Object.values(dictionaries["en-US"]).every(
      text => !/[\u3400-\u9fff]/.test(text)
    )
  );
});

test("player names and user text are inserted once, preserving braces and Unicode", () => {
  const name = "玩家 <img src=x> {p1}";
  assert.equal(
    core.translate(
      "确认 {p0} / {p1}",
      { "确认 {p0} / {p1}": "Confirm {p0} / {p1}" },
      { p0: name, p1: "76561198000000001" }
    ),
    "Confirm " + name + " / 76561198000000001"
  );
  assert.equal(core.translate(name, dictionaries["en-US"]), name);
  assert.equal(
    core.translate("User template: {x}", {}, {}),
    "User template: {x}"
  );
  assert.equal(
    core.translate("constructor", dictionaries["en-US"]),
    "constructor"
  );
  assert.equal(core.normalizeLocale("untrusted"), "zh-CN");
});

test("dangerous command prompt and expected confirmation use the same localized token", () => {
  for (const messages of Object.values(dictionaries)) {
    const expected = core.translate("确认执行", messages);
    const prompt = core.translate(
      "再次确认执行“{p0}”。请输入“{p1}”后才会发送命令。",
      messages,
      { p0: "endmatch", p1: expected }
    );
    assert.ok(prompt.includes(expected) && prompt.includes("endmatch"));
    assert.ok(
      core
        .translate("请输入：{p0}", messages, { p0: expected })
        .includes(expected)
    );
  }
});
