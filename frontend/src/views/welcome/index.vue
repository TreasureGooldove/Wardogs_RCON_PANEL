<script setup lang="ts">
import { onActivated, onDeactivated, onMounted, onUnmounted, ref } from "vue";
import { getCapabilities, type CapabilitiesResponse } from "@/api/capabilities";
import { getServerStatus, type StatusResponse } from "@/api/status";
import { getRotation, type RotationResponse } from "@/api/rotation";
import { getApiErrorMessage } from "@/api/errors";
import { usePlayersPolling } from "@/composables/usePlayersPolling";
import CommandOverview from "@/components/CommandOverview.vue";

defineOptions({ name: "Welcome" });
const status = ref<StatusResponse | null>(null);
const capabilities = ref<CapabilitiesResponse | null>(null);
const rotation = ref<RotationResponse | null>(null);
const loading = ref(false);
const error = ref("");
const rotationError = ref("");
const realtime = ref(true);
const pollingPaused = ref(false);
const {
  snapshot: playerSnapshot,
  loading: playerLoading,
  error: playerError,
  refresh: refreshPlayers
} = usePlayersPolling({ paused: pollingPaused });
let timer: ReturnType<typeof setInterval> | undefined;
let active = false;
let rerunWhenIdle = false;
let rotationPending = false;
let lastRotationAt = 0;

async function refreshRotation(force = false) {
  if (
    !active ||
    document.hidden ||
    rotationPending ||
    (!force && Date.now() - lastRotationAt < 60_000)
  )
    return;
  rotationPending = true;
  lastRotationAt = Date.now();
  try {
    const result = await getRotation();
    if (active) {
      rotation.value = result;
      rotationError.value = "";
    }
  } catch (reason) {
    if (active) {
      if (rotation.value) rotation.value = { ...rotation.value, stale: true };
      rotationError.value = getApiErrorMessage(reason);
    }
  } finally {
    rotationPending = false;
  }
}

async function refresh(manual = false) {
  if (!active || document.hidden || (!realtime.value && !manual)) return;
  if (loading.value) {
    rerunWhenIdle = true;
    return;
  }
  loading.value = true;
  const [statusResult, capabilityResult] = await Promise.allSettled([
    getServerStatus(),
    getCapabilities()
  ]);
  if (active && !document.hidden) {
    if (statusResult.status === "fulfilled") {
      const nextChanged =
        status.value?.rotation?.nextIndex !==
        statusResult.value.rotation?.nextIndex;
      status.value = statusResult.value;
      error.value = "";
      void refreshRotation(nextChanged);
    } else {
      if (status.value) status.value = { ...status.value, stale: true };
      error.value = getApiErrorMessage(statusResult.reason);
    }
    capabilities.value =
      capabilityResult.status === "fulfilled" ? capabilityResult.value : null;
  }
  loading.value = false;
  if (rerunWhenIdle) {
    rerunWhenIdle = false;
    void refresh();
  }
}

function refreshAll() {
  void refresh(true);
  // A manual refresh may read one roster while automatic refresh is paused.
  void refreshPlayers(true);
  void refreshRotation(true);
}

function startTimer() {
  if (!timer && !document.hidden && realtime.value)
    timer = setInterval(() => void refresh(), 2_000);
}
function setRealtime(value: boolean) {
  realtime.value = value;
  pollingPaused.value = !value;
  if (!value) {
    if (timer) clearInterval(timer);
    timer = undefined;
  } else {
    void refresh();
    startTimer();
  }
}
function onVisibilityChange() {
  if (!active) return;
  if (!document.hidden) {
    void refresh();
    startTimer();
  } else {
    if (timer) clearInterval(timer);
    timer = undefined;
  }
}
function start() {
  if (active) return;
  active = true;
  void refresh();
  startTimer();
  document.addEventListener("visibilitychange", onVisibilityChange);
}
function stop() {
  active = false;
  rerunWhenIdle = false;
  if (timer) clearInterval(timer);
  timer = undefined;
  document.removeEventListener("visibilitychange", onVisibilityChange);
}
onMounted(start);
onActivated(start);
onDeactivated(stop);
onUnmounted(stop);
</script>

<template>
  <CommandOverview
    :status="status"
    :players="playerSnapshot"
    :rotation="rotation"
    :capabilities="capabilities"
    :loading="loading || playerLoading"
    :realtime="realtime"
    :error="error"
    :player-error="playerError"
    :rotation-error="rotationError"
    @refresh="refreshAll"
    @realtime="setRealtime"
  />
</template>
