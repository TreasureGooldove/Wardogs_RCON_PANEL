<script setup lang="ts">
import { t } from "@/i18n";
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { getApiErrorMessage } from "@/api/errors";
import { formatObservedAt } from "@/api/snapshot";
import {
  getHistoryMatch,
  getHistoryMatches,
  getHistoryPlayer,
  getHistoryPlayers,
  type HistoryMatch,
  type HistoryPlayer,
  type MatchDetail,
  type PlayerDetail
} from "@/api/history";
import { factionDisplay } from "@/utils/factions";

defineOptions({ name: "HistoryRecords" });
const route = useRoute();
const router = useRouter();
const search = ref("");
const offset = ref(0);
const loading = ref(false);
const error = ref("");
const matches = ref<HistoryMatch[]>([]);
const players = ref<HistoryPlayer[]>([]);
const total = ref(0);
const matchDetail = ref<MatchDetail | null>(null);
const playerDetail = ref<PlayerDetail | null>(null);
let requestId = 0;
const section = computed(() =>
  route.path.startsWith("/history/matches") ? "matches" : "players"
);
const detail = computed(() =>
  Boolean(route.params.steamId || route.params.matchId)
);
const factionColor = (name: string | null) => factionDisplay(name).color;
const time = (value: string | null | undefined) =>
  value ? formatObservedAt(value) : t("仍在观测");
const scoreText = (match: HistoryMatch) =>
  match.scores?.map(item => `${item.name} ${item.score}`).join(" · ") ||
  t("未知");

async function refresh() {
  const current = ++requestId;
  loading.value = true;
  error.value = "";
  matchDetail.value = null;
  playerDetail.value = null;
  try {
    if (route.params.matchId)
      matchDetail.value = await getHistoryMatch(String(route.params.matchId));
    else if (route.params.steamId)
      playerDetail.value = await getHistoryPlayer(String(route.params.steamId));
    else if (section.value === "matches") {
      const page = await getHistoryMatches(offset.value);
      matches.value = page.items;
      total.value = page.total;
    } else {
      const page = await getHistoryPlayers(search.value, offset.value);
      players.value = page.items;
      total.value = page.total;
    }
  } catch (cause) {
    if (current === requestId) error.value = getApiErrorMessage(cause);
  } finally {
    if (current === requestId) loading.value = false;
  }
}
watch(
  () => route.fullPath,
  () => {
    offset.value = 0;
    void refresh();
  },
  { immediate: true }
);
function searchPlayers() {
  offset.value = 0;
  void refresh();
}
function pageChange(page: number) {
  offset.value = (page - 1) * 30;
  void refresh();
}
const playerPath = (id: string) => `/history/players/${id}`;
const matchPath = (id: string) => `/history/matches/${id}`;
</script>

