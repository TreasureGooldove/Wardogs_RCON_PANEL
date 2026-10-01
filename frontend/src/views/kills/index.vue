<script setup lang="ts">
import { ref } from "vue";
import { t } from "@/i18n";
import KillRecords from "@/components/KillRecords.vue";
const scope = ref("current");
</script>
<template>
  <div class="p-6">
    <h1 class="mb-4 text-2xl font-semibold">{{ t("击杀记录") }}</h1>
    <el-tabs v-model="scope">
      <el-tab-pane :label="t('当前对局')" name="current">
        <KillRecords v-if="scope === 'current'" match-id="current" />
      </el-tab-pane>
      <el-tab-pane :label="t('历史对局')" name="all">
        <KillRecords v-if="scope === 'all'" match-id="all" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
