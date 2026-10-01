<script setup lang="ts">
import Actions from "@/views/actions/index.vue";

defineOptions({ name: "BanManagement" });
</script>

<template>
  <Actions :bans-only="true" />
</template>
