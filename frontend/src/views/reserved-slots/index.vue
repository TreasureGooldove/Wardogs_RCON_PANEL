<script setup lang="ts">
import {
  computed,
  onActivated,
  onBeforeUnmount,
  onDeactivated,
  onMounted,
  reactive,
  ref
} from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  addReservedSlot,
  getReservedSlots,
  removeReservedSlot,
  updateReservedSlotMetadata,
  type ReservedSlots
} from "@/api/configDoc";
import { getApiErrorMessage } from "@/api/errors";
import { useUserStoreHook } from "@/store/modules/user";
import {
  acknowledgeWarmup,
  getWarmup,
  getWarmupStatus,
  saveWarmup,
  type WarmupState,
  type WarmupUpdate
} from "@/api/warmup";

defineOptions({ name: "ReservedSlots" });

const snapshot = ref<ReservedSlots | null>(null);
const userStore = useUserStoreHook();
const canManage = computed(() => userStore.role === "owner" || userStore.permissions.includes("reserved"));
const isOwner = computed(() => userStore.role === "owner");
const warmup = ref<WarmupState | null>(null);
const warmupLoading = ref(false);
const warmupSaving = ref(false);
const warmupError = ref("");
let warmupPollTimer: ReturnType<typeof setInterval> | null = null;
let warmupPolling = false;
const warmupForm = reactive({
  enabled: false,
  playerThreshold: 20,
  giftDays: 1,
  intervalMode: "daily" as "daily" | "hours",
  intervalHours: 24,
  notificationMode: "private" as "private" | "broadcast",
  notificationText: "感谢您的暖服支持！您已获赠{x}天预留位。"
});
const notificationPreview = computed(() =>
  warmupForm.notificationText.replaceAll("{x}", String(warmupForm.giftDays))
);
const warmupProgress = computed(() => {
  const count = warmup.value?.observedPlayers;
  const target = warmup.value?.playerThreshold;
  return count !== null && count !== undefined && target !== undefined && count < target
    ? `${count} / ${target}`
    : null;
});
const search = ref("");
const newSteamId = ref("");
const reason = ref("");
const days = ref<number | null>(30);
const editingId = ref("");
const editReason = ref("");
const editDays = ref<number | null>(null);
const loading = ref(false);
const submitting = ref(false);
const error = ref("");
const steamIdPattern = /^[1-9][0-9]{16}$/;
const configured = computed(() =>
  snapshot.value?.configuredReservedSlots ?? snapshot.value?.reservedSlots ?? []
);
const filtered = computed(() =>
  configured.value.filter(id => id.includes(search.value.trim()))
);

async function refresh() {
  if (loading.value) return;
  loading.value = true;
  error.value = "";
  try {
    snapshot.value = await getReservedSlots();
  } catch (reason) {
    error.value = getApiErrorMessage(reason);
  } finally {
    loading.value = false;
  }
}

async function refreshWarmup() {
  if (!isOwner.value || warmupLoading.value) return;
  warmupLoading.value = true;
  warmupError.value = "";
  try {
    const state = await getWarmup();
    warmup.value = state;
    Object.assign(warmupForm, {
      enabled: state.enabled,
      playerThreshold: state.playerThreshold,
      giftDays: state.giftDays,
      intervalMode: state.intervalMode,
      intervalHours: state.intervalHours,
      notificationMode: state.notificationMode,
      notificationText: state.notificationText
    });
  } catch (cause) {
    warmupError.value = getApiErrorMessage(cause);
  } finally {
    warmupLoading.value = false;
  }
}

async function pollWarmupStatus() {
  if (!isOwner.value || !warmup.value || warmupPolling || document.hidden) return;
  warmupPolling = true;
  try {
    const status = await getWarmupStatus();
    if (status.lastRunId !== (warmup.value.runs[0]?.id ?? null)) {
      warmup.value = await getWarmup();
    } else {
      Object.assign(warmup.value, status);
    }
  } catch {
    // Leave the previous sampled count visible until the next successful poll.
  } finally {
    warmupPolling = false;
  }
}

function startWarmupPolling() {
  if (warmupPollTimer || !isOwner.value) return;
  warmupPollTimer = setInterval(() => void pollWarmupStatus(), 5000);
}

function stopWarmupPolling() {
  if (warmupPollTimer) clearInterval(warmupPollTimer);
  warmupPollTimer = null;
}

async function promptPassword(title: string, message: string): Promise<string | null> {
  try {
    const result = await ElMessageBox.prompt(message, title, {
      type: "warning",
      inputType: "password",
      inputPlaceholder: "输入当前管理员密码",
      inputValidator: value => Boolean(value) || "请输入当前管理员密码",
      confirmButtonText: "确认",
      cancelButtonText: "取消"
    });
    return result.value;
  } catch {
    return null;
  }
}

