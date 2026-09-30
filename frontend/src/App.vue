<template>
  <el-config-provider :locale="elementLocale">
    <router-view v-if="httpAcknowledged" :key="locale" />
    <el-dialog
      :model-value="!httpAcknowledged"
      :title="$t('HTTP 部署安全提醒')"
      width="min(480px, 90vw)"
      :show-close="false"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
    >
      <el-alert
        :title="$t('您未部署在https版本 请留意数据安全')"
        type="warning"
        :closable="false"
        show-icon
      />
      <p class="mt-4">
        {{
          $t(
            "HTTP 不加密，账号密码和登录会话可能被窃取。建议按照部署教程配置 HTTPS。"
          )
        }}
      </p>
      <template #footer>
        <el-button type="primary" @click="httpAcknowledged = true">
          {{ $t("我已了解，继续使用") }}
        </el-button>
      </template>
    </el-dialog>
    <ReDialog />
  </el-config-provider>
</template>

<script lang="ts">
import { defineComponent, watchEffect } from "vue";
import { ElConfigProvider } from "element-plus";
import { ReDialog } from "@/components/ReDialog";
import { elementLocale, locale, t } from "@/i18n";
import router from "@/router";
import { getConfig } from "@/config";

export default defineComponent({
  name: "app",
  components: {
    [ElConfigProvider.name]: ElConfigProvider,
    ReDialog
  },
  data() {
    return { httpAcknowledged: window.location.protocol !== "http:" };
  },
  setup() {
    watchEffect(() => {
      document.documentElement.lang = locale.value;
      const title = router.currentRoute.value.meta.title;
      const panelTitle = t(getConfig().Title || "Wardogs 服务器管理面板");
      document.title = title ? `${t(title)} | ${panelTitle}` : panelTitle;
    });
    return { elementLocale, locale };
  }
});
</script>
