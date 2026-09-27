<script setup lang="ts">
import { onMounted, ref } from "vue";
import {
  getCapabilities,
  type CapabilitiesResponse,
  type FeatureKey
} from "@/api/capabilities";
import { getApiErrorMessage } from "@/api/errors";
import { formatObservedAt } from "@/api/snapshot";

defineOptions({ name: "Capabilities" });

const featureLabels: Record<FeatureKey, string> = {
  status: "服务器状态",
  players: "在线玩家",
  rotation: "地图轮换",
  maps: "地图目录",
  experiences: "模式目录",
  lightings: "光照目录",
  kick: "踢出玩家",
  ban: "永久封禁玩家"
};
const featureKeys = Object.keys(featureLabels) as FeatureKey[];

const capabilities = ref<CapabilitiesResponse | null>(null);
const loading = ref(false);
const error = ref("");

function availability(feature: FeatureKey) {
  const value = capabilities.value?.features[feature];
  if (value === true) return { label: "支持", type: "success" as const };
  if (value === false) return { label: "不支持", type: "info" as const };
  return { label: "未知", type: "warning" as const };
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
  } finally {
    loading.value = false;
  }
}

onMounted(refresh);
</script>

<template>
  <div class="p-5 space-y-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-2xl font-semibold">服务器能力</h1>
        <p class="text-sm text-gray-500">
          能力状态来自目标服务器的查询与管理能力探测
        </p>
      </div>
      <el-button :loading="loading" @click="refresh">重新探测</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert
      v-if="capabilities?.state === 'stale'"
      title="探测信息已过期，以下结果仅代表上次成功读取"
      type="warning"
      :closable="false"
    />
    <el-alert
      v-if="capabilities?.state === 'unavailable'"
      title="尚未成功探测服务器能力，不能判断是否支持"
      type="info"
      :closable="false"
    />

    <el-card shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span>服务器能力</span>
          <span class="text-sm text-gray-500">
            探测时间：{{ formatObservedAt(capabilities?.observedAt ?? null) }}
          </span>
        </div>
      </template>
      <el-skeleton v-if="loading && !capabilities" :rows="6" animated />
      <el-empty v-else-if="!capabilities" description="尚无能力探测结果" />
      <div v-else class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        <div
          v-for="feature in featureKeys"
          :key="feature"
          class="flex items-center justify-between rounded-lg border border-[var(--el-border-color)] p-4"
        >
          <span>{{ featureLabels[feature] }}</span>
          <el-tag :type="availability(feature).type">
            {{ availability(feature).label }}
          </el-tag>
        </div>
      </div>
    </el-card>
  </div>
</template>
