<script setup lang="ts">
import { getConfig } from "@/config";

const TITLE = getConfig("Title");
</script>

<template>
  <footer
    class="layout-footer text-[rgba(0,0,0,0.6)] dark:text-[rgba(220,220,242,0.8)]"
  >
    {{ $t(TITLE) }} {{ $t("· 前端基于") }}
    <a
      class="hover:text-primary!"
      href="https://github.com/pure-admin/pure-admin-thin"
      target="_blank"
      rel="noopener noreferrer"
    >
      &nbsp;pure-admin-thin（MIT）
    </a>
  </footer>
</template>

<style lang="scss" scoped>
.layout-footer {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  padding: 0 0 8px;
  font-size: 14px;
}
</style>
