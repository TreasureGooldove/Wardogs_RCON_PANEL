<script setup lang="ts">
import KillRecords from "@/components/KillRecords.vue";
import { t } from "@/i18n";
import {
  computed,
  onActivated,
  onDeactivated,
  onMounted,
  onUnmounted,
  ref,
  watch
} from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { getCapabilities, type CapabilitiesResponse } from "@/api/capabilities";
import { changePlayerFaction, killPlayer, messagePlayer } from "@/api/actions";
import type { AdvertisedAction } from "@/api/capabilities";
import { getApiErrorCode, getApiErrorMessage } from "@/api/errors";
import { banPlayerPermanently, kickPlayer, type Player } from "@/api/players";
import { formatObservedAt } from "@/api/snapshot";
import { getSteamProfiles, type SteamProfile } from "@/api/steam";
import { getServerStatus } from "@/api/status";
import {
  getWarningHistory,
  sendWarning,
  type WarningHistory,
  type WarningOutcome
} from "@/api/warnings";
import FactionCounts from "@/components/FactionCounts.vue";
import { usePlayersPolling } from "@/composables/usePlayersPolling";
import { FACTIONS, normalizeFaction } from "@/utils/factions";
import { useUserStoreHook } from "@/store/modules/user";
import { validateActionMessage } from "@/utils/actionSafety";
import PlayerCard from "./PlayerCard.vue";

defineOptions({ name: "Players" });

type ModerationAction = "kick" | "ban";
interface IndexedPlayer {
  player: Player;
  index: number;
}
interface PlayerGroup {
  key: string;
  name: string;
  color: string;
  total: number;
  players: IndexedPlayer[];
  unknown: boolean;
}

const nameCollator = new Intl.Collator("zh-CN", {
  numeric: true,
  sensitivity: "base"
});

const { snapshot, loading, error, refresh, refreshAfterMutation } =
  usePlayersPolling();
const userStore = useUserStoreHook();
const search = ref("");
const capabilities = ref<CapabilitiesResponse | null>(null);
const capabilityError = ref("");
const capabilityFetchedAt = ref(0);
const capabilityClock = ref(Date.now());
const actionPending = ref(false);
const actionError = ref("");
const steamProfilesEnabled = ref(readSteamProfilesEnabled());
const steamProfiles = ref<Record<string, SteamProfile>>({});
const steamProfileNextFetchAt = new Map<string, number>();
let steamProfileRequesting = false;
const factionDialogVisible = ref(false);
const factionDialogLoading = ref(false);
const factionDialogError = ref("");
const factionPlayer = ref<Player | null>(null);
const factionTargetRevision = ref("");
const factionChoices = ref<string[]>([]);
const chosenFaction = ref("");
const shouldRespawn = ref(false);
const warningDialogVisible = ref(false);
const warningDialogLoading = ref(false);
const warningDialogError = ref("");
const warningWriteError = ref("");
const warningUncertain = ref(false);
const warningHistory = ref<WarningHistory | null>(null);
const warningPlayer = ref<Player | null>(null);
const warningTargetRevision = ref("");
const warningReason = ref("");
let pageActive = false;
let capabilityRequest: Promise<void> | null = null;
let capabilityTimer: ReturnType<typeof setInterval> | null = null;
let factionReadId = 0;
let warningReadId = 0;

function readSteamProfilesEnabled(): boolean {
  try {
    return localStorage.getItem("wardogs-steam-profiles-enabled") !== "false";
  } catch {
    return true;
  }
}

const indexedPlayers = computed<IndexedPlayer[]>(() =>
  (snapshot.value?.players ?? []).map((player, index) => ({ player, index }))
);

const visiblePlayers = computed(() => {
  const query = search.value.trim().toLocaleLowerCase();
  const players = indexedPlayers.value;
  if (!query) return players;
  return players.filter(
    ({ player }) =>
      (player.steamId?.includes(query) ?? false) ||
      player.name.toLocaleLowerCase().includes(query)
  );
});

function sortByName(players: IndexedPlayer[]) {
  return [...players].sort(
    (left, right) =>
      nameCollator.compare(left.player.name, right.player.name) ||
      left.index - right.index
  );
}

const groups = computed<PlayerGroup[]>(() => {
  const all = indexedPlayers.value;
  const visible = visiblePlayers.value;
  const known = FACTIONS.map(faction => ({
    key: faction.code,
    name: faction.name,
    color: faction.color,
    total: all.filter(
      entry => normalizeFaction(entry.player.faction) === faction.code
    ).length,
    players: sortByName(
      visible.filter(
        entry => normalizeFaction(entry.player.faction) === faction.code
      )
    ),
    unknown: false
  }));
  const unknown = all.filter(entry => !normalizeFaction(entry.player.faction));
  if (unknown.length === 0) return known;
  return [
    ...known,
    {
      key: "UNKNOWN",
      name: t("未知阵营"),
      color: "#909399",
      total: unknown.length,
      players: sortByName(
        visible.filter(entry => !normalizeFaction(entry.player.faction))
      ),
      unknown: true
    }
  ];
});