async function saveWarmupSettings() {
  const current = warmup.value;
  if (!isOwner.value || !current || warmupSaving.value) return;
  if (!Number.isInteger(warmupForm.playerThreshold) || !Number.isInteger(warmupForm.giftDays) ||
      !Number.isInteger(warmupForm.intervalHours)) {
    ElMessage.warning("人数、天数和间隔必须是整数");
    return;
  }
  if (!warmupForm.notificationText.trim() || notificationPreview.value.length > 200 ||
      /[\x00-\x1f\x7f]/.test(warmupForm.notificationText)) {
    ElMessage.warning("通知内容不能为空、包含换行或超过 200 字");
    return;
  }
  let password: string | null = null;
  if (warmupForm.enabled) {
    password = await promptPassword(
      "启用暖服自动赠送",
      "达到人数门槛时，面板会自动写入真实服务器预留位配置。若当前已达到门槛，保存后可能立即触发。确认前请备份服务器配置。"
    );
    if (!password) return;
  }
  warmupSaving.value = true;
  warmupError.value = "";
  try {
    const payload: WarmupUpdate = {
      ...warmupForm,
      targetRevision: current.targetRevision,
      ...(password ? { password } : {})
    };
    warmup.value = await saveWarmup(payload);
    ElMessage.success(warmupForm.enabled ? "暖服赠送规则已启用" : "暖服赠送规则已保存（关闭）");
  } catch (cause) {
    warmupError.value = getApiErrorMessage(cause);
  } finally {
    warmupSaving.value = false;
  }
}

async function acknowledgeRun(runId: string) {
  const current = warmup.value;
  if (!current || warmupSaving.value) return;
  const password = await promptPassword(
    "确认人工核查",
    "请先比对服务器配置与面板预留列表。本操作不会补发本轮奖励，只解除后续检测的暂停。"
  );
  if (!password) return;
  warmupSaving.value = true;
  warmupError.value = "";
  try {
    warmup.value = await acknowledgeWarmup(runId, {
      targetRevision: current.targetRevision,
      password
    });
    ElMessage.success("已记录人工核查，可在下一次检测时间继续运行");
  } catch (cause) {
    warmupError.value = getApiErrorMessage(cause);
  } finally {
    warmupSaving.value = false;
  }
}

async function change(steamId: string, operation: "add" | "remove") {
  const current = snapshot.value;
  if (!canManage.value || !current?.writable || !current.revision || submitting.value) return;
  if (!steamIdPattern.test(steamId)) {
    ElMessage.warning("请填写有效的 17 位 SteamID64");
    return;
  }
  const adding = operation === "add";
  if (adding && (!reason.value.trim() || days.value === null)) {
    ElMessage.warning("请填写预留原因和有效天数");
    return;
  }
  if (adding && configured.value.includes(steamId)) {
    ElMessage.warning("该 SteamID 已在预留位列表中");
    return;
  }
  if (!adding && !configured.value.includes(steamId)) {
    ElMessage.warning("此 SteamID 不在当前列表，请刷新");
    return;
  }
  try {
    await ElMessageBox.confirm(
      `${adding ? "添加" : "移除"} SteamID ${steamId} 的预留位？此操作会修改真实服务器配置。`,
      adding ? "添加预留位" : "移除预留位",
      {
        type: "warning",
        confirmButtonText: adding ? "确认添加" : "确认移除",
        cancelButtonText: "取消"
      }
    );
  } catch {
    return;
  }
  submitting.value = true;
  error.value = "";
  try {
    const request = {
      steamId,
      revision: current.revision,
      targetRevision: current.targetRevision,
      ...(adding ? { reason: reason.value.trim(), days: days.value } : {})
    };
    if (adding) await addReservedSlot(request);
    else await removeReservedSlot(request);
    ElMessage.success("已写入服务器配置；实时列表将单独核对");
    if (adding) {
      newSteamId.value = "";
      reason.value = "";
      days.value = 30;
    }
    snapshot.value = null;
    await refresh();
  } catch (reason) {
    error.value = getApiErrorMessage(reason);
  } finally {
    submitting.value = false;
  }
}

function edit(steamId: string) {
  editingId.value = steamId;
  editReason.value = snapshot.value?.metadata?.[steamId]?.reason ?? "";
  editDays.value = null;
}

async function saveMetadata() {
  const current = snapshot.value;
  if (!canManage.value || !current || !editingId.value || submitting.value) return;
  submitting.value = true;
  error.value = "";
  try {
    snapshot.value = await updateReservedSlotMetadata({
      steamId: editingId.value,
      reason: editReason.value.trim(),
      days: editDays.value,
      targetRevision: current.targetRevision
    });
    editingId.value = "";
    ElMessage.success("面板备注和期限已保存，服务器配置未修改");
  } catch (cause) {
    error.value = getApiErrorMessage(cause);
  } finally {
    submitting.value = false;
  }
}

