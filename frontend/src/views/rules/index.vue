<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { getApiErrorMessage } from "@/api/errors";
import { getPlayers, type Player } from "@/api/players";
import { formatObservedAt } from "@/api/snapshot";
import {
  getRules, getRulesDeliveries, saveRules, sendRulesManually,
  type RulesConfig, type RulesDelivery
} from "@/api/rules";

defineOptions({ name: "RulesAnnouncer" });
const config = ref<RulesConfig | null>(null);
const deliveries = ref<RulesDelivery[]>([]);
const players = ref<Player[]>([]);
const selectedSteamId = ref("");
const loading = ref(false);
const saving = ref(false);
const sending = ref(false);
const error = ref("");
const firstParts = computed(() => Math.ceil((config.value?.firstText.length || 0) / 200));
const secondParts = computed(() => Math.ceil((config.value?.secondText.length || 0) / 200));

async function refresh() {
  loading.value = true;
  error.value = "";
  try {
    const [settings, history, roster] = await Promise.all([
      getRules(), getRulesDeliveries(), getPlayers()
    ]);
    config.value = settings;
    deliveries.value = history.items;
    players.value = roster.players.filter(item => Boolean(item.steamId));
  } catch (cause) {
    error.value = getApiErrorMessage(cause);
  } finally {
    loading.value = false;
  }
}

async function save() {
  if (!config.value || saving.value) return;
  saving.value = true;
  try {
    config.value = await saveRules(config.value);
    ElMessage.success(config.value.enabled ? "已启用；下一次采集只建立基线，不向当前在线玩家群发" : "配置已保存，自动播报已关闭");
  } catch (cause) {
    ElMessage.error(getApiErrorMessage(cause));
  } finally {
    saving.value = false;
  }
}

async function manualSend() {
  if (!config.value || !selectedSteamId.value || sending.value) return;
  const player = players.value.find(item => item.steamId === selectedSteamId.value);
  try {
    await ElMessageBox.confirm(
      `将向 ${player?.name || selectedSteamId.value} 私聊发送完整服规。此操作会实际联系在线玩家，发送失败不会自动重试。`,
      "确认手动发送",
      { confirmButtonText: "确认发送", cancelButtonText: "取消", type: "warning" }
    );
  } catch { return; }
  sending.value = true;
  try {
    const result = await sendRulesManually(selectedSteamId.value, config.value.targetRevision);
    if (result.firstSent && result.secondSent !== false) ElMessage.success("服规发送已完成");
    else ElMessage.warning("发送未完整完成；请查看发送记录核实，避免重复发送");
    deliveries.value = (await getRulesDeliveries()).items;
  } catch (cause) {
    ElMessage.error(`${getApiErrorMessage(cause)}；请查看发送记录核实，避免盲目重发`);
  } finally {
    sending.value = false;
  }
}

onMounted(refresh);
</script>

<template>
  <div class="space-y-5 p-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-2xl font-semibold">服规播报</h1>
        <p class="text-sm text-gray-500">新玩家进服后延迟私聊；复用面板的 5 秒玩家采集</p>
      </div>
      <el-button :loading="loading" @click="refresh">刷新状态</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="warning" :closable="false" />
    <el-alert title="自动播报默认关闭。开启后的第一轮只建立在线基线，不发送给已在线玩家；断线超时也会重新建立基线。" type="info" :closable="false" />
    <el-card v-if="config" shadow="never">
      <template #header>自动播报设置</template>
      <el-form label-width="130px" class="max-w-4xl">
        <el-form-item label="自动播报">
          <el-switch v-model="config.enabled" :disabled="!config.collectorEnabled" />
          <span class="ml-3 text-sm text-gray-500">{{ config.enabled ? "待保存：开启" : "待保存：关闭" }}</span>
        </el-form-item>
        <el-alert v-if="!config.collectorEnabled" title="后台玩家采集未启用，无法开启自动播报" type="warning" :closable="false" class="mb-4" />
        <el-alert v-if="config.configuredOrigin && config.configuredOrigin !== config.currentOrigin" title="服务器连接已切换；请检查服规并重新保存到当前服务器" type="warning" :closable="false" class="mb-4" />
        <el-form-item label="第一段服规">
          <div class="w-full"><el-input v-model="config.firstText" type="textarea" :rows="4" :maxlength="2000" show-word-limit /><p class="text-xs text-gray-500">支持 {name}、{steamid}、{count}；预计 {{ firstParts }} 条私聊，每条最多 200 字</p></div>
        </el-form-item>
        <el-form-item label="第二段服规">
          <div class="w-full"><el-input v-model="config.secondText" type="textarea" :rows="7" :maxlength="2000" show-word-limit /><p class="text-xs text-gray-500">留空则只发送第一段；长文本会完整分段，预计 {{ secondParts }} 条</p></div>
        </el-form-item>
        <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
          <el-form-item label="进服延迟（秒）"><el-input-number v-model="config.delaySeconds" :min="0" :max="600" /></el-form-item>
          <el-form-item label="两段间隔（秒）"><el-input-number v-model="config.gapSeconds" :min="0" :max="120" /></el-form-item>
          <el-form-item label="冷却（分钟）"><el-input-number v-model="config.cooldownMinutes" :min="1" :max="1440" /></el-form-item>
          <el-form-item label="每 5 秒最多新玩家"><el-input-number v-model="config.maxPerRound" :min="1" :max="10" /></el-form-item>
        </div>
        <el-form-item><el-button type="primary" :loading="saving" @click="save">保存设置</el-button></el-form-item>
      </el-form>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="当前排队">{{ config.pending }}</el-descriptions-item>
        <el-descriptions-item label="已建立基线">{{ config.baselineReady ? "是" : "否" }}</el-descriptions-item>
        <el-descriptions-item label="最近在线观测">{{ config.onlineObserved }}</el-descriptions-item>
        <el-descriptions-item label="名单仍新鲜">{{ config.lastPlayerSnapshotFresh ? "是" : "否" }}</el-descriptions-item>
      </el-descriptions>
    </el-card>
    <el-card v-if="config" shadow="never">
      <template #header>手动发送</template>
      <p class="mb-3 text-sm text-gray-500">仅向确认在线的指定玩家发送；会发送两段及全部分段，执行前需要再次确认。</p>
      <div class="flex flex-wrap gap-3">
        <el-select v-model="selectedSteamId" filterable placeholder="选择在线玩家" class="w-80">
          <el-option v-for="player in players" :key="player.steamId!" :label="`${player.name} (${player.steamId})`" :value="player.steamId!" />
        </el-select>
        <el-button type="warning" :loading="sending" :disabled="!selectedSteamId || config.configuredOrigin !== config.currentOrigin" @click="manualSend">发送服规</el-button>
      </div>
    </el-card>
    <el-card shadow="never">
      <template #header>发送记录（最近 50 条）</template>
      <el-table :data="deliveries" stripe>
        <el-table-column prop="player_name" label="玩家" min-width="140" />
        <el-table-column prop="steam_id" label="SteamID" min-width="175" />
        <el-table-column label="段落" width="95"><template #default="{ row }">{{ row.stage }}-{{ row.part }}</template></el-table-column>
        <el-table-column label="结果" width="110"><template #default="{ row }">{{ row.outcome === "accepted" ? "已接受" : row.outcome === "attempting" ? "待核实" : row.outcome === "uncertain" ? "不确定" : "已拒绝" }}</template></el-table-column>
        <el-table-column label="发送时间" min-width="175"><template #default="{ row }">{{ formatObservedAt(row.created_at) }}</template></el-table-column>
      </el-table>
    </el-card>
  </div>
</template>