async function refreshSteamProfiles() {
  if (
    steamProfileRequesting ||
    !steamProfilesEnabled.value ||
    !pageActive ||
    document.visibilityState !== "visible" ||
    !snapshot.value ||
    snapshot.value.stale
  ) {
    return;
  }
  const now = Date.now();
  const steamIds = [
    ...new Set(
      snapshot.value.players
        .map(player => player.steamId)
        .filter((id): id is string =>
          typeof id === "string" ? /^[1-9][0-9]{16}$/.test(id) : false
        )
    )
  ].filter(id => (steamProfileNextFetchAt.get(id) ?? 0) <= now);
  if (!steamIds.length) return;
  steamProfileRequesting = true;
  try {
    for (let index = 0; index < steamIds.length; index += 100) {
      if (
        !pageActive ||
        !steamProfilesEnabled.value ||
        document.visibilityState !== "visible"
      )
        break;
      const batch = steamIds.slice(index, index + 100);
      try {
        const response = await getSteamProfiles(batch);
        const updated = { ...steamProfiles.value };
        for (const id of batch) delete updated[id];
        for (const profile of response.profiles) {
          if (batch.includes(profile.steamId))
            updated[profile.steamId] = profile;
        }
        steamProfiles.value = updated;
        const nextFetch = Date.now() + 10 * 60_000;
        for (const id of batch) steamProfileNextFetchAt.set(id, nextFetch);
      } catch {
        // Optional public profile data must not disrupt RCON player data.
        const nextFetch = Date.now() + 60_000;
        for (const id of batch) steamProfileNextFetchAt.set(id, nextFetch);
      }
    }
  } finally {
    steamProfileRequesting = false;
  }
}

watch(
  () => snapshot.value?.players,
  () => void refreshSteamProfiles()
);

function loadCapabilities(): Promise<void> {
  if (!pageActive || document.visibilityState !== "visible") {
    return Promise.resolve();
  }
  if (capabilityRequest) return capabilityRequest;
  capabilityError.value = "";
  capabilityRequest = (async () => {
    try {
      const response = await getCapabilities();
      if (pageActive && document.visibilityState === "visible") {
        capabilities.value = response;
        capabilityFetchedAt.value = Date.now();
        capabilityClock.value = Date.now();
      }
    } catch (reason) {
      if (pageActive && document.visibilityState === "visible") {
        capabilities.value = null;
        capabilityFetchedAt.value = 0;
        capabilityError.value = getApiErrorMessage(reason);
      }
    }
  })().finally(() => {
    capabilityRequest = null;
  });
  return capabilityRequest;
}

function refreshCapabilities() {
  if (capabilityRequest) {
    void capabilityRequest.then(() => {
      if (pageActive) void loadCapabilities();
    });
  } else {
    void loadCapabilities();
  }
}

function disabledReason(player: Player, action: ModerationAction): string {
  if (!pageActive) return t("当前页面未激活");
  if (action === "kick" && !userStore.canKick) return t("当前账号没有踢出权限");
  if (action === "ban" && !userStore.canBan)
    return t("当前账号没有永久封禁权限");
  if (!player.steamId) return t("该玩家没有可用的 SteamID，无法执行管理操作");
  if (!snapshot.value || snapshot.value.stale)
    return t("玩家名单已过期，请先刷新");
  if (!snapshot.value.targetRevision)
    return t("无法确认服务器目标，请刷新玩家名单");
  if (loading.value) return t("正在刷新玩家名单");
  if (actionPending.value) return t("请等待当前管理操作完成");
  if (capabilities.value?.state !== "available")
    return t("管理能力尚未确认可用");
  if (capabilityClock.value - capabilityFetchedAt.value > 35_000)
    return t("管理能力已过期，请刷新");
  if (capabilities.value.features[action] !== true) {
    return action === "kick"
      ? t("目标服务器未开放踢出能力")
      : t("目标服务器未开放封禁能力");
  }
  return "";
}

