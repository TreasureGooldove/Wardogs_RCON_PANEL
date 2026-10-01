<script setup lang="ts">
import { ref } from "vue";
import { t } from "@/i18n";
import type { RulesConfig } from "@/api/rules";
import { previewRules, type Preview } from "@/api/community";
import { getApiErrorMessage } from "@/api/errors";
import { formatObservedAt } from "@/api/snapshot";
const props = defineProps<{ config: RulesConfig }>();
const result = ref<Preview>();
const busy = ref(false);
const error = ref("");
async function run() {
  busy.value = true;
  error.value = "";
  try {
    result.value = await previewRules(props.config);
  } catch (e) {
    error.value = getApiErrorMessage(e);
  } finally {
    busy.value = false;
  }
}
const reasons: Record<string, string> = {
  baseline: "初次采集或断档，不视为加入",
  cooldown: "处于冷却时间",
  left_before_delivery: "发送前已离线",
  not_due: "尚未到发送时间",
  would_send: "预计发送"
};
</script>
<template>
  <el-card shadow="never" class="mt-4"
    ><template #header>{{ t("规则预演") }}</template
    ><el-alert
      :title="
        t(
          '使用当前草稿回放最近 24 小时观测到的加入会话，近似计算延迟、冷却及发送上限；不会保存规则或发送消息。新版本之前的会话无法回放。'
        )
      "
      :closable="false" /><el-button
      class="my-4"
      :loading="busy"
      @click="run"
      >{{ t("预演当前草稿") }}</el-button
    ><el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false" /><template v-if="result"
      ><p>
        {{ t("预演会话") }}：{{ result.total }} · {{ t("实际发送指令") }}：{{
          result.commandsSent
        }}<span v-if="result.truncated"> · {{ t("结果已截断") }}</span>
      </p>
      <el-table :data="result.items"
        ><el-table-column prop="name" :label="t('玩家')" /><el-table-column
          :label="t('预计发送时间')"
          ><template #default="{ row }">{{
            formatObservedAt(row.wouldSendAt)
          }}</template></el-table-column
        ><el-table-column :label="t('结果')"
          ><template #default="{ row }">{{
            t(reasons[row.result] || row.result)
          }}</template></el-table-column
        ><el-table-column :label="t('消息预览')"
          ><template #default="{ row }"
            ><p v-for="(part, index) in row.parts" :key="index">
              {{ part }}
            </p></template
          ></el-table-column
        ></el-table
      ><el-empty
        v-if="!result.items.length"
        :description="t('暂无可预演会话')" /></template
  ></el-card>
</template>
