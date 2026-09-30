<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { http } from "@/utils/http";

interface ReleaseStatus {
  currentVersion: string;
  latestVersion: string | null;
  updateAvailable: boolean;
  releaseUrl: string;
  publishedAt: string | null;
  checkedAt: string;
  status: "ok" | "no_release" | "rate_limited" | "unavailable";
  cached: boolean;
}
const state = ref<ReleaseStatus | null>(null);
const visible = ref(false);
const loading = ref(false);
const failed = ref(false);
let timer: ReturnType<typeof setInterval>;
const message = computed(() => {
  if (failed.value) return "检查失败，请稍后重试";
  if (!state.value) return "正在检查版本";
  if (state.value.status === "no_release") return "仓库尚未发布正式版本";
  if (state.value.status === "rate_limited") return "GitHub 请求受限，稍后会自动重试";
  if (state.value.status !== "ok") return "暂时无法连接 GitHub，请稍后重试";
  return state.value.updateAvailable ? "发现新版本" : "当前版本已是最新或更新";
});
async function check(refresh = false) {
  if (loading.value) return;
  loading.value = true;
  failed.value = false;
  try {
    state.value = await http.request<ReleaseStatus>("get", "/api/panel/updates", {
      params: { refresh }
    });
  } catch {
    failed.value = true;
  } finally {
    loading.value = false;
  }
}
onMounted(() => {
  void check();
  timer = setInterval(() => { if (!document.hidden) void check(); }, 3600000);
});
onBeforeUnmount(() => clearInterval(timer));
</script>

<template>
  <el-button text :type="state?.updateAvailable ? 'warning' : 'default'" @click="visible = true">
    {{ state?.updateAvailable ? "发现新版本" : "检查更新" }}
  </el-button>
  <el-dialog v-model="visible" title="面板版本与更新" width="min(520px, 94vw)" append-to-body>
    <div class="space-y-3">
      <p>当前版本：{{ state?.currentVersion ?? "读取中" }}</p>
      <p>GitHub 最新 Release：{{ state?.latestVersion ?? "尚未获取" }}</p>
      <el-alert :title="message" :type="state?.updateAvailable ? 'warning' : 'info'" :closable="false" />
      <p v-if="state" class="text-sm text-gray-500">检查时间：{{ new Date(state.checkedAt).toLocaleString() }}{{ state.cached ? "（缓存结果）" : "" }}</p>
      <p class="text-sm text-gray-500">登录后及页面打开期间每小时自动检查。升级前请备份服务器配置和面板数据。</p>
    </div>
    <template #footer>
      <el-button :loading="loading" @click="check(true)">立即检查</el-button>
      <el-button tag="a" :href="state?.releaseUrl ?? 'https://github.com/TreasureGooldove/Wardogs_RCON_PANEL/releases'" target="_blank" rel="noopener noreferrer" type="primary">查看 Release</el-button>
    </template>
  </el-dialog>
</template>
