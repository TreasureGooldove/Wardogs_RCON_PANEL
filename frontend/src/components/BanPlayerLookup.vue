<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { t } from "@/i18n";
import {
  getPlayers,
  banPlayerPermanently,
  type Player,
  type PlayersResponse
} from "@/api/players";
import { getHistoryPlayer, type PlayerDetail } from "@/api/history";
import { getSteamProfiles, type SteamProfile } from "@/api/steam";
import { getCapabilities } from "@/api/capabilities";
import { getApiErrorCode, getApiErrorMessage } from "@/api/errors";
import { useUserStoreHook } from "@/store/modules/user";
import { factionDisplay } from "@/utils/factions";
import { validateActionMessage } from "@/utils/actionSafety";
import PlayerCard from "@/views/players/PlayerCard.vue";

const props = defineProps<{ bannedIds: string[]; targetRevision: string }>();
const emit = defineEmits<{ changed: [] }>();
const user = useUserStoreHook();
const input = ref("");
const loading = ref(false);
const pending = ref(false);
const error = ref("");
const notice = ref("");
const snapshot = ref<PlayersResponse | null>(null);
const history = ref<PlayerDetail | null>(null);
const profile = ref<SteamProfile>();
const player = ref<Player | null>(null);
// Keep uncertain targets blocked even after another lookup; never retry silently.
const uncertainTargets = ref(new Set<string>());
const acceptedTargets = ref(new Set<string>());
let requestId = 0;
const online = computed(
  () =>
    snapshot.value?.players.some(p => p.steamId === player.value?.steamId) ??
    false
);
const blocked = computed(() => {
  if (!user.canBan) return t("当前账号没有此操作权限");
  if (pending.value || loading.value) return t("正在处理，请稍候");
  if (
    !player.value ||
    !snapshot.value ||
    snapshot.value.stale ||
    !props.targetRevision ||
    props.targetRevision !== snapshot.value.targetRevision
  )
    return t("当前数据未能确认同一服务器目标，所有写入操作已停用");
  const id = player.value.steamId!;
  if (uncertainTargets.value.has(id))
    return t("操作结果不确定，请先核查封禁列表，勿重复提交");
  if (props.bannedIds.includes(id) || acceptedTargets.value.has(id))
    return t("该玩家已在封禁列表中");
  return "";
});

watch(input, () => {
  requestId++;
  loading.value = false;
  player.value = null;
  snapshot.value = null;
  history.value = null;
  profile.value = undefined;
  error.value = notice.value = "";
});
watch(
  () => props.targetRevision,
  () => {
    requestId++;
    player.value = null;
    snapshot.value = null;
    loading.value = false;
  }
);

async function lookup() {
  if (pending.value) return;
  const id = input.value.trim();
  if (!/^[1-9][0-9]{16}$/.test(id)) {
    error.value = t("请输入 17 位纯数字 SteamID64");
    return;
  }
  const generation = ++requestId;
  loading.value = true;
  player.value = null;
  profile.value = undefined;
  history.value = null;
  error.value = notice.value = "";
  try {
    const [current, recorded, steam] = await Promise.allSettled([
      getPlayers(),
      getHistoryPlayer(id),
      getSteamProfiles([id])
    ]);
    if (generation !== requestId) return;
    if (current.status === "rejected") throw current.reason;
    snapshot.value = current.value;
    if (
      recorded.status === "fulfilled" &&
      recorded.value.targetRevision === current.value.targetRevision
    )
      history.value = recorded.value;
    if (steam.status === "fulfilled")
      profile.value = steam.value.profiles.find(p => p.steamId === id);
    const notes: string[] = [];
    if (
      recorded.status === "rejected" &&
      recorded.reason?.response?.status !== 404
    )
      notes.push(t("本服历史读取失败：") + getApiErrorMessage(recorded.reason));
    if (steam.status === "rejected")
      notes.push(t("Steam 资料读取失败：") + getApiErrorMessage(steam.reason));
    notice.value = notes.join("；");
    const live = current.value.players.find(p => p.steamId === id);
    const totals = history.value?.totals;
    player.value = live ?? {
      steamId: id,
      name:
        history.value?.name || profile.value?.personaName || t("未命名玩家"),
      faction: null,
      kills: totals?.total_kills ?? null,
      deaths: totals?.total_deaths ?? null,
      cash: totals?.latest_cash ?? null,
      pingMs: null
    };
  } catch (cause) {
    if (generation === requestId) error.value = getApiErrorMessage(cause);
  } finally {
    if (generation === requestId) loading.value = false;
  }
}

