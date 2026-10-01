<script setup lang="ts">
import { t } from "@/i18n";
import {
  computed,
  onActivated,
  onDeactivated,
  onMounted,
  onUnmounted,
  reactive,
  ref
} from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  broadcastMessage,
  changeMap,
  endMatch,
  getAudit,
  getBans,
  getMapAlternators,
  getMapExperiences,
  restartMatch,
  setLighting,
  unbanPlayer,
  type AuditLimit,
  type AuditResponse,
  type BannedPlayer,
  type BansResponse,
  type MapAlternatorsResponse,
  type MapExperiencesResponse
} from "@/api/actions";
import {
  getCapabilities,
  type AdvertisedAction,
  type CapabilitiesResponse
} from "@/api/capabilities";
import { getCatalog, type CatalogItem } from "@/api/catalog";
import { getApiErrorCode, getApiErrorMessage } from "@/api/errors";
import { formatObservedAt } from "@/api/snapshot";
import { useUserStoreHook } from "@/store/modules/user";
import { validateActionMessage, validateMapId } from "@/utils/actionSafety";
import BanPlayerLookup from "@/components/BanPlayerLookup.vue";

defineOptions({ name: "Actions" });

const props = withDefaults(defineProps<{ bansOnly?: boolean }>(), {
  bansOnly: false
});
const banSearch = ref("");

const userStore = useUserStoreHook();
const bans = ref<BansResponse | null>(null);
const audit = ref<AuditResponse | null>(null);
const capabilities = ref<CapabilitiesResponse | null>(null);
const capabilityFetchedAt = ref(0);
const capabilityClock = ref(Date.now());
const capabilityError = ref("");
const maps = ref<CatalogItem[]>([]);
const lightings = ref<CatalogItem[]>([]);
const experiences = ref<CatalogItem[]>([]);
const alternators = ref<CatalogItem[]>([]);
const mapExperienceSnapshot = ref<MapExperiencesResponse | null>(null);
const mapAlternatorSnapshot = ref<MapAlternatorsResponse | null>(null);
const auditLimit = ref<AuditLimit>(50);
const loading = ref(false);
const mapOptionsLoading = ref(false);
const pending = ref(false);
const readError = ref("");
const writeError = ref("");
const mapOptionsError = ref("");
const broadcastText = ref("");
const lightingSelection = ref("");
const mapForm = reactive({
  map: "",
  experiences: [] as string[],
  lighting: "",
  zoneAlternator: ""
});
let pageActive = false;
let mapRequestId = 0;
let capabilityTimer: ReturnType<typeof setInterval> | null = null;
let capabilityRequest: Promise<void> | null = null;

const targetRevision = computed(() => {
  if (props.bansOnly) {
    return bans.value && !bans.value.stale ? bans.value.targetRevision : "";
  }
  if (
    !bans.value ||
    !audit.value ||
    bans.value.stale ||
    audit.value.stale ||
    !bans.value.targetRevision ||
    bans.value.targetRevision !== audit.value.targetRevision
  ) {
    return "";
  }
  return bans.value.targetRevision;
});

const validBans = computed(() =>
  (bans.value?.bans ?? []).filter(row => /^[1-9][0-9]{16}$/.test(row.steamId))
);
const sortedBans = computed(() =>
  [...validBans.value]
    .filter(row => {
      const query = banSearch.value.trim().toLowerCase();
      return (
        !query ||
        [row.steamId, row.reason, row.bannedBy].some(value =>
          value?.toLowerCase().includes(query)
        )
      );
    })
    .sort((left, right) => left.steamId.localeCompare(right.steamId))
);

function blockReason(action: AdvertisedAction): string {
  if (userStore.role !== "owner" && !userStore.permissions.includes(action))
    return t("当前账号没有此项操作权限");
  if (!pageActive || document.visibilityState !== "visible")
    return t("当前页面未激活");
  if (loading.value || pending.value) return t("请等待当前请求完成");
  if (!targetRevision.value) return t("服务器目标或当前数据未确认，请刷新");
  if (capabilities.value?.state !== "available")
    return t("服务器管理能力尚未确认可用，请刷新");
  if (capabilityClock.value - capabilityFetchedAt.value > 35_000)
    return t("服务器管理能力已过期，请刷新");
  if (capabilities.value.advertisedActions?.[action] !== true)
    return t("目标服务器未开放此项操作");
  return "";
}

