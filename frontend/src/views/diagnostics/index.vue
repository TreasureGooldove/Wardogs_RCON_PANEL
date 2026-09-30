<script setup lang="ts">
import { onMounted, ref } from "vue";
import {
  getHealth,
  getServerId,
  getSponsor,
  type HealthResponse,
  type ServerIdResponse,
  type SponsorResponse
} from "@/api/diagnostics";
import { getApiErrorMessage } from "@/api/errors";
import { formatObservedAt } from "@/api/snapshot";

defineOptions({ name: "ServerDiagnostics" });

const health = ref<HealthResponse | null>(null);
const serverId = ref<ServerIdResponse | null>(null);
const sponsor = ref<SponsorResponse | null>(null);
const loading = ref(false);
const errors = ref<Record<string, string>>({});

async function refresh() {
  if (loading.value) return;
  loading.value = true;
  errors.value = {};
  const result = await Promise.allSettled([
    getHealth(),
    getServerId(),
    getSponsor()
  ]);
  if (result[0].status === "fulfilled") health.value = result[0].value;
  else errors.value.health = getApiErrorMessage(result[0].reason);
  if (result[1].status === "fulfilled") serverId.value = result[1].value;
  else errors.value.serverId = getApiErrorMessage(result[1].reason);
  if (result[2].status === "fulfilled") sponsor.value = result[2].value;
  else errors.value.sponsor = getApiErrorMessage(result[2].reason);
  loading.value = false;
}

onMounted(refresh);
</script>

<template>
  <div class="space-y-5 p-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-2xl font-semibold">{{ $t("服务器诊断") }}</h1>
        <p class="text-sm text-gray-500">
          {{ $t("只读查看健康状态、服务器标识与展示图片") }}
        </p>
      </div>
      <el-button :loading="loading" @click="refresh">{{
        $t("重新查询")
      }}</el-button>
    </div>
    <div class="grid grid-cols-1 gap-5 lg:grid-cols-2">
      <el-card shadow="never">
        <template #header>{{ $t("RCON 健康状态") }}</template>
        <el-alert
          v-if="errors.health"
          :title="errors.health"
          type="warning"
          :closable="false"
        />
        <el-empty v-if="!health" :description="$t('尚无健康状态')" />
        <el-descriptions v-else :column="1" border>
          <el-descriptions-item :label="$t('报告状态')">{{
            health.reportedState ?? $t("未知")
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('运行时间')">{{
            health.uptimeSeconds === null
              ? $t("未知")
              : $t("{p0} 分钟", { p0: Math.floor(health.uptimeSeconds / 60) })
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('活动连接')">{{
            health.connections?.active ?? $t("未知")
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('游戏线程执行中')">{{
            health.gameThreadQueue?.inFlight ?? $t("未知")
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('游戏线程队列')">{{
            health.gameThreadQueue?.depth ?? $t("未知")
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('累计拒绝')">{{
            health.gameThreadQueue?.rejectedTotal ?? $t("未知")
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('查询时间')">{{
            formatObservedAt(health.observedAt)
          }}</el-descriptions-item>
        </el-descriptions>
      </el-card>
      <el-card shadow="never">
        <template #header>{{ $t("服务器资料") }}</template>
        <el-alert
          v-if="errors.serverId"
          :title="errors.serverId"
          type="warning"
          :closable="false"
        />
        <el-alert
          v-if="errors.sponsor"
          :title="errors.sponsor"
          type="warning"
          :closable="false"
          class="mt-2"
        />
        <el-descriptions :column="1" border>
          <el-descriptions-item label="ServerID">{{
            serverId?.serverId || $t("未设置或未知")
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('展示图片')">{{
            sponsor?.hasImage ? $t("已设置") : $t("未设置或未知")
          }}</el-descriptions-item>
        </el-descriptions>
        <img
          v-if="sponsor?.imageUrl"
          :src="sponsor.imageUrl"
          :alt="$t('服务器展示图片')"
          class="mt-4 max-h-64 max-w-full rounded border border-[var(--el-border-color-light)] object-contain"
        />
        <p class="mt-3 text-xs text-gray-500">
          {{ $t("展示图片地址可在“配置文件”的 ServerImageURL 中编辑。") }}
        </p>
      </el-card>
    </div>
  </div>
</template>
