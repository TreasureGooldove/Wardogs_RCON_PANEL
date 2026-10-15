const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function setup(getPlayers) {
  const hooks = {};
  const timers = new Map();
  const events = new Map();
  let timerId = 0;
  const vue = {
    ref(value) {
      const observers = [];
      return {
        get value() {
          return value;
        },
        set value(next) {
          value = next;
          observers.forEach(fn => fn(next));
        },
        observers
      };
    },
    watch(source, callback) {
      source.observers.push(callback);
    }
  };
  for (const name of [
    "onMounted",
    "onUnmounted",
    "onActivated",
    "onDeactivated"
  ])
    vue[name] = callback => {
      hooks[name] = callback;
    };
  const document = {
    visibilityState: "visible",
    addEventListener: (name, callback) => events.set(name, callback),
    removeEventListener: name => events.delete(name)
  };
  const exports = {};
  const source = fs.readFileSync(
    path.join(__dirname, "../src/composables/usePlayersPolling.ts"),
    "utf8"
  );
  vm.runInNewContext(
    ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022
      }
    }).outputText,
    {
      exports,
      document,
      require(name) {
        if (name === "vue") return vue;
        if (name === "@/api/players") return { getPlayers };
        if (name === "@/api/errors")
          return { getApiErrorMessage: () => "unavailable" };
        throw Error(name);
      },
      setInterval(callback) {
        timers.set(++timerId, callback);
        return timerId;
      },
      clearInterval(id) {
        timers.delete(id);
      }
    }
  );
  return { ...exports, vue, hooks, timers, events, document };
}

test("pausing stops roster requests, manual refresh reads once, resuming and unmount clean up", async () => {
  let calls = 0;
  const env = setup(async () => {
    calls++;
    return { players: [], observedAt: "fixture" };
  });
  const paused = env.vue.ref(false);
  const polling = env.usePlayersPolling({ paused });
  env.hooks.onMounted();
  await polling.refresh();
  assert.equal(calls, 1);
  assert.equal(env.timers.size, 1);
  paused.value = true;
  assert.equal(env.timers.size, 0);
  await polling.refresh();
  assert.equal(calls, 1);
  await polling.refresh(true);
  assert.equal(calls, 2);
  assert.equal(env.timers.size, 0);
  paused.value = false;
  await polling.refresh();
  assert.equal(calls, 3);
  assert.equal(env.timers.size, 1);
  env.hooks.onUnmounted();
  assert.equal(env.timers.size, 0);
  assert.equal(env.events.size, 0);
  await polling.refresh(true);
  assert.equal(calls, 3);
});

test("overlapping manual and automatic refreshes share one request and hidden pages do not poll", async () => {
  let calls = 0,
    resolve;
  const env = setup(() => {
    calls++;
    return new Promise(done => {
      resolve = done;
    });
  });
  const polling = env.usePlayersPolling();
  env.hooks.onMounted();
  const first = polling.refresh();
  const second = polling.refresh(true);
  assert.equal(calls, 1);
  assert.equal(first, second);
  resolve({ players: [], observedAt: "fixture" });
  await first;
  env.document.visibilityState = "hidden";
  env.events.get("visibilitychange")();
  await polling.refresh(true);
  assert.equal(calls, 1);
  assert.equal(env.timers.size, 0);
  env.hooks.onUnmounted();
});
