<script setup lang="ts">
import type { FactionCode } from "@/utils/factions";

defineProps<{ faction: FactionCode }>();
</script>

<template>
  <svg viewBox="0 0 64 64" fill="none" aria-hidden="true" focusable="false">
    <template v-if="faction === 'BLU'">
      <path
        d="m32 3 8 19 21 2-16 14 5 23-18-12-18 12 5-23L3 24l21-2Z"
        fill="currentColor"
        opacity=".24"
      />
      <path
        d="m32 5 7.6 18.6L59 25l-15 13 4.5 20L32 47 15.5 58 20 38 5 25l19.4-1.4Z"
        stroke="currentColor"
        stroke-width="3"
        stroke-linejoin="miter"
      />
      <path d="m32 16 5 14 14 1-11 9 3 14-11-8V16Z" fill="currentColor" />
      <path
        d="m18 31 10-1 4-11v25l-9 6 3-13Z"
        fill="currentColor"
        opacity=".7"
      />
    </template>
    <template v-else-if="faction === 'RED'">
      <path
        d="m4 15 17 5L32 6l11 14 17-5-12 28-7-13-9 26-9-26-7 13Z"
        fill="currentColor"
        opacity=".16"
      />
      <path
        d="m4 15 17 5L32 6l11 14 17-5-12 28-7-13-9 26-9-26-7 13Z"
        stroke="currentColor"
        stroke-width="3.5"
        stroke-linejoin="miter"
      />
      <path
        d="m21 20 11 36 11-36M16 43l5-23m22 0 5 23M26 25l6-8 6 8-6 18Z"
        stroke="currentColor"
        stroke-width="3"
      />
    </template>
    <template v-else>
      <path
        d="m5 12 17 12L32 8l10 16 17-12-9 44-10-9-8 12-8-12-10 9Z"
        fill="currentColor"
        opacity=".16"
      />
      <path
        d="m5 12 17 12L32 8l10 16 17-12-9 44-10-9-8 12-8-12-10 9Z"
        stroke="currentColor"
        stroke-width="3.5"
        stroke-linejoin="miter"
      />
      <path
        d="m14 56 8-32 10 15 10-15 8 32M24 47l8-27 8 27m-14-6h12"
        stroke="currentColor"
        stroke-width="3"
      />
    </template>
  </svg>
</template>
