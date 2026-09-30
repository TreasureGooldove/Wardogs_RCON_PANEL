<script setup lang="ts">
import { ElMessageBox } from "element-plus";
import { activeLanguage, languages, locale, setLocale, t } from "@/i18n";

async function select(code: string) {
  if (code === locale.value) return;
  // Reload component state so setup-time labels and validation messages also
  // change language. Confirm before discarding an administrator's draft.
  try {
    await ElMessageBox.confirm(
      t("切换语言会重新载入当前页面，请先保存未提交的草稿。是否继续？"),
      t("切换语言"),
      {
        confirmButtonText: t("切换"),
        cancelButtonText: t("取消"),
        type: "info"
      }
    );
  } catch {
    return;
  }
  setLocale(code);
}
</script>

<template>
  <el-dropdown trigger="click" @command="select">
    <el-button text :aria-label="$t('选择语言')" class="max-w-[180px]">
      <span aria-hidden="true">🌐</span>
      <span class="ml-1 hidden truncate sm:inline">{{
        activeLanguage.label
      }}</span>
    </el-button>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item
          v-for="language in languages"
          :key="language.code"
          :command="language.code"
          :disabled="language.code === locale"
        >
          {{ language.label }}
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>