onMounted(() => {
  void refresh();
  void refreshWarmup();
  startWarmupPolling();
});
onActivated(startWarmupPolling);
onDeactivated(stopWarmupPolling);
onBeforeUnmount(stopWarmupPolling);
</script>

<template>
  <div class="space-y-5 p-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-2xl font-semibold">预留位</h1>
        <p class="text-sm text-gray-500">读取服务器预留列表；备注和到期时间仅保存在面板</p>
      </div>
      <el-button :loading="loading" @click="refresh">刷新列表</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-card v-if="isOwner" shadow="never" v-loading="warmupLoading">
      <template #header>
        <div class="flex items-center justify-between gap-3">
          <span>暖服预留位自动赠送</span>
          <el-button size="small" @click="refreshWarmup">刷新状态</el-button>
        </div>
      </template>
      <div class="space-y-4">
        <el-alert v-if="warmupError" :title="warmupError" type="error" :closable="false" />
        <el-alert
          v-if="warmup?.attentionRequired"
          title="上次配置写入结果不确定，自动赠送已暂停。请先人工核对，再在下方确认核查。"
          type="error"
          :closable="false"
        />
        <el-alert
          v-if="warmup && !warmup.collectorEnabled"
          title="历史采样未启用，暖服人数检测不可用。"
          type="warning"
          :closable="false"
        />
        <p class="text-sm text-gray-500">
          达到人数门槛时，为该次采样中所有在线玩家赠送预留位。已有更长的限时预留位不会缩短；永久或非面板管理的预留位保持原样。预留位可能需服务器重启后实时生效。
        </p>
        <div class="flex flex-wrap items-center gap-4">
          <span>启用</span><el-switch v-model="warmupForm.enabled" :disabled="!warmup?.collectorEnabled" />
          <span>人数门槛</span><el-input-number v-model="warmupForm.playerThreshold" :min="1" :max="100" />
          <span>赠送天数</span><el-input-number v-model="warmupForm.giftDays" :min="1" :max="3650" />
        </div>
        <div class="flex flex-wrap items-center gap-4">
          <span>下一轮检测</span>
          <el-select v-model="warmupForm.intervalMode" class="w-48">
            <el-option label="北京时间每天一次" value="daily" />
            <el-option label="按小时间隔" value="hours" />
          </el-select>
          <template v-if="warmupForm.intervalMode === 'hours'">
            <el-input-number v-model="warmupForm.intervalHours" :min="1" :max="720" />
            <span>小时后</span>
          </template>
          <span>赠送通知</span>
          <el-select v-model="warmupForm.notificationMode" class="w-44">
            <el-option label="逐个私聊" value="private" />
            <el-option label="全服公告" value="broadcast" />
          </el-select>
        </div>
        <div class="space-y-2">
          <label class="block text-sm">赠送通知文本</label>
          <el-input v-model="warmupForm.notificationText" maxlength="200" show-word-limit placeholder="输入通知内容，{x} 表示赠送天数" />
          <p class="text-sm text-gray-500">预览：{{ notificationPreview }}。{x} 会替换为赠送天数。全服公告也会被未获赠预留位的玩家看到。</p>
        </div>
        <p class="text-sm text-gray-500">
          <span v-if="warmupProgress">当前在线人数：{{ warmupProgress }}；</span>
          下次可检测：{{ warmup?.nextDetectionAt ? new Date(warmup.nextDetectionAt).toLocaleString() : "现在" }}。
          自动赠送默认关闭，保存为启用时需再次输入管理员密码。
        </p>
        <el-button type="primary" :loading="warmupSaving" :disabled="!warmup?.configured" @click="saveWarmupSettings">保存暖服规则</el-button>
        <el-divider>最近赠送记录</el-divider>
        <el-empty v-if="!warmup?.runs.length" description="尚无暖服赠送记录" />
        <el-table v-else :data="warmup.runs" border>
          <el-table-column type="expand">
            <template #default="scope">
              <el-table :data="scope.row.targets" size="small">
                <el-table-column prop="player_name" label="玩家" min-width="150" />
                <el-table-column prop="steam_id" label="SteamID" min-width="185" />
                <el-table-column prop="action" label="处理" width="100" />
                <el-table-column prop="expires_at" label="到期时间" min-width="180" />
                <el-table-column prop="notification_status" label="通知状态" min-width="130" />
              </el-table>
            </template>
          </el-table-column>
          <el-table-column prop="started_at" label="检测时间" min-width="190" />
          <el-table-column prop="player_count" label="在线人数" width="105" />
          <el-table-column prop="awarded_count" label="赠送人数" width="105" />
          <el-table-column prop="skipped_count" label="跳过人数" width="105" />
          <el-table-column prop="outcome" label="结果" min-width="120" />
          <el-table-column label="通知" min-width="135">
            <template #default="scope">{{ scope.row.notification_mode === 'broadcast' ? scope.row.notification_status : '逐个私聊（展开查看）' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="135">
            <template #default="scope">
              <el-button v-if="scope.row.outcome === 'attention'" type="warning" size="small" @click="acknowledgeRun(scope.row.id)">确认已核查</el-button>
              <span v-else>{{ scope.row.detail || "—" }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>
    <el-card shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span>预留位配置</span>
          <span class="text-sm text-gray-500">
            {{ snapshot ? `配置 ${configured.length} 个 · 实时 ${snapshot.reservedSlots.length} 个` : "数量未知" }}
          </span>
        </div>
      </template>

      <el-skeleton v-if="loading && !snapshot" :rows="6" animated />
      <el-empty v-else-if="!snapshot" description="尚未读取预留位列表" />
      <div v-else class="space-y-4">
        <el-alert
          v-if="snapshot.pendingRestart"
          title="配置列表与实时生效列表不同；服务器可能需要在合适时间重启后才会应用预留位变更。"
          type="warning"
          :closable="false"
        />
        <el-alert
          v-if="!snapshot.writable"
          title="当前服务器的配置文档不可写，预留位只能查看。"
          type="info"
          :closable="false"
        />
        <div class="flex flex-wrap items-center gap-2">
          <el-input
            v-model.trim="newSteamId"
            class="max-w-xs"
            maxlength="17"
            placeholder="输入 17 位 SteamID64"
            :disabled="!canManage || !snapshot.writable || submitting"
            @keyup.enter="change(newSteamId, 'add')"
          />
          <el-button
            type="primary"
            :loading="submitting"
            :disabled="!canManage || !snapshot.writable"
            @click="change(newSteamId, 'add')"
          >添加预留位</el-button>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <el-input v-model="reason" class="max-w-sm" maxlength="200" placeholder="预留原因（仅面板记录）" />
          <span class="text-sm">有效天数</span>
          <el-input-number v-model="days" :min="1" :max="3650" />
        </div>
        <el-input
          v-model="search"
          clearable
          class="max-w-xs"
          placeholder="搜索 SteamID"
        />
        <el-empty
          v-if="configured.length === 0"
          description="当前配置中没有预留位"
        />
        <el-empty v-else-if="filtered.length === 0" description="没有匹配的 SteamID" />
        <el-table v-else :data="filtered.map(steamId => ({ steamId }))" border>
          <el-table-column prop="steamId" label="SteamID64" min-width="220" />
          <el-table-column label="原因" min-width="160">
            <template #default="scope">{{ snapshot.metadata?.[scope.row.steamId]?.reason || "—" }}</template>
          </el-table-column>
          <el-table-column label="到期时间" min-width="190">
            <template #default="scope">{{ snapshot.metadata?.[scope.row.steamId]?.expiresAt || "永久 / 未设置" }}</template>
          </el-table-column>
          <el-table-column label="状态" width="115">
            <template #default="scope">{{ snapshot.metadata?.[scope.row.steamId]?.status === 'attention' ? '需人工核查' : snapshot.metadata?.[scope.row.steamId]?.status === 'processing' ? '处理中' : '正常' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="190">
            <template #default="scope">
              <el-button size="small" :disabled="!canManage || !steamIdPattern.test(scope.row.steamId)" @click="edit(scope.row.steamId)">备注/期限</el-button>
              <el-button
                type="danger"
                size="small"
                :loading="submitting"
                :disabled="!canManage || !snapshot.writable || !steamIdPattern.test(scope.row.steamId)"
                @click="change(scope.row.steamId, 'remove')"
              >移除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>
    <el-dialog :model-value="Boolean(editingId)" title="预留位备注和期限" width="min(520px, 94vw)" @update:model-value="value => { if (!value) editingId = ''; }">
      <p class="mb-3 text-sm">{{ editingId }} · 仅更新面板记录，不提交服务器配置</p>
      <el-input v-model="editReason" maxlength="200" placeholder="预留原因" />
      <div class="mt-3 flex items-center gap-3"><span>从现在起有效天数</span><el-input-number v-model="editDays" :min="1" :max="3650" /><span class="text-sm text-gray-500">留空则永久</span></div>
      <template #footer><el-button @click="editingId = ''">取消</el-button><el-button type="primary" :loading="submitting" @click="saveMetadata">保存</el-button></template>
    </el-dialog>
  </div>
</template>
