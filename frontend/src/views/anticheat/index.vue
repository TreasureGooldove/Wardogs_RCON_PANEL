<script setup lang="ts">
import { ref, onMounted } from "vue";
import { ElMessageBox } from "element-plus";
import { http } from "@/utils/http";
import { t } from "@/i18n";
import { useUserStoreHook } from "@/store/modules/user";
import { getApiErrorMessage } from "@/api/errors";
import KillRecords from "@/components/KillRecords.vue";
import { getFeedSetup } from "@/api/community";

defineOptions({ name: "AntiCheat" });
type Rule = {
  enabled: boolean;
  seconds: number;
  distanceMeters: number;
  players: number;
  headshots: number;
  headshotPercent: number;
};
type Kind = "shot" | "cluster" | "burst" | "headshot" | "longshot";
type Settings = { enabled: boolean; action: string } & Record<Kind, Rule>;
type Finding = {
  fingerprint: string;
  name: string;
  steamId: string;
  rule: Kind;
  players: number;
  headshots: number;
  seconds: number;
  distanceMeters: number;
  headshotPercent: number | null;
};
type View = {
  settings: Settings;
  active: boolean;
  targetRevision: string;
  items: Finding[];
  coordinateSamples: number;
  eventSamples: number;
  receipts: {
    player: string;
    action: string;
    outcome: string;
    created_at: string;
  }[];
};
const data = ref<View>();
const busy = ref(false);
const error = ref("");
const owner = useUserStoreHook().role === "owner";
async function showFeedSetup() {
  await run(async () => {
    const setup = await getFeedSetup();
    await ElMessageBox.alert(
      setup.configured
        ? `${JSON.stringify(setup, null, 2)}\n\n${t("仅主账号可查看；请勿分享令牌。修改游戏配置后需重启游戏服务器。")}`
        : t("击杀推送尚未配置，请先配置服务器端推送令牌与目标地址。"),
      t("游戏推送配置"),
      { confirmButtonText: t("关闭") }
    );
  });
}
const rules: { key: Kind; label: string; explanation: string }[] = [
  {
    key: "shot",
    label: "近距离组合规则",
    explanation:
      "窗口内，每次射击距离不超过阈值；不同受害者数量和爆头数同时达到阈值。"
  },
  {
    key: "cluster",
    label: "区域聚集组合规则",
    explanation:
      "窗口内，受害者位置两两距离不超过阈值；人数和爆头数同时达标。缺少真实坐标不判定。"
  },
  {
    key: "burst",
    label: "短时连续击杀",
    explanation:
      "窗口内不同受害者人数及爆头数达到阈值；爆炸、多杀和高水平操作可能误判。"
  },
  {
    key: "headshot",
    label: "高爆头比例",
    explanation:
      "窗口内达到最小不同受害者样本，所有样本标签已知，爆头比例达到阈值。"
  },
  {
    key: "longshot",
    label: "远距离连续爆头",
    explanation:
      "窗口内，每次射击距离不低于阈值；不同受害者人数和爆头数同时达标。"
  }
];
async function run(work: () => Promise<void>) {
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
async function load() {
  data.value = await http.request<View>("get", "/api/anticheat");
}
async function save() {
  await run(async () => {
    if (!data.value) return;
    let password: string | undefined;
    if (data.value.settings.enabled) {
      const answer = await ElMessageBox.prompt(
        t(
          "实验性规则可能误判正常玩家。自动踢出或封禁可能影响玩家，异常不等于作弊。保存会重新开始采样；请审核阈值并输入主账号密码确认风险。"
        ),
        t("风险确认"),
        {
          inputType: "password",
          confirmButtonText: t("接受风险并保存"),
          cancelButtonText: t("取消")
        }
      );
      password = answer.value;
    }
    await http.request("put", "/api/anticheat/settings", {
      data: {
        ...data.value.settings,
        targetRevision: data.value.targetRevision,
        acknowledgeRisk: !!password,
        password
      }
    });
    await load();
  });
}
function label(key: Kind) {
  return t(rules.find(r => r.key === key)?.label || key);
}
function outcomeLabel(outcome: string) {
  const labels: Record<string, string> = {
    accepted: "已接受",
    rejected: "已拒绝",
    uncertain: "不确定",
    processing: "处理中"
  };
  return t(labels[outcome] || outcome);
}
onMounted(() => run(load));
</script>

<template>
  <main class="anti-page" v-loading="busy">
    <h2>{{ t("反作弊（实验性）") }}</h2>
    <el-alert
      type="warning"
      :closable="false"
      :title="
        t(
          '阈值只代表疑似异常，不构成作弊证据。所有规则和系统默认关闭；不建议直接启用自动封禁。'
        )
      "
    />
    <el-alert
      v-if="error"
      class="my-3"
      type="error"
      :closable="false"
      :title="error"
    />
    <div class="my-4">
      <el-button :loading="busy" @click="run(load)">{{ t("刷新") }}</el-button>
      <el-button v-if="owner" :disabled="busy" @click="showFeedSetup">
        {{ t("查看游戏推送配置") }}
      </el-button>
    </div>
    <template v-if="data">
      <p class="my-4">
        {{ t("真实击杀事件") }}：{{ data.eventSamples }} ·
        {{ t("坐标已知样本") }}：{{ data.coordinateSamples }}
      </p>
      <el-alert
        v-if="!data.coordinateSamples"
        type="info"
        :closable="false"
        :title="
          t(
            '区域聚集规则数据不足：推送需提供 victimPositionMeters（x/y/z，世界坐标，单位米）。不从射击距离猜测坐标。'
          )
        "
      />
      <el-form label-position="top" :disabled="!owner">
        <div class="controls">
          <el-form-item :label="t('系统开关')"
            ><el-switch v-model="data.settings.enabled"
          /></el-form-item>
          <el-form-item :label="t('命中后处理')"
            ><el-select v-model="data.settings.action">
              <el-option value="alert" :label="t('仅提示')" /><el-option
                value="kick"
                :label="t('踢出')"
              /><el-option value="ban" :label="t('封禁')" /> </el-select
          ></el-form-item>
        </div>
        <section v-for="rule in rules" :key="rule.key" class="rule-section">
          <h3>
            {{ t(rule.label) }}
            <el-switch v-model="data.settings[rule.key].enabled" />
          </h3>
          <p>{{ t(rule.explanation) }}</p>
          <div class="thresholds">
            <el-form-item :label="t('时间窗口（秒）')"
              ><el-input-number
                v-model="data.settings[rule.key].seconds"
                :min="1"
                :max="300"
            /></el-form-item>
            <el-form-item
              v-if="['shot', 'cluster', 'longshot'].includes(rule.key)"
              :label="t('距离阈值（米）')"
              ><el-input-number
                v-model="data.settings[rule.key].distanceMeters"
                :min="0"
                :max="10000"
            /></el-form-item>
            <el-form-item :label="t('不同受害者人数')"
              ><el-input-number
                v-model="data.settings[rule.key].players"
                :min="2"
                :max="100"
            /></el-form-item>
            <el-form-item :label="t('最小爆头数')"
              ><el-input-number
                v-model="data.settings[rule.key].headshots"
                :min="0"
                :max="data.settings[rule.key].players"
            /></el-form-item>
            <el-form-item
              v-if="rule.key === 'headshot'"
              :label="t('爆头比例阈值（%）')"
              ><el-input-number
                v-model="data.settings[rule.key].headshotPercent"
                :min="0"
                :max="100"
            /></el-form-item>
          </div>
        </section>
        <el-button v-if="owner" type="primary" :loading="busy" @click="save">{{
          t("保存设置")
        }}</el-button>
      </el-form>
      <p class="my-4">
        {{ t(data.active ? "系统已生效" : "系统未生效") }} ·
        {{
          t(
            "仅处理启用后收到的新事件；缺失时间、距离、爆头标签或坐标不猜测。同一玩家同一对局最多自动处罚一次，未知结果不重试。"
          )
        }}
      </p>
      <h3>{{ t("疑似异常与依据") }}</h3>
      <el-empty
        v-if="!data.items.length"
        :description="t('暂无命中或数据不足；不代表玩家已通过反作弊认证。')"
      />
      <section
        v-for="item in data.items"
        :key="item.fingerprint"
        class="finding"
        aria-live="polite"
      >
        <h4>
          {{
            t("疑似 {player} 存在异常", { player: item.name || item.steamId })
          }}
        </h4>
        <p>
          {{ label(item.rule) }} ·
          {{
            t("{seconds} 秒内击杀 {players} 名不同玩家，其中 {heads} 次爆头", {
              seconds: item.seconds,
              players: item.players,
              heads: item.headshots
            })
          }}
        </p>
        <p v-if="['shot', 'cluster', 'longshot'].includes(item.rule)">
          {{ t("距离阈值（米）") }}：{{ item.distanceMeters }} ·
          {{
            t(
              item.rule === "longshot"
                ? "射击距离不低于阈值"
                : item.rule === "cluster"
                  ? "受害者两两距离不超过阈值"
                  : "射击距离不超过阈值"
            )
          }}
        </p>
        <p v-if="item.rule === 'headshot'">
          {{ t("爆头比例（%）") }}：{{ item.headshotPercent?.toFixed(1) }}
        </p>
        <small>{{
          t("请结合录像、武器、爆炸多杀、延迟及事件数据质量人工复核。")
        }}</small>
      </section>
      <h3 class="my-4">{{ t("自动处理记录") }}</h3>
      <el-table :data="data.receipts"
        ><el-table-column
          prop="player"
          label="SteamID64"
          min-width="180" /><el-table-column :label="t('操作')"
          ><template #default="{ row }">{{
            t(row.action === "kick" ? "踢出" : "封禁")
          }}</template></el-table-column
        ><el-table-column :label="t('结果')"
          ><template #default="{ row }">{{
            outcomeLabel(row.outcome)
          }}</template></el-table-column
        ><el-table-column prop="created_at" :label="t('时间')" min-width="200"
      /></el-table>
    </template>
    <KillRecords match-id="all" class="my-6" />
  </main>
</template>

<style scoped>
.anti-page {
  padding: 20px;
}
h2,
h3 {
  margin-bottom: 16px;
}
.controls,
.thresholds {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
}
.rule-section,
.finding {
  border-top: 1px solid var(--el-border-color);
  padding: 20px 0;
}
.rule-section p,
.finding p {
  margin-bottom: 14px;
  color: var(--el-text-color-regular);
}
.controls {
  max-width: 540px;
}
@media (max-width: 600px) {
  .anti-page {
    padding: 12px;
  }
  .thresholds {
    grid-template-columns: 1fr;
  }
}
</style>