async function load() {
  if (loading.value) return;
  loading.value = true;
  readError.value = "";
  bans.value = null;
  audit.value = null;
  capabilities.value = null;
  capabilityFetchedAt.value = 0;
  capabilityError.value = "";
  if (props.bansOnly) {
    const results = await Promise.allSettled([getBans(), getCapabilities()]);
    const errors: string[] = [];
    if (results[0].status === "fulfilled") bans.value = results[0].value;
    else errors.push(getApiErrorMessage(results[0].reason));
    if (results[1].status === "fulfilled") {
      capabilities.value = results[1].value;
      capabilityFetchedAt.value = Date.now();
      capabilityClock.value = Date.now();
    } else errors.push(getApiErrorMessage(results[1].reason));
    readError.value = errors.join("；");
    loading.value = false;
    return;
  }
  const results = await Promise.allSettled([
    getBans(),
    getAudit(auditLimit.value),
    getCapabilities(),
    getCatalog("maps"),
    getCatalog("lightings")
  ] as const);
  const errors: string[] = [];
  if (results[0].status === "fulfilled") bans.value = results[0].value;
  else errors.push(t("封禁列表：") + getApiErrorMessage(results[0].reason));
  if (results[1].status === "fulfilled") audit.value = results[1].value;
  else errors.push(t("审计记录：") + getApiErrorMessage(results[1].reason));
  if (results[2].status === "fulfilled") {
    capabilities.value = results[2].value;
    capabilityFetchedAt.value = Date.now();
    capabilityClock.value = Date.now();
  } else {
    errors.push(t("管理能力：") + getApiErrorMessage(results[2].reason));
  }
  if (results[3].status === "fulfilled" && !results[3].value.stale) {
    maps.value = results[3].value.items;
  } else {
    maps.value = [];
    errors.push(t("地图目录不可用"));
  }
  if (results[4].status === "fulfilled" && !results[4].value.stale) {
    lightings.value = results[4].value.items;
  } else {
    lightings.value = [];
    errors.push(t("光照目录不可用"));
  }
  readError.value = errors.join("；");
  loading.value = false;
  if (mapForm.map) await loadMapOptions(mapForm.map);
}

function loadCapabilityOnly(): Promise<void> {
  if (loading.value || !pageActive || document.visibilityState !== "visible")
    return Promise.resolve();
  if (capabilityRequest) return capabilityRequest;
  capabilityRequest = (async () => {
    capabilityError.value = "";
    try {
      const result = await getCapabilities();
      if (pageActive && document.visibilityState === "visible") {
        capabilities.value = result;
        capabilityFetchedAt.value = Date.now();
        capabilityClock.value = Date.now();
      }
    } catch (reason) {
      if (pageActive) {
        capabilities.value = null;
        capabilityFetchedAt.value = 0;
        capabilityError.value = getApiErrorMessage(reason);
      }
    }
  })().finally(() => {
    capabilityRequest = null;
  });
  return capabilityRequest;
}

async function loadMapOptions(map: string) {
  const requestId = ++mapRequestId;
  experiences.value = [];
  alternators.value = [];
  mapExperienceSnapshot.value = null;
  mapAlternatorSnapshot.value = null;
  mapOptionsError.value = "";
  if (!map) return;
  mapOptionsLoading.value = true;
  const results = await Promise.allSettled([
    getMapExperiences(map),
    getMapAlternators(map)
  ] as const);
  if (requestId !== mapRequestId || mapForm.map !== map) return;
  if (results[0].status === "fulfilled" && !results[0].value.stale) {
    mapExperienceSnapshot.value = results[0].value;
    experiences.value = results[0].value.items;
  } else {
    mapOptionsError.value = t("地图体验选项读取失败");
  }
  if (results[1].status === "fulfilled" && !results[1].value.stale) {
    mapAlternatorSnapshot.value = results[1].value;
    alternators.value = results[1].value.items;
  } else {
    mapOptionsError.value = [mapOptionsError.value, t("地图区域选项读取失败")]
      .filter(Boolean)
      .join("；");
  }
  mapOptionsLoading.value = false;
}

function onMapChange() {
  mapForm.experiences = [];
  mapForm.zoneAlternator = "";
  void loadMapOptions(mapForm.map);
}

