<script setup lang="ts">
import { t } from "@/i18n";
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { getCapabilities, type CapabilitiesResponse } from "@/api/capabilities";
import { getRotation, type RotationResponse } from "@/api/rotation";
import { getCatalog, type CatalogItem } from "@/api/catalog";
import {
  getMapAlternators,
  getMapExperiences,
  type MapOption
} from "@/api/mapOptions";
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
  setRotationSetting,
  setInfantryOnRotation,
  hasInfantryOnly,
  INFANTRY_ONLY
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
const infantryBusy = ref(false);
const infantryResult = ref<ReturnType<typeof setInfantryOnRotation> | null>(
  null
);
const maps = ref<CatalogItem[]>([]);
const lightings = ref<CatalogItem[]>([]);
const experiences = ref<MapOption[]>([]);
const alternators = ref<MapOption[]>([]);
const newEntry = ref({
  map: "",
  experience: "",
  lighting: "",
  zoneAlternator: ""
});
const selectedExperiences = ref<string[]>([]);
const draft = computed(() => {
  if (!config.value) return { data: null, error: "" };
  try {
    return { data: parseRotationDraft(draftText.value), error: "" };
  } catch (reason) {
    return {
      data: null,
      error: reason instanceof Error ? t(reason.message) : t("轮换配置无法解析")
    };
  }
});
const dirty = computed(
  () => config.value !== null && draftText.value !== config.value.text
);
const mayEdit = computed(
  () => user.role === "owner" || user.permissions.includes("config")
);
const editable = computed(
  () => mayEdit.value && config.value?.writable === true && !!draft.value.data
);
const canModifyDraft = computed(
  () =>
    editable.value &&
    !loading.value &&
    !saving.value &&
    !validating.value &&
    !infantryBusy.value
);
const liveInfantryCount = computed(
  () =>
    orderedItems.value.filter(item => hasInfantryOnly(item.experiences)).length
);
const draftInfantryCount = computed(
  () =>
    draft.value.data?.entries.filter(entry => hasInfantryOnly(entry.experience))
      .length ?? 0
);

const availability = computed(() => {
  const value = capabilities.value?.features.rotation;
  if (value === false) return t("目标服务器明确不支持地图轮换查询");
  if (value === null || value === undefined) return t("地图轮换能力未知");
  return "";
});

const modeLabel = computed(() => {
  if (!rotation.value) return t("未知");
  return {
    ordered: t("按顺序"),
    random: t("随机"),
    unknown: t("未知")
  }[rotation.value.mode];
});

const orderedItems = computed(() =>
  [...(rotation.value?.items ?? [])].sort((a, b) => a.order - b.order)
);

function displayExperiences(value: string[] | null) {
  if (value === null) return t("未知");
  return value.length ? value.join("、") : t("无");
}

async function refresh() {
  if (loading.value || saving.value || validating.value || infantryBusy.value)
    return;
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
      config.value = await getConfigDocument(true);
      draftText.value = config.value.text;
      validation.value = null;
      infantryResult.value = null;
    } catch (reason) {
      config.value = null;
      error.value = getApiErrorMessage(reason);
    }
    const catalogs = await Promise.allSettled([
      getCatalog("maps"),
      getCatalog("lightings")
    ]);
    if (catalogs[0].status === "fulfilled")
      maps.value = catalogs[0].value.items;
    if (catalogs[1].status === "fulfilled")
      lightings.value = catalogs[1].value.items;
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
  const result = await Promise.allSettled([
    getMapExperiences(map),
    getMapAlternators(map)
  ]);
  if (result[0].status === "fulfilled")
    experiences.value = result[0].value.items;
  if (result[1].status === "fulfilled")
    alternators.value = result[1].value.items;
}

function stage(change: (text: string) => string) {
  if (!canModifyDraft.value) return;
  try {
    draftText.value = change(draftText.value);
    validation.value = null;
    infantryResult.value = null;
  } catch (reason) {
    ElMessage.warning(
      reason instanceof Error ? t(reason.message) : t("修改轮换草稿失败")
    );
  }
}

