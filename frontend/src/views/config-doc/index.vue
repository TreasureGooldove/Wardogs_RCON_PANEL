<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  getConfigDocument,
  saveConfigDocument,
  validateConfigDocument,
  type ConfigDocument,
  type ConfigValidation
} from "@/api/configDoc";
import { getApiErrorMessage } from "@/api/errors";
import {
  CONFIG_FIELD_GROUPS,
  applyConfigPreset,
  captureConfigPreset,
  readConfigField,
  writeConfigField,
  type ConfigField,
  type ConfigPreset
} from "@/utils/configFields";
import { changedLines } from "@/utils/diffLines";

defineOptions({ name: "ConfigDoc" });

const document = ref<ConfigDocument | null>(null);
const draft = ref("");
const validation = ref<ConfigValidation | null>(null);
const loading = ref(false);
const validating = ref(false);
const saving = ref(false);
const fullApply = ref(false);
const error = ref("");
const presets = ref<ConfigPreset[]>([]);
const presetName = ref("");
const selectedPreset = ref("");
const configDebug = ref(false);
const presetStorageKey = "wardogs-config-presets-v1";
const dirty = computed(() => document.value !== null && draft.value !== document.value.text);
const canWrite = computed(() => document.value?.writable === true);
const diff = computed(() =>
  document.value && configDebug.value && dirty.value
    ? changedLines(document.value.text, draft.value)
    : []
);

function syncDebugPreference() {
  configDebug.value = localStorage.getItem("wardogs-config-debug") === "true";
}

function diffText(line: string) {
  return /^\s*[+\-! .]*[A-Za-z0-9_.-]*(?:password|bearer|token|secret|api[_-]?key)[A-Za-z0-9_.-]*\s*=/i.test(line)
    ? `${line.split("=", 1)[0]}=[已隐藏]`
    : line;
}

function loadPresets() {
  try {
    const raw = localStorage.getItem(presetStorageKey);
    if (!raw || raw.length > 50_000) return;
    const data: unknown = JSON.parse(raw);
    if (!Array.isArray(data)) return;
    presets.value = data.filter(
      item => item && typeof item.name === "string" &&
        item.name.length <= 40 && item.values && typeof item.values === "object"
    ).slice(0, 10) as ConfigPreset[];
  } catch {
    presets.value = [];
  }
}

function persistPresets() {
  localStorage.setItem(presetStorageKey, JSON.stringify(presets.value));
}

function stageField(field: ConfigField, value: string) {
  if (!canWrite.value) return;
  try {
    draft.value = writeConfigField(draft.value, field, value);
    validation.value = null;
  } catch (reason) {
    ElMessage.warning(reason instanceof Error ? reason.message : "配置值无效");
  }
}

function savePreset() {
  if (!document.value) return;
  try {
    const preset = captureConfigPreset(presetName.value, draft.value);
    presets.value = [preset, ...presets.value.filter(item => item.name !== preset.name)].slice(0, 10);
    selectedPreset.value = preset.name;
    presetName.value = "";
    persistPresets();
    ElMessage.success("预设已保存在当前浏览器（不含敏感凭据）");
  } catch (reason) {
    ElMessage.warning(reason instanceof Error ? reason.message : "保存预设失败");
  }
}

function applyPreset() {
  const preset = presets.value.find(item => item.name === selectedPreset.value);
  if (!preset || !canWrite.value) return;
  try {
    draft.value = applyConfigPreset(draft.value, preset);
    validation.value = null;
    ElMessage.success("预设已填入草稿，尚未提交服务器");
  } catch (reason) {
    ElMessage.warning(reason instanceof Error ? reason.message : "应用预设失败");
  }
}

function removePreset() {
  if (!selectedPreset.value) return;
  presets.value = presets.value.filter(item => item.name !== selectedPreset.value);
  selectedPreset.value = "";
  persistPresets();
}

function issueText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value);
}

async function load() {
  if (loading.value || saving.value) return;
  if (dirty.value) {
    try {
      await ElMessageBox.confirm("重新读取会丢弃当前尚未保存的草稿。", "重新读取配置", {
        type: "warning",
        confirmButtonText: "丢弃草稿并读取",
        cancelButtonText: "取消"
      });
    } catch {
      return;
    }
  }
  loading.value = true;
  error.value = "";
  validation.value = null;
  try {
    const current = await getConfigDocument();
    document.value = current;
    draft.value = current.text;
  } catch (reason) {
    error.value = getApiErrorMessage(reason);
  } finally {
    loading.value = false;
  }
}

async function validate() {
  const current = document.value;
  if (!current || validating.value || saving.value) return;
  validating.value = true;
  error.value = "";
  try {
    validation.value = await validateConfigDocument(draft.value, current.targetRevision);
  } catch (reason) {
    validation.value = null;
    error.value = getApiErrorMessage(reason);
  } finally {
    validating.value = false;
  }
}