async function runAction(
  action: AdvertisedAction,
  title: string,
  description: string,
  execute: (targetRevision: string) => Promise<unknown>,
  stillValid: () => boolean = () => true,
  critical = false
) {
  const target = targetRevision.value;
  const blocked = blockReason(action);
  if (blocked || !target || !stillValid()) {
    ElMessage.warning(blocked || t("当前数据已变化，请刷新后重试"));
    return;
  }
  try {
    await ElMessageBox.confirm(description, title, {
      type: "warning",
      confirmButtonText: critical ? t("继续二次确认") : t("确认执行"),
      cancelButtonText: t("取消")
    });
    if (critical) {
      const confirmation = t("确认执行");
      await ElMessageBox.prompt(
        t("再次确认执行“{p0}”。请输入“{p1}”后才会发送命令。", {
          p0: title,
          p1: confirmation
        }),
        t("管理员二次确认"),
        {
          type: "error",
          confirmButtonText: t("发送命令"),
          cancelButtonText: t("取消"),
          inputType: "text",
          inputPlaceholder: t("请输入：{p0}", { p0: confirmation }),
          inputValidator: value =>
            value === confirmation
              ? true
              : t("请输入完整的“{p0}”", { p0: confirmation })
        }
      );
    }
  } catch {
    return;
  }
  await loadCapabilityOnly();
  const latestBlock = blockReason(action);
  if (latestBlock || targetRevision.value !== target || !stillValid()) {
    ElMessage.warning(
      latestBlock || t("服务器目标或当前数据已变化，请刷新后重试")
    );
    return;
  }
  pending.value = true;
  writeError.value = "";
  try {
    await execute(target);
    ElMessage.success(title + t("命令已执行"));
    if (pageActive && document.visibilityState === "visible") {
      await load();
    } else {
      bans.value = null;
      audit.value = null;
    }
  } catch (reason) {
    writeError.value = getApiErrorMessage(reason);
    if (getApiErrorCode(reason) === "stale_server_target") {
      bans.value = null;
      audit.value = null;
      writeError.value = t("服务器设置已变更，请刷新当前数据后重试");
    }
  } finally {
    pending.value = false;
  }
}

function unban(row: BannedPlayer) {
  void runAction(
    "unban",
    t("解除封禁"),
    t("确认解除 SteamID {p0} 的封禁？此操作会影响真实服务器。", {
      p0: row.steamId
    }),
    target => unbanPlayer({ steamId: row.steamId, targetRevision: target }),
    () => bans.value?.bans.some(item => item.steamId === row.steamId) ?? false,
    props.bansOnly
  );
}

function broadcast() {
  const message = broadcastText.value.trim();
  const invalid = validateActionMessage(broadcastText.value);
  if (invalid) return void ElMessage.warning(t(invalid));
  void runAction(
    "broadcast",
    t("发送全服公告"),
    t("确认向全服发送公告：“{p0}”？", { p0: message }),
    target => broadcastMessage({ message, targetRevision: target }),
    () => broadcastText.value.trim() === message
  );
}

function mapBlockReason(): string {
  const blocked = blockReason("changeMap");
  if (blocked) return blocked;
  if (!mapForm.map || !maps.value.some(item => item.id === mapForm.map))
    return t("请从地图目录选择地图");
  if (mapOptionsLoading.value) return t("正在读取地图选项");
  if (
    !mapExperienceSnapshot.value ||
    !mapAlternatorSnapshot.value ||
    mapExperienceSnapshot.value.targetRevision !== targetRevision.value ||
    mapAlternatorSnapshot.value.targetRevision !== targetRevision.value
  ) {
    return t("地图选项与当前服务器目标不一致，请刷新");
  }
  return "";
}