function extraDisabledReason(player: Player, action: AdvertisedAction): string {
  if (userStore.role !== "owner" && !userStore.permissions.includes(action))
    return t("当前账号没有此项操作权限");
  if (!pageActive || document.visibilityState !== "visible")
    return t("当前页面未激活");
  if (!player.steamId) return t("该玩家没有可用的 SteamID，无法执行管理操作");
  if (!snapshot.value || snapshot.value.stale)
    return t("玩家名单已过期，请先刷新");
  if (!snapshot.value.targetRevision)
    return t("无法确认服务器目标，请刷新玩家名单");
  if (loading.value || actionPending.value) return t("请等待当前请求完成");
  if (capabilities.value?.state !== "available")
    return t("管理能力尚未确认可用");
  if (capabilityClock.value - capabilityFetchedAt.value > 35_000)
    return t("管理能力已过期，请刷新");
  if (capabilities.value.advertisedActions?.[action] !== true)
    return t("目标服务器未开放此项操作");
  return "";
}

function samePlayerAndTarget(player: Player, targetRevision: string): boolean {
  const current = snapshot.value?.players.find(
    item => item.steamId === player.steamId
  );
  return (
    snapshot.value?.targetRevision === targetRevision &&
    !snapshot.value?.stale &&
    Boolean(current) &&
    current?.name === player.name &&
    current?.faction === player.faction
  );
}

function warningHistoryDisabledReason(player: Player): string {
  if (!pageActive || document.visibilityState !== "visible")
    return t("当前页面未激活");
  if (!player.steamId) return t("该玩家没有可用的 SteamID，无法查询警告记录");
  return "";
}

function warningWriteBlockedReason(): string {
  const player = warningPlayer.value;
  if (!player) return t("请选择玩家");
  if (!warningDialogVisible.value) return t("警告窗口已关闭");
  if (warningUncertain.value)
    return t("上一条警告结果不确定，请先人工核查，勿立即重复发送");
  if (userStore.role !== "owner" && !userStore.permissions.includes("warning"))
    return t("当前账号没有警告权限");
  const blocked = extraDisabledReason(player, "message");
  if (blocked) return blocked;
  if (!samePlayerAndTarget(player, warningTargetRevision.value))
    return t("玩家名单或服务器目标已变化，请刷新名单后重试");
  if (
    warningDialogLoading.value ||
    !warningHistory.value ||
    warningHistory.value.stale ||
    warningHistory.value.targetRevision !== warningTargetRevision.value
  ) {
    return t("警告记录尚未与当前服务器目标核对");
  }
  return "";
}

async function loadWarningHistory(steamId: string, targetRevision: string) {
  const readId = ++warningReadId;
  warningDialogLoading.value = true;
  warningDialogError.value = "";
  warningHistory.value = null;
  try {
    const result = await getWarningHistory(steamId);
    if (readId !== warningReadId || !warningDialogVisible.value) return;
    if (
      result.stale ||
      result.source !== "panel_local" ||
      result.steamId !== steamId ||
      (targetRevision && result.targetRevision !== targetRevision)
    ) {
      warningDialogError.value = t(
        "警告记录与当前玩家或服务器目标不一致，请刷新名单后重试"
      );
      return;
    }
    warningHistory.value = result;
  } catch (reason) {
    if (readId === warningReadId)
      warningDialogError.value = getApiErrorMessage(reason);
  } finally {
    if (readId === warningReadId) warningDialogLoading.value = false;
  }
}

function openWarnings(player: Player) {
  const blocked = warningHistoryDisabledReason(player);
  if (blocked || !player.steamId) {
    ElMessage.warning(blocked || t("请刷新玩家名单后重试"));
    return;
  }
  warningPlayer.value = player;
  warningTargetRevision.value = snapshot.value?.targetRevision ?? "";
  warningReason.value = "";
  warningWriteError.value = "";
  warningUncertain.value = false;
  warningDialogVisible.value = true;
  void loadWarningHistory(player.steamId, warningTargetRevision.value);
}

function validateWarningReason(value: string): string {
  if (/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/u.test(value))
    return t("警告原因必须是单行文字，不能含控制字符");
  const length = value.trim().length;
  return length >= 1 && length <= 180 ? "" : t("警告原因需为 1–180 个字符");
}

function warningOutcomeLabel(value: WarningOutcome): string {
  return {
    accepted: t("已发送"),
    rejected: t("发送失败"),
    uncertain: t("结果不确定")
  }[value];
}

function warningOutcomeType(value: WarningOutcome) {
  return {
    accepted: "success",
    rejected: "danger",
    uncertain: "warning"
  }[value] as "success" | "danger" | "warning";
}