async function save() {
  const current = document.value;
  if (!current || !canWrite.value || !dirty.value || saving.value || !current.revision) return;
  if (!draft.value.trim()) {
    ElMessage.warning("配置内容不能为空");
    return;
  }
  try {
    await ElMessageBox.confirm(
      "将当前草稿提交到真实服务器。服务器会按配置版本检查冲突；请确认修改内容。",
      "应用服务器配置",
      {
        type: "warning",
        confirmButtonText: "先验证草稿",
        cancelButtonText: "取消"
      }
    );
  } catch {
    return;
  }
  saving.value = true;
  error.value = "";
  try {
    const checked = await validateConfigDocument(draft.value, current.targetRevision);
    validation.value = checked;
    if (checked.ok !== true || (checked.errors?.length ?? 0) > 0) {
      error.value = "草稿验证未通过，尚未提交服务器";
      return;
    }
    let password = "";
    try {
      const answer = await ElMessageBox.prompt(
        "草稿验证已通过。请输入当前账号密码，验证身份后写入真实服务器配置。",
        "二次验证应用",
        {
          type: "warning",
          inputType: "password",
          inputPlaceholder: "当前账号密码",
          inputValidator: value => value.length > 0 || "请输入密码",
          confirmButtonText: "验证并写入",
          cancelButtonText: "取消"
        }
      );
      password = answer.value;
    } catch {
      return;
    }
    const result = await saveConfigDocument({
      text: draft.value,
      revision: current.revision,
      targetRevision: current.targetRevision,
      fullApply: fullApply.value,
      password
    });
    password = "";
    validation.value = result;
    if (result.ok === false) {
      error.value = "服务器未接受配置，请检查验证结果";
      return;
    }
    ElMessage.success("服务器已接受配置");
    document.value = null;
    draft.value = "";
    await load();
  } catch (reason) {
    error.value = getApiErrorMessage(reason);
  } finally {
    saving.value = false;
  }
}

