<script setup lang="ts">
import { t, formatDate } from "@/i18n";
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
const canManage = computed(
  () => userStore.role === "owner" || userStore.permissions.includes("reserved")
);
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
  resetThreshold: 10,
  resetMinutes: 10,
  giftDays: 1,
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
  return count !== null &&
    count !== undefined &&
    target !== undefined &&
    count < target
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
const configured = computed(
  () =>
    snapshot.value?.configuredReservedSlots ??
    snapshot.value?.reservedSlots ??
    []
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
      resetThreshold: state.resetThreshold,
      resetMinutes: state.resetMinutes,
      giftDays: state.giftDays,
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
  if (!isOwner.value || !warmup.value || warmupPolling || document.hidden)
    return;
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

async function promptPassword(
  title: string,
  message: string
): Promise<string | null> {
  try {
    const result = await ElMessageBox.prompt(message, title, {
      type: "warning",
      inputType: "password",
      inputPlaceholder: t("输入当前管理员密码"),
      inputValidator: value => Boolean(value) || t("请输入当前管理员密码"),
      confirmButtonText: t("确认"),
      cancelButtonText: t("取消")
    });
    return result.value;
  } catch {
    return null;
  }
}

async function saveWarmupSettings() {
  const current = warmup.value;
  if (!isOwner.value || !current || warmupSaving.value) return;
  if (
    !Number.isInteger(warmupForm.playerThreshold) ||
    !Number.isInteger(warmupForm.giftDays) ||
    !Number.isInteger(warmupForm.intervalHours) ||
    !Number.isInteger(warmupForm.resetThreshold) ||
    !Number.isInteger(warmupForm.resetMinutes) ||
    warmupForm.resetThreshold >= warmupForm.playerThreshold
  ) {
    ElMessage.warning(t("人数、天数和间隔必须是整数，回落人数须小于目标人数"));
    return;
  }
  if (
    !warmupForm.notificationText.trim() ||
    notificationPreview.value.length > 200 ||
    /[\x00-\x1f\x7f]/.test(warmupForm.notificationText)
  ) {
    ElMessage.warning(t("通知内容不能为空、包含换行或超过 200 字"));
    return;
  }
  let password: string | null = null;
  if (warmupForm.enabled) {
    password = await promptPassword(
      t("启用暖服自动赠送"),
      t(
        "只有人数持续回落后再次达到门槛，且满足每天最多一次和间隔小时数，才会自动写入真实服务器预留位配置。确认前请备份服务器配置。"
      )
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
    ElMessage.success(
      warmupForm.enabled
        ? t("暖服赠送规则已启用")
        : t("暖服赠送规则已保存（关闭）")
    );
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
    t("确认人工核查"),
    t(
      "请先比对服务器配置与面板预留列表。本操作不会补发本轮奖励，只解除后续检测的暂停。"
    )
  );
  if (!password) return;
  warmupSaving.value = true;
  warmupError.value = "";
  try {
    warmup.value = await acknowledgeWarmup(runId, {
      targetRevision: current.targetRevision,
      password
    });
    ElMessage.success(t("已记录人工核查，可在下一次检测时间继续运行"));
  } catch (cause) {
    warmupError.value = getApiErrorMessage(cause);
  } finally {
    warmupSaving.value = false;
  }
}

async function change(steamId: string, operation: "add" | "remove") {
  const current = snapshot.value;
  if (
    !canManage.value ||
    !current?.writable ||
    !current.revision ||
    submitting.value
  )
    return;
  if (!steamIdPattern.test(steamId)) {
    ElMessage.warning(t("请填写有效的 17 位 SteamID64"));
    return;
  }
  const adding = operation === "add";
  if (adding && (!reason.value.trim() || days.value === null)) {
    ElMessage.warning(t("请填写预留原因和有效天数"));
    return;
  }
  if (adding && configured.value.includes(steamId)) {
    ElMessage.warning(t("该 SteamID 已在预留位列表中"));
    return;
  }
  if (!adding && !configured.value.includes(steamId)) {
    ElMessage.warning(t("此 SteamID 不在当前列表，请刷新"));
    return;
  }
  try {
    await ElMessageBox.confirm(
      t("{p0} SteamID {p1} 的预留位？此操作会修改真实服务器配置。", {
        p0: adding ? t("添加") : t("移除"),
        p1: steamId
      }),
      adding ? t("添加预留位") : t("移除预留位"),
      {
        type: "warning",
        confirmButtonText: adding ? t("确认添加") : t("确认移除"),
        cancelButtonText: t("取消")
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
    ElMessage.success(t("已写入服务器配置；实时列表将单独核对"));
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
  if (!canManage.value || !current || !editingId.value || submitting.value)
    return;
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
    ElMessage.success(t("面板备注和期限已保存，服务器配置未修改"));
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
        <h1 class="text-2xl font-semibold">{{ $t("预留位") }}</h1>
        <p class="text-sm text-gray-500">
          {{ $t("读取服务器预留列表；备注和到期时间仅保存在面板") }}
        </p>
      </div>
      <el-button :loading="loading" @click="refresh">{{
        $t("刷新列表")
      }}</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-card v-if="isOwner" shadow="never" v-loading="warmupLoading">
      <template #header>
        <div class="flex items-center justify-between gap-3">
          <span>{{ $t("暖服预留位自动赠送") }}</span>
          <el-button size="small" @click="refreshWarmup">{{
            $t("刷新状态")
          }}</el-button>
        </div>
      </template>
      <div class="space-y-4">
        <el-alert
          v-if="warmupError"
          :title="warmupError"
          type="error"
          :closable="false"
        />
        <el-alert
          v-if="warmup?.attentionRequired"
          :title="
            $t(
              '上次配置写入结果不确定，自动赠送已暂停。请先人工核对，再在下方确认核查。'
            )
          "
          type="error"
          :closable="false"
        />
        <el-alert
          v-if="warmup && !warmup.collectorEnabled"
          :title="$t('历史采样未启用，暖服人数检测不可用。')"
          type="warning"
          :closable="false"
        />
        <p class="text-sm text-gray-500">
          {{
            $t(
              "人数持续回落后重新达到目标、北京时间进入新的一天、且距离上次赠送已满设定小时数，三项同时满足才赠送。达标时若仍在冷却期，本轮跳过，不会补发。已有更长的限时预留位不会缩短；永久或非面板管理的预留位保持原样。预留位可能需服务器重启后实时生效。"
            )
          }}
        </p>
        <div class="flex flex-wrap items-center gap-4">
          <span>{{ $t("启用") }}</span
          ><el-switch
            v-model="warmupForm.enabled"
            :disabled="!warmup?.collectorEnabled"
          />
          <span>{{ $t("人数门槛") }}</span
          ><el-input-number
            v-model="warmupForm.playerThreshold"
            :min="1"
            :max="100"
          />
          <span>{{ $t("回落至") }}</span
          ><el-input-number
            v-model="warmupForm.resetThreshold"
            :min="0"
            :max="99"
          /><span>{{ $t("人或以下") }}</span> <span>{{ $t("持续") }}</span
          ><el-input-number
            v-model="warmupForm.resetMinutes"
            :min="1"
            :max="180"
          /><span>{{ $t("分钟") }}</span> <span>{{ $t("赠送天数") }}</span
          ><el-input-number
            v-model="warmupForm.giftDays"
            :min="1"
            :max="3650"
          />
        </div>
        <div class="flex flex-wrap items-center gap-4">
          <span>{{ $t("北京时间每天最多一次；两次赠送至少间隔") }}</span>
          <el-input-number
            v-model="warmupForm.intervalHours"
            :min="1"
            :max="720"
          />
          <span>{{ $t("小时") }}</span>
          <span>{{ $t("赠送通知") }}</span>
          <el-select v-model="warmupForm.notificationMode" class="w-44">
            <el-option :label="$t('逐个私聊')" value="private" />
            <el-option :label="$t('全服公告')" value="broadcast" />
          </el-select>
        </div>
        <div class="space-y-2">
          <label class="block text-sm">{{ $t("赠送通知文本") }}</label>
          <el-input
            v-model="warmupForm.notificationText"
            maxlength="200"
            show-word-limit
            :placeholder="$t('输入通知内容，{x} 表示赠送天数')"
          />
          <p class="text-sm text-gray-500">
            {{ $t("预览：") }}{{ notificationPreview
            }}{{
              $t(
                "。{x} 会替换为赠送天数。全服公告也会被未获赠预留位的玩家看到。"
              )
            }}
          </p>
        </div>
        <p class="text-sm text-gray-500">
          <span v-if="warmupProgress"
            >{{ $t("当前在线人数：") }}{{ warmupProgress }}；</span
          >
          {{ $t("人数状态：")
          }}{{
            warmup?.cyclePhase === "armed"
              ? $t("已完成回落，等待重新达标")
              : $t("等待人数持续回落")
          }}{{ $t("；冷却结束：")
          }}{{
            warmup?.nextDetectionAt
              ? formatDate(warmup.nextDetectionAt)
              : $t("无历史赠送")
          }}{{ $t("。 自动赠送默认关闭，保存为启用时需再次输入管理员密码。") }}
        </p>
        <el-button
          type="primary"
          :loading="warmupSaving"
          :disabled="!warmup?.configured"
          @click="saveWarmupSettings"
          >{{ $t("保存暖服规则") }}</el-button
        >
        <el-divider>{{ $t("最近赠送记录") }}</el-divider>
        <el-empty
          v-if="!warmup?.runs.length"
          :description="$t('尚无暖服赠送记录')"
        />
        <el-table v-else :data="warmup.runs" border>
          <el-table-column type="expand">
            <template #default="scope">
              <el-table :data="scope.row.targets" size="small">
                <el-table-column
                  prop="player_name"
                  :label="$t('玩家')"
                  min-width="150"
                />
                <el-table-column
                  prop="steam_id"
                  label="SteamID"
                  min-width="185"
                />
                <el-table-column
                  prop="action"
                  :label="$t('处理')"
                  width="100"
                />
                <el-table-column
                  prop="expires_at"
                  :label="$t('到期时间')"
                  min-width="180"
                />
                <el-table-column
                  prop="notification_status"
                  :label="$t('通知状态')"
                  min-width="130"
                />
              </el-table>
            </template>
          </el-table-column>
          <el-table-column
            prop="started_at"
            :label="$t('检测时间')"
            min-width="190"
          />
          <el-table-column
            prop="player_count"
            :label="$t('在线人数')"
            width="105"
          />
          <el-table-column
            prop="awarded_count"
            :label="$t('赠送人数')"
            width="105"
          />
          <el-table-column
            prop="skipped_count"
            :label="$t('跳过人数')"
            width="105"
          />
          <el-table-column prop="outcome" :label="$t('结果')" min-width="120" />
          <el-table-column :label="$t('通知')" min-width="135">
            <template #default="scope">{{
              scope.row.notification_mode === "broadcast"
                ? scope.row.notification_status
                : $t("逐个私聊（展开查看）")
            }}</template>
          </el-table-column>
          <el-table-column :label="$t('操作')" width="135">
            <template #default="scope">
              <el-button
                v-if="scope.row.outcome === 'attention'"
                type="warning"
                size="small"
                @click="acknowledgeRun(scope.row.id)"
                >{{ $t("确认已核查") }}</el-button
              >
              <span v-else>{{ scope.row.detail || "—" }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>
    <el-card shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span>{{ $t("预留位配置") }}</span>
          <span class="text-sm text-gray-500">
            {{
              snapshot
                ? $t("配置 {p0} 个 · 实时 {p1} 个", {
                    p0: configured.length,
                    p1: snapshot.reservedSlots.length
                  })
                : $t("数量未知")
            }}
          </span>
        </div>
      </template>

      <el-skeleton v-if="loading && !snapshot" :rows="6" animated />
      <el-empty v-else-if="!snapshot" :description="$t('尚未读取预留位列表')" />
      <div v-else class="space-y-4">
        <el-alert
          v-if="snapshot.pendingRestart"
          :title="
            $t(
              '配置列表与实时生效列表不同；服务器可能需要在合适时间重启后才会应用预留位变更。'
            )
          "
          type="warning"
          :closable="false"
        />
        <el-alert
          v-if="!snapshot.writable"
          :title="$t('当前服务器的配置文档不可写，预留位只能查看。')"
          type="info"
          :closable="false"
        />
        <div class="flex flex-wrap items-center gap-2">
          <el-input
            v-model.trim="newSteamId"
            class="max-w-xs"
            maxlength="17"
            :placeholder="$t('输入 17 位 SteamID64')"
            :disabled="!canManage || !snapshot.writable || submitting"
            @keyup.enter="change(newSteamId, 'add')"
          />
          <el-button
            type="primary"
            :loading="submitting"
            :disabled="!canManage || !snapshot.writable"
            @click="change(newSteamId, 'add')"
            >{{ $t("添加预留位") }}</el-button
          >
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <el-input
            v-model="reason"
            class="max-w-sm"
            maxlength="200"
            :placeholder="$t('预留原因（仅面板记录）')"
          />
          <span class="text-sm">{{ $t("有效天数") }}</span>
          <el-input-number v-model="days" :min="1" :max="3650" />
        </div>
        <el-input
          v-model="search"
          clearable
          class="max-w-xs"
          :placeholder="$t('搜索 SteamID')"
        />
        <el-empty
          v-if="configured.length === 0"
          :description="$t('当前配置中没有预留位')"
        />
        <el-empty
          v-else-if="filtered.length === 0"
          :description="$t('没有匹配的 SteamID')"
        />
        <el-table v-else :data="filtered.map(steamId => ({ steamId }))" border>
          <el-table-column prop="steamId" label="SteamID64" min-width="220" />
          <el-table-column :label="$t('原因')" min-width="160">
            <template #default="scope">{{
              snapshot.metadata?.[scope.row.steamId]?.reason || "—"
            }}</template>
          </el-table-column>
          <el-table-column :label="$t('到期时间')" min-width="190">
            <template #default="scope">{{
              snapshot.metadata?.[scope.row.steamId]?.expiresAt ||
              $t("永久 / 未设置")
            }}</template>
          </el-table-column>
          <el-table-column :label="$t('状态')" width="115">
            <template #default="scope">{{
              snapshot.metadata?.[scope.row.steamId]?.status === "attention"
                ? $t("需人工核查")
                : snapshot.metadata?.[scope.row.steamId]?.status ===
                    "processing"
                  ? $t("处理中")
                  : $t("正常")
            }}</template>
          </el-table-column>
          <el-table-column :label="$t('操作')" width="190">
            <template #default="scope">
              <el-button
                size="small"
                :disabled="
                  !canManage || !steamIdPattern.test(scope.row.steamId)
                "
                @click="edit(scope.row.steamId)"
                >{{ $t("备注/期限") }}</el-button
              >
              <el-button
                type="danger"
                size="small"
                :loading="submitting"
                :disabled="
                  !canManage ||
                  !snapshot.writable ||
                  !steamIdPattern.test(scope.row.steamId)
                "
                @click="change(scope.row.steamId, 'remove')"
                >{{ $t("移除") }}</el-button
              >
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>
    <el-dialog
      :model-value="Boolean(editingId)"
      :title="$t('预留位备注和期限')"
      width="min(520px, 94vw)"
      @update:model-value="
        value => {
          if (!value) editingId = '';
        }
      "
    >
      <p class="mb-3 text-sm">
        {{ editingId }} {{ $t("· 仅更新面板记录，不提交服务器配置") }}
      </p>
      <el-input
        v-model="editReason"
        maxlength="200"
        :placeholder="$t('预留原因')"
      />
      <div class="mt-3 flex items-center gap-3">
        <span>{{ $t("从现在起有效天数") }}</span
        ><el-input-number v-model="editDays" :min="1" :max="3650" /><span
          class="text-sm text-gray-500"
          >{{ $t("留空则永久") }}</span
        >
      </div>
      <template #footer
        ><el-button @click="editingId = ''">{{ $t("取消") }}</el-button
        ><el-button
          type="primary"
          :loading="submitting"
          @click="saveMetadata"
          >{{ $t("保存") }}</el-button
        ></template
      >
    </el-dialog>
  </div>
</template>
