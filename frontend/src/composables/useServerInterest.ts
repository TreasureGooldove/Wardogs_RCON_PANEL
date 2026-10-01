import { onMounted, onUnmounted } from "vue";
import { http } from "@/utils/http";

/** One lease per layout; hidden browsers do not keep the worker in watched cadence. */
export function useServerInterest() {
  let timer: ReturnType<typeof setTimeout>;
  let stopped = false;
  async function beat() {
    if (stopped) return;
    if (!document.hidden) {
      try {
        await http.request("get", "/api/community/observation");
      } catch {
        /* Page APIs display errors. */
      }
    }
    if (!stopped) timer = setTimeout(beat, 5000);
  }
  function visible() {
    if (!document.hidden) {
      clearTimeout(timer);
      void beat();
    }
  }
  onMounted(() => {
    void beat();
    document.addEventListener("visibilitychange", visible);
  });
  onUnmounted(() => {
    stopped = true;
    clearTimeout(timer);
    document.removeEventListener("visibilitychange", visible);
  });
}
