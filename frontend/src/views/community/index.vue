<script setup lang="ts">
import { ref, computed, onUnmounted, onDeactivated } from "vue";
import { ElMessageBox } from "element-plus";
import { t } from "@/i18n";
import { useUserStoreHook } from "@/store/modules/user";
import { getPlayers, type Player } from "@/api/players";
import { getApiErrorMessage } from "@/api/errors";
import { formatObservedAt } from "@/api/snapshot";
import { normalizeFaction } from "@/utils/factions";
import {
  getRisk,
  sendBatch,
  getReceipt,
  getAudit,
  downloadText,
  type SteamRisk,
  type Receipt,
  type AuditRow
} from "@/api/community";
import PlayerDossier from "@/components/PlayerDossier.vue";
import RoundAwards from "@/components/RoundAwards.vue";
defineOptions({ name: "CommunityTools" });
const user = useUserStoreHook();
const allowed = (permission: string) =>
  user.role === "owner" || user.permissions.includes(permission);
const tab = ref("dossier");
const error = ref("");
const busy = ref(false);
const sid = ref("");
const selectedSid = ref("");
const roster = ref<Player[]>([]);
const revision = ref("");
const selected = ref<string[]>([]);
const message = ref("");
const receipt = ref<Receipt>();
const risks = ref<SteamRisk[]>([]);
const auditRows = ref<AuditRow[]>([]);
const auditTotal = ref(0);
const auditTruncated = ref(false);
const filters = ref({ action: "", outcome: "", actor: "", steamId: "" });
const dates = ref<Date[]>([]);
const validId = computed(
  () => /^[0-9]{17}$/.test(sid.value) && Number(sid.value) > 0
);
let polling: ReturnType<typeof setTimeout>;
let disposed = false;
let requestId = "";
let fingerprint = "";
const outcomeLabels: Record<string, string> = {
  queued: "排队中",
  processing: "发送中",
  accepted: "已接受",
  rejected: "已拒绝",
  uncertain: "不确定",
  skipped: "已跳过"
};
const signalLabels: Record<string, string> = {
  vac_ban: "Steam VAC 封禁记录",
  game_ban: "Steam 游戏封禁记录",
  community_ban: "Steam 社区封禁",
  economy_ban: "Steam 交易限制",
  new_account: "Steam 账号创建不足 30 天"
};
async function action(work: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await work();
  } catch (e) {
    if (e !== "cancel" && e !== "close") error.value = getApiErrorMessage(e);
  } finally {
    busy.value = false;
  }
}
async function loadRoster() {
  const result = await getPlayers();
  roster.value = result.players.filter(p => p.steamId);
  revision.value = result.targetRevision;
  selected.value = selected.value.filter(id =>
    roster.value.some(p => p.steamId === id)
  );
}
function selectFaction(name: string) {
  selected.value = roster.value
    .filter(
      p =>
        name === "all" ||
        normalizeFaction(p.faction) ===
          (
            { Lonestar: "BLU", Valkyra: "RED", Manticore: "GRN" } as Record<
              string,
              string
            >
          )[name]
    )
    .map(p => p.steamId!);
}
function uuid() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
async function poll() {
  clearTimeout(polling);
  if (disposed || !receipt.value || receipt.value.complete) return;
  polling = setTimeout(async () => {
    try {
      receipt.value = await getReceipt(receipt.value!.id);
    } catch (e) {
      error.value = getApiErrorMessage(e);
      return;
    }
    await poll();
  }, 2000);
}
async function send() {
  await action(async () => {
    if (
      !selected.value.length ||
      !message.value.trim() ||
      /[\x00-\x1f\x7f]/.test(message.value)
    )
      throw new Error(t("请选择玩家并填写单行消息"));
    await ElMessageBox.confirm(
      `${t("确认向所选玩家发送私聊")} (${selected.value.length})\n${message.value}`,
      t("批量私聊"),
      {
        type: "warning",
        confirmButtonText: t("确认发送"),
        cancelButtonText: t("取消")
      }
    );
    const key = JSON.stringify([
      revision.value,
      [...selected.value].sort(),
      message.value.trim()
    ]);
    if (fingerprint !== key) {
      requestId = uuid();
      fingerprint = key;
    }
    receipt.value = await sendBatch({
      requestId,
      steamIds: selected.value,
      message: message.value.trim(),
      targetRevision: revision.value
    });
    disposed = false;
    await poll();
  });
}
async function loadTab(name: string) {
  error.value = "";
  await action(async () => {
    if (name === "messages" && allowed("message")) await loadRoster();
    if (name === "audit" && allowed("audit")) await loadAudit();
  });
}
async function loadAudit() {
  const params: Record<string, string | number> = {
    ...filters.value,
    limit: 5000
  };
  if (dates.value?.length === 2) {
    params.since = dates.value[0].toISOString();
    params.until = dates.value[1].toISOString();
  }
  const result = await getAudit(params);
  auditRows.value = result.items;
  auditTotal.value = result.total;
  auditTruncated.value = result.truncated;
}
function exportAudit(format: string) {
  if (format === "json")
    downloadText(
      JSON.stringify(
        {
          source: "panel_operations",
          total: auditTotal.value,
          truncated: auditTruncated.value,
          items: auditRows.value
        },
        null,
        2
      ),
      "panel-audit.json",
      "application/json"
    );
  else {
    const keys = (
      auditRows.value[0]
        ? Object.keys(auditRows.value[0])
        : [
            "id",
            "created_at",
            "actor",
            "action",
            "steam_id",
            "outcome",
            "reason",
            "request_id"
          ]
    ) as (keyof AuditRow)[];
    const quote = (value: unknown) => {
      let s = String(value ?? "");
      if (/^[\s]*[=+\-@\t\r]/.test(s)) s = "'" + s;
      return '"' + s.replace(/"/g, '""') + '"';
    };
    downloadText(
      "\ufeff" +
        [
          keys.join(","),
          ...auditRows.value.map(r => keys.map(k => quote(r[k])).join(","))
        ].join("\r\n"),
      "panel-audit.csv",
      "text/csv;charset=utf-8"
    );
  }
}
onDeactivated(() => {
  disposed = true;
  clearTimeout(polling);
});
onUnmounted(() => {
  disposed = true;
  clearTimeout(polling);
});
</script>
<template>
  <div class="space-y-4">
    <h1 class="text-2xl font-bold">{{ t("玩家工具") }}</h1>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-tabs v-model="tab" @tab-change="value => loadTab(String(value))">
      <el-tab-pane :label="t('玩家档案')" name="dossier"
        ><el-form inline @submit.prevent="validId && (selectedSid = sid)"
          ><el-form-item label="SteamID64"
            ><el-input v-model="sid" maxlength="17" /></el-form-item
          ><el-button :disabled="!validId" @click="selectedSid = sid">{{
            t("查看档案")
          }}</el-button></el-form
        ><PlayerDossier v-if="selectedSid" :steam-id="selectedSid"
      /></el-tab-pane>
      <el-tab-pane :label="t('批量私聊')" name="messages"
        ><el-alert
          :title="
            t(
              '发送前逐人核对在线状态与权限；中断或超时不会自动重试。相同请求编号仅执行一次。'
            )
          "
          :closable="false" /><el-alert
          v-if="!allowed('message')"
          :title="t('需要私聊玩家权限')"
          type="warning"
          :closable="false" /><template v-else
          ><div class="my-4 flex flex-wrap gap-2">
            <el-button
              :disabled="busy || (receipt && !receipt.complete)"
              @click="action(loadRoster)"
              >{{ t("刷新玩家") }}</el-button
            ><el-button
              v-for="name in ['all', 'Lonestar', 'Valkyra', 'Manticore']"
              :key="name"
              @click="selectFaction(name)"
              >{{ name === "all" ? t("全选") : name }}</el-button
            ><el-button @click="selected = []">{{ t("清空选择") }}</el-button>
          </div>
          <el-select
            v-model="selected"
            multiple
            filterable
            style="width: 100%"
            :placeholder="t('选择在线玩家')"
            ><el-option
              v-for="p in roster"
              :key="p.steamId!"
              :value="p.steamId!"
              :label="`${p.name} · ${p.steamId}`" /></el-select
          ><el-input
            class="my-4"
            v-model="message"
            maxlength="200"
            show-word-limit
            :placeholder="t('单行消息，最多 200 字符')" /><el-button
            type="primary"
            :loading="busy"
            :disabled="
              !selected.length ||
              !message.trim() ||
              (receipt && !receipt.complete)
            "
            @click="send"
            >{{ t("确认发送") }} ({{ selected.length }})</el-button
          ><el-button
            v-if="receipt && !receipt.complete"
            class="ml-2"
            @click="
              disposed = false;
              poll();
            "
            >{{ t("查询发送结果") }}</el-button
          ><el-table v-if="receipt" :data="receipt.items" class="mt-4"
            ><el-table-column prop="name" :label="t('玩家')" /><el-table-column
              prop="steam_id"
              label="SteamID64" /><el-table-column :label="t('结果')"
              ><template #default="{ row }">{{
                t(outcomeLabels[row.outcome] || row.outcome)
              }}</template></el-table-column
            ><el-table-column
              prop="error"
              :label="t('错误码')" /></el-table></template
      ></el-tab-pane>
      <el-tab-pane :label="t('Steam 风险提示')" name="risk"
        ><el-alert
          :title="
            t(
              'Steam 记录仅供人工参考，历史封禁或新账号不能证明玩家在本服作弊；未公开数据返回未知，不自动处罚。'
            )
          "
          type="warning"
          :closable="false"
        /><el-form inline class="my-4" @submit.prevent
          ><el-form-item label="SteamID64"
            ><el-input v-model="sid" maxlength="17" /></el-form-item
          ><el-button
            :loading="busy"
            :disabled="!validId"
            @click="
              action(async () => {
                risks = (await getRisk([sid])).items;
              })
            "
            >{{ t("查询风险记录") }}</el-button
          ></el-form
        ><el-table :data="risks"
          ><el-table-column
            prop="steamId"
            label="SteamID64"
            min-width="180"
          /><el-table-column :label="t('VAC 封禁次数')"
            ><template #default="{ row }">{{
              row.vacBans ?? t("未知")
            }}</template></el-table-column
          ><el-table-column :label="t('游戏封禁次数')"
            ><template #default="{ row }">{{
              row.gameBans ?? t("未知")
            }}</template></el-table-column
          ><el-table-column :label="t('账号年龄（天）')"
            ><template #default="{ row }">{{
              row.accountAgeDays ?? t("未知")
            }}</template></el-table-column
          ><el-table-column :label="t('上次封禁距今（天）')"
            ><template #default="{ row }">{{
              row.daysSinceLastBan ?? t("未知")
            }}</template></el-table-column
          ><el-table-column :label="t('参考信号')" min-width="220"
            ><template #default="{ row }">{{
              row.signals
                .map((s: string) => t(signalLabels[s] || s))
                .join("；") || t("未发现已知信号")
            }}</template></el-table-column
          ><el-table-column :label="t('更新时间')" min-width="170"
            ><template #default="{ row }">{{
              formatObservedAt(row.updatedAt)
            }}</template></el-table-column
          ></el-table
        ></el-tab-pane
      >
      <el-tab-pane :label="t('开局榜单播报')" name="awards"
        ><RoundAwards v-if="tab === 'awards'"
      /></el-tab-pane>
      <el-tab-pane :label="t('审计导出')" name="audit"
        ><el-alert
          :title="
            t(
              '导出当前服务器的面板操作审计，最多 5000 条；不包含服务器完整日志、凭据或玩家备注正文。CSV 已防止公式注入。'
            )
          "
          :closable="false" /><el-alert
          v-if="!allowed('audit')"
          :title="t('需要审计查看与导出权限')"
          type="warning"
          :closable="false" /><template v-else
          ><el-form inline class="my-4"
            ><el-form-item
              v-for="(label, key) in {
                action: '操作',
                outcome: '结果',
                actor: '操作者',
                steamId: 'SteamID64'
              }"
              :key="key"
              :label="t(label)"
              ><el-input
                v-model="filters[key]"
                :maxlength="key === 'steamId' ? 17 : 64" /></el-form-item
            ><el-form-item :label="t('时间范围')"
              ><el-date-picker
                v-model="dates"
                type="datetimerange" /></el-form-item
            ><el-button :loading="busy" @click="action(loadAudit)">{{
              t("查询")
            }}</el-button></el-form
          >
          <p>
            {{ t("审计条数") }}：{{ auditTotal }}
            <span v-if="auditTruncated">{{
              t("结果已截断，请缩小筛选范围")
            }}</span>
          </p>
          <div class="my-3 flex gap-2">
            <el-button
              :disabled="busy || !auditRows.length"
              @click="exportAudit('csv')"
              >{{ t("导出 CSV") }}</el-button
            ><el-button
              :disabled="busy || !auditRows.length"
              @click="exportAudit('json')"
              >{{ t("导出 JSON") }}</el-button
            >
          </div>
          <el-table :data="auditRows" max-height="600"
            ><el-table-column :label="t('时间')" min-width="170"
              ><template #default="{ row }">{{
                formatObservedAt(row.created_at)
              }}</template></el-table-column
            ><el-table-column
              prop="actor"
              :label="t('操作者')" /><el-table-column
              prop="action"
              :label="t('操作')" /><el-table-column
              prop="steam_id"
              label="SteamID64"
              min-width="180" /><el-table-column
              prop="outcome"
              :label="t('结果')" /><el-table-column
              prop="reason"
              :label="t('原因')"
              min-width="180" /><el-table-column
              prop="request_id"
              :label="t('请求编号')"
              min-width="170" /></el-table></template
      ></el-tab-pane>
    </el-tabs>
  </div>
</template>