function submitMap() {
  const blocked = mapBlockReason();
  if (blocked) return void ElMessage.warning(blocked);
  const map = mapForm.map;
  const selectedExperiences = [...mapForm.experiences];
  const lighting = mapForm.lighting;
  const alternator = mapForm.zoneAlternator;
  const invalidMap = validateMapId(map);
  if (invalidMap) return void ElMessage.warning(invalidMap);
  if (
    selectedExperiences.length > 16 ||
    new Set(selectedExperiences).size !== selectedExperiences.length ||
    selectedExperiences.some(
      item =>
        item.length < 1 ||
        item.length > 128 ||
        !experiences.value.some(option => option.id === item)
    )
  ) {
    return void ElMessage.warning(t("地图体验选项无效"));
  }
  if (lighting && !lightings.value.some(item => item.id === lighting))
    return void ElMessage.warning(t("光照选项无效"));
  if (alternator && !alternators.value.some(item => item.id === alternator))
    return void ElMessage.warning(t("区域选项无效"));
  const sameForm = () =>
    !mapBlockReason() &&
    mapForm.map === map &&
    mapForm.lighting === lighting &&
    mapForm.zoneAlternator === alternator &&
    JSON.stringify(mapForm.experiences) === JSON.stringify(selectedExperiences);
  void runAction(
    "changeMap",
    t("切换地图"),
    t("确认将服务器地图切换为 {p0}？此操作会影响当前比赛。", {
      p0: maps.value.find(item => item.id === map)?.label ?? map
    }),
    target =>
      changeMap({
        map,
        ...(selectedExperiences.length
          ? { experiences: selectedExperiences }
          : {}),
        ...(lighting ? { lighting } : {}),
        ...(alternator && alternator !== "None"
          ? { zoneAlternator: alternator }
          : {}),
        targetRevision: target
      }),
    sameForm,
    true
  );
}

function lightingBlockReason(): string {
  const blocked = blockReason("setLighting");
  if (blocked) return blocked;
  if (
    !lightingSelection.value ||
    !lightings.value.some(item => item.id === lightingSelection.value)
  ) {
    return t("请从光照目录选择光照");
  }
  return "";
}

function submitLighting() {
  const blocked = lightingBlockReason();
  if (blocked) return void ElMessage.warning(blocked);
  const lighting = lightingSelection.value;
  const label =
    lightings.value.find(item => item.id === lighting)?.label ?? lighting;
  void runAction(
    "setLighting",
    t("切换光照"),
    t("确认将当前世界光照切换为 {p0}？此操作会影响真实服务器。", { p0: label }),
    target => setLighting({ lighting, targetRevision: target }),
    () => !lightingBlockReason() && lightingSelection.value === lighting,
    true
  );
}

function readableTime(value: string | null) {
  if (value?.startsWith("0001-")) return t("时间未知");
  return formatObservedAt(value);
}

function activate() {
  if (pageActive) return;
  pageActive = true;
  void load();
  startCapabilityTimer();
  document.addEventListener("visibilitychange", onVisibilityChange);
}
function deactivate() {
  pageActive = false;
  ++mapRequestId;
  capabilityFetchedAt.value = 0;
  stopCapabilityTimer();
  document.removeEventListener("visibilitychange", onVisibilityChange);
}
function onVisibilityChange() {
  if (document.visibilityState === "visible") {
    if (!bans.value || !audit.value) void load();
    else void loadCapabilityOnly();
    startCapabilityTimer();
  } else {
    capabilityFetchedAt.value = 0;
    stopCapabilityTimer();
  }
}
function startCapabilityTimer() {
  if (capabilityTimer || document.visibilityState !== "visible") return;
  capabilityTimer = setInterval(() => {
    capabilityClock.value = Date.now();
    if (capabilityClock.value - capabilityFetchedAt.value > 25_000)
      void loadCapabilityOnly();
  }, 5_000);
}
function stopCapabilityTimer() {
  if (!capabilityTimer) return;
  clearInterval(capabilityTimer);
  capabilityTimer = null;
}
onMounted(activate);
onActivated(activate);
onDeactivated(deactivate);
onUnmounted(deactivate);
</script>

