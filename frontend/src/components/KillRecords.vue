<script setup lang="ts">
import { ref, watch, onUnmounted, onDeactivated, onActivated } from "vue";
import { t } from "@/i18n";
import { getKills, type KillResponse } from "@/api/community";
import { getApiErrorMessage } from "@/api/errors";
import { formatObservedAt } from "@/api/snapshot";
import { useRouter } from "vue-router";
const router = useRouter();
const props = withDefaults(defineProps<{ matchId?: string }>(), {
  matchId: "current"
});
const data = ref<KillResponse>();
const error = ref("");
const busy = ref(false);
const page = ref(1);
const round = ref("");
let timer: ReturnType<typeof setTimeout>;
let active = true;
let generation = 0;
async function refresh() {
  if (busy.value) return;
  busy.value = true;
  const token = generation;
  error.value = "";
  try {
    const selected = data.value?.rounds.find(
      r => JSON.stringify([r.instance_id, r.game_match_id]) === round.value
    );
    const result = await getKills({
      matchId: props.matchId,
      offset: (page.value - 1) * 50,
      limit: 50,
      ...(round.value !== "" && selected
        ? {
            gameMatchId: selected.game_match_id,
            instanceId: selected.instance_id
          }
        : {})
    });
    if (token === generation) data.value = result;
  } catch (e) {
    if (token === generation) error.value = getApiErrorMessage(e);
  } finally {
    busy.value = false;
  }
}
function schedule() {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    if (active && !document.hidden && props.matchId === "current")
      await refresh();
    if (active) schedule();
  }, 5000);
}
watch(
  () => props.matchId,
  () => {
    generation++;
    page.value = 1;
    round.value = "";
    data.value = undefined;
    void refresh();
    schedule();
  },
  { immediate: true }
);
onActivated(() => {
  active = true;
  schedule();
});
onDeactivated(() => {
  active = false;
  clearTimeout(timer);
});
onUnmounted(() => {
  active = false;
  clearTimeout(timer);
  generation++;
});
</script>
<template>
  <el-card class="mt-4" shadow="never"
    ><template #header
      >{{ t("击杀记录") }}
      <el-button class="ml-4" :loading="busy" @click="refresh">{{
        t("刷新")
      }}</el-button></template
    ><el-alert
      :title="
        t(
          '仅展示游戏服务器推送的真实事件；历史对局关联依据地图与观测边界推断。未关联记录可在玩家工具中按游戏对局查看。'
        )
      "
      :closable="false" /><el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false" /><el-alert
      v-if="data && !data.configured"
      :title="t('击杀推送尚未配置，请管理员查看反作弊页面中的推送配置。')"
      type="warning"
      :closable="false" /><template v-if="data"
      ><p class="my-3">
        {{ t("最后接收") }}：{{
          data.lastReceivedAt
            ? formatObservedAt(data.lastReceivedAt)
            : t("尚未接收事件")
        }}
        · {{ t("记录数") }}：{{ data.total }} · {{ t("未关联对局") }}：{{
          data.unlinkedRounds
        }}
      </p>
      <el-select
        v-if="props.matchId === 'all'"
        v-model="round"
        clearable
        :placeholder="t('按游戏对局筛选')"
        class="mb-4"
        style="width: min(100%, 600px)"
        @change="
          page = 1;
          refresh();
        "
        ><el-option
          v-for="r in data.rounds"
          :key="r.instance_id + r.game_match_id"
          :value="JSON.stringify([r.instance_id, r.game_match_id])"
          :label="`${r.map} · ${r.game_match_id} · ${r.instance_id}`" /></el-select
      ><el-table :data="data.items" stripe
        ><el-table-column :label="t('关联对局')" min-width="150">
          <template #default="{ row }">
            <el-link
              v-if="row.local_match_id"
              type="primary"
              @click="
                router.push(
                  `/history/matches/${encodeURIComponent(row.local_match_id)}`
                )
              "
              >{{ row.map || t("查看对局详情") }}</el-link
            >
            <span v-else>{{ t("未关联对局") }}</span>
          </template> </el-table-column
        ><el-table-column :label="t('接收时间')" min-width="170"
          ><template #default="{ row }">{{
            formatObservedAt(row.received_at)
          }}</template></el-table-column
        ><el-table-column :label="t('击杀者')" min-width="160"
          ><template #default="{ row }">{{
            row.killer_name || row.killer_id || t("未知")
          }}</template></el-table-column
        ><el-table-column :label="t('受害者')" min-width="160"
          ><template #default="{ row }">{{
            row.victim_name || row.victim_id || t("未知")
          }}</template></el-table-column
        ><el-table-column
          prop="cause"
          :label="t('原因')"
          min-width="160"
        /><el-table-column :label="t('距离（米）')" width="110"
          ><template #default="{ row }">{{
            row.distance_meters ?? t("未知")
          }}</template></el-table-column
        ><el-table-column :label="t('事件标签')" min-width="170"
          ><template #default="{ row }">{{
            row.tags === null ? t("未知") : row.tags.join(", ")
          }}</template></el-table-column
        ></el-table
      ><el-pagination
        class="mt-4"
        layout="prev,pager,next"
        :page-size="50"
        :total="data.total"
        :current-page="page"
        @current-change="
          p => {
            page = p;
            refresh();
          }
        " /><el-empty
        v-if="!data.items.length"
        :description="t('暂无击杀事件，不会从击杀数推造日志')" /></template
  ></el-card>
</template>