<template>
  <div class="space-y-5 p-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-2xl font-semibold">
          {{
            detail
              ? section === "matches"
                ? $t("对局详情")
                : $t("历史玩家")
              : $t("历史记录")
          }}
        </h1>
        <p class="text-sm text-gray-500">
          {{
            $t("每 5 秒保存一次成功取得的 RCON 快照；时间和对局边界均为观测值")
          }}
        </p>
      </div>
      <el-button :loading="loading" @click="refresh">{{
        $t("刷新记录")
      }}</el-button>
    </div>
    <el-alert
      :title="
        $t(
          '记录从启用采集后开始；对局开始、结束与参与时间根据快照推断，可能漏掉短暂上线的玩家。服务器未提供最终胜方。'
        )
      "
      type="info"
      :closable="false"
    />
    <el-alert v-if="error" :title="error" type="warning" :closable="false" />
    <div class="flex gap-2">
      <el-button
        :type="section === 'players' && !detail ? 'primary' : 'default'"
        @click="router.push('/history/players')"
        >{{ $t("历史玩家") }}</el-button
      >
      <el-button
        :type="section === 'matches' && !detail ? 'primary' : 'default'"
        @click="router.push('/history/matches')"
        >{{ $t("历史对局") }}</el-button
      >
      <el-button v-if="detail" @click="router.back()">{{
        $t("返回")
      }}</el-button>
    </div>
    <el-card v-if="route.params.matchId" v-loading="loading" shadow="never">
      <template #header
        >{{ matchDetail?.map || $t("地图未知") }}
        {{ $t("· 对局详情") }}</template
      >
      <template v-if="matchDetail">
        <el-descriptions :column="2" border class="mb-5">
          <el-descriptions-item :label="$t('首次观测')">{{
            time(matchDetail.first_seen)
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('最后观测')">{{
            time(matchDetail.last_seen)
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('结束识别')">{{
            time(matchDetail.ended_seen)
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('地图')">{{
            matchDetail.map || $t("未知")
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('玩法')">{{
            matchDetail.experiences?.join("、") || $t("未知")
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('光照')">{{
            matchDetail.lighting || $t("未知")
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('最后比分')">{{
            scoreText(matchDetail)
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('峰值在线')">{{
            matchDetail.peak_players
          }}</el-descriptions-item>
        </el-descriptions>
        <h2 class="mb-3 font-semibold">
          {{ $t("观测到的玩家（") }}{{ matchDetail.players.length }}）
        </h2>
        <el-table :data="matchDetail.players" stripe>
          <el-table-column :label="$t('玩家')" min-width="160"
            ><template #default="{ row }"
              ><el-link
                type="primary"
                @click="router.push(playerPath(row.steam_id))"
                >{{ row.name }}</el-link
              ></template
            ></el-table-column
          >
          <el-table-column prop="steam_id" label="SteamID" min-width="170" />
          <el-table-column :label="$t('阵营')" min-width="110"
            ><template #default="{ row }"
              ><span :style="{ color: factionColor(row.faction) }">{{
                row.faction || $t("未知")
              }}</span></template
            ></el-table-column
          >
          <el-table-column
            prop="kills"
            :label="$t('击杀')"
            width="75"
          /><el-table-column prop="deaths" :label="$t('死亡')" width="75" />
          <el-table-column :label="$t('首次观测')" min-width="170"
            ><template #default="{ row }">{{
              time(row.first_seen)
            }}</template></el-table-column
          >
          <el-table-column :label="$t('最后观测')" min-width="170"
            ><template #default="{ row }">{{
              time(row.last_seen)
            }}</template></el-table-column
          >
        </el-table>
      </template>
    </el-card>
    <el-card
      v-else-if="route.params.steamId"
      v-loading="loading"
      shadow="never"
    >
      <template #header
        >{{ playerDetail?.name || $t("玩家") }} ·
        {{ playerDetail?.steamId }}</template
      >
      <p class="mb-3 text-sm text-gray-500">
        {{ $t("最近 100 场观测到的对局") }}
      </p>
      <el-table :data="playerDetail?.matches || []" stripe>
        <el-table-column :label="$t('对局')" min-width="160"
          ><template #default="{ row }"
            ><el-link
              type="primary"
              @click="router.push(matchPath(row.match_id))"
              >{{ row.map || $t("地图未知") }}</el-link
            ></template
          ></el-table-column
        >
        <el-table-column :label="$t('对局首次观测')" min-width="170"
          ><template #default="{ row }">{{
            time(row.match_first_seen)
          }}</template></el-table-column
        >
        <el-table-column :label="$t('玩家首次观测')" min-width="170"
          ><template #default="{ row }">{{
            time(row.first_seen)
          }}</template></el-table-column
        >
        <el-table-column :label="$t('阵营')" min-width="100"
          ><template #default="{ row }"
            ><span :style="{ color: factionColor(row.faction) }">{{
              row.faction || $t("未知")
            }}</span></template
          ></el-table-column
        >
        <el-table-column
          prop="kills"
          :label="$t('最后击杀')"
          width="95"
        /><el-table-column prop="deaths" :label="$t('最后死亡')" width="95" />
      </el-table>
    </el-card>
    <el-card
      v-else-if="section === 'players'"
      v-loading="loading"
      shadow="never"
    >
      <template #header>{{ $t("历史玩家（") }}{{ total }}）</template>
      <div class="mb-4 flex gap-2">
        <el-input
          v-model="search"
          :placeholder="$t('搜索昵称或 SteamID')"
          clearable
          class="max-w-sm"
          @keyup.enter="searchPlayers"
        /><el-button @click="searchPlayers">{{ $t("搜索") }}</el-button>
      </div>
      <el-table :data="players" stripe>
        <el-table-column :label="$t('玩家')" min-width="160"
          ><template #default="{ row }"
            ><el-link
              type="primary"
              @click="router.push(playerPath(row.steam_id))"
              >{{ row.name }}</el-link
            ></template
          ></el-table-column
        >
        <el-table-column prop="steam_id" label="SteamID" min-width="175" />
        <el-table-column
          prop="match_count"
          :label="$t('观测对局')"
          width="110"
        />
        <el-table-column :label="$t('首次观测')" min-width="170"
          ><template #default="{ row }">{{
            time(row.first_seen)
          }}</template></el-table-column
        >
        <el-table-column :label="$t('最后观测')" min-width="170"
          ><template #default="{ row }">{{
            time(row.last_seen)
          }}</template></el-table-column
        >
      </el-table>
      <el-pagination
        class="mt-4"
        layout="prev, pager, next"
        :total="total"
        :page-size="30"
        :current-page="offset / 30 + 1"
        @current-change="pageChange"
      />
    </el-card>
    <el-card v-else v-loading="loading" shadow="never">
      <template #header>{{ $t("历史对局（") }}{{ total }}）</template>
      <el-table :data="matches" stripe>
        <el-table-column :label="$t('地图')" min-width="170"
          ><template #default="{ row }"
            ><el-link type="primary" @click="router.push(matchPath(row.id))">{{
              row.map || $t("地图未知")
            }}</el-link></template
          ></el-table-column
        >
        <el-table-column :label="$t('首次观测')" min-width="170"
          ><template #default="{ row }">{{
            time(row.first_seen)
          }}</template></el-table-column
        >
        <el-table-column :label="$t('最后观测')" min-width="170"
          ><template #default="{ row }">{{
            time(row.last_seen)
          }}</template></el-table-column
        >
        <el-table-column
          prop="player_count"
          :label="$t('观测玩家')"
          width="105"
        />
        <el-table-column :label="$t('状态')" width="110"
          ><template #default="{ row }">{{
            row.ended_seen ? $t("已识别切换") : $t("观测中")
          }}</template></el-table-column
        >
      </el-table>
      <el-pagination
        class="mt-4"
        layout="prev, pager, next"
        :total="total"
        :page-size="30"
        :current-page="offset / 30 + 1"
        @current-change="pageChange"
      />
    </el-card>
  </div>
</template>