async function submitWarning() {
  const player = warningPlayer.value;
  const steamId = player?.steamId;
  const targetRevision = warningTargetRevision.value;
  const reason = warningReason.value.trim();
  if (!player || !steamId) return;
  const invalid = validateWarningReason(warningReason.value);
  if (invalid) return void ElMessage.warning(t(invalid));
  const blocked = warningWriteBlockedReason();
  if (blocked) return void ElMessage.warning(blocked);
  try {
    await ElMessageBox.confirm(
      t(
        "确认向 {p0}（SteamID：{p1}）发送管理员警告私聊，并在面板本地记录？原因：{p2}。这不是游戏原生处罚。",
        { p0: player.name, p1: steamId, p2: reason }
      ),
      t("发送警告"),
      {
        type: "warning",
        confirmButtonText: t("确认发送警告"),
        cancelButtonText: t("取消")
      }
    );
  } catch {
    return;
  }
  await loadCapabilities();
  const latestBlock = warningWriteBlockedReason();
  if (
    latestBlock ||
    !warningDialogVisible.value ||
    warningReason.value.trim() !== reason
  ) {
    ElMessage.warning(latestBlock || t("玩家或警告原因已变化，请重新确认"));
    return;
  }
  actionPending.value = true;
  actionError.value = "";
  warningWriteError.value = "";
  try {
    const result = await sendWarning({ steamId, reason, targetRevision });
    warningReason.value = "";
    if (result.recorded) {
      ElMessage.success(t("管理员警告私聊已发送并记入面板"));
    } else {
      ElMessage.warning(t("警告私聊已发送，但面板记录未保存，请人工核查"));
    }
  } catch (error) {
    const code = getApiErrorCode(error);
    warningUncertain.value = code === "action_uncertain";
    warningWriteError.value =
      code === "action_uncertain"
        ? t(
            "警告私聊发送结果不确定，可能已送达；请核查本地记录与原 RCON，勿立即重复发送"
          )
        : getApiErrorMessage(error);
    if (
      (code === "stale_server_target" || code === "player_not_online") &&
      snapshot.value
    ) {
      snapshot.value = { ...snapshot.value, stale: true };
    }
  } finally {
    actionPending.value = false;
    if (warningDialogVisible.value)
      await loadWarningHistory(steamId, targetRevision);
  }
}

async function runExtraAction(player: Player, action: "kill" | "message") {
  const blocked = extraDisabledReason(player, action);
  const steamId = player.steamId;
  const targetRevision = snapshot.value?.targetRevision;
  if (blocked || !steamId || !targetRevision) {
    ElMessage.warning(blocked || t("请刷新玩家名单后重试"));
    return;
  }
  let message = "";
  try {
    if (action === "message") {
      const answer = await ElMessageBox.prompt(
        t("确认向 {p0}（SteamID：{p1}）发送私聊？", {
          p0: player.name,
          p1: steamId
        }),
        t("发送私聊"),
        {
          type: "warning",
          confirmButtonText: t("确认发送"),
          cancelButtonText: t("取消"),
          inputType: "text",
          inputPlaceholder: t("输入单行消息（1–200 字）"),
          inputValidator: value => t(validateActionMessage(value)) || true
        }
      );
      const invalid = validateActionMessage(answer.value);
      if (invalid) return void ElMessage.warning(t(invalid));
      message = answer.value.trim();
    } else {
      await ElMessageBox.confirm(
        t(
          "确认击杀 {p0}（SteamID：{p1}）的当前角色？玩家可正常重生，此操作不会踢出或封禁。",
          { p0: player.name, p1: steamId }
        ),
        t("击杀玩家"),
        {
          type: "warning",
          confirmButtonText: t("确认击杀"),
          cancelButtonText: t("取消")
        }
      );
    }
  } catch {
    return;
  }
  await loadCapabilities();
  const latestBlock = extraDisabledReason(player, action);
  if (latestBlock || !samePlayerAndTarget(player, targetRevision)) {
    ElMessage.warning(
      latestBlock || t("玩家名单或服务器目标已变化，请刷新后重试")
    );
    return;
  }
  actionPending.value = true;
  actionError.value = "";
  try {
    if (action === "kill") {
      await killPlayer({ steamId, targetRevision });
    } else {
      await messagePlayer({ steamId, message, targetRevision });
    }
    ElMessage.success(
      action === "kill" ? t("击杀命令已执行") : t("私聊命令已执行")
    );
    await refreshAfterMutation();
  } catch (reason) {
    actionError.value = getApiErrorMessage(reason);
    if (getApiErrorCode(reason) === "stale_server_target" && snapshot.value) {
      snapshot.value = { ...snapshot.value, stale: true };
    }
  } finally {
    actionPending.value = false;
  }
}