async function ban() {
  if (blocked.value || !player.value || !snapshot.value) return;
  const selected = player.value;
  const target = snapshot.value.targetRevision;
  pending.value = true;
  try {
    const capability = await getCapabilities();
    if (capability.state !== "available" || capability.features.ban !== true) {
      ElMessage.warning(t("服务器不支持此管理操作"));
      return;
    }
    let reason: string;
    try {
      const answer = await ElMessageBox.prompt(
        `${selected.name} · SteamID64: ${selected.steamId}`,
        t("永久封禁玩家"),
        {
          type: "warning",
          inputPlaceholder: t("填写单行操作原因（1–200 字）"),
          inputValidator: value => t(validateActionMessage(value)) || true,
          confirmButtonText: t("下一步"),
          cancelButtonText: t("取消")
        }
      );
      reason = answer.value.trim();
      await ElMessageBox.confirm(
        `${t("确认永久封禁 ")}${selected.name}（SteamID64: ${selected.steamId}）\n${t("原因：")}${reason}`,
        t("二次确认"),
        {
          type: "warning",
          confirmButtonText: t("确认永久封禁"),
          cancelButtonText: t("取消")
        }
      );
    } catch {
      return;
    }
    const current = await getPlayers();
    if (
      !user.canBan ||
      player.value !== selected ||
      current.stale ||
      current.targetRevision !== target ||
      props.targetRevision !== target
    ) {
      ElMessage.warning(t("服务器设置已变更，请刷新玩家名单后重试"));
      return;
    }
    await banPlayerPermanently({
      steamId: selected.steamId!,
      reason,
      targetRevision: target
    });
    acceptedTargets.value.add(selected.steamId!);
    ElMessage.success(t("永久封禁命令已执行"));
    emit("changed");
  } catch (cause) {
    if (getApiErrorCode(cause) === "action_uncertain")
      uncertainTargets.value.add(selected.steamId!);
    error.value = getApiErrorMessage(cause);
    ElMessage.error(error.value);
  } finally {
    pending.value = false;
  }
}
</script>

<template>
  <el-card shadow="never" data-testid="ban-player-lookup">
    <template #header>{{ $t("按 SteamID64 查看玩家") }}</template>
    <p class="mb-3 text-sm text-gray-500">
      {{ $t("先查看玩家身份和本服记录，再决定是否封禁。查询不会执行封禁。") }}
    </p>
    <div class="flex flex-wrap gap-3">
      <el-input
        v-model="input"
        class="min-w-0 flex-1"
        style="max-width: 420px"
        :disabled="pending"
        maxlength="17"
        :placeholder="$t('请输入 17 位纯数字 SteamID64')"
        @keyup.enter="lookup"
      />
      <el-button
        type="primary"
        :loading="loading"
        :disabled="pending"
        @click="lookup"
        >{{ $t("查看玩家卡片") }}</el-button
      >
    </div>
    <el-alert
      v-if="error"
      class="mt-3"
      type="error"
      :title="error"
      :closable="false"
    />
    <el-alert
      v-if="notice"
      class="mt-3"
      type="warning"
      :title="notice"
      :closable="false"
    />
    <div v-if="player" class="mt-4 max-w-xl">
      <div class="mb-3 flex flex-wrap items-center gap-2">
        <el-tag :type="online ? 'success' : 'info'">{{
          online ? $t("在线") : $t("离线")
        }}</el-tag>
        <span class="text-sm text-gray-500">{{
          online
            ? $t("当前比赛观测数据")
            : history
              ? $t("本服累计观测；现金为最近余额")
              : $t("尚无本服记录，缺失统计显示未知")
        }}</span>
      </div>
      <PlayerCard
        :player="player"
        :steam-profile="profile"
        :accent-color="factionDisplay(player.faction).color"
        kick-disabled-reason=""
        :ban-disabled-reason="blocked"
        kill-disabled-reason=""
        message-disabled-reason=""
        faction-disabled-reason=""
        warning-history-disabled-reason=""
        :show-kick="false"
        :show-ban="Boolean(user.canBan)"
        :show-extra="false"
        :show-warning-history="false"
        :show-warning-action="false"
        :show-faction="online"
        @ban="ban"
      />
      <p v-if="!online" class="mt-3 text-sm text-gray-500">
        {{
          $t(
            "玩家当前离线；封禁是否可执行由服务器判定，失败或结果不确定时不会自动重试。"
          )
        }}
      </p>
      <p v-if="blocked && user.canBan" class="mt-3 text-sm text-gray-500">
        {{ blocked }}
      </p>
    </div>
  </el-card>
</template>
