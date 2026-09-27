<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { getCapabilities, type CapabilitiesResponse } from "@/api/capabilities";
import { getRotation, type RotationResponse } from "@/api/rotation";
import { getCatalog, type CatalogItem } from "@/api/catalog";
import { getMapAlternators, getMapExperiences, type MapOption } from "@/api/mapOptions";
import {
  getConfigDocument,
  saveConfigDocument,
  validateConfigDocument,
  type ConfigDocument,
  type ConfigValidation
} from "@/api/configDoc";
import { getApiErrorMessage } from "@/api/errors";
import { formatObservedAt } from "@/api/snapshot";
import { useUserStoreHook } from "@/store/modules/user";
import {
  addRotationEntry,
  moveRotationEntry,
  parseRotationDraft,
  removeRotationEntry,
  setRotationSetting
} from "@/utils/rotationConfig";

defineOptions({ name: "Rotation" });

const capabilities = ref<CapabilitiesResponse | null>(null);
const rotation = ref<RotationResponse | null>(null);
const loading = ref(false);
const error = ref("");
const user = useUserStoreHook();
const config = ref<ConfigDocument | null>(null);
const draftText = ref("");
const validation = ref<ConfigValidation | null>(null);
const saving = ref(false);
const validating = ref(false);
const maps = ref<CatalogItem[]>([]);
const lightings = ref<CatalogItem[]>([]);
const experiences = ref<MapOption[]>([]);
const alternators = ref<MapOption[]>([]);
const newEntry = ref({ map: "", experience: "", lighting: "", zoneAlternator: "" });
const selectedExperiences = ref<string[]>([]);
const draft = computed(() => {
  if (!config.value) return { data: null, error: "" };
  try {
    return { data: parseRotationDraft(draftText.value), error: "" };
  } catch (reason) {
    return { data: null, error: reason instanceof Error ? reason.message : "轮换配置无法解析" };
  }
});
const dirty = computed(() => config.value !== null && draftText.value !== config.value.text);
const mayEdit = computed(() => user.role === "owner" || user.permissions.includes("config"));
const editable = computed(() => mayEdit.value && config.value?.writable === true && !!draft.value.data);

const availability = computed(() => {
  const value = capabilities.value?.features.rotation;
  if (value === false) return "目标服务器明确不支持地图轮换查询";
  if (value === null || value === undefined) return "地图轮换能力未知";
  return "";
});

const modeLabel = computed(() => {
  if (!rotation.value) return "未知";
  return {
    ordered: "按顺序",
    random: "随机",
    unknown: "未知"
  }[rotation.value.mode];
});

const orderedItems = computed(() =>
  [...(rotation.value?.items ?? [])].sort((a, b) => a.order - b.order)
);

function displayExperiences(value: string[] | null) {
  if (value === null) return "未知";
  return value.length ? value.join("、") : "无";
}

async function refresh() {
  if (loading.value) return;
  loading.value = true;
  error.value = "";
  try {
    capabilities.value = await getCapabilities();
  } catch (reason) {
    capabilities.value = null;
    error.value = getApiErrorMessage(reason);
  }

  if (capabilities.value?.features.rotation === true) {
    try {
      rotation.value = await getRotation();
    } catch (reason) {
      if (rotation.value) rotation.value = { ...rotation.value, stale: true };
      error.value = getApiErrorMessage(reason);
    }
  } else {
    rotation.value = null;
  }
  if (mayEdit.value) {
    try {
      config.value = await getConfigDocument();
      draftText.value = config.value.text;
      validation.value = null;
    } catch (reason) {
      config.value = null;
      error.value = getApiErrorMessage(reason);
    }
    const catalogs = await Promise.allSettled([getCatalog("maps"), getCatalog("lightings")]);
    if (catalogs[0].status === "fulfilled") maps.value = catalogs[0].value.items;
    if (catalogs[1].status === "fulfilled") lightings.value = catalogs[1].value.items;
  }
  loading.value = false;
}

async function selectMap(map: string) {
  newEntry.value.map = map;
  newEntry.value.experience = "";
  selectedExperiences.value = [];
  newEntry.value.zoneAlternator = "";
  experiences.value = [];
  alternators.value = [];
  if (!map) return;
  const result = await Promise.allSettled([getMapExperiences(map), getMapAlternators(map)]);
  if (result[0].status === "fulfilled") experiences.value = result[0].value.items;
  if (result[1].status === "fulfilled") alternators.value = result[1].value.items;
}

function stage(change: (text: string) => string) {
  if (!editable.value) return;
  try {
    draftText.value = change(draftText.value);
    validation.value = null;
  } catch (reason) {
    ElMessage.warning(reason instanceof Error ? reason.message : "修改轮换草稿失败");
  }
}

function addEntry() {
  const entry = { ...newEntry.value, experience: selectedExperiences.value.join("+") };
  if (!entry.map) {
    ElMessage.warning("请先选择地图");
    return;
  }
  stage(text => addRotationEntry(text, entry));
}

