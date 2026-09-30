<script setup lang="ts">
import { computed } from "vue";
import type { Player } from "@/api/players";
import { FACTIONS, normalizeFaction, type FactionCode } from "@/utils/factions";

const props = defineProps<{ players: Player[] | null }>();

const counts = computed(() => {
  const result: Record<FactionCode, number> = { BLU: 0, RED: 0, GRN: 0 };
  for (const player of props.players ?? []) {
    const faction = normalizeFaction(player.faction);
    if (faction) {
      result[faction] += 1;
    }
  }
  return result;
});

const unknownCount = computed(
  () =>
    props.players?.filter(player => !normalizeFaction(player.faction)).length ??
    0
);
</script>

<template>
  <div>
    <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <div
        v-for="faction in FACTIONS"
        :key="faction.code"
        class="rounded-lg border border-[var(--el-border-color-light)] bg-[var(--el-fill-color-blank)] px-4 py-3"
      >
        <div class="flex items-center justify-between gap-3">
          <div class="flex items-center gap-2">
            <span
              class="h-2.5 w-2.5 rounded-full"
              :style="{ backgroundColor: faction.color }"
            />
            <span class="font-medium" :style="{ color: faction.color }">{{
              faction.name
            }}</span>
          </div>
          <strong class="text-xl" :style="{ color: faction.color }">
            {{ players === null ? "—" : counts[faction.code] }}
          </strong>
        </div>
      </div>
    </div>
    <p v-if="unknownCount" class="mt-2 text-xs text-gray-500">
      {{ $t("另有") }} {{ unknownCount }} {{ $t("位玩家的阵营未知") }}
    </p>
  </div>
</template>
