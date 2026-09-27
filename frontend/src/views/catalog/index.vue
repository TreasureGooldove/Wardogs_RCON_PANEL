<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { getCapabilities, type CapabilitiesResponse } from "@/api/capabilities";
import {
  getCatalog,
  type CatalogKind,
  type CatalogResponse
} from "@/api/catalog";
import { getApiErrorMessage } from "@/api/errors";
import { formatObservedAt } from "@/api/snapshot";

defineOptions({ name: "Catalog" });

const kinds: Array<{ key: CatalogKind; title: string }> = [
  { key: "maps", title: "地图" },
  { key: "experiences", title: "游戏模式" },
  { key: "lightings", title: "光照" }
];
const capabilities = ref<CapabilitiesResponse | null>(null);
const catalogs = reactive<Record<CatalogKind, CatalogResponse | null>>({
  maps: null,
  experiences: null,
  lightings: null
});
const errors = reactive<Record<CatalogKind, string>>({
  maps: "",
  experiences: "",
  lightings: ""
});
const capabilityError = ref("");
const loading = ref(false);

function availability(kind: CatalogKind) {
  const value = capabilities.value?.features[kind];
  if (value === false) return "目标服务器明确不支持此目录";
  if (value === null || value === undefined) return "此目录的可用性未知";
  return "";
}

async function refresh() {
  if (loading.value) return;
  loading.value = true;
  capabilityError.value = "";
  try {
    capabilities.value = await getCapabilities();
  } catch (reason) {
    capabilities.value = null;
    capabilityError.value = getApiErrorMessage(reason);
  }

  await Promise.all(
    kinds.map(async ({ key }) => {
      errors[key] = "";
      if (capabilities.value?.features[key] !== true) {
        catalogs[key] = null;
        return;
      }
      try {
        catalogs[key] = await getCatalog(key);
      } catch (reason) {
        if (catalogs[key]) catalogs[key] = { ...catalogs[key], stale: true };
        errors[key] = getApiErrorMessage(reason);
      }
    })
  );
  loading.value = false;
}

onMounted(refresh);
</script>

<template>
  <div class="p-5 space-y-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-2xl font-semibold">参考目录</h1>
        <p class="text-sm text-gray-500">仅查看目标服务器提供的地图、模式和光照</p>
      </div>
      <el-button :loading="loading" @click="refresh">刷新目录</el-button>
    </div>

    <el-alert
      v-if="capabilityError"
      :title="capabilityError"
      type="error"
      :closable="false"
    />
    <el-alert
      v-if="capabilities?.state === 'stale'"
      title="能力探测信息已过期，目录可用性可能已变化"
      type="warning"
      :closable="false"
    />

    <div class="grid grid-cols-1 gap-5 xl:grid-cols-3">
      <el-card v-for="kind in kinds" :key="kind.key" shadow="never">
        <template #header>
          <div class="flex items-center justify-between gap-3">
            <span>{{ kind.title }}</span>
            <span class="text-xs text-gray-500">
              {{ formatObservedAt(catalogs[kind.key]?.observedAt ?? null) }}
            </span>
          </div>
        </template>

        <el-alert
          v-if="errors[kind.key]"
          class="mb-3"
          :title="errors[kind.key]"
          type="error"
          :closable="false"
        />
        <el-alert
          v-if="catalogs[kind.key]?.stale"
          class="mb-3"
          title="以下为过期目录快照"
          type="warning"
          :closable="false"
        />

        <el-skeleton v-if="loading && !catalogs[kind.key]" :rows="4" animated />
        <el-empty
          v-else-if="availability(kind.key)"
          :description="availability(kind.key)"
        />
        <el-empty
          v-else-if="!catalogs[kind.key]"
          description="尚无可显示的目录数据"
        />
        <el-empty
          v-else-if="catalogs[kind.key]?.items.length === 0"
          description="目录为空"
        />
        <el-table v-else :data="catalogs[kind.key]?.items" border>
          <el-table-column prop="label" label="名称" min-width="140" />
          <el-table-column prop="id" label="标识" min-width="120" />
        </el-table>
      </el-card>
    </div>
  </div>
</template>
