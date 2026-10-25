<script setup lang="ts">
import { computed, onDeactivated, onUnmounted, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import { http } from "@/utils/http";
import { t, formatDate } from "@/i18n";
import { getApiErrorMessage } from "@/api/errors";
import { useUserStoreHook } from "@/store/modules/user";
const owner = computed(() => useUserStoreHook().role === "owner");
const loading = ref(false),
  error = ref(""),
  rows = ref<Record<string, unknown>[]>([]),
  unlocked = ref(false);
const filter = ref("");
const visibleRows = computed(() =>
  rows.value.filter(row =>
    JSON.stringify(row).toLowerCase().includes(filter.value.toLowerCase())
  )
);
let timer: ReturnType<typeof setTimeout> | undefined;
function lock() {
  rows.value = [];
  unlocked.value = false;
  clearTimeout(timer);
}
watch(owner, lock);
onDeactivated(lock);
onUnmounted(lock);
async function load() {
  if (loading.value) return;
  let password = "";
  error.value = "";
  try {
    const answer = await ElMessageBox.prompt(
      t(
        "请输入当前主账号的面板登录密码以查看日志。日志将在离开页面或 5 分钟后隐藏。"
      ),
      t("验证密码查看日志"),
      {
        inputType: "password",
        confirmButtonText: t("查看"),
        cancelButtonText: t("取消")
      }
    );
    password = answer.value;
    loading.value = true;
    const result = await http.request<{
      available: boolean;
      dropped: number;
      items: Record<string, unknown>[];
    }>("post", "/api/panel/logs/query", { data: { password, limit: 300 } });
    lock();
    rows.value = result.items;
    unlocked.value = true;
    if (!result.available)
      error.value = t("日志文件暂不可写，请检查面板数据目录权限与磁盘空间。");
    else if (result.dropped > 0)
      error.value = t("部分日志未能写入，请检查面板运行状态后重新读取。");
    timer = setTimeout(lock, 300000);
  } catch (reason) {
    if (reason !== "cancel" && reason !== "close")
      error.value = getApiErrorMessage(reason);
  } finally {
    password = "";
    loading.value = false;
  }
}
function details(row: Record<string, unknown>) {
  const { at, kind, level, ...rest } = row;
  return JSON.stringify(rest);
}
</script>
<template>
  <el-card v-if="owner" shadow="never">
    <template #header>{{ t("面板日志") }}</template>
    <p class="mb-3 text-sm text-gray-500">
      {{
        t(
          "记录请求、错误、后台任务和管理结果；不记录密码、令牌、Cookie、查询参数或原始配置。滚动文件最多保留约 50MB，界面显示最近 300 条。"
        )
      }}
    </p>
    <el-button type="primary" :loading="loading" @click="load">{{
      t("验证密码查看日志")
    }}</el-button>
    <el-button v-if="unlocked" @click="lock">{{ t("隐藏日志") }}</el-button>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      class="my-3"
    />
    <template v-if="unlocked">
      <el-input
        v-model="filter"
        :placeholder="t('筛选日志或请求编号')"
        clearable
        class="my-3"
      />
      <el-table :data="visibleRows" max-height="600">
        <el-table-column :label="t('时间')" width="185"
          ><template #default="{ row }">{{
            formatDate(row.at as string)
          }}</template></el-table-column
        >
        <el-table-column prop="level" :label="t('级别')" width="95" />
        <el-table-column prop="kind" :label="t('类型')" width="95" />
        <el-table-column :label="t('详情')" min-width="500"
          ><template #default="{ row }"
            ><code class="break-all">{{ details(row) }}</code></template
          ></el-table-column
        >
      </el-table>
    </template>
  </el-card>
</template>
