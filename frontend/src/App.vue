<template>
  <el-config-provider :locale="currentLocale">
    <router-view v-if="httpAcknowledged" />
    <el-dialog
      :model-value="!httpAcknowledged"
      title="HTTP 部署安全提醒"
      width="min(480px, 90vw)"
      :show-close="false"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
    >
      <el-alert
        title="您未部署在https版本 请留意数据安全"
        type="warning"
        :closable="false"
        show-icon
      />
      <p class="mt-4">HTTP 不加密，账号密码和登录会话可能被窃取。建议按照部署教程配置 HTTPS。</p>
      <template #footer>
        <el-button type="primary" @click="httpAcknowledged = true">
          我已了解，继续使用
        </el-button>
      </template>
    </el-dialog>
    <ReDialog />
  </el-config-provider>
</template>

<script lang="ts">
import { defineComponent } from "vue";
import { ElConfigProvider } from "element-plus";
import { ReDialog } from "@/components/ReDialog";
import zhCn from "element-plus/es/locale/lang/zh-cn";

export default defineComponent({
  name: "app",
  components: {
    [ElConfigProvider.name]: ElConfigProvider,
    ReDialog
  },
  data() {
    return { httpAcknowledged: window.location.protocol !== "http:" };
  },
  computed: {
    currentLocale() {
      return zhCn;
    }
  }
});
</script>
