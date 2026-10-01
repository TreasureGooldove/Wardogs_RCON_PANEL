<script setup lang="ts">
import { t } from "@/i18n";
import {
  computed,
  onActivated,
  onDeactivated,
  onMounted,
  onUnmounted,
  ref
} from "vue";
import { getCapabilities, type CapabilitiesResponse } from "@/api/capabilities";
import { getServerStatus, type StatusResponse } from "@/api/status";
import { getApiErrorMessage } from "@/api/errors";
import { formatObservedAt } from "@/api/snapshot";
import FactionCounts from "@/components/FactionCounts.vue";
import FactionCashChart from "@/components/FactionCashChart.vue";
import { usePlayersPolling } from "@/composables/usePlayersPolling";
import { factionDisplay } from "@/utils/factions";

defineOptions({ name: "Welcome" });

const status = ref<StatusResponse | null>(null);
const capabilities = ref<CapabilitiesResponse | null>(null);
const loading = ref(false);
const error = ref("");
const {
  snapshot: playerSnapshot,
  loading: playerLoading,
  error: playerError,
  refresh: refreshPlayers
} = usePlayersPolling();
let timer: ReturnType<typeof setInterval> | undefined;
let active = false;
let rerunWhenIdle = false;

const statusCapability = computed(() => {
  if (!capabilities.value || capabilities.value.state === "unavailable") {
    return t("未知");
  }
  if (capabilities.value.features.status === null) return t("未知");
  return capabilities.value.features.status ? t("支持") : t("不支持");
});

const playerCount = computed(() => {
  if (!status.value) return t("未知");
  const current = status.value.playerCount;
  const maximum = status.value.maxPlayers;
  return t("{p0} / {p1}", {
    p0: current ?? t("未知"),
    p1: maximum ?? t("未知")
  });
});

async function refresh() {
  if (!active || document.visibilityState !== "visible") return;
  if (loading.value) {
    rerunWhenIdle = true;
    return;
  }
  loading.value = true;
  error.value = "";
  const [statusResult, capabilityResult] = await Promise.allSettled([
    getServerStatus(),
    getCapabilities()
  ]);
  if (active && document.visibilityState === "visible") {
    if (statusResult.status === "fulfilled") {
      status.value = statusResult.value;
    } else {
      if (status.value) status.value = { ...status.value, stale: true };
      error.value = getApiErrorMessage(statusResult.reason);
    }
    if (capabilityResult.status === "fulfilled") {
      capabilities.value = capabilityResult.value;
    } else {
      capabilities.value = null;
    }
  }
  loading.value = false;
  if (rerunWhenIdle) {
    rerunWhenIdle = false;
    void refresh();
  }
}

function refreshAll() {
  void refresh();
  void refreshPlayers();
}

function startTimer() {
  if (!timer && document.visibilityState === "visible") {
    timer = setInterval(() => void refresh(), 2_000);
  }
}

function onVisibilityChange() {
  if (!active) return;
  if (document.visibilityState === "visible") {
    void refresh();
    startTimer();
  } else if (timer) {
    clearInterval(timer);
    timer = undefined;
  }
}

function start() {
  if (active) return;
  active = true;
  void refresh();
  startTimer();
  document.addEventListener("visibilitychange", onVisibilityChange);
}

function stop() {
  if (!active) return;
  active = false;
  rerunWhenIdle = false;
  if (timer) clearInterval(timer);
  timer = undefined;
  document.removeEventListener("visibilitychange", onVisibilityChange);
}

onMounted(start);
onActivated(start);
onDeactivated(stop);
onUnmounted(stop);
</script>

