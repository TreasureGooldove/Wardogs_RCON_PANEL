const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");
const axios = require("axios");

function client(code, probeStatus = 200) {
  const stats = { clears: 0, events: 0, probes: 0 };
  const adapter = async config => {
    const probe = config.url === "/api/auth/me";
    if (probe) stats.probes++;
    const status = probe ? probeStatus : 401;
    if (status === 0) throw new axios.AxiosError("fixture network failure");
    const response = {
      status,
      statusText: "",
      data: probe ? {} : { code },
      headers: {},
      config
    };
    if (status >= 400)
      throw new axios.AxiosError(
        "fixture HTTP error",
        "ERR_BAD_REQUEST",
        config,
        null,
        response
      );
    return response;
  };
  const facade = {
    ...axios,
    create: config => axios.create({ ...config, adapter }),
    get: (url, config) => axios.get(url, { ...config, adapter })
  };
  const modules = {};
  function load(name) {
    if (modules[name]) return modules[name];
    const api = {};
    const filename = path.join(__dirname, "../src/utils/http/", name + ".ts");
    vm.runInNewContext(
      ts.transpileModule(fs.readFileSync(filename, "utf8"), {
        compilerOptions: {
          module: ts.ModuleKind.CommonJS,
          target: ts.ScriptTarget.ES2022
        }
      }).outputText,
      {
        exports: api,
        require: id =>
          id === "axios"
            ? { default: facade }
            : id === "qs"
              ? require("qs")
              : id.includes("store/modules/user")
                ? {
                    useUserStoreHook: () => ({
                      clearSession: () => stats.clears++
                    })
                  }
                : id === "./session"
                  ? load("session")
                  : (() => {
                      throw new Error("Unexpected import: " + id);
                    })(),
        window: { dispatchEvent: () => stats.events++ },
        Event: class {
          constructor(type) {
            this.type = type;
          }
        }
      }
    );
    modules[name] = api;
    return api;
  }
  return { http: load("index").http, stats };
}

test("wrong second-confirmation password never clears an authenticated panel session", async () => {
  const { http, stats } = client("invalid_credentials");
  await assert.rejects(
    http.request("put", "/api/server/config/rotation", {
      data: { password: "Fictional-wrong-password" }
    })
  );
  assert.equal(stats.clears, 0);
  assert.equal(stats.events, 0);
  assert.equal(stats.probes, 0);
});
test("a legacy origin rejection is checked against the real session before redirecting", async () => {
  const { http, stats } = client("not_authenticated", 200);
  await assert.rejects(http.request("put", "/api/server/config/rotation"));
  assert.equal(stats.probes, 1);
  assert.equal(stats.clears, 0);
});
test("concurrent authentication failures share one session probe and expiry notification", async () => {
  const { http, stats } = client("not_authenticated", 401);
  await Promise.allSettled(
    [1, 2, 3].map(() => http.request("get", "/api/server/status"))
  );
  assert.equal(stats.probes, 1);
  assert.equal(stats.clears, 1);
  assert.equal(stats.events, 1);
});
test("a failed session probe does not invent session expiry", async () => {
  const { http, stats } = client("not_authenticated", 0);
  await assert.rejects(http.request("put", "/api/server/config/rotation"));
  assert.equal(stats.clears, 0);
});
