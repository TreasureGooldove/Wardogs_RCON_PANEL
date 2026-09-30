<script setup lang="ts">
import { t } from "@/i18n";
import { computed } from "vue";
import type { Player } from "@/api/players";
import type { SteamProfile } from "@/api/steam";

const props = defineProps<{
  player: Player;
  steamProfile?: SteamProfile;
  accentColor: string;
  kickDisabledReason: string;
  banDisabledReason: string;
  killDisabledReason: string;
  messageDisabledReason: string;
  factionDisabledReason: string;
  warningHistoryDisabledReason: string;
  showKick: boolean;
  showBan: boolean;
  showExtra: boolean;
  showWarningHistory: boolean;
  showWarningAction: boolean;
  showFaction?: boolean;
}>();

const safeProfileUrl = computed(() => {
  const steamId = props.player.steamId;
  return steamId && /^[1-9][0-9]{16}$/.test(steamId)
    ? `https://steamcommunity.com/profiles/${steamId}/`
    : "";
});

const safeAvatarUrl = computed(() => {
  if (!props.steamProfile?.avatarUrl) return "";
  try {
    const url = new URL(props.steamProfile.avatarUrl);
    return url.protocol === "https:" &&
      (url.hostname === "steamstatic.com" ||
        url.hostname.endsWith(".steamstatic.com") ||
        url.hostname === "steamcdn-a.akamaihd.net")
      ? url.href
      : "";
  } catch {
    return "";
  }
});

defineEmits<{
  kick: [];
  ban: [];
  kill: [];
  message: [];
  changeFaction: [];
  warnings: [];
}>();

function displayNumber(value: number | null) {
  return value === null ? t("未知") : value;
}
</script>

<template>
  <article
    class="rounded-lg border border-[var(--el-border-color-light)] bg-[var(--el-fill-color-blank)] p-3"
    :style="{ borderLeftColor: accentColor, borderLeftWidth: '3px' }"
  >
    <div class="flex min-w-0 items-start gap-3">
      <el-avatar
        v-if="safeAvatarUrl"
        :size="36"
        :src="safeAvatarUrl"
        class="shrink-0"
      />
      <div class="min-w-0">
        <a
          v-if="safeProfileUrl"
          :href="safeProfileUrl"
          target="_blank"
          rel="noopener noreferrer"
          class="break-all text-sm font-semibold text-[var(--el-color-primary)] hover:underline focus-visible:underline"
          :title="
            $t('在新标签页打开 {p0} 的 Steam 主页', {
              p0: player.name || $t('玩家')
            })
          "
        >
          {{ player.name || $t("未命名玩家") }}
        </a>
        <div
          v-else
          class="break-all text-sm font-semibold"
          :title="player.name"
        >
          {{ player.name || $t("未命名玩家") }}
        </div>
        <div
          v-if="steamProfile?.personaName"
          class="mt-1 break-all text-xs text-gray-500"
        >
          Steam：
          <a
            v-if="safeProfileUrl"
            :href="safeProfileUrl"
            target="_blank"
            rel="noopener noreferrer"
            class="hover:underline"
            >{{ steamProfile.personaName }}</a
          >
          <span v-else>{{ steamProfile.personaName }}</span>
        </div>
        <div v-if="showFaction" class="mt-1 text-xs text-gray-500">
          {{ $t("原始阵营：") }}{{ player.faction || $t("未知") }}
        </div>
        <div class="mt-1 break-all font-mono text-xs text-gray-500">
          SteamID：{{ player.steamId ?? $t("未知") }}
        </div>
      </div>
    </div>

    <dl class="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
      <div>
        <dt class="text-gray-500">{{ $t("击杀") }}</dt>
        <dd class="font-medium">{{ displayNumber(player.kills) }}</dd>
      </div>
      <div>
        <dt class="text-gray-500">{{ $t("死亡") }}</dt>
        <dd class="font-medium">{{ displayNumber(player.deaths) }}</dd>
      </div>
      <div>
        <dt class="text-gray-500">{{ $t("现金") }}</dt>
        <dd class="font-medium">{{ displayNumber(player.cash) }}</dd>
      </div>
      <div>
        <dt class="text-gray-500">{{ $t("延迟") }}</dt>
        <dd class="font-medium">
          {{ player.pingMs === null ? $t("未知") : player.pingMs + " ms" }}
        </dd>
      </div>
    </dl>

    <div
      v-if="showKick || showBan || showExtra || showWarningHistory"
      class="mt-3 flex flex-wrap gap-2 border-t border-[var(--el-border-color-lighter)] pt-3"
    >
      <el-tooltip
        v-if="showWarningHistory"
        :content="
          warningHistoryDisabledReason ||
          (showWarningAction
            ? $t('发送管理员警告私聊，查看面板本地记录')
            : $t('查看面板本地警告记录'))
        "
      >
        <span>
          <el-button
            size="small"
            type="warning"
            plain
            :disabled="Boolean(warningHistoryDisabledReason)"
            @click="$emit('warnings')"
            >{{ showWarningAction ? $t("警告") : $t("警告记录") }}</el-button
          >
        </span>
      </el-tooltip>
      <el-tooltip
        v-if="showKick"
        :content="kickDisabledReason || $t('踢出该玩家')"
      >
        <span>
          <el-button
            size="small"
            type="warning"
            :disabled="Boolean(kickDisabledReason)"
            @click="$emit('kick')"
            >{{ $t("踢出") }}</el-button
          >
        </span>
      </el-tooltip>
      <el-tooltip
        v-if="showBan"
        :content="banDisabledReason || $t('永久封禁该玩家')"
      >
        <span>
          <el-button
            size="small"
            type="danger"
            :disabled="Boolean(banDisabledReason)"
            @click="$emit('ban')"
            >{{ $t("永久封禁") }}</el-button
          >
        </span>
      </el-tooltip>
      <el-tooltip
        v-if="showExtra"
        :content="killDisabledReason || $t('击杀当前角色，玩家仍可正常重生')"
      >
        <span>
          <el-button
            size="small"
            type="danger"
            plain
            :disabled="Boolean(killDisabledReason)"
            @click="$emit('kill')"
            >{{ $t("击杀玩家") }}</el-button
          >
        </span>
      </el-tooltip>
      <el-tooltip
        v-if="showExtra"
        :content="messageDisabledReason || $t('向该玩家发送私聊')"
      >
        <span>
          <el-button
            size="small"
            :disabled="Boolean(messageDisabledReason)"
            @click="$emit('message')"
            >{{ $t("私聊") }}</el-button
          >
        </span>
      </el-tooltip>
      <el-tooltip
        v-if="showExtra"
        :content="factionDisabledReason || $t('更换玩家阵营')"
      >
        <span>
          <el-button
            size="small"
            :disabled="Boolean(factionDisabledReason)"
            @click="$emit('changeFaction')"
            >{{ $t("更换阵营") }}</el-button
          >
        </span>
      </el-tooltip>
    </div>
    <p
      v-else
      class="mt-3 border-t border-[var(--el-border-color-lighter)] pt-3 text-xs text-gray-500"
    >
      {{ $t("当前账号仅可查看玩家") }}
    </p>
  </article>
</template>