async function openFactionDialog(player: Player) {
  const blocked = extraDisabledReason(player, "changeFaction");
  const targetRevision = snapshot.value?.targetRevision;
  if (blocked || !targetRevision) {
    ElMessage.warning(blocked || t("请刷新玩家名单后重试"));
    return;
  }
  factionPlayer.value = player;
  factionTargetRevision.value = targetRevision;
  factionChoices.value = [];
  chosenFaction.value = "";
  shouldRespawn.value = false;
  factionDialogError.value = "";
  factionDialogVisible.value = true;
  factionDialogLoading.value = true;
  const readId = ++factionReadId;
  try {
    const response = await getServerStatus();
    if (readId !== factionReadId || !factionDialogVisible.value) return;
    if (response.stale || !samePlayerAndTarget(player, targetRevision)) {
      factionDialogError.value = t("阵营数据或玩家名单已变化，请刷新后重试");
      return;
    }
    factionChoices.value = [
      ...new Set(
        (response.factionScores ?? [])
          .map(item => item.name)
          .filter(name => typeof name === "string" && name.length > 0)
      )
    ];
    if (factionChoices.value.length === 0) {
      factionDialogError.value = t("服务器尚未返回可用阵营");
    }
  } catch (reason) {
    if (readId === factionReadId)
      factionDialogError.value = getApiErrorMessage(reason);
  } finally {
    if (readId === factionReadId) factionDialogLoading.value = false;
  }
}

async function submitFaction() {
  const player = factionPlayer.value;
  const steamId = player?.steamId;
  const targetRevision = factionTargetRevision.value;
  const faction = chosenFaction.value;
  const respawn = shouldRespawn.value;
  if (!player || !steamId || !factionChoices.value.includes(faction)) {
    ElMessage.warning(t("请选择服务器返回的阵营"));
    return;
  }
  const blocked = extraDisabledReason(player, "changeFaction");
  if (blocked || !samePlayerAndTarget(player, targetRevision)) {
    ElMessage.warning(blocked || t("玩家名单或服务器目标已变化，请刷新后重试"));
    return;
  }
  try {
    await ElMessageBox.confirm(
      t("确认将 {p0}（SteamID：{p1}）切换至 {p2}{p3}？", {
        p0: player.name,
        p1: steamId,
        p2: faction,
        p3: respawn ? t("并重生") : ""
      }),
      t("更换玩家阵营"),
      {
        type: "warning",
        confirmButtonText: t("确认更换"),
        cancelButtonText: t("取消")
      }
    );
  } catch {
    return;
  }
  await loadCapabilities();
  const latestBlock = extraDisabledReason(player, "changeFaction");
  if (
    latestBlock ||
    !samePlayerAndTarget(player, targetRevision) ||
    chosenFaction.value !== faction ||
    shouldRespawn.value !== respawn
  ) {
    ElMessage.warning(latestBlock || t("玩家或操作选项已变化，请重新确认"));
    return;
  }
  actionPending.value = true;
  actionError.value = "";
  try {
    const result = await changePlayerFaction({
      steamId,
      faction,
      respawn,
      targetRevision
    });
    factionDialogVisible.value = false;
    if (respawn && result.respawnUncertain) {
      ElMessage.warning(
        t("阵营已更换，重生结果不确定；请到 RCON 原管理页核查，勿立即重复操作")
      );
    } else if (respawn && (result.respawnError || result.respawned === false)) {
      ElMessage.warning(
        t("阵营已更换，但重生命令失败；请人工核查，勿立即重复更换")
      );
    } else {
      ElMessage.success(t("更换阵营命令已执行"));
    }
    await refreshAfterMutation();
  } catch (reason) {
    actionError.value = getApiErrorMessage(reason);
    if (getApiErrorCode(reason) === "stale_server_target" && snapshot.value) {
      snapshot.value = { ...snapshot.value, stale: true };
    }
  } finally {
    actionPending.value = false;
  }
}

function validateReason(value: string): true | string {
  for (const character of value) {
    const code = character.codePointAt(0) ?? 0;
    if (
      code < 32 ||
      (code >= 127 && code <= 159) ||
      code === 0x2028 ||
      code === 0x2029
    ) {
      return t("操作原因只能输入单行文字，不能含换行或控制字符");
    }
  }
  const length = value.trim().length;
  return length >= 1 && length <= 200 ? true : t("操作原因需为 1–200 个字符");
}