<template>
  <div class="space-y-5 p-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-2xl font-semibold">
          {{ $t(bansOnly ? "封禁管理" : "管理操作") }}
        </h1>
        <p class="text-sm text-gray-500">
          {{
            $t(
              bansOnly
                ? "查看服务器封禁记录；解除封禁需要权限和二次确认"
                : "主管理员专用 · 每次写入均需确认"
            )
          }}
        </p>
      </div>
      <el-button :loading="loading" @click="load">{{
        $t("刷新当前数据")
      }}</el-button>
    </div>

    <el-alert
      v-if="readError"
      :title="readError"
      type="warning"
      :closable="false"
    />
    <el-alert
      v-if="writeError"
      :title="writeError"
      type="error"
      :closable="false"
    />
    <el-alert
      v-if="capabilityError"
      :title="capabilityError"
      type="warning"
      :closable="false"
    />
    <el-alert
      v-if="!targetRevision && !loading"
      :title="$t('当前数据未能确认同一服务器目标，所有写入操作已停用')"
      type="warning"
      :closable="false"
    />
    <el-alert
      v-if="capabilities && capabilities.state !== 'available'"
      :title="$t('服务器管理能力未确认，所有写入操作已停用')"
      type="warning"
      :closable="false"
    />

    <BanPlayerLookup
      v-if="bansOnly"
      :banned-ids="validBans.map(row => row.steamId)"
      :target-revision="targetRevision"
      @changed="load"
    />

    <div v-if="!bansOnly" class="grid grid-cols-1 gap-5 xl:grid-cols-2">
      <el-card shadow="never">
        <template #header>{{ $t("全服公告") }}</template>
        <el-input
          v-model="broadcastText"
          maxlength="200"
          show-word-limit
          :placeholder="$t('输入单行公告（1–200 字）')"
        />
        <div class="mt-3">
          <el-tooltip :content="blockReason('broadcast') || $t('发送到全服')">
            <span>
              <el-button
                type="primary"
                :disabled="Boolean(blockReason('broadcast'))"
                @click="broadcast"
                >{{ $t("发送公告") }}</el-button
              >
            </span>
          </el-tooltip>
        </div>
      </el-card>

      <el-card shadow="never">
        <template #header>{{ $t("比赛控制") }}</template>
        <p class="mb-3 text-sm text-gray-500">
          {{ $t("这些操作会直接影响当前比赛。") }}
        </p>
        <div class="flex flex-wrap gap-2">
          <el-tooltip
            :content="
              blockReason('endMatch') || $t('结束当前比赛并强制进入下一地图')
            "
          >
            <span>
              <el-button
                type="danger"
                :disabled="Boolean(blockReason('endMatch'))"
                @click="
                  runAction(
                    'endMatch',
                    $t('强制下一地图'),
                    $t(
                      '确认结束当前比赛并强制进入下一地图？此操作会影响真实服务器。'
                    ),
                    target => endMatch({ targetRevision: target }),
                    undefined,
                    true
                  )
                "
                >{{ $t("结束比赛／下一地图") }}</el-button
              >
            </span>
          </el-tooltip>
          <el-tooltip
            :content="blockReason('restartMatch') || $t('重启当前比赛')"
          >
            <span>
              <el-button
                type="warning"
                :disabled="Boolean(blockReason('restartMatch'))"
                @click="
                  runAction(
                    'restartMatch',
                    $t('重启比赛'),
                    $t('确认重启当前比赛？此操作会影响真实服务器。'),
                    target => restartMatch({ targetRevision: target }),
                    undefined,
                    true
                  )
                "
                >{{ $t("重启比赛") }}</el-button
              >
            </span>
          </el-tooltip>
          <el-tooltip :content="$t('当前 RCON 未开放经过确认的关闭服务器命令')">
            <span
              ><el-button type="danger" disabled>{{
                $t("关闭服务器（未开放）")
              }}</el-button></span
            >
          </el-tooltip>
        </div>
      </el-card>
    </div>

    <el-card v-if="!bansOnly" shadow="never">
      <template #header>{{ $t("切换地图") }}</template>
      <div class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <el-select
          v-model="mapForm.map"
          filterable
          :placeholder="$t('选择地图')"
          @change="onMapChange"
        >
          <el-option
            v-for="item in maps"
            :key="item.id"
            :label="item.label"
            :value="item.id"
          />
        </el-select>
        <el-select
          v-model="mapForm.experiences"
          multiple
          collapse-tags
          :multiple-limit="16"
          :loading="mapOptionsLoading"
          :placeholder="$t('地图体验（可选）')"
        >
          <el-option
            v-for="item in experiences"
            :key="item.id"
            :label="item.label"
            :value="item.id"
          />
        </el-select>
        <el-select
          v-model="mapForm.lighting"
          clearable
          :placeholder="$t('光照（可选）')"
        >
          <el-option
            v-for="item in lightings"
            :key="item.id"
            :label="item.label"
            :value="item.id"
          />
        </el-select>
        <el-select
          v-model="mapForm.zoneAlternator"
          clearable
          :loading="mapOptionsLoading"
          :placeholder="$t('区域变体（可选）')"
        >
          <el-option
            v-for="item in alternators"
            :key="item.id"
            :label="item.label"
            :value="item.id"
          />
        </el-select>
      </div>
      <el-alert
        v-if="mapOptionsError"
        class="mt-3"
        :title="mapOptionsError"
        type="warning"
        :closable="false"
      />
      <div class="mt-4">
        <el-tooltip :content="mapBlockReason() || $t('切换真实服务器地图')">
          <span>
            <el-button
              type="warning"
              :disabled="Boolean(mapBlockReason())"
              @click="submitMap"
            >
              {{ $t("确认切换地图") }}
            </el-button>
          </span>
        </el-tooltip>
      </div>
    </el-card>

    <el-card v-if="!bansOnly" shadow="never">
      <template #header>{{ $t("切换当前光照") }}</template>
      <p class="mb-3 text-sm text-gray-500">
        {{ $t("单独切换当前世界光照，不更换地图。") }}
      </p>
      <div class="flex flex-wrap items-center gap-3">
        <el-select
          v-model="lightingSelection"
          class="w-64"
          :placeholder="$t('从光照目录选择')"
        >
          <el-option
            v-for="item in lightings"
            :key="item.id"
            :label="item.label"
            :value="item.id"
          />
        </el-select>
        <el-tooltip
          :content="lightingBlockReason() || $t('切换真实服务器光照')"
        >
          <span>
            <el-button
              type="warning"
              :disabled="Boolean(lightingBlockReason())"
              @click="submitLighting"
              >{{ $t("确认切换光照") }}</el-button
            >
          </span>
        </el-tooltip>
      </div>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span
            >{{ $t("封禁列表")
            }}<template v-if="bans">（{{ validBans.length }}）</template></span
          >
          <span class="text-xs text-gray-500"
            >{{ $t("采集时间：")
            }}{{ readableTime(bans?.observedAt ?? null) }}</span
          >
        </div>
      </template>
      <el-input
        v-model="banSearch"
        class="mb-4"
        clearable
        :placeholder="$t('搜索 SteamID、原因或执行者')"
      />
      <el-table
        v-if="bans"
        :data="sortedBans"
        :empty-text="$t('暂无封禁记录')"
        size="small"
      >
        <el-table-column prop="steamId" label="SteamID" min-width="190" />
        <el-table-column
          prop="reason"
          :label="$t('原因')"
          min-width="180"
          show-overflow-tooltip
        />
        <el-table-column :label="$t('封禁时间')" min-width="170">
          <template #default="scope">{{
            readableTime(scope.row.bannedAtUtc)
          }}</template>
        </el-table-column>
        <el-table-column
          prop="bannedBy"
          :label="$t('执行者')"
          min-width="120"
        />
        <el-table-column :label="$t('操作')" width="125" fixed="right">
          <template #default="scope">
            <el-tooltip :content="blockReason('unban') || $t('解除封禁')">
              <span>
                <el-button
                  size="small"
                  type="warning"
                  :disabled="
                    scope.row.source === 'config' ||
                    !/^[1-9][0-9]{16}$/.test(scope.row.steamId) ||
                    Boolean(blockReason('unban'))
                  "
                  @click="unban(scope.row)"
                  >{{ $t("解除封禁") }}</el-button
                >
              </span>
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else :description="$t('封禁列表尚未读取')" />
    </el-card>

    <el-card v-if="!bansOnly" shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span>{{ $t("审计记录") }}</span>
          <div class="flex items-center gap-2">
            <span class="text-xs text-gray-500"
              >{{ $t("采集时间：")
              }}{{ readableTime(audit?.observedAt ?? null) }}</span
            >
            <el-select
              v-model="auditLimit"
              class="w-28"
              size="small"
              @change="load"
            >
              <el-option
                v-for="count in [25, 50, 100, 200]"
                :key="count"
                :label="count + $t(' 条')"
                :value="count"
              />
            </el-select>
          </div>
        </div>
      </template>
      <el-table
        v-if="audit"
        :data="audit.entries"
        :empty-text="$t('暂无审计记录')"
        size="small"
      >
        <el-table-column :label="$t('时间')" min-width="170">
          <template #default="scope">{{
            readableTime(scope.row.timestampUtc)
          }}</template>
        </el-table-column>
        <el-table-column prop="event" :label="$t('事件')" min-width="135" />
        <el-table-column
          prop="peer"
          :label="$t('来源')"
          min-width="140"
          show-overflow-tooltip
        />
        <el-table-column
          prop="sessionId"
          :label="$t('会话')"
          min-width="130"
          show-overflow-tooltip
        />
        <el-table-column
          prop="detail"
          :label="$t('详情')"
          min-width="280"
          show-overflow-tooltip
        />
      </el-table>
      <el-empty v-else :description="$t('审计记录尚未读取')" />
    </el-card>
  </div>
</template>
