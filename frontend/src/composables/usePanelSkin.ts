import { ref, watch } from "vue";

const key = "wardogs-panel-skin";
function readSkin() {
  try {
    return localStorage.getItem(key) !== "classic";
  } catch {
    return true;
  }
}

export const commandSkin = ref(readSkin());

watch(
  commandSkin,
  enabled => {
    document.documentElement.dataset.panelSkin = enabled
      ? "command"
      : "classic";
    try {
      localStorage.setItem(key, enabled ? "command" : "classic");
    } catch {
      // Appearance remains usable when browser storage is unavailable.
    }
  },
  { immediate: true }
);