async function moderate(player: Player, action: ModerationAction) {
  const steamId = player.steamId;
  const targetRevision = snapshot.value?.targetRevision;
  if (!steamId || !targetRevision || disabledReason(player, action)) return;
  const isBan = action === "ban";
  let reason = "";
  try {
    const answer = await ElMessageBox.prompt(
      isBan
        ? t("确认永久封禁 ") +
            player.name +
            "（SteamID：" +
            steamId +
            t("）？此操作会影响真实服务器。")
        : t("确认踢出 ") +
            player.name +
            "（SteamID：" +
            steamId +
            t("）？此操作会影响真实服务器。"),
      isBan ? t("永久封禁玩家") : t("踢出玩家"),
      {
        type: "warning",
        confirmButtonText: isBan ? t("确认永久封禁") : t("确认踢出"),
        cancelButtonText: t("取消"),
        inputType: "text",
        inputPlaceholder: t("填写单行操作原因（1–200 字）"),
        inputValidator: validateReason
      }
    );
    const reasonCheck = validateReason(answer.value);
    if (reasonCheck !== true) {
      ElMessage.warning(reasonCheck);
      return;
    }
    reason = answer.value.trim();
  } catch {
    return;
  }

  const currentBlock = disabledReason(player, action);
  if (currentBlock) {
    ElMessage.warning(currentBlock);
    return;
  }
  const currentPlayer = snapshot.value?.players.find(
    item => item.steamId === steamId
  );
  if (
    snapshot.value?.targetRevision !== targetRevision ||
    !currentPlayer ||
    currentPlayer.name !== player.name
  ) {
    ElMessage.warning(t("玩家名单或服务器目标已变化，请刷新名单后重新确认"));
    return;
  }
  actionPending.value = true;
  actionError.value = "";
  try {
    const request = { steamId, reason, targetRevision };
    if (isBan) {
      await banPlayerPermanently(request);
    } else {
      await kickPlayer(request);
    }
    ElMessage.success(isBan ? t("永久封禁命令已执行") : t("踢出命令已执行"));
    await refreshAfterMutation();
  } catch (error) {
    actionError.value = getApiErrorMessage(error);
    if (getApiErrorCode(error) === "stale_server_target" && snapshot.value) {
      snapshot.value = { ...snapshot.value, stale: true };
    }
  } finally {
    actionPending.value = false;
  }
}

function onVisibilityChange() {
  if (document.visibilityState === "visible") {
    refreshCapabilities();
    startCapabilityTimer();
    void refreshSteamProfiles();
  } else {
    capabilityFetchedAt.value = 0;
    stopCapabilityTimer();
  }
}

function onDisplaySettingsChange(event: Event) {
  if (
    event.type === "storage" &&
    (event as StorageEvent).key !== "wardogs-steam-profiles-enabled"
  )
    return;
  steamProfilesEnabled.value = readSteamProfilesEnabled();
  if (steamProfilesEnabled.value) void refreshSteamProfiles();
}

function startCapabilityTimer() {
  if (capabilityTimer || document.visibilityState !== "visible") return;
  capabilityTimer = setInterval(() => {
    capabilityClock.value = Date.now();
    if (capabilityClock.value - capabilityFetchedAt.value > 25_000)
      void loadCapabilities();
  }, 5_000);
}

function stopCapabilityTimer() {
  if (!capabilityTimer) return;
  clearInterval(capabilityTimer);
  capabilityTimer = null;
}

function start() {
  if (pageActive) return;
  pageActive = true;
  refreshCapabilities();
  startCapabilityTimer();
  void refreshSteamProfiles();
  document.addEventListener("visibilitychange", onVisibilityChange);
  window.addEventListener(
    "wardogs-display-settings-changed",
    onDisplaySettingsChange
  );
  window.addEventListener("storage", onDisplaySettingsChange);
}

function stop() {
  if (!pageActive) return;
  pageActive = false;
  ++factionReadId;
  ++warningReadId;
  warningDialogVisible.value = false;
  capabilityFetchedAt.value = 0;
  stopCapabilityTimer();
  document.removeEventListener("visibilitychange", onVisibilityChange);
  window.removeEventListener(
    "wardogs-display-settings-changed",
    onDisplaySettingsChange
  );
  window.removeEventListener("storage", onDisplaySettingsChange);
}

onMounted(start);
onActivated(start);
onDeactivated(stop);
onUnmounted(stop);
</script>

