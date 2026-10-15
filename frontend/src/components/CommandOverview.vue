<script setup lang="ts">
import { computed } from "vue";
import { t } from "@/i18n";
import { formatObservedAt } from "@/api/snapshot";
import type { StatusResponse } from "@/api/status";
import type { PlayersResponse } from "@/api/players";
import type { RotationResponse } from "@/api/rotation";
import type { CapabilitiesResponse } from "@/api/capabilities";
import { FACTIONS, normalizeFaction } from "@/utils/factions";
import { mapArtwork } from "@/utils/mapArtwork";
import FactionCashChart from "@/components/FactionCashChart.vue";
import FactionEmblem from "@/components/FactionEmblem.vue";
import RefreshIcon from "~icons/ri/refresh-line";
import MapIcon from "~icons/ri/map-2-line";
import UsersIcon from "~icons/ri/group-line";
import ClockIcon from "~icons/ri/time-line";
import TargetIcon from "~icons/ri/focus-3-line";
import FlagIcon from "~icons/ri/flag-line";
import ArrowIcon from "~icons/ri/arrow-right-up-line";
import ChartIcon from "~icons/ri/line-chart-line";

const props = defineProps<{
  status: StatusResponse | null;
  players: PlayersResponse | null;
  rotation: RotationResponse | null;
  capabilities: CapabilitiesResponse | null;
  loading: boolean;
  realtime: boolean;
  error: string;
  playerError: string;
  rotationError: string;
}>();
defineEmits<{ refresh: []; realtime: [enabled: boolean] }>();

const current = computed(() => mapArtwork(props.status?.map));
const nextEntry = computed(() => {
  const index = props.status?.rotation?.nextIndex;
  if (
    index == null ||
    props.rotation?.enabled === false ||
    props.rotation?.stale
  )
    return null;
  return props.rotation?.items.find(item => item.order === index) ?? null;
});
const next = computed(() => mapArtwork(nextEntry.value?.map));
const fresh = computed(
  () => !!props.status && !props.status.stale && !props.error
);
const stateText = computed(() =>
  !props.status
    ? t("等待连接")
    : !fresh.value
      ? t("数据已过期")
      : props.realtime
        ? t("连接正常")
        : t("已暂停刷新")
);
const occupancy = computed(() => {
  const count = props.status?.playerCount;
  const max = props.status?.maxPlayers;
  return count == null || !max
    ? 0
    : Math.min(100, Math.max(0, (count / max) * 100));
});
const duration = computed(() => {
  const seconds = props.status?.matchSeconds;
  return seconds == null
    ? "—"
    : `${Math.floor(seconds / 3600)
        .toString()
        .padStart(2, "0")}:${Math.floor((seconds / 60) % 60)
        .toString()
        .padStart(2, "0")}:${Math.floor(seconds % 60)
        .toString()
        .padStart(2, "0")}`;
});
const factions = computed(() =>
  FACTIONS.map(faction => {
    const scores = props.status?.factionScores;
    const score =
      scores?.find(item => normalizeFaction(item.name) === faction.code)
        ?.score ?? null;
    const players = props.players?.players;
    return {
      ...faction,
      score,
      count: players
        ? players.filter(
            player => normalizeFaction(player.faction) === faction.code
          ).length
        : null,
      progress:
        score == null || !props.status?.scoreCap
          ? 0
          : Math.max(0, Math.min(100, (score / props.status.scoreCap) * 100))
    };
  })
);
const unknownPlayers = computed(
  () =>
    props.players?.players.filter(player => !normalizeFaction(player.faction))
      .length ?? 0
);
const mode = computed(() =>
  props.status?.experiences == null
    ? t("未知")
    : props.status.experiences.join(" / ") || t("无")
);
const rotationMode = computed(() =>
  props.rotation?.mode === "ordered"
    ? t("顺序")
    : props.rotation?.mode === "random"
      ? t("随机")
      : t("未知")
);
const clock = computed(() => {
  const time = props.status?.observedAt;
  if (!time) return "—";
  return new Date(time).toLocaleTimeString([], { hour12: false });
});
</script>