function downloadDraft() {
  if (!document.value) return;
  const blob = new Blob([draft.value], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = window.document.createElement("a");
  anchor.href = url;
  anchor.download = "wardogs-config-draft.ini";
  anchor.click();
  URL.revokeObjectURL(url);
}

onMounted(() => {
  loadPresets();
  syncDebugPreference();
  window.addEventListener("storage", syncDebugPreference);
  window.addEventListener("wardogs-display-settings-changed", syncDebugPreference);
  void load();
});
onUnmounted(() => {
  window.removeEventListener("storage", syncDebugPreference);
  window.removeEventListener("wardogs-display-settings-changed", syncDebugPreference);
});
</script>

<template>
  <div class="space-y-5 p-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-2xl font-semibold">服务器配置</h1>
        <p class="text-sm text-gray-500">查看、验证和编辑 Wardogs 配置文档</p>
      </div>
      <el-button :loading="loading" @click="load">重新读取</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-card v-if="document" shadow="never">
      <template #header>常用配置项</template>
      <p class="mb-4 text-sm text-gray-500">
        修改后先进入草稿；检查并点击“应用到服务器”才会提交。未列出的配置行仍保留在原文中。
      </p>
      <div v-for="group in CONFIG_FIELD_GROUPS" :key="group.title" class="mb-5">
        <h2 class="mb-3 font-semibold">{{ group.title }}</h2>
        <div class="grid grid-cols-1 gap-x-5 gap-y-4 md:grid-cols-2 xl:grid-cols-3">
          <div v-for="field in group.fields" :key="`${field.section}|${field.key}`">
            <label class="mb-1 block text-sm text-gray-500">{{ field.label }}</label>
            <el-switch
              v-if="field.kind === 'boolean'"
              :model-value="readConfigField(draft, field).toLowerCase() === 'true'"
              :disabled="!canWrite"
              active-text="启用"
              inactive-text="关闭"
              @change="stageField(field, $event ? 'True' : 'False')"
            />
            <el-input-number
              v-else-if="field.kind === 'number'"
              :model-value="Number(readConfigField(draft, field) || 0)"
              :min="0"
              :max="999999999"
              controls-position="right"
              :disabled="!canWrite"
              class="!w-full"
              @change="stageField(field, String($event))"
            />
            <el-input
              v-else
              :model-value="readConfigField(draft, field)"
              :type="field.kind === 'secret' ? 'password' : 'text'"
              :show-password="field.kind === 'secret'"
              :disabled="!canWrite"
              autocomplete="off"
              @change="stageField(field, $event)"
            />
          </div>
        </div>
      </div>
      <el-divider />
      <h2 class="mb-3 font-semibold">浏览器本地预设</h2>
      <p class="mb-3 text-xs text-gray-500">
        预设仅保存上方常用字段，不包含密码和 RCON 凭据；应用预设只修改当前草稿。
      </p>
      <div class="flex flex-wrap gap-2">
        <el-input v-model="presetName" class="max-w-xs" maxlength="40" placeholder="预设名称" />
        <el-button :disabled="!canWrite" @click="savePreset">保存当前草稿为预设</el-button>
        <el-select v-model="selectedPreset" class="max-w-xs" clearable placeholder="选择已有预设">
          <el-option v-for="preset in presets" :key="preset.name" :label="preset.name" :value="preset.name" />
        </el-select>
        <el-button :disabled="!canWrite || !selectedPreset" @click="applyPreset">填入草稿</el-button>
        <el-button :disabled="!selectedPreset" type="danger" plain @click="removePreset">移除预设</el-button>
      </div>
    </el-card>
    <el-card shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span>配置文件</span>
          <div class="flex items-center gap-2">
            <el-tag :type="canWrite ? 'success' : 'info'">
              {{ canWrite ? "服务器允许写入" : "只读或尚未加载" }}
            </el-tag>
            <el-tag v-if="dirty" type="warning">草稿已修改</el-tag>
          </div>
        </div>
      </template>

      <el-skeleton v-if="loading && !document" :rows="8" animated />
      <el-empty v-else-if="!document" description="尚未读取配置文档" />
      <div v-else class="space-y-4">
        <p class="text-sm text-gray-500">
          当前版本：<span class="font-mono">{{ document.revision }}</span>
        </p>
        <el-alert
          title="服务器已有的敏感值已遮蔽，提交时需匹配当前配置版本。若您填入新密码或密钥，下载的草稿会包含新值，请妥善保存。"
          type="warning"
          :closable="false"
        />
        <el-input
          v-model="draft"
          type="textarea"
          :rows="24"
          spellcheck="false"
          class="font-mono"
          :readonly="!canWrite"
          @input="validation = null"
        />
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex flex-wrap gap-2">
            <el-button :loading="validating" @click="validate">验证草稿</el-button>
            <el-button @click="downloadDraft">下载草稿 .ini</el-button>
            <el-button :disabled="!dirty" @click="draft = document.text">撤销草稿</el-button>
          </div>
          <div class="flex items-center gap-3">
            <el-checkbox v-model="fullApply" :disabled="!canWrite">完整应用</el-checkbox>
            <el-button
              type="primary"
              :loading="saving"
              :disabled="!canWrite || !dirty"
              @click="save"
            >应用到服务器</el-button>
          </div>
        </div>
        <div v-if="document.warnings.length" class="space-y-2">
          <h2 class="text-sm font-medium">当前配置提示</h2>
          <el-alert
            v-for="(warning, index) in document.warnings"
            :key="index"
            :title="issueText(warning)"
            type="warning"
            :closable="false"
          />
        </div>
        <div v-if="validation" class="space-y-2">
          <h2 class="text-sm font-medium">验证结果</h2>
          <el-tag :type="validation.ok === false ? 'danger' : 'success'">
            {{ validation.ok === false ? "未通过" : "已返回" }}
          </el-tag>
          <el-alert
            v-for="(issue, index) in validation.errors ?? []"
            :key="`error-${index}`"
            :title="issueText(issue)"
            type="error"
            :closable="false"
          />
          <el-alert
            v-for="(issue, index) in validation.warnings ?? []"
            :key="`warning-${index}`"
            :title="issueText(issue)"
            type="warning"
            :closable="false"
          />
        </div>
        <div v-if="configDebug" class="space-y-3 border-t border-[var(--el-border-color-light)] pt-4">
          <h2 class="text-sm font-medium">配置调试</h2>
          <p class="text-xs text-gray-500">以下仅在当前浏览器显示。敏感赋值行已隐藏。</p>
          <el-empty v-if="!dirty" description="当前草稿与服务器返回的安全草稿相同" />
          <el-alert
            v-else-if="diff.length === 0"
            title="文件较大，差异视图已停用；请在上方文件视图检查草稿。"
            type="info"
            :closable="false"
          />
          <div v-else class="max-h-80 overflow-auto rounded border border-[var(--el-border-color-light)] p-3 font-mono text-xs">
            <div
              v-for="(line, index) in diff"
              :key="index"
              class="whitespace-pre-wrap break-all"
              :class="line.kind === 'added' ? 'text-green-600' : 'text-red-600'"
            >{{ line.kind === "added" ? "+" : "−" }} {{ diffText(line.text) }}</div>
          </div>
          <div v-if="validation">
            <p class="mb-2 text-sm">服务器返回详情</p>
            <pre class="max-h-80 overflow-auto rounded border border-[var(--el-border-color-light)] p-3 text-xs">{{ JSON.stringify(validation, null, 2) }}</pre>
          </div>
        </div>
      </div>
    </el-card>
  </div>
</template>
