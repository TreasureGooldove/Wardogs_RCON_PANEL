<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Player } from "@/api/players";
import { FACTIONS, normalizeFaction, type FactionCode } from "@/utils/factions";

const props = defineProps<{
  players: Player[] | null;
  observedAt: string | null;
}>();

interface Sample {
  observedAt: string;
  cash: Record<FactionCode, number>;
}

const samples = ref<Sample[]>([]);
watch(
  () => props.observedAt,
  observedAt => {
    if (!observedAt || !props.players || samples.value.at(-1)?.observedAt === observedAt) return;
    const cash: Record<FactionCode, number> = { BLU: 0, RED: 0, GRN: 0 };
    for (const player of props.players) {
      const faction = normalizeFaction(player.faction);
      if (faction && player.cash !== null) cash[faction] += player.cash;
    }
    samples.value = [...samples.value.slice(-59), { observedAt, cash }];
  },
  { immediate: true }
);

const ceiling = computed(() =>
  Math.max(1, ...samples.value.flatMap(sample => FACTIONS.map(faction => sample.cash[faction.code])))
);
const current = computed(() => samples.value.at(-1)?.cash ?? null);

function points(faction: FactionCode) {
  if (!samples.value.length) return "";
  return samples.value
    .map((sample, index) => {
      const x = samples.value.length === 1 ? 0 : (index / (samples.value.length - 1)) * 600;
      const y = 140 - (sample.cash[faction] / ceiling.value) * 130;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}
</script>

<template>
  <div class="space-y-3">
    <p class="text-xs text-gray-500">页面打开期间每次获取玩家名单时采样；刷新页面后重新开始</p>
    <el-empty v-if="samples.length === 0" description="等待玩家现金数据" />
    <template v-else>
      <div class="flex flex-wrap gap-4 text-sm">
        <div v-for="faction in FACTIONS" :key="faction.code" class="flex items-center gap-2">
          <span class="h-2.5 w-2.5 rounded-full" :style="{ backgroundColor: faction.color }" />
          <span :style="{ color: faction.color }">{{ faction.name }}</span>
          <strong>{{ current?.[faction.code].toLocaleString() ?? "—" }}</strong>
        </div>
      </div>
      <svg
        viewBox="0 0 600 150"
        preserveAspectRatio="none"
        class="h-44 w-full rounded border border-[var(--el-border-color-light)] bg-[var(--el-fill-color-blank)]"
        role="img"
        aria-label="三阵营现金总量趋势"
      >
        <line x1="0" y1="140" x2="600" y2="140" stroke="var(--el-border-color)" />
        <line x1="0" y1="75" x2="600" y2="75" stroke="var(--el-border-color-lighter)" />
        <polyline
          v-for="faction in FACTIONS"
          :key="faction.code"
          :points="points(faction.code)"
          fill="none"
          :stroke="faction.color"
          stroke-width="2.5"
          vector-effect="non-scaling-stroke"
        />
      </svg>
      <p class="text-right text-xs text-gray-500">最高刻度：{{ ceiling.toLocaleString() }}</p>
    </template>
  </div>
</template>