async function validateDraft() {
  if (!config.value || validating.value) return;
  validating.value = true;
  error.value = "";
  try {
    validation.value = await validateConfigDocument(draftText.value, config.value.targetRevision);
  } catch (reason) {
    error.value = getApiErrorMessage(reason);
  } finally {
    validating.value = false;
  }
}

async function saveDraft() {
  const current = config.value;
  if (!current || !editable.value || !dirty.value || saving.value) return;
  try {
    await ElMessageBox.confirm(
      "将轮换草稿应用到真实服务器配置。服务器会检查配置版本；请确认已检查条目和顺序。",
      "应用地图轮换",
      { type: "warning", confirmButtonText: "先验证草稿", cancelButtonText: "取消" }
    );
  } catch {
    return;
  }
  saving.value = true;
  error.value = "";
  try {
    const checked = await validateConfigDocument(draftText.value, current.targetRevision);
    validation.value = checked;
    if (checked.ok !== true || (checked.errors?.length ?? 0) > 0) {
      error.value = "轮换草稿验证未通过，尚未提交服务器";
      return;
    }
    let password = "";
    try {
      const answer = await ElMessageBox.prompt(
        "草稿验证已通过。请输入当前账号密码，验证身份后写入真实服务器配置。",
        "二次验证应用",
        { type: "warning", inputType: "password", inputPlaceholder: "当前账号密码",
          inputValidator: value => value.length > 0 || "请输入密码",
          confirmButtonText: "验证并写入", cancelButtonText: "取消" }
      );
      password = answer.value;
    } catch {
      return;
    }
    const result = await saveConfigDocument({
      text: draftText.value,
      revision: current.revision,
      targetRevision: current.targetRevision,
      password
    });
    password = "";
    validation.value = result;
    if (result.ok === false) {
      error.value = "服务器未接受轮换配置，请检查验证结果";
      return;
    }
    ElMessage.success("服务器已接受地图轮换配置");
    config.value = null;
    draftText.value = "";
    await refresh();
  } catch (reason) {
    error.value = getApiErrorMessage(reason);
  } finally {
    saving.value = false;
  }
}

onMounted(refresh);
</script>