<template>
  <div class="p-5 space-y-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-2xl font-semibold">{{ $t("在线玩家") }}</h1>
        <p class="text-sm text-gray-500">
          {{ $t("共享玩家快照每 1 秒刷新；隐藏页面时暂停查询") }}
        </p>
      </div>
      <el-button :loading="loading" @click="refresh">{{
        $t("刷新玩家")
      }}</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert
      v-if="actionError"
      :title="actionError"
      type="error"
      :closable="false"
    />
    <el-alert
      v-if="snapshot?.stale"
      :title="$t('当前显示的是过期玩家快照，管理操作已停用，请先刷新')"
      type="warning"
      :closable="false"
    />
    <el-alert
      v-if="
        capabilityError || (capabilities && capabilities.state !== 'available')
      "
      :title="
        capabilityError || $t('管理能力尚未确认可用，踢出与封禁操作已停用')
      "
      type="warning"
      :closable="false"
    />

    <el-card shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span>{{ $t("阵营人数") }}</span>
          <span class="text-sm text-gray-500">
            {{ $t("采集时间：")
            }}{{ formatObservedAt(snapshot?.observedAt ?? null) }}
          </span>
        </div>
      </template>
      <FactionCounts :players="snapshot?.players ?? null" />
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span>{{ $t("玩家列表") }}</span>
          <span class="text-sm text-gray-500">
            {{
              snapshot ? snapshot.players.length + $t(" 人") : $t("人数未知")
            }}
            {{ $t("· 采集时间：")
            }}{{ formatObservedAt(snapshot?.observedAt ?? null) }}
          </span>
        </div>
      </template>

      <el-input
        v-model="search"
        class="mb-4 max-w-sm"
        clearable
        :placeholder="$t('搜索玩家姓名或 SteamID')"
        :disabled="!snapshot"
      />

      <el-skeleton v-if="loading && !snapshot" :rows="6" animated />
      <el-empty v-else-if="!snapshot" :description="$t('尚无可用的玩家快照')" />
      <el-empty
        v-else-if="snapshot.players.length === 0"
        :description="$t('当前无人在线')"
      />
      <el-empty
        v-else-if="visiblePlayers.length === 0"
        :description="$t('未找到匹配玩家')"
      />
      <div v-else class="grid grid-cols-1 items-start gap-4 lg:grid-cols-3">
        <section
          v-for="group in groups"
          :key="group.key"
          class="min-w-0 rounded-lg border border-[var(--el-border-color-light)] bg-[var(--el-fill-color-light)] p-3"
          :class="{ 'lg:col-span-3': group.unknown }"
          :style="{ borderTopColor: group.color, borderTopWidth: '3px' }"
        >
          <div class="mb-3 flex items-center justify-between gap-3">
            <div class="flex items-center gap-2">
              <span
                class="h-2.5 w-2.5 rounded-full"
                :style="{ backgroundColor: group.color }"
              />
              <h2 class="font-semibold" :style="{ color: group.color }">
                {{ group.name }}
              </h2>
            </div>
            <span class="text-sm text-gray-500">
              {{
                search.trim()
                  ? group.players.length + " / " + group.total + $t(" 人")
                  : group.total + $t(" 人")
              }}
            </span>
          </div>
          <div
            :class="
              group.unknown
                ? 'grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3'
                : 'space-y-3'
            "
          >
            <p
              v-if="group.players.length === 0"
              class="rounded-lg border border-dashed border-[var(--el-border-color)] p-4 text-center text-sm text-gray-500"
            >
              {{ search.trim() ? $t("暂无匹配玩家") : $t("当前无人在线") }}
            </p>
            <PlayerCard
              v-for="entry in group.players"
              :key="entry.index"
              :player="entry.player"
              :steam-profile="
                steamProfilesEnabled
                  ? steamProfiles[entry.player.steamId ?? '']
                  : undefined
              "
              :accent-color="group.color"
              :show-faction="group.unknown"
              :show-kick="Boolean(userStore.canKick)"
              :show-ban="Boolean(userStore.canBan)"
              :show-extra="
                userStore.role === 'owner' ||
                ['kill', 'message', 'changeFaction'].some(permission =>
                  userStore.permissions.includes(permission)
                )
              "
              :show-warning-history="true"
              :show-warning-action="
                userStore.role === 'owner' ||
                userStore.permissions.includes('warning')
              "
              :kick-disabled-reason="disabledReason(entry.player, 'kick')"
              :ban-disabled-reason="disabledReason(entry.player, 'ban')"
              :kill-disabled-reason="extraDisabledReason(entry.player, 'kill')"
              :message-disabled-reason="
                extraDisabledReason(entry.player, 'message')
              "
              :faction-disabled-reason="
                extraDisabledReason(entry.player, 'changeFaction')
              "
              :warning-history-disabled-reason="
                warningHistoryDisabledReason(entry.player)
              "
              @kick="moderate(entry.player, 'kick')"
              @ban="moderate(entry.player, 'ban')"
              @kill="runExtraAction(entry.player, 'kill')"
              @message="runExtraAction(entry.player, 'message')"
              @change-faction="openFactionDialog(entry.player)"
              @warnings="openWarnings(entry.player)"
            />
          </div>
        </section>
      </div>
    </el-card>

    <el-dialog
      v-model="warningDialogVisible"
      :title="$t('玩家警告')"
      width="min(680px, 94vw)"
      :close-on-click-modal="false"
      @close="++warningReadId"
    >
      <p class="mb-2 break-all text-sm text-gray-500">
        {{ warningPlayer?.name }} · SteamID：{{ warningPlayer?.steamId }}
      </p>
      <p class="mb-3 text-sm text-gray-500">
        {{ $t("警告通过 RCON 私聊发送，并在面板本地记录；不是游戏原生处罚。") }}
      </p>
      <el-alert
        v-if="warningDialogError"
        :title="warningDialogError"
        type="warning"
        :closable="false"
        class="mb-3"
      />
      <el-alert
        v-if="warningWriteError"
        :title="warningWriteError"
        type="error"
        :closable="false"
        class="mb-3"
      />

      <div
        v-if="
          userStore.role === 'owner' ||
          userStore.permissions.includes('warning')
        "
        class="mb-5"
      >
        <el-input
          v-model="warningReason"
          maxlength="180"
          show-word-limit
          :placeholder="$t('输入单行警告原因（1–180 字）')"
          :disabled="actionPending"
        />
        <div class="mt-3">
          <el-tooltip
            :content="
              warningWriteBlockedReason() || $t('发送警告私聊并记入面板')
            "
          >
            <span>
              <el-button
                type="warning"
                :loading="actionPending"
                :disabled="
                  Boolean(warningWriteBlockedReason()) || !warningReason.trim()
                "
                @click="submitWarning"
                >{{ $t("发送警告并记录") }}</el-button
              >
            </span>
          </el-tooltip>
        </div>
      </div>

      <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
        <span class="font-medium">
          {{ $t("已成功发送") }} {{ warningHistory?.count ?? "—" }}
          {{ $t("次") }}
        </span>
        <span class="text-xs text-gray-500">
          {{ $t("面板本地记录 · 采集时间：")
          }}{{ formatObservedAt(warningHistory?.observedAt ?? null) }}
        </span>
      </div>
      <el-skeleton v-if="warningDialogLoading" :rows="3" animated />
      <el-table
        v-else-if="warningHistory"
        :data="warningHistory.entries"
        :empty-text="$t('暂无警告记录')"
        size="small"
        max-height="300"
      >
        <el-table-column :label="$t('时间')" min-width="155">
          <template #default="scope">{{
            formatObservedAt(scope.row.createdAt)
          }}</template>
        </el-table-column>
        <el-table-column prop="actor" :label="$t('处理人')" min-width="100" />
        <el-table-column
          prop="reason"
          :label="$t('原因')"
          min-width="190"
          show-overflow-tooltip
        />
        <el-table-column :label="$t('结果')" min-width="100">
          <template #default="scope">
            <el-tag :type="warningOutcomeType(scope.row.outcome)">
              {{ warningOutcomeLabel(scope.row.outcome) }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else :description="$t('尚无可显示的面板警告记录')" />
      <template #footer>
        <el-button @click="warningDialogVisible = false">{{
          $t("关闭")
        }}</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="factionDialogVisible"
      :title="$t('更换玩家阵营')"
      width="min(460px, 94vw)"
      :close-on-click-modal="false"
      @closed="++factionReadId"
    >
      <p class="mb-4 break-all text-sm text-gray-500">
        {{ factionPlayer?.name }} · SteamID：{{ factionPlayer?.steamId }}
      </p>
      <el-alert
        v-if="factionDialogError"
        :title="factionDialogError"
        type="warning"
        :closable="false"
        class="mb-3"
      />
      <el-select
        v-model="chosenFaction"
        class="w-full"
        :loading="factionDialogLoading"
        :disabled="factionDialogLoading || Boolean(factionDialogError)"
        :placeholder="$t('从服务器当前阵营中选择')"
      >
        <el-option
          v-for="name in factionChoices"
          :key="name"
          :label="name"
          :value="name"
        />
      </el-select>
      <el-checkbox v-model="shouldRespawn" class="mt-3">{{
        $t("切换后重生玩家")
      }}</el-checkbox>
      <p class="mt-1 text-xs text-gray-500">
        {{ $t("重生会额外发送一条管理命令。") }}
      </p>
      <template #footer>
        <el-button @click="factionDialogVisible = false">{{
          $t("取消")
        }}</el-button>
        <el-button
          type="warning"
          :loading="actionPending"
          :disabled="
            factionDialogLoading ||
            Boolean(factionDialogError) ||
            !chosenFaction
          "
          @click="submitFaction"
          >{{ $t("确认更换") }}</el-button
        >
      </template>
    </el-dialog>
    <KillRecords />
  </div>
</template>