<template>
  <div class="command-overview">
    <header class="overview-toolbar">
      <div class="overview-heading">
        <span class="eyebrow">SERVER COMMAND</span>
        <div class="heading-line">
          <h1>{{ t("首页总览") }}</h1>
          <span class="connection-badge" :class="{ healthy: fresh && realtime }"
            ><i />{{ stateText }}</span
          >
        </div>
        <p class="server-caption">
          {{ status?.serverName ?? t("尚无可显示的服务器状态") }}
        </p>
      </div>
      <div class="overview-actions">
        <label class="realtime-control"
          ><span>{{ t("实时更新") }}</span
          ><el-switch
            :model-value="realtime"
            :aria-label="t('实时更新')"
            @change="$emit('realtime', Boolean($event))"
        /></label>
        <span
          class="last-update"
          :title="formatObservedAt(status?.observedAt ?? null)"
          >{{ t("更新于") }} <b>{{ clock }}</b></span
        >
        <el-button
          :loading="loading"
          :icon="RefreshIcon"
          @click="$emit('refresh')"
          >{{ t("刷新") }}</el-button
        >
      </div>
    </header>

    <div
      v-if="
        error ||
        playerError ||
        rotationError ||
        status?.stale ||
        players?.stale ||
        capabilities?.state === 'stale'
      "
      class="overview-notices"
    >
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        show-icon
      />
      <el-alert
        v-if="playerError"
        :title="playerError"
        type="error"
        :closable="false"
        show-icon
      />
      <el-alert
        v-if="rotationError"
        :title="rotationError"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-alert
        v-if="status?.stale"
        :title="t('当前显示的是过期快照，并非实时服务器状态')"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-alert
        v-if="players?.stale"
        :title="t('阵营人数来自过期的玩家快照')"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-alert
        v-if="capabilities?.state === 'stale'"
        :title="t('能力信息已过期，功能可用性以新一次探测为准')"
        type="warning"
        :closable="false"
        show-icon
      />
    </div>

    <section class="telemetry-strip" :aria-label="t('当前快照')">
      <div class="telemetry-item">
        <UsersIcon />
        <div>
          <span>{{ t("在线人数") }}</span>
          <p>
            <strong>{{ status?.playerCount ?? "—" }}</strong
            ><small>/ {{ status?.maxPlayers ?? "—" }}</small>
          </p>
        </div>
        <div class="telemetry-meter">
          <i :style="{ transform: `scaleX(${occupancy / 100})` }" />
        </div>
      </div>
      <div class="telemetry-item">
        <ClockIcon />
        <div>
          <span>{{ t("比赛时长") }}</span>
          <p>
            <strong class="telemetry-time">{{ duration }}</strong>
          </p>
        </div>
      </div>
      <div class="telemetry-item">
        <TargetIcon />
        <div>
          <span>{{ t("控制区") }}</span>
          <p class="telemetry-value">{{ status?.alternator ?? t("未知") }}</p>
        </div>
      </div>
      <router-link to="/rotation/index" class="telemetry-item telemetry-next"
        ><MapIcon />
        <div>
          <span>{{ t("下一张地图") }}</span>
          <p class="telemetry-value">
            {{ nextEntry ? next.name : t("待服务器确认") }}
          </p>
        </div>
        <ArrowIcon
      /></router-link>
    </section>

    <section class="map-grid" :aria-label="t('当前地图')">
      <article
        class="map-card map-current"
        :class="{ 'without-art': !current.image }"
      >
        <img
          v-if="current.image"
          :src="current.image"
          :alt="t('地图示意图')"
          width="1280"
          height="720"
          fetchpriority="high"
        />
        <div class="map-shade" />
        <span class="map-label"><MapIcon />{{ t("当前地图") }}</span>
        <span class="map-watermark">{{ t("地图示意图") }}</span>
        <div class="map-caption">
          <div>
            <span class="map-kicker">{{ t("当前对局") }}</span>
            <h2>{{ current.name }}</h2>
            <p>{{ mode }} <span>·</span> {{ status?.lighting ?? t("未知") }}</p>
          </div>
          <div class="zone-chip">
            <TargetIcon />
            <div>
              <b>{{ status?.alternator ?? t("未知") }}</b
              ><small>{{ t("控制区") }}</small>
            </div>
          </div>
        </div>
      </article>
      <section class="overview-panel scoreboard">
        <div class="panel-heading">
          <h2><FlagIcon />{{ t("阵营比分") }}</h2>
          <span>{{ t("比分上限") }} {{ status?.scoreCap ?? "—" }}</span
          ><span class="scoreboard-updated"
            >{{ t("采集时间：")
            }}{{ formatObservedAt(players?.observedAt ?? null) }}</span
          >
        </div>
        <div class="faction-grid">
          <div
            v-for="faction in factions"
            :key="faction.code"
            class="faction-stat"
            :style="{ '--faction-color': faction.color }"
          >
            <FactionEmblem class="faction-emblem" :faction="faction.code" />
            <div class="faction-body">
              <div class="faction-line">
                <b>{{ faction.name }}</b
                ><strong>{{ faction.score ?? "—" }}</strong>
              </div>
              <div class="score-track">
                <i
                  :style="{ transform: `scaleX(${faction.progress / 100})` }"
                />
              </div>
              <div class="faction-roster">
                <span>{{ t("在线玩家") }}</span
                ><b
                  >{{ faction.count ?? "—" }} <small>{{ t("人") }}</small></b
                >
              </div>
            </div>
          </div>
        </div>
        <p v-if="unknownPlayers" class="unknown-note">
          {{ t("另有") }} {{ unknownPlayers }} {{ t("位玩家的阵营未知") }}
        </p>
      </section>
    </section>

    <div class="details-grid">
      <section class="overview-panel">
        <div class="panel-heading">
          <h2><FlagIcon />{{ t("对局信息") }}</h2>
          <span class="panel-code">01 / MATCH</span>
        </div>
        <dl class="detail-list">
          <div>
            <dt>{{ t("游戏模式") }}</dt>
            <dd>{{ mode }}</dd>
          </div>
          <div>
            <dt>{{ t("控制区") }}</dt>
            <dd>{{ status?.alternator ?? t("未知") }}</dd>
          </div>
          <div>
            <dt>{{ t("光照") }}</dt>
            <dd>{{ status?.lighting ?? t("未知") }}</dd>
          </div>
          <div>
            <dt>{{ t("比分周期") }}</dt>
            <dd>{{ status?.scoreTick?.current ?? "—" }}</dd>
          </div>
          <div>
            <dt>{{ t("比赛时长") }}</dt>
            <dd>{{ duration }}</dd>
          </div>
          <div>
            <dt>{{ t("状态查询能力") }}</dt>
            <dd>
              {{
                capabilities?.features.status === true
                  ? t("支持")
                  : capabilities?.features.status === false
                    ? t("不支持")
                    : t("未知")
              }}
            </dd>
          </div>
        </dl>
      </section>
      <section class="overview-panel">
        <div class="panel-heading">
          <h2><MapIcon />{{ t("地图轮换与状态") }}</h2>
          <router-link to="/rotation/index" class="panel-link"
            >{{ t("查看轮换") }}<ArrowIcon
          /></router-link>
        </div>
        <div v-if="nextEntry" class="next-map-row">
          <img
            v-if="next.image"
            :src="next.image"
            :alt="t('地图示意图')"
            width="96"
            height="60"
            loading="lazy"
          />
          <div>
            <small>{{ t("下一张地图") }}</small
            ><strong>{{ next.name }}</strong>
          </div>
          <span>{{ nextEntry.lighting ?? t("未知") }}</span>
        </div>
        <dl class="detail-list">
          <div>
            <dt>{{ t("轮换方式") }}</dt>
            <dd>{{ rotationMode }}</dd>
          </div>
          <div>
            <dt>{{ t("轮换条目") }}</dt>
            <dd>{{ rotation?.items.length ?? "—" }}</dd>
          </div>
          <div>
            <dt>{{ t("地图轮换位置") }}</dt>
            <dd>
              {{ status?.rotation?.nowIndex ?? "—" }} →
              {{ status?.rotation?.nextIndex ?? "—" }}
            </dd>
          </div>
          <div>
            <dt>{{ t("服务器状态") }}</dt>
            <dd class="detail-state">
              <i :class="{ healthy: fresh }" />{{ stateText }}
            </dd>
          </div>
          <div class="detail-wide">
            <dt>{{ t("服务器名称") }}</dt>
            <dd>{{ status?.serverName ?? t("未知") }}</dd>
          </div>
        </dl>
      </section>
    </div>

    <section class="overview-panel cash-panel">
      <div class="panel-heading">
        <h2><ChartIcon />{{ t("阵营现金趋势") }}</h2>
        <span class="panel-code">02 / ECONOMY</span>
      </div>
      <div class="chart-body">
        <FactionCashChart
          :players="players?.players ?? null"
          :observed-at="players?.observedAt ?? null"
        />
      </div>
    </section>
    <footer class="overview-footnote">
      <span>WARDOGS <i /> {{ t("服务器管理工作台") }}</span
      ><span>{{ t("共享采集：有人查看时玩家每 1 秒、状态每 2 秒刷新") }}</span>
    </footer>
  </div>
