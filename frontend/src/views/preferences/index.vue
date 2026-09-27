<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useDataThemeChange } from "@/layout/hooks/useDataThemeChange";

defineOptions({ name: "PanelPreferences" });

const theme = useDataThemeChange();
const configDebug = ref(false);
const steamProfiles = ref(true);

onMounted(() => {
  configDebug.value = localStorage.getItem("wardogs-config-debug") === "true";
  steamProfiles.value = localStorage.getItem("wardogs-steam-profiles-enabled") !== "false";
});

function setDark(value: boolean) {
  theme.dataTheme.value = value;
  theme.dataThemeChange();
}

function setConfigDebug(value: boolean) {
  configDebug.value = value;
  localStorage.setItem("wardogs-config-debug", String(value));
  window.dispatchEvent(new Event("wardogs-display-settings-changed"));
}

function setSteamProfiles(value: boolean) {
  steamProfiles.value = value;
  localStorage.setItem("wardogs-steam-profiles-enabled", String(value));
  window.dispatchEvent(new Event("wardogs-display-settings-changed"));
}
</script>

<template>
  <div class="space-y-5 p-5">
    <div>
      <h1 class="text-2xl font-semibold">界面设置</h1>
      <p class="text-sm text-gray-500">这些偏好只保存在当前浏览器</p>
    </div>
    <el-card shadow="never">
      <template #header>外观</template>
      <div class="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div class="font-medium">主题</div>
          <p class="text-sm text-gray-500">切换浅色与深色界面</p>
        </div>
        <el-switch
          :model-value="theme.dataTheme.value"
          active-text="深色"
          inactive-text="浅色"
          @change="setDark(Boolean($event))"
        />
      </div>
    </el-card>
    <el-card shadow="never">
      <template #header>配置调试</template>
      <div class="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div class="font-medium">显示配置变更详情</div>
          <p class="text-sm text-gray-500">在配置文件页显示草稿差异和服务器验证详情</p>
        </div>
        <el-switch :model-value="configDebug" @change="setConfigDebug(Boolean($event))" />
      </div>
    </el-card>
    <el-card shadow="never">
      <template #header>Steam 资料</template>
      <div class="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div class="font-medium">显示公开头像与昵称</div>
          <p class="text-sm text-gray-500">
            由面板后端使用服务器保存的 API Key 查询。Key 不会发送到浏览器。
          </p>
        </div>
        <el-switch :model-value="steamProfiles" @change="setSteamProfiles(Boolean($event))" />
      </div>
    </el-card>
  </div>
</template>
