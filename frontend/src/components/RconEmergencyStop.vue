<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { http } from "@/utils/http";
import { getApiErrorMessage } from "@/api/errors";
import { useUserStoreHook } from "@/store/modules/user";
import { t } from "@/i18n";

interface ConnectionState {
  paused: boolean;
  configured: boolean;
  updatedAt: string | null;
  targetRevision: string;
}
const state = ref<ConnectionState | null>(null);
const failed = ref(false);
const busy = ref(false);
const confirming = ref(false);
let revision = 0;
const owner = computed(() => useUserStoreHook().role === "owner");
let timer: ReturnType<typeof setInterval>;
async function refresh() {
  if (busy.value || document.hidden) return;
  const requested = ++revision;
  try {
    const result = await http.request<ConnectionState>(
      "get",
      "/api/server/connection"
    );
    if (requested === revision && !busy.value) {
      state.value = result;
      failed.value = false;
    }
  } catch {
    if (requested === revision && !busy.value) failed.value = true;
  }
}
async function change() {
  if (busy.value || confirming.value) return;
  const resume = state.value?.paused === true;
  let password = "";
  confirming.value = true;
  try {
    const answer = await ElMessageBox.prompt(
      t(
        resume
          ? "恢复后重新连接服务器；阵营、物品和反作弊自动规则需要重新确认启用。请输入管理员密码。"
          : "停止后将暂停 RCON 采集、自动规则和机器人管理操作；不会关闭游戏服务器。已发出的请求可能已经执行，无法撤销。请输入管理员密码。"
      ),
      t(resume ? "恢复连接" : "紧急停止连接"),
      {
        inputType: "password",
        inputValidator: value => !!value || t("请输入密码"),
        confirmButtonText: t("确认"),
        cancelButtonText: t("取消")
      }
    );
    password = answer.value;
  } catch {
    return;
  } finally {
    confirming.value = false;
  }
  busy.value = true;
  ++revision;
  try {
    state.value = await http.request<ConnectionState>(
      "post",
      `/api/server/connection/${resume ? "resume" : "stop"}`,
      { data: { password } }
    );
    ElMessage.success(t(resume ? "连接已恢复" : "连接已紧急停止"));
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error));
  } finally {
    password = "";
    busy.value = false;
    void refresh();
  }
}
onMounted(() => {
  void refresh();
  timer = setInterval(() => void refresh(), 5000);
});
onBeforeUnmount(() => clearInterval(timer));
</script>

<template>
  <span class="rcon-emergency">
    <el-button
      v-if="owner"
      size="small"
      :type="state?.paused ? 'warning' : 'danger'"
      :loading="busy"
      :title="
        failed
          ? t('连接状态读取失败')
          : state?.paused
            ? t('停止状态会保留，重启面板不会自动恢复。')
            : t('紧急停止连接')
      "
      @click="change"
    >
      {{ t(state?.paused ? "恢复连接" : "停止连接") }}
    </el-button>
    <el-tag v-else-if="state?.paused" type="danger" size="small">{{
      t("RCON 已停止")
    }}</el-tag>
  </span>
</template>

<style scoped>
.rcon-emergency {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  margin: 0 8px;
}
</style>
