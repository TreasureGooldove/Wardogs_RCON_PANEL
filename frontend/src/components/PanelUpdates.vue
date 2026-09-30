<script setup lang="ts">
import { t, formatDate } from "@/i18n";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { http } from "@/utils/http";
import { ElMessage, ElMessageBox } from "element-plus";
import { getApiErrorMessage } from "@/api/errors";
import { useUserStoreHook } from "@/store/modules/user";

interface ReleaseStatus {
  currentVersion: string;
  latestVersion: string | null;
  updateAvailable: boolean;
  releaseUrl: string;
  publishedAt: string | null;
  checkedAt: string;
  status: "ok" | "no_release" | "rate_limited" | "unavailable";
  cached: boolean;
  source: "github" | "gitee" | null;
  installable: boolean;
  fallback: boolean;
}
interface InstallState {
  agentAvailable: boolean;
  policy: { autoInstall: boolean; source: string };
  job: { state: string; version?: string; code?: string | null };
}
const installState = ref<InstallState | null>(null);
const source = ref("auto");
const autoInstall = ref(false);
const submitting = ref(false);
const owner = computed(() => useUserStoreHook().role === "owner");
const state = ref<ReleaseStatus | null>(null);
const visible = ref(false);
const loading = ref(false);
const failed = ref(false);
let timer: ReturnType<typeof setInterval>;
const message = computed(() => {
  if (failed.value) return t("检查失败，请稍后重试");
  if (!state.value) return t("正在检查版本");
  if (state.value.status === "no_release") return t("仓库尚未发布正式版本");
  if (state.value.status === "rate_limited")
    return t("更新源请求受限，稍后会自动重试");
  if (state.value.status !== "ok") return t("暂时无法连接更新源，请稍后重试");
  return state.value.updateAvailable
    ? t("发现新版本")
    : t("当前版本已是最新或更新");
});
async function check(refresh = false) {
  if (loading.value) return;
  loading.value = true;
  failed.value = false;
  try {
    state.value = await http.request<ReleaseStatus>(
      "get",
      "/api/panel/updates",
      {
        params: { refresh, source: source.value }
      }
    );
  } catch {
    failed.value = true;
  } finally {
    loading.value = false;
  }
}
async function pollState() {
  if (!owner.value) return;
  try {
    installState.value = await http.request<InstallState>(
      "get",
      "/api/panel/updates/state"
    );
  } catch {
    /* Container restarts may temporarily disconnect polling. */
  }
}
async function open() {
  visible.value = true;
  await pollState();
  if (installState.value) {
    source.value = installState.value.policy.source;
    autoInstall.value = installState.value.policy.autoInstall;
  }
  await check(true);
}
async function submit(policy: boolean) {
  if (submitting.value) return;
  let password = "";
  try {
    const answer = await ElMessageBox.prompt(
      t(
        "更新会备份面板数据库并短暂重启面板；不会重启游戏服务器。请输入管理员密码确认。"
      ),
      policy ? t("保存自动更新设置") : t("安装新版本"),
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
  }
  submitting.value = true;
  try {
    await http.request(
      policy ? "put" : "post",
      policy ? "/api/panel/updates/policy" : "/api/panel/updates/install",
      {
        data: {
          source: source.value,
          password,
          ...(policy ? { autoInstall: autoInstall.value } : {})
        }
      }
    );
    ElMessage.success(
      policy ? t("更新设置已保存") : t("更新任务已提交，请等待结果")
    );
    await pollState();
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error));
  } finally {
    password = "";
    submitting.value = false;
  }
}
const jobMessage = computed(() => {
  const labels: Record<string, string> = {
    idle: "尚无更新任务",
    queued: "等待更新",
    downloading: "下载并校验安装包",
    building: "备份完成，正在构建镜像",
    installing: "正在安装并检查健康状态",
    success: "更新成功",
    rolled_back: "更新失败，已回退旧版本",
    failed: "更新失败，请检查宿主机更新服务"
  };
  return t(labels[installState.value?.job.state ?? "idle"] ?? "尚无更新任务");
});
let pollTimer: ReturnType<typeof setInterval>;
onMounted(() => {
  void check();
  timer = setInterval(() => {
    if (!document.hidden) void check();
  }, 3600000);
  pollTimer = setInterval(() => {
    if (visible.value) void pollState();
  }, 5000);
});
onBeforeUnmount(() => {
  clearInterval(timer);
  clearInterval(pollTimer);
});
</script>

<template>
  <el-button
    text
    :type="state?.updateAvailable ? 'warning' : 'default'"
    @click="open"
  >
    {{ state?.updateAvailable ? $t("发现新版本") : $t("检查更新") }}
  </el-button>
  <el-dialog
    v-model="visible"
    :title="$t('面板版本与更新')"
    width="min(520px, 94vw)"
    append-to-body
  >
    <div class="space-y-3">
      <p>{{ $t("当前版本：") }}{{ state?.currentVersion ?? $t("读取中") }}</p>
      <p>
        {{ $t("最新发行版本：") }}{{ state?.latestVersion ?? $t("尚未获取") }}
      </p>
      <el-select v-model="source" :disabled="submitting" @change="check(true)">
        <el-option value="auto" :label="$t('自动选择（同版本优先 Gitee）')" />
        <el-option value="gitee" label="Gitee" />
        <el-option value="github" label="GitHub" />
      </el-select>
      <p v-if="state?.source">
        {{ $t("本次更新源：") }}{{ state.source
        }}{{
          state.fallback ? $t("（Release 为空或缺少安装包，使用更新清单）") : ""
        }}
      </p>
      <el-alert
        :title="message"
        :type="state?.updateAvailable ? 'warning' : 'info'"
        :closable="false"
      />
      <p v-if="state" class="text-sm text-gray-500">
        {{ $t("检查时间：") }}{{ formatDate(state.checkedAt)
        }}{{ state.cached ? $t("（缓存结果）") : "" }}
      </p>
      <p class="text-sm text-gray-500">
        {{
          $t(
            "登录后及页面打开期间每小时自动检查。升级前请备份服务器配置和面板数据。"
          )
        }}
      </p>
      <template v-if="owner">
        <el-alert
          v-if="!installState?.agentAvailable"
          :title="$t('宿主机更新程序未启用，请按部署教程安装更新服务')"
          type="warning"
          :closable="false"
        />
        <p>{{ jobMessage }} {{ installState?.job.version ?? "" }}</p>
        <el-switch
          v-model="autoInstall"
          :disabled="!installState?.agentAvailable"
          :active-text="$t('启用后台自动安装（每小时检查）')"
        />
        <el-button :loading="submitting" @click="submit(true)">{{
          $t("保存自动更新设置")
        }}</el-button>
      </template>
    </div>
    <template #footer>
      <el-button :loading="loading" @click="check(true)">{{
        $t("立即检查")
      }}</el-button>
      <el-button
        v-if="owner"
        type="warning"
        :loading="submitting"
        :disabled="
          !state?.updateAvailable ||
          !state.installable ||
          !installState?.agentAvailable ||
          ['queued', 'downloading', 'building', 'installing'].includes(
            installState.job.state
          )
        "
        @click="submit(false)"
        >{{ $t("安装新版本") }}</el-button
      >
      <el-button
        tag="a"
        :href="
          state?.releaseUrl ??
          'https://github.com/TreasureGooldove/Wardogs_RCON_PANEL/releases'
        "
        target="_blank"
        rel="noopener noreferrer"
        type="primary"
        >{{ $t("查看 Release") }}</el-button
      >
    </template>
  </el-dialog>
</template>
