import { onActivated, onDeactivated, onMounted, onUnmounted, ref } from "vue";
import { getApiErrorMessage } from "@/api/errors";
import { getPlayers, type PlayersResponse } from "@/api/players";

const PLAYER_POLL_INTERVAL_MS = 5_000;

export function usePlayersPolling() {
  const snapshot = ref<PlayersResponse | null>(null);
  const loading = ref(false);
  const error = ref("");
  let timer: ReturnType<typeof setInterval> | undefined;
  let active = false;
  let pending: Promise<void> | null = null;

  function stopTimer() {
    if (timer) clearInterval(timer);
    timer = undefined;
  }

  function startTimer() {
    if (!timer && document.visibilityState === "visible") {
      timer = setInterval(() => void refresh(), PLAYER_POLL_INTERVAL_MS);
    }
  }

  function refresh(): Promise<void> {
    if (!active || document.visibilityState !== "visible") {
      return Promise.resolve();
    }
    if (pending) return pending;

    loading.value = true;
    error.value = "";
    pending = (async () => {
      try {
        const response = await getPlayers();
        if (active && document.visibilityState === "visible") {
          snapshot.value = response;
        }
      } catch (reason) {
        if (active && document.visibilityState === "visible") {
          if (snapshot.value)
            snapshot.value = { ...snapshot.value, stale: true };
          error.value = getApiErrorMessage(reason);
        }
      } finally {
        loading.value = false;
      }
    })().finally(() => {
      pending = null;
    });
    return pending;
  }

  async function refreshAfterMutation(): Promise<void> {
    if (pending) await pending;
    await refresh();
  }

  function onVisibilityChange() {
    if (!active) return;
    if (document.visibilityState === "visible") {
      void refresh();
      startTimer();
    } else {
      stopTimer();
    }
  }

  function start() {
    if (active) return;
    active = true;
    if (pending) {
      void pending.then(() => {
        if (active) void refresh();
      });
    } else {
      void refresh();
    }
    startTimer();
    document.addEventListener("visibilitychange", onVisibilityChange);
  }

  function stop() {
    if (!active) return;
    active = false;
    stopTimer();
    document.removeEventListener("visibilitychange", onVisibilityChange);
  }

  onMounted(start);
  onActivated(start);
  onDeactivated(stop);
  onUnmounted(stop);

  return { snapshot, loading, error, refresh, refreshAfterMutation };
}