<template>
  <div class="p-5 space-y-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-2xl font-semibold">地图轮换</h1>
        <p class="text-sm text-gray-500">查看轮换规则；管理员可调整顺序、条目和模式</p>
      </div>
      <el-button :loading="loading" @click="refresh">刷新轮换</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert
      v-if="capabilities?.state === 'stale'"
      title="能力信息已过期，轮换查询是否可用需要重新探测"
      type="warning"
      :closable="false"
    />
    <el-alert
      v-if="rotation?.stale"
      title="当前显示的是过期轮换快照"
      type="warning"
      :closable="false"
    />

    <el-card shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span>轮换列表</span>
          <span class="text-sm text-gray-500">
            采集时间：{{ formatObservedAt(rotation?.observedAt ?? null) }}
          </span>
        </div>
      </template>

      <el-skeleton v-if="loading && !rotation" :rows="5" animated />
      <el-empty v-else-if="availability" :description="availability" />
      <el-empty v-else-if="!rotation" description="尚无可显示的轮换数据" />
      <div v-else class="space-y-4">
        <div class="text-sm text-gray-600">
          轮换模式：<el-tag>{{ modeLabel }}</el-tag>
          <el-tag v-if="rotation.enabled !== null" class="ml-2" :type="rotation.enabled ? 'success' : 'info'">
            {{ rotation.enabled ? "已启用" : "已停用" }}
          </el-tag>
        </div>
        <el-empty v-if="rotation.items.length === 0" description="轮换列表为空" />
        <el-table v-else :data="orderedItems" border>
          <el-table-column prop="order" label="顺序" width="80" />
          <el-table-column prop="map" label="地图" min-width="150" />
          <el-table-column label="模式" min-width="180">
            <template #default="scope">
              {{ displayExperiences(scope.row.experiences) }}
            </template>
          </el-table-column>
          <el-table-column label="光照" min-width="120">
            <template #default="scope">{{ scope.row.lighting ?? "未知" }}</template>
          </el-table-column>
          <el-table-column prop="zoneAlternator" label="控制区" min-width="180" />
          <el-table-column label="状态" width="120">
            <template #default="scope">
              <el-tag v-if="scope.row.denied" type="danger">不可用</el-tag>
              <el-tag v-else-if="scope.row.status">{{ scope.row.status }}</el-tag>
              <span v-else>—</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>

    <el-card v-if="mayEdit" shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span>编辑地图轮换</span>
          <el-tag :type="editable ? 'success' : 'info'">
            {{ editable ? "可编辑草稿" : "当前不可编辑" }}
          </el-tag>
        </div>
      </template>

      <el-empty v-if="!config" description="尚未读取可编辑的配置文档" />
      <div v-else class="space-y-4">
        <el-alert v-if="draft.error" :title="draft.error" type="warning" :closable="false" />
        <el-alert
          v-if="!config.writable"
          title="目标服务器的配置文档只读，轮换管理暂不可用。"
          type="info"
          :closable="false"
        />
        <div v-if="draft.data" class="flex flex-wrap items-center gap-2">
          <span class="text-sm">轮换状态</span>
          <el-button
            size="small"
            :disabled="!editable"
            @click="stage(text => setRotationSetting(text, 'enabled', !(draft.data?.enabled ?? false)))"
          >{{ draft.data.enabled ? "停用" : "启用" }}</el-button>
          <span class="ml-3 text-sm">顺序模式</span>
          <el-button
            size="small"
            :type="draft.data.mode === 'ordered' ? 'primary' : 'default'"
            :disabled="!editable"
            @click="stage(text => setRotationSetting(text, 'mode', 'ordered'))"
          >顺序</el-button>
          <el-button
            size="small"
            :type="draft.data.mode === 'random' ? 'primary' : 'default'"
            :disabled="!editable"
            @click="stage(text => setRotationSetting(text, 'mode', 'random'))"
          >随机</el-button>
        </div>

        <div class="grid grid-cols-1 gap-2 lg:grid-cols-5">
          <el-select
            v-model="newEntry.map"
            filterable
            clearable
            placeholder="地图"
            :disabled="!editable"
            @change="selectMap"
          >
            <el-option v-for="item in maps" :key="item.id" :label="item.label" :value="item.id" />
          </el-select>
          <el-select v-model="selectedExperiences" multiple collapse-tags filterable clearable placeholder="模式／修饰符" :disabled="!editable">
            <el-option v-for="item in experiences" :key="item.id" :label="item.label" :value="item.id" />
          </el-select>
          <el-select v-model="newEntry.lighting" filterable clearable placeholder="光照" :disabled="!editable">
            <el-option v-for="item in lightings" :key="item.id" :label="item.label" :value="item.id" />
          </el-select>
          <el-select v-model="newEntry.zoneAlternator" filterable clearable placeholder="控制区" :disabled="!editable">
            <el-option v-for="item in alternators" :key="item.id" :label="item.label" :value="item.id" />
          </el-select>
          <el-button type="primary" plain :disabled="!editable" @click="addEntry">加入草稿</el-button>
        </div>

        <el-empty v-if="draft.data?.entries.length === 0" description="草稿中没有轮换地图" />
        <el-table v-else :data="draft.data?.entries ?? []" border max-height="560">
          <el-table-column type="index" label="#" width="55" />
          <el-table-column prop="map" label="地图" min-width="140" />
          <el-table-column prop="experience" label="模式" min-width="180" />
          <el-table-column prop="lighting" label="光照" min-width="140" />
          <el-table-column prop="zoneAlternator" label="控制区" min-width="200" />
          <el-table-column label="草稿操作" width="200">
            <template #default="scope">
              <el-button
                text
                size="small"
                :disabled="!editable || scope.$index === 0"
                @click="stage(text => moveRotationEntry(text, scope.$index, scope.$index - 1))"
              >上移</el-button>
              <el-button
                text
                size="small"
                :disabled="!editable || scope.$index >= (draft.data?.entries.length ?? 0) - 1"
                @click="stage(text => moveRotationEntry(text, scope.$index, scope.$index + 1))"
              >下移</el-button>
              <el-button
                text
                size="small"
                type="danger"
                :disabled="!editable"
                @click="stage(text => removeRotationEntry(text, scope.$index))"
              >移除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex gap-2">
            <el-button :loading="validating" :disabled="!config" @click="validateDraft">验证草稿</el-button>
            <el-button :disabled="!dirty" @click="draftText = config.text; validation = null">撤销草稿</el-button>
          </div>
          <div class="flex items-center gap-2">
            <el-tag v-if="dirty" type="warning">有未应用的修改</el-tag>
            <el-button type="primary" :loading="saving" :disabled="!editable || !dirty" @click="saveDraft">
              应用轮换到服务器
            </el-button>
          </div>
        </div>
        <div v-if="validation" class="space-y-2">
          <el-alert
            :title="validation.ok === false ? '验证未通过' : '验证已返回'"
            :type="validation.ok === false ? 'error' : 'success'"
            :closable="false"
          />
          <el-alert
            v-for="(issue, index) in validation.errors ?? []"
            :key="`error-${index}`"
            :title="typeof issue === 'string' ? issue : JSON.stringify(issue)"
            type="error"
            :closable="false"
          />
          <el-alert
            v-for="(issue, index) in validation.warnings ?? []"
            :key="`warning-${index}`"
            :title="typeof issue === 'string' ? issue : JSON.stringify(issue)"
            type="warning"
            :closable="false"
          />
        </div>
      </div>
    </el-card>
  </div>
</template>