</template>

<style scoped>
.command-overview {
  --surface: var(--wd-panel, var(--el-bg-color));
  --line: var(--wd-line, var(--el-border-color-light));
  --muted: var(--wd-muted, var(--el-text-color-secondary));
  --accent: var(--wd-gold, var(--el-color-primary));
  --text: var(--wd-text, var(--el-text-color-primary));
  max-width: 1800px;
  margin: 0 auto;
  padding: 24px;
  color: var(--text);
}
.overview-toolbar,
.overview-actions,
.heading-line {
  display: flex;
  align-items: center;
}
.overview-toolbar {
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 22px;
}
.eyebrow {
  display: block;
  margin-bottom: 6px;
  color: var(--accent);
  font:
    10px/1.4 ui-monospace,
    monospace;
  letter-spacing: 2.5px;
}
.heading-line {
  flex-wrap: wrap;
  gap: 16px;
}
h1 {
  margin: 0;
  font-size: 23px;
  font-weight: 700;
  letter-spacing: -0.5px;
}
.server-caption {
  max-width: 620px;
  margin-top: 7px;
  color: var(--muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.connection-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 9px;
  border: 1px solid var(--line);
  border-radius: 20px;
  color: var(--muted);
  font-size: 11px;
}
.connection-badge i,
.detail-state i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #b59b68;
}
.connection-badge.healthy {
  background: rgb(89 157 124 / 10%);
  border-color: rgb(89 157 124 / 30%);
  color: #81cba5;
}
.connection-badge.healthy i,
.detail-state i.healthy {
  background: #81cba5;
}
.overview-actions {
  flex-wrap: wrap;
  gap: 16px;
  flex-shrink: 0;
}
.realtime-control {
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--muted);
  font-size: 12px;
}
.last-update {
  padding-left: 16px;
  border-left: 1px solid var(--line);
  color: var(--muted);
  font-size: 11px;
}
.last-update b {
  margin-left: 6px;
  color: var(--text);
  font:
    11px ui-monospace,
    monospace;
}
.overview-actions :deep(.el-button) {
  min-height: 36px;
}
.overview-notices {
  display: grid;
  gap: 8px;
  margin-bottom: 16px;
}
.map-grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr);
  gap: 14px;
}
.map-card {
  position: relative;
  min-width: 0;
  overflow: hidden;
  background: #202a32;
  border: 1px solid var(--line);
  border-radius: 9px;
}
.map-current {
  min-height: 365px;
}
.map-card img,
.map-shade {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.map-card img {
  object-position: 50% 45%;
}
.map-shade {
  background: linear-gradient(
    180deg,
    rgb(10 15 20 / 8%) 32%,
    rgb(10 15 20 / 30%) 58%,
    rgb(10 15 20 / 92%) 100%
  );
}
.without-art {
  background:
    repeating-linear-gradient(
      0deg,
      transparent,
      transparent 39px,
      #2a343d 40px
    ),
    repeating-linear-gradient(90deg, #1b252e, #1b252e 39px, #2a343d 40px);
}
.map-label {
  position: absolute;
  top: 17px;
  left: 17px;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 10px;
  border: 1px solid rgb(213 181 120 / 35%);
  border-left: 2px solid #d5b578;
  border-radius: 3px;
  background: rgb(16 21 26 / 85%);
  color: #e7cf9f;
  font-size: 11px;
}
.map-label svg {
  width: 14px;
  height: 14px;
}
.map-watermark {
  position: absolute;
  top: 19px;
  right: 18px;
  padding: 4px 7px;
  border-radius: 3px;
  color: #fff;
  background: rgb(16 21 26 / 60%);
  font-size: 10px;
}
.map-caption {
  position: absolute;
  right: 23px;
  bottom: 24px;
  left: 23px;
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 16px;
  color: #fff;
}
.map-caption > div:first-child {
  min-width: 0;
  border-left: 3px solid #d5b578;
  padding-left: 14px;
}
.map-kicker {
  font:
    10px/1.5 ui-monospace,
    monospace;
  letter-spacing: 3px;
  color: #e7cf9f;
}
.map-caption h2 {
  margin: 3px 0 8px;
  overflow-wrap: anywhere;
  font-size: clamp(30px, 3.6vw, 55px);
  font-weight: 800;
  line-height: 1;
  letter-spacing: -1.5px;
}
.map-caption p {
  font-size: 12px;
  color: #d6dce0;
  overflow-wrap: anywhere;
}
.map-caption p span {
  margin: 0 7px;
  opacity: 0.7;
}
.zone-chip {
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: 45%;
  padding: 10px 13px;
  border: 1px solid rgb(255 255 255 / 15%);
  border-radius: 6px;
  background: rgb(16 21 26 / 70%);
}
.zone-chip svg {
  flex: 0 0 26px;
  width: 26px;
  height: 26px;
  color: #d5b578;
}
.zone-chip b {
  display: block;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.zone-chip small {
  display: block;
  margin-top: 4px;
  color: #b6c0c8;
  font-size: 10px;
}
.overview-panel {
  margin-top: 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
}
.panel-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  min-height: 46px;
  padding: 12px 17px;
  border-bottom: 1px solid var(--line);
}
.panel-heading h2 {
  display: flex;
  align-items: center;
  gap: 9px;
  margin: 0;
  font-size: 13px;
  font-weight: 600;
}
.panel-heading h2 svg {
  width: 16px;
  height: 16px;
  color: var(--accent);
}
.panel-heading > span {
  color: var(--muted);
  font-size: 10px;
}
.panel-heading .scoreboard-updated,
.panel-code,
.panel-link {
  margin-left: auto;
}
.panel-code {
  font:
    9px ui-monospace,
    monospace;
  letter-spacing: 1px;
}
.panel-link {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--accent);
  font-size: 11px;
}
.panel-link svg {
  width: 13px;
  height: 13px;
}
.panel-link:hover {
  text-decoration: underline;
}
.faction-grid {
  display: grid;
  grid-template-columns: 1fr;
  padding: 0 18px;
}
.faction-stat {
  display: flex;
  align-items: center;
  gap: 15px;
  padding: 19px 0;
}
.faction-stat + .faction-stat {
  border-top: 1px solid var(--line);
}
.faction-body {
  flex: 1;
  min-width: 0;
}
.faction-line,
.faction-roster {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.faction-line b {
  font-size: 13px;
}
.faction-line strong {
  font-size: 27px;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.score-track {
  height: 5px;
  margin-top: 11px;
  border-radius: 10px;
  background: var(--line);
  overflow: hidden;
}
.score-track i {
  display: block;
  height: 100%;
  background: var(--faction-color);
  transform-origin: left;
  transition: transform 0.25s;
}
.faction-roster {
  margin-top: 9px;
  color: var(--muted);
  font-size: 10px;
}
.faction-roster b {
  color: var(--faction-color);
  font-size: 12px;
}
.faction-roster small {
  color: var(--muted);
  font-size: 10px;
  font-weight: 400;
}
.unknown-note {
  margin: 0 18px 14px;
  color: var(--muted);
  font-size: 11px;
}
.details-grid {
  display: grid;
  grid-template-columns: 1fr 1.1fr;
  gap: 14px;
}
.detail-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px 24px;
  padding: 20px 18px;
  font-size: 12px;
}
.detail-list > div {
  display: flex;
  align-items: baseline;
  gap: 16px;
  min-width: 0;
}
.detail-list dt {
  flex-shrink: 0;
  color: var(--muted);
  font-size: 11px;
}
.detail-list dd {
  min-width: 0;
  overflow-wrap: anywhere;
}
.detail-wide {
  grid-column: 1 / -1;
}
.detail-state {
  display: flex;
  align-items: center;
  gap: 6px;
}
.chart-body {
  padding: 18px;
}
.overview-footnote {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 12px;
  margin-top: 20px;
  font-size: 10px;
  color: var(--muted);
}
.overview-footnote > span:first-child {
  display: flex;
  align-items: center;
  gap: 10px;
  letter-spacing: 1px;
}
.overview-footnote i {
  height: 10px;
  border-left: 1px solid var(--line);
}

.telemetry-strip {
  display: grid;
  grid-template-columns: 0.9fr 1.1fr 1.2fr 1fr;
  margin-bottom: 18px;
  border-block: 1px solid var(--line);
}
.telemetry-item {
  position: relative;
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 13px;
  padding: 19px 24px;
}
.telemetry-item:first-child {
  padding-left: 0;
}
.telemetry-item + .telemetry-item {
  border-left: 1px solid var(--line);
}
.telemetry-item > svg {
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  color: var(--accent);
}
.telemetry-item > div {
  min-width: 0;
}
.telemetry-item span,
.telemetry-item small {
  color: var(--muted);
  font-size: 11px;
}
.telemetry-item p {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-top: 5px;
}
.telemetry-item strong {
  font-size: 29px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}
.telemetry-item .telemetry-time {
  font:
    23px/1.5 ui-monospace,
    monospace;
}
.telemetry-item .telemetry-value {
  display: block;
  color: var(--text);
  font-size: 13px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.telemetry-meter {
  position: absolute;
  left: 35px;
  bottom: 13px;
  width: 70px;
  height: 2px;
  background: var(--line);
}
.telemetry-meter i {
  display: block;
  height: 100%;
  background: var(--accent);
  transform-origin: left;
}
.telemetry-next:hover {
  background: var(--surface);
}
.telemetry-next > svg:last-child {
  width: 15px;
  margin-left: auto;
}
.scoreboard {
  margin-top: 0;
  border-top: 2px solid var(--accent);
}
.scoreboard .panel-heading {
  padding-block: 16px;
}
.scoreboard-updated {
  display: none;
}
.faction-emblem {
  width: 48px;
  height: 56px;
  flex-shrink: 0;
  color: var(--faction-color);
}
.faction-line b {
  letter-spacing: 0.3px;
}
.faction-line strong {
  color: var(--faction-color);
}
.next-map-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px 18px 0;
}
.next-map-row img {
  width: 96px;
  height: 60px;
  object-fit: cover;
  border-radius: 4px;
}
.next-map-row small,
.next-map-row > span {
  font-size: 10px;
  color: var(--muted);
}
.next-map-row strong {
  display: block;
  margin-top: 4px;
  font-size: 16px;
  letter-spacing: 1px;
}
.next-map-row > span {
  margin-left: auto;
}
@media (width >= 1500px) {
  .map-current {
    min-height: 420px;
  }
}
@media (width <= 1150px) {
  .overview-toolbar {
    align-items: start;
  }
  .overview-actions {
    max-width: 290px;
    justify-content: end;
    gap: 8px;
  }
  .telemetry-item {
    padding-inline: 14px;
  }
  .telemetry-item > svg {
    display: none;
  }
  .telemetry-meter {
    left: 0;
  }
  .zone-chip {
    display: none;
  }
  .map-grid {
    grid-template-columns: minmax(0, 1.6fr) minmax(260px, 1fr);
  }
  .details-grid {
    grid-template-columns: 1fr;
  }
}
@media (width <= 760px) {
  .command-overview {
    padding: 16px;
  }
  .overview-toolbar {
    flex-direction: column;
    gap: 14px;
  }
  .overview-actions {
    max-width: none;
    width: 100%;
    justify-content: space-between;
  }
  .overview-actions :deep(.el-button) {
    min-height: 44px;
  }
  .telemetry-strip {
    grid-template-columns: 1fr 1fr;
  }
  .telemetry-item {
    min-height: 86px;
    padding: 12px;
  }
  .telemetry-item:first-child {
    padding-left: 12px;
  }
  .telemetry-item:nth-child(3) {
    border-left: 0;
    border-top: 1px solid var(--line);
  }
  .telemetry-item:nth-child(4) {
    border-top: 1px solid var(--line);
  }
  .telemetry-meter {
    left: 12px;
    bottom: 8px;
  }
  .map-grid {
    grid-template-columns: 1fr;
  }
  .map-current {
    min-height: 330px;
  }
  .scoreboard .faction-grid {
    padding-inline: 16px;
  }
  .scoreboard .faction-stat {
    padding-block: 15px;
  }
  .detail-list {
    grid-template-columns: 1fr;
  }
  .last-update {
    padding-left: 8px;
  }
  .overview-footnote {
    font-size: 9px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .score-track i {
    transition: none;
  }
}
</style>
