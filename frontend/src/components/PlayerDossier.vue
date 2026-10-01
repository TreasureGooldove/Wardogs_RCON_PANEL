<script setup lang="ts">
import { ref, watch, computed } from "vue";
import { t } from "@/i18n";
import { getDossier, saveAnnotation, type Dossier } from "@/api/community";
import { getApiErrorMessage } from "@/api/errors";
import { formatObservedAt } from "@/api/snapshot";
import { useUserStoreHook } from "@/store/modules/user";
const props = defineProps<{ steamId: string }>();
const user = useUserStoreHook();
const canEdit = computed(
  () => user.role === "owner" || user.permissions.includes("notes")
);
const data = ref<Dossier>();
const note = ref("");
const watched = ref(false);
const error = ref("");
const busy = ref(false);
async function refresh() {
  busy.value = true;
  error.value = "";
  try {
    const result = await getDossier(props.steamId);
    if (result.steamId !== props.steamId) return;
    data.value = result;
    note.value = result.annotation.note;
    watched.value = !!result.annotation.watched;
  } catch (e) {
    error.value = getApiErrorMessage(e);
  } finally {
    busy.value = false;
  }
}
async function save() {
  if (!data.value) return;
  busy.value = true;
  error.value = "";
  try {
    await saveAnnotation(props.steamId, {
      note: note.value,
      watched: watched.value,
      targetRevision: data.value.targetRevision
    });
    await refresh();
  } catch (e) {
    error.value = getApiErrorMessage(e);
  } finally {
    busy.value = false;
  }
}
watch(
  () => props.steamId,
  () => {
    data.value = undefined;
    void refresh();
  },
  { immediate: true }
);
</script>
<template>
  <el-card class="mt-4" shadow="never" v-loading="busy">
    <template #header
      >{{ t("玩家档案") }}
      <el-button class="ml-4" @click="refresh">{{
        t("刷新")
      }}</el-button></template
    >
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert
      :title="
        t(
          '档案仅包含面板观测记录；在线时长从本功能启用后开始累计，不包含采集断档。'
        )
      "
      :closable="false"
    />
    <template v-if="data">
      <p class="my-4">
        {{ t("已观测在线时长（分钟）") }}：{{
          data.observedPlaytimeSeconds === null
            ? t("未知")
            : (data.observedPlaytimeSeconds / 60).toFixed(1)
        }}
      </p>
      <el-checkbox v-model="watched" :disabled="!canEdit">{{
        t("关注玩家")
      }}</el-checkbox>
      <el-input
        class="my-3"
        v-model="note"
        type="textarea"
        :rows="3"
        :maxlength="2000"
        show-word-limit
        :disabled="!canEdit"
        :placeholder="t('玩家备注')"
      />
      <el-button v-if="canEdit" type="primary" :disabled="busy" @click="save">{{
        t("保存档案")
      }}</el-button>
      <el-tabs class="mt-4">
        <el-tab-pane :label="t('历史昵称')"
          ><el-table :data="data.aliases"
            ><el-table-column prop="name" :label="t('玩家')" /><el-table-column
              :label="t('最后观测')"
              ><template #default="{ row }">{{
                formatObservedAt(row.last_seen)
              }}</template></el-table-column
            ></el-table
          ></el-tab-pane
        >
        <el-tab-pane :label="t('在线会话')"
          ><el-table :data="data.sessions"
            ><el-table-column :label="t('首次观测')"
              ><template #default="{ row }">{{
                formatObservedAt(row.started_at)
              }}</template></el-table-column
            ><el-table-column :label="t('最后观测')"
              ><template #default="{ row }">{{
                formatObservedAt(row.ended_at || row.last_seen)
              }}</template></el-table-column
            ><el-table-column
              :label="t('观测时长（秒）')"
              prop="observed_seconds"
            /><el-table-column :label="t('状态')"
              ><template #default="{ row }">{{
                row.end_reason === "observation_gap"
                  ? t("采集断档")
                  : row.ended_at
                    ? t("已离线")
                    : t("观测中")
              }}</template></el-table-column
            ></el-table
          ></el-tab-pane
        >
        <el-tab-pane :label="t('玩家操作记录')"
          ><el-table :data="data.actions"
            ><el-table-column
              prop="action"
              :label="t('操作')"
            /><el-table-column
              prop="actor"
              :label="t('操作者')"
            /><el-table-column
              prop="outcome"
              :label="t('结果')"
            /><el-table-column
              prop="reason"
              :label="t('原因')"
            /><el-table-column :label="t('时间')"
              ><template #default="{ row }">{{
                formatObservedAt(row.created_at)
              }}</template></el-table-column
            ></el-table
          ></el-tab-pane
        >
      </el-tabs>
    </template>
  </el-card>
</template>