async function stageInfantry(enabled: boolean) {
  const current = config.value;
  if (!current || !canModifyDraft.value || !draft.value.data?.entries.length)
    return;
  const text = draftText.value;
  const entries = draft.value.data.entries;
  const offered = new Set<string>();
  infantryBusy.value = true;
  error.value = "";
  infantryResult.value = null;
  try {
    if (enabled) {
      const uniqueMaps = [...new Set(entries.map(entry => entry.map))];
      // Bound concurrent reads; stale or failed catalogs never authorize adding a modifier.
      for (let offset = 0; offset < uniqueMaps.length; offset += 4) {
        const group = uniqueMaps.slice(offset, offset + 4);
        const results = await Promise.allSettled(
          group.map(map => getMapExperiences(map))
        );
        results.forEach((result, index) => {
          if (result.status !== "fulfilled") return;
          if (result.value.targetRevision !== current.targetRevision)
            throw new Error(
              t("服务器或草稿已变化，请重新读取后再生成纯步兵草稿。")
            );
          if (
            !result.value.stale &&
            result.value.items.some(
              item => item.id.toLowerCase() === INFANTRY_ONLY.toLowerCase()
            )
          )
            offered.add(group[index]);
        });
      }
    }
    if (config.value !== current || draftText.value !== text || !editable.value)
      throw new Error(t("服务器或草稿已变化，请重新读取后再生成纯步兵草稿。"));
    const result = setInfantryOnRotation(text, enabled, map =>
      offered.has(map)
    );
    draftText.value = result.text;
    validation.value = null;
    infantryResult.value = result;
  } catch (reason) {
    error.value =
      reason instanceof Error ? t(reason.message) : getApiErrorMessage(reason);
  } finally {
    infantryBusy.value = false;
  }
}

function addEntry() {
  const entry = {
    ...newEntry.value,
    experience: selectedExperiences.value.join("+")
  };
  if (!entry.map) {
    ElMessage.warning(t("请先选择地图"));
    return;
  }
  stage(text => addRotationEntry(text, entry));
}

async function validateDraft() {
  if (
    !config.value ||
    validating.value ||
    infantryBusy.value ||
    saving.value ||
    loading.value
  )
    return;
  validating.value = true;
  error.value = "";
  try {
    validation.value = await validateConfigDocument(
      draftText.value,
      config.value.targetRevision,
      true
    );
  } catch (reason) {
    error.value = getApiErrorMessage(reason);
  } finally {
    validating.value = false;
  }
}