<template>
  <div class="p-5 space-y-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-2xl font-semibold">{{ $t("服务器概况") }}</h1>
        <p class="text-sm text-gray-500">
          {{ $t("共享采集：有人查看时玩家每 1 秒、状态每 2 秒刷新") }}
        </p>
      </div>
      <el-button :loading="loading || playerLoading" @click="refreshAll">{{
        $t("刷新状态")
      }}</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert
      v-if="playerError"
      :title="playerError"
      type="error"
      :closable="false"
    />
    <el-alert
      v-if="status?.stale"
      :title="$t('当前显示的是过期快照，并非实时服务器状态')"
      type="warning"
      :closable="false"
    />
    <el-alert
      v-if="capabilities?.state === 'stale'"
      :title="$t('能力信息已过期，功能可用性以新一次探测为准')"
      type="warning"
      :closable="false"
    />
    <el-alert
      v-if="playerSnapshot?.stale"
      :title="$t('阵营人数来自过期的玩家快照')"
      type="warning"
      :closable="false"
    />

    <el-card shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span>{{ $t("实时阵营人数") }}</span>
          <span class="text-sm text-gray-500">
            {{ $t("采集时间：")
            }}{{ formatObservedAt(playerSnapshot?.observedAt ?? null) }}
          </span>
        </div>
      </template>
      <FactionCounts :players="playerSnapshot?.players ?? null" />
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span>{{ $t("当前快照") }}</span>
          <span class="text-sm text-gray-500">
            {{ $t("采集时间：")
            }}{{ formatObservedAt(status?.observedAt ?? null) }}
          </span>
        </div>
      </template>

      <el-skeleton v-if="loading && !status" :rows="5" animated />
      <el-empty
        v-else-if="!status"
        :description="$t('尚无可显示的服务器状态')"
      />
      <div v-else class="space-y-5">
        <div class="grid grid-cols-1 gap-4 md:grid-cols-3">
          <div class="rounded-lg bg-[var(--el-fill-color-light)] p-5">
            <div class="text-sm text-gray-500">{{ $t("当前地图") }}</div>
            <div class="mt-2 text-xl font-medium">
              {{ status.map ?? $t("未知") }}
            </div>
          </div>
          <div class="rounded-lg bg-[var(--el-fill-color-light)] p-5">
            <div class="text-sm text-gray-500">{{ $t("在线人数 / 容量") }}</div>
            <div class="mt-2 text-xl font-medium">{{ playerCount }}</div>
          </div>
          <div class="rounded-lg bg-[var(--el-fill-color-light)] p-5">
            <div class="text-sm text-gray-500">{{ $t("状态查询能力") }}</div>
            <div class="mt-2 text-xl font-medium">{{ statusCapability }}</div>
          </div>
        </div>

        <el-descriptions :column="1" border>
          <el-descriptions-item :label="$t('服务器名称')">
            {{ status.serverName ?? $t("未知") }}
          </el-descriptions-item>
          <el-descriptions-item :label="$t('游戏模式')">
            {{
              status.experiences === null
                ? $t("未知")
                : status.experiences.length
                  ? status.experiences.join("、")
                  : $t("无")
            }}
          </el-descriptions-item>
          <el-descriptions-item :label="$t('光照')">
            {{ status.lighting ?? $t("未知") }}
          </el-descriptions-item>
          <el-descriptions-item :label="$t('控制区')">
            {{ status.alternator ?? $t("未知") }}
          </el-descriptions-item>
          <el-descriptions-item :label="$t('比分周期')">
            {{ status.scoreTick?.current ?? $t("未知") }}
            <span
              v-if="
                status.scoreTick?.min != null && status.scoreTick?.max != null
              "
            >
              {{ $t("（范围") }} {{ status.scoreTick?.min }} –
              {{ status.scoreTick?.max }}）
            </span>
          </el-descriptions-item>
          <el-descriptions-item :label="$t('比分上限')">
            {{ status.scoreCap ?? $t("未知") }}
          </el-descriptions-item>
          <el-descriptions-item :label="$t('比赛时长')">
            {{
              status.matchSeconds === null
                ? $t("未知")
                : $t("{p0} 分 {p1} 秒", {
                    p0: Math.floor(status.matchSeconds / 60),
                    p1: status.matchSeconds % 60
                  })
            }}
          </el-descriptions-item>
          <el-descriptions-item :label="$t('地图轮换位置')">
            {{ status.rotation?.nowIndex ?? $t("未知") }} →
            {{ status.rotation?.nextIndex ?? $t("未知") }}
          </el-descriptions-item>
        </el-descriptions>

        <div>
          <h2 class="mb-3 text-base font-medium">{{ $t("阵营比分") }}</h2>
          <p v-if="status.factionScores === null" class="text-sm text-gray-500">
            {{ $t("比分未知") }}
          </p>
          <p
            v-else-if="status.factionScores.length === 0"
            class="text-sm text-gray-500"
          >
            {{ $t("暂无比分") }}
          </p>
          <el-table v-else :data="status.factionScores" border>
            <el-table-column :label="$t('阵营')">
              <template #default="scope">
                <span
                  :style="{ color: factionDisplay(scope.row.name).color }"
                  >{{ scope.row.name }}</span
                >
              </template>
            </el-table-column>
            <el-table-column prop="score" :label="$t('分数')" />
          </el-table>
        </div>
      </div>
    </el-card>

    <el-card shadow="never">
      <template #header>{{ $t("阵营现金趋势") }}</template>
      <FactionCashChart
        :players="playerSnapshot?.players ?? null"
        :observed-at="playerSnapshot?.observedAt ?? null"
      />
    </el-card>
  </div>
</template>
