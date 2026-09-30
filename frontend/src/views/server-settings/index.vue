<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { getApiErrorMessage } from "@/api/errors";
import {
  getServerSettings,
  updateServerSettings,
  type ServerSettings,
  type ServerSettingsUpdate
} from "@/api/serverSettings";
import { formatObservedAt } from "@/api/snapshot";

defineOptions({ name: "ServerSettings" });

const current = ref<ServerSettings | null>(null);
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const form = reactive({
  name: "",
  origin: "",
  bearer: "",
  allowPublicHttp: false
});

const httpOrigin = computed(() =>
  form.origin.trim().toLowerCase().startsWith("http://")
);
const changedOrigin = computed(
  () =>
    normalizeOrigin(form.origin) !==
    normalizeOrigin(current.value?.origin ?? "")
);

function applyCurrent(settings: ServerSettings) {
  current.value = settings;
  form.name = settings.name;
  form.origin = settings.origin;
  form.bearer = "";
  form.allowPublicHttp = settings.allowPublicHttp;
}

async function load() {
  if (loading.value) return;
  loading.value = true;
  error.value = "";
  try {
    applyCurrent(await getServerSettings());
  } catch (reason) {
    error.value = getApiErrorMessage(reason);
  } finally {
    loading.value = false;
  }
}

function normalizeOrigin(value: string): string | null {
  try {
    const url = new URL(value.trim());
    if (
      !["http:", "https:"].includes(url.protocol) ||
      !url.hostname ||
      url.username ||
      url.password ||
      url.pathname !== "/" ||
      url.search ||
      url.hash
    ) {
      return null;
    }
    return url.origin;
  } catch {
    return null;
  }
}

async function save() {
  if (saving.value || !current.value) return;
  const name = form.name.trim();
  const origin = normalizeOrigin(form.origin);
  const bearer = form.bearer;
  if (!name || name.length > 64) {
    ElMessage.error("服务器名称需为 1–64 个字符");
    return;
  }
  if (!origin) {
    ElMessage.error("请输入不含路径、账号或参数的 HTTP(S) RCON 地址");
    return;
  }
  if ((!current.value.hasBearer || changedOrigin.value) && !bearer.trim()) {
    ElMessage.error("首次配置或更改 RCON 地址时，请重新填写 RCON 密码（Bearer 密钥）");
    return;
  }
  if (origin.startsWith("http://") && form.allowPublicHttp) {
    try {
      await ElMessageBox.confirm(
        "公网 HTTP 会明文传输具备踢出和永久封禁权限的 Bearer 密钥。只有已评估风险且服务器部署策略允许时才能保存。是否继续？",
        "确认明文 RCON 风险",
        {
          type: "warning",
          confirmButtonText: "确认保存",
          cancelButtonText: "取消"
        }
      );
    } catch {
      return;
    }
  }

  const update: ServerSettingsUpdate = {
    name,
    origin,
    allowPublicHttp: origin.startsWith("http://") && form.allowPublicHttp
  };
  if (bearer.trim()) update.bearer = bearer;

  saving.value = true;
  error.value = "";
  try {
    await updateServerSettings(update);
    form.bearer = "";
    applyCurrent(await getServerSettings());
    ElMessage.success("服务器设置已保存");
  } catch (reason) {
    error.value = getApiErrorMessage(reason);
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="p-5 space-y-5">
    <div>
      <h1 class="text-2xl font-semibold">服务器设置</h1>
      <p class="text-sm text-gray-500">管理一台 Wardogs 服务器的 RCON 连接</p>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-card v-loading="loading" shadow="never" class="max-w-3xl">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span>连接配置</span>
          <el-tag :type="current?.configured ? 'success' : 'warning'">
            {{ current?.configured ? "已配置" : "未配置" }}
          </el-tag>
        </div>
      </template>

      <el-form label-position="top" @submit.prevent="save">
        <el-form-item label="服务器名称">
          <el-input
            v-model="form.name"
            maxlength="64"
            show-word-limit
            placeholder="例如：Wardogs 主服"
          />
        </el-form-item>
        <el-form-item label="RCON 地址">
          <el-input
            v-model="form.origin"
            placeholder="https://rcon.example.com 或 http://主机:端口"
            autocomplete="off"
          />
          <div class="mt-1 text-xs text-gray-500">
            只填协议、主机和端口，不包含 API 路径。
          </div>
        </el-form-item>
        <el-form-item label="RCON 密码（Bearer 密钥）">
          <el-input
            v-model="form.bearer"
            type="password"
            autocomplete="new-password"
            placeholder="留空则保留现有 RCON 密码"
          />
          <div class="mt-1 text-xs text-gray-500">
            {{
              current?.hasBearer
                ? "RCON 密码已保存，不会回显；修改地址时需要重新填写。"
                : "尚未保存 RCON 密码，请填写。"
            }}
          </div>
        </el-form-item>

        <el-alert
          v-if="httpOrigin"
          class="mb-4"
          type="warning"
          :closable="false"
          title="HTTP 不加密：公网传输会暴露具备踢出和永久封禁权限的 Bearer 密钥。建议先为 RCON 配置 HTTPS 或私网通道。"
        />
        <el-form-item>
          <el-checkbox v-model="form.allowPublicHttp" :disabled="!httpOrigin">
            允许公网 HTTP RCON（高风险；还需服务器部署策略允许）
          </el-checkbox>
        </el-form-item>
        <el-alert
          v-if="httpOrigin && current && !current.publicHttpRconAllowed"
          class="mb-4"
          type="info"
          :closable="false"
          title="公网 HTTP RCON 部署门禁尚未开启"
          description="如已接受明文风险，请在 deploy/panel.env 设置 PANEL_ALLOW_PUBLIC_HTTP_RCON=true，再运行 docker compose -f deploy/compose.yaml up -d --force-recreate panel，并勾选上方允许公网 HTTP RCON。面板使用 HTTPS 不代表 RCON 连接也已加密。详见仓库 deploy/README.md。"
        />

        <div class="flex flex-wrap items-center justify-between gap-3">
          <span class="text-xs text-gray-500">
            上次更新：{{ formatObservedAt(current?.updatedAt ?? null) }}
          </span>
          <el-button
            type="primary"
            native-type="submit"
            :disabled="!current"
            :loading="saving"
          >
            保存设置
          </el-button>
        </div>
      </el-form>
    </el-card>
  </div>
</template>