async function saveDraft() {
  const current = config.value;
  if (!current || !canModifyDraft.value || !dirty.value) return;
  const text = draftText.value;
  saving.value = true;
  try {
    await ElMessageBox.confirm(
      t(
        "将轮换草稿应用到真实服务器配置。服务器会检查配置版本；请确认已检查条目和顺序。"
      ),
      t("应用地图轮换"),
      {
        type: "warning",
        confirmButtonText: t("先验证草稿"),
        cancelButtonText: t("取消")
      }
    );
  } catch {
    saving.value = false;
    return;
  }
  error.value = "";
  try {
    const checked = await validateConfigDocument(
      text,
      current.targetRevision,
      true
    );
    validation.value = checked;
    if (checked.ok !== true || (checked.errors?.length ?? 0) > 0) {
      error.value = t("轮换草稿验证未通过，尚未提交服务器");
      return;
    }
    let password = "";
    try {
      const answer = await ElMessageBox.prompt(
        t("草稿验证已通过。请输入当前账号密码，验证身份后写入真实服务器配置。"),
        t("二次验证应用"),
        {
          type: "warning",
          inputType: "password",
          inputPlaceholder: t("当前账号密码"),
          inputValidator: value => value.length > 0 || t("请输入密码"),
          confirmButtonText: t("验证并写入"),
          cancelButtonText: t("取消")
        }
      );
      password = answer.value;
    } catch {
      return;
    }
    const result = await saveConfigDocument(
      {
        text,
        revision: current.revision,
        targetRevision: current.targetRevision,
        password
      },
      true
    );
    password = "";
    validation.value = result;
    if (result.ok === false) {
      error.value = t("服务器未接受轮换配置，请检查验证结果");
      return;
    }
    ElMessage.success(t("服务器已接受地图轮换配置"));
    config.value = null;
    draftText.value = "";
    saving.value = false;
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
        <h1 class="text-2xl font-semibold">{{ $t("地图轮换") }}</h1>
        <p class="text-sm text-gray-500">
          {{ $t("查看轮换规则；管理员可调整顺序、条目和模式") }}
        </p>
      </div>
      <el-button :loading="loading" @click="refresh">{{
        $t("刷新轮换")
      }}</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert
      v-if="capabilities?.state === 'stale'"
      :title="$t('能力信息已过期，轮换查询是否可用需要重新探测')"
      type="warning"
      :closable="false"
    />
    <el-alert
      v-if="rotation?.stale"
      :title="$t('当前显示的是过期轮换快照')"
      type="warning"
      :closable="false"
    />

    <el-card shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span>{{ $t("轮换列表") }}</span>
          <span class="text-sm text-gray-500">
            {{ $t("采集时间：")
            }}{{ formatObservedAt(rotation?.observedAt ?? null) }}
          </span>
        </div>
      </template>

      <el-skeleton v-if="loading && !rotation" :rows="5" animated />
      <el-empty v-else-if="availability" :description="availability" />
      <el-empty
        v-else-if="!rotation"
        :description="$t('尚无可显示的轮换数据')"
      />
      <div v-else class="space-y-4">
        <div class="text-sm text-gray-600">
          {{ $t("轮换模式：") }}<el-tag>{{ modeLabel }}</el-tag>
          <el-tag
            v-if="rotation.enabled !== null"
            class="ml-2"
            :type="rotation.enabled ? 'success' : 'info'"
          >
            {{ rotation.enabled ? $t("已启用") : $t("已停用") }}
          </el-tag>
        </div>
        <el-empty
          v-if="rotation.items.length === 0"
          :description="$t('轮换列表为空')"
        />
        <el-table v-else :data="orderedItems" border>
          <el-table-column prop="order" :label="$t('顺序')" width="80" />
          <el-table-column prop="map" :label="$t('地图')" min-width="150" />
          <el-table-column :label="$t('模式')" min-width="180">
            <template #default="scope">
              {{ displayExperiences(scope.row.experiences) }}
            </template>
          </el-table-column>
          <el-table-column :label="$t('光照')" min-width="120">
            <template #default="scope">{{
              scope.row.lighting ?? $t("未知")
            }}</template>
          </el-table-column>
          <el-table-column
            prop="zoneAlternator"
            :label="$t('控制区')"
            min-width="180"
          />
          <el-table-column :label="$t('状态')" width="120">
            <template #default="scope">
              <el-tag v-if="scope.row.denied" type="danger">{{
                $t("不可用")
              }}</el-tag>
              <el-tag v-else-if="scope.row.status">{{
                scope.row.status
              }}</el-tag>
              <span v-else>—</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>

    <el-card v-if="rotation || config" shadow="never">
      <template #header>{{ $t("官方纯步兵模式") }}</template>
      <div class="space-y-3">
        <p class="text-sm text-gray-500">
          {{
            $t(
              "使用游戏原生 Infantry Only 规则。只调整轮换条目的体验组合，应用后从下次切图生效，当前对局保持原样。"
            )
          }}
        </p>
        <div class="flex flex-wrap items-center gap-2">
          <el-tag v-if="rotation">{{
            $t("实机轮换纯步兵：{p0} / {p1} 条", {
              p0: liveInfantryCount,
              p1: orderedItems.length
            })
          }}</el-tag>
          <el-tag
            v-if="rotation?.items.some(item => item.experiences === null)"
            type="warning"
            >{{ $t("部分轮换条目的模式未知") }}</el-tag
          >
          <el-tag v-if="draft.data" :type="dirty ? 'warning' : 'info'">{{
            $t("草稿纯步兵：{p0} / {p1} 条", {
              p0: draftInfantryCount,
              p1: draft.data.entries.length
            })
          }}</el-tag>
        </div>
        <template v-if="mayEdit">
          <div class="flex flex-wrap gap-2">
            <el-button
              type="primary"
              plain
              :loading="infantryBusy"
              :disabled="!canModifyDraft || !draft.data?.entries.length"
              @click="stageInfantry(true)"
              >{{ $t("全部轮换添加纯步兵") }}</el-button
            >
            <el-button
              :disabled="!canModifyDraft || !draftInfantryCount"
              @click="stageInfantry(false)"
              >{{ $t("全部轮换移除纯步兵") }}</el-button
            >
          </div>
          <p class="text-sm text-gray-500">
            {{
              $t(
                "以上按钮只修改草稿。添加前逐图读取实机支持项；不支持、读取失败或缺少基础玩法的条目会跳过。检查草稿后，点击“应用轮换到服务器”并输入密码确认。"
              )
            }}
          </p>
          <el-alert
            v-if="infantryResult"
            :closable="false"
            :type="infantryResult.skipped ? 'warning' : 'success'"
            :title="
              $t('纯步兵草稿：修改 {p0} 条，保持 {p1} 条，跳过 {p2} 条。', {
                p0: infantryResult.changed,
                p1: infantryResult.unchanged,
                p2: infantryResult.skipped
              })
            "
          />
          <p
            v-if="infantryResult?.skippedMaps.length"
            class="text-sm text-gray-500"
          >
            {{
              $t("未处理地图：{p0}", {
                p0: infantryResult.skippedMaps.join("、")
              })
            }}
          </p>
        </template>
      </div>
    </el-card>

    <el-card v-if="mayEdit" shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span>{{ $t("编辑地图轮换") }}</span>
          <el-tag :type="editable ? 'success' : 'info'">
            {{ editable ? $t("可编辑草稿") : $t("当前不可编辑") }}
          </el-tag>
        </div>
      </template>

      <el-empty v-if="!config" :description="$t('尚未读取可编辑的配置文档')" />
      <div v-else class="space-y-4">
        <el-alert
          v-if="draft.error"
          :title="draft.error"
          type="warning"
          :closable="false"
        />
        <el-alert
          v-if="!config.writable"
          :title="$t('目标服务器的配置文档只读，轮换管理暂不可用。')"
          type="info"
          :closable="false"
        />
        <div v-if="draft.data" class="flex flex-wrap items-center gap-2">
          <span class="text-sm">{{ $t("轮换状态") }}</span>
          <el-button
            size="small"
            :disabled="!canModifyDraft"
            @click="
              stage(text =>
                setRotationSetting(
                  text,
                  'enabled',
                  !(draft.data?.enabled ?? false)
                )
              )
            "
            >{{ draft.data.enabled ? $t("停用") : $t("启用") }}</el-button
          >
          <span class="ml-3 text-sm">{{ $t("顺序模式") }}</span>
          <el-button
            size="small"
            :type="draft.data.mode === 'ordered' ? 'primary' : 'default'"
            :disabled="!canModifyDraft"
            @click="stage(text => setRotationSetting(text, 'mode', 'ordered'))"
            >{{ $t("顺序") }}</el-button
          >
          <el-button
            size="small"
            :type="draft.data.mode === 'random' ? 'primary' : 'default'"
            :disabled="!canModifyDraft"
            @click="stage(text => setRotationSetting(text, 'mode', 'random'))"
            >{{ $t("随机") }}</el-button
          >
        </div>

        <div class="grid grid-cols-1 gap-2 lg:grid-cols-5">
          <el-select
            v-model="newEntry.map"
            filterable
            clearable
            :placeholder="$t('地图')"
            :disabled="!canModifyDraft"
            @change="selectMap"
          >
            <el-option
              v-for="item in maps"
              :key="item.id"
              :label="item.label"
              :value="item.id"
            />
          </el-select>
          <el-select
            v-model="selectedExperiences"
            multiple
            collapse-tags
            filterable
            clearable
            :placeholder="$t('模式／修饰符')"
            :disabled="!canModifyDraft"
          >
            <el-option
              v-for="item in experiences"
              :key="item.id"
              :label="item.label"
              :value="item.id"
            />
          </el-select>
          <el-select
            v-model="newEntry.lighting"
            filterable
            clearable
            :placeholder="$t('光照')"
            :disabled="!canModifyDraft"
          >
            <el-option
              v-for="item in lightings"
              :key="item.id"
              :label="item.label"
              :value="item.id"
            />
          </el-select>
          <el-select
            v-model="newEntry.zoneAlternator"
            filterable
            clearable
            :placeholder="$t('控制区')"
            :disabled="!canModifyDraft"
          >
            <el-option
              v-for="item in alternators"
              :key="item.id"
              :label="item.label"
              :value="item.id"
            />
          </el-select>
          <el-button
            type="primary"
            plain
            :disabled="!canModifyDraft"
            @click="addEntry"
            >{{ $t("加入草稿") }}</el-button
          >
        </div>

        <el-empty
          v-if="draft.data?.entries.length === 0"
          :description="$t('草稿中没有轮换地图')"
        />
        <el-table
          v-else
          :data="draft.data?.entries ?? []"
          border
          max-height="560"
        >
          <el-table-column type="index" label="#" width="55" />
          <el-table-column prop="map" :label="$t('地图')" min-width="140" />
          <el-table-column
            prop="experience"
            :label="$t('模式')"
            min-width="180"
          />
          <el-table-column
            prop="lighting"
            :label="$t('光照')"
            min-width="140"
          />
          <el-table-column
            prop="zoneAlternator"
            :label="$t('控制区')"
            min-width="200"
          />
          <el-table-column :label="$t('草稿操作')" width="200">
            <template #default="scope">
              <el-button
                text
                size="small"
                :disabled="!canModifyDraft || scope.$index === 0"
                @click="
                  stage(text =>
                    moveRotationEntry(text, scope.$index, scope.$index - 1)
                  )
                "
                >{{ $t("上移") }}</el-button
              >
              <el-button
                text
                size="small"
                :disabled="
                  !canModifyDraft ||
                  scope.$index >= (draft.data?.entries.length ?? 0) - 1
                "
                @click="
                  stage(text =>
                    moveRotationEntry(text, scope.$index, scope.$index + 1)
                  )
                "
                >{{ $t("下移") }}</el-button
              >
              <el-button
                text
                size="small"
                type="danger"
                :disabled="!canModifyDraft"
                @click="stage(text => removeRotationEntry(text, scope.$index))"
                >{{ $t("移除") }}</el-button
              >
            </template>
          </el-table-column>
        </el-table>

        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex gap-2">
            <el-button
              :loading="validating"
              :disabled="!config || infantryBusy || loading || saving"
              @click="validateDraft"
              >{{ $t("验证草稿") }}</el-button
            >
            <el-button
              :disabled="
                !dirty || infantryBusy || loading || saving || validating
              "
              @click="
                draftText = config.text;
                validation = null;
                infantryResult = null;
              "
              >{{ $t("撤销草稿") }}</el-button
            >
          </div>
          <div class="flex items-center gap-2">
            <el-tag v-if="dirty" type="warning">{{
              $t("有未应用的修改")
            }}</el-tag>
            <el-button
              type="primary"
              :loading="saving"
              :disabled="!canModifyDraft || !dirty"
              @click="saveDraft"
            >
              {{ $t("应用轮换到服务器") }}
            </el-button>
          </div>
        </div>
        <div v-if="validation" class="space-y-2">
          <el-alert
            :title="
              validation.ok === false ? $t('验证未通过') : $t('验证已返回')
            "
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
