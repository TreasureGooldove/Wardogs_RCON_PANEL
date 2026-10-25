<script setup lang="ts">
import {
  computed,
  onActivated,
  onDeactivated,
  onMounted,
  onUnmounted,
  ref
} from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  getGameRules,
  previewFactionShuffle,
  executeFactionShuffle,
  getFactionShuffle,
  type ShuffleJob,
  saveFactionRules,
  saveItemRules,
  type GameRulesView,
  type FactionSettings,
  type FactionName,
  type ItemSettings,
  type RuleReceipt
} from "@/api/gameRules";
import { getApiErrorMessage } from "@/api/errors";
import { useUserStoreHook } from "@/store/modules/user";
import { FACTIONS } from "@/utils/factions";
import { fiftyVsFiftyDraft } from "@/utils/factionControl";
import FactionEmblem from "@/components/FactionEmblem.vue";
import { formatDate, locale, t } from "@/i18n";

defineOptions({ name: "GameRules" });
const data = ref<GameRulesView>();
const factionForm = ref<FactionSettings>();
const excludedFaction = ref<FactionName>("Lonestar");
const shuffle = ref<ShuffleJob>();
const shuffleVisible = ref(false);
const shuffleRevision = ref("");
const shuffleCounts = computed(() =>
  FACTIONS.map(faction => ({
    name: faction.name,
    count: shuffle.value?.plan.filter(p => p.to === faction.name).length || 0
  }))
);
const shufflePhases: Record<string, string> = {
  preview: "待确认",
  running: "正在打乱",
  completed: "已完成并核对名单",
  stopped: "已停止",
  uncertain: "部分结果不确定，请核查"
};
async function previewShuffle() {
  await run(async () => {
    if (!data.value) return;
    shuffleRevision.value = data.value.targetRevision;
    shuffle.value = await previewFactionShuffle(shuffleRevision.value);
    shuffleVisible.value = true;
  });
}
async function executeShuffle() {
  await run(async () => {
    if (!shuffle.value) return;
    const answer = await ElMessageBox.prompt(
      t(
        "将按预览逐人转移，可能影响战斗，期间人数可能短暂偏差。不会击杀或踢出玩家，也不能自动撤销。请输入主账号密码确认。"
      ),
      t("确认随机打乱"),
      {
        inputType: "password",
        confirmButtonText: t("确认执行"),
        cancelButtonText: t("取消")
      }
    );
    shuffle.value = await executeFactionShuffle(shuffle.value.id, {
      targetRevision: shuffleRevision.value,
      acknowledgeRisk: true,
      password: answer.value
    });
  });
}
const lastOpenLimits: Partial<Record<FactionName, number>> = {};
function setFactionOpen(name: FactionName, open: boolean) {
  if (!factionForm.value) return;
  const limits = factionForm.value.limits;
  if (!open && limits[name] !== 0) lastOpenLimits[name] = limits[name];
  limits[name] = open ? (lastOpenLimits[name] ?? -1) : 0;
}
function applyFiftyVsFifty() {
  if (factionForm.value)
    factionForm.value = fiftyVsFiftyDraft(
      factionForm.value,
      excludedFaction.value
    );
}
const itemForm = ref<ItemSettings>();
const selectedItem = ref("");
const busy = ref(false);
const initialized = ref(false);
const error = ref("");
const owner = computed(() => useUserStoreHook().role === "owner");
const tab = ref("factions");
const catalog = computed(() => data.value?.catalog.items || []);
const catalogGroups = computed(() => [
  {
    kind: "weapons",
    label: "武器",
    items: catalog.value.filter(i => i.kind === "weapons")
  },
  {
    kind: "equipment",
    label: "装备",
    items: catalog.value.filter(i => i.kind === "equipment")
  }
]);
const states: Record<string, string> = {
  no_samples: "尚无人数采样",
  shuffle_running: "随机打乱执行中，自动平衡暂时暂停",
  disabled: "自动控制已关闭",
  within_limits: "人数符合限制",
  faction_closed: "关闭的阵营存在玩家，等待转移",
  below_minimum: "未达到控制最低人数",
  unknown_faction: "阵营数据不完整，暂停转移",
  no_destination: "没有可容纳玩家的目标阵营",
  waiting_stability: "等待超限持续时间",
  over_limit: "阵营人数超过上限",
  unbalanced: "阵营人数差超过限制",
  no_actionable_player: "缺少可操作的玩家标识"
};
function itemLabel(id: string) {
  const item = catalog.value.find(i => i.id === id);
  return item
    ? `${locale.value.startsWith("zh") ? item.name : item.englishName} (${item.id})`
    : id;
}
function receiptLabel(row: RuleReceipt) {
  return row.details.itemId
    ? itemLabel(row.details.itemId)
    : `${row.details.from || ""} → ${row.details.to || ""}`;
}
function outcome(value: string) {
  return t(
    (
      {
        accepted: "已接受",
        rejected: "已拒绝",
        uncertain: "不确定",
        processing: "处理中",
        queued: "等待执行",
        unchanged: "保持阵营",
        skipped: "已跳过"
      } as Record<string, string>
    )[value] || value
  );
}
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
async function load(reset = false) {
  const result = await getGameRules();
  data.value = result;
  if (!shuffle.value && result.latestShuffle) {
    shuffle.value = result.latestShuffle;
    shuffleRevision.value = result.targetRevision;
  }
  if (shuffle.value && shuffle.value.phase === "running")
    shuffle.value = await getFactionShuffle(shuffle.value.id);
  // Refresh observations without discarding unsaved form edits.
  if (reset || !factionForm.value)
    factionForm.value = structuredClone(result.factions.settings);
  if (reset || !itemForm.value)
    itemForm.value = structuredClone(result.items.settings);
}
async function save(kind: "factions" | "items") {
  await run(async () => {
    if (!data.value || !factionForm.value || !itemForm.value) return;
    if (
      kind === "factions" &&
      factionForm.value.enabled &&
      Object.values(factionForm.value.limits).every(limit => limit === 0)
    ) {
      ElMessage.warning(t("启用控制前请至少开放一个阵营。"));
      return;
    }
    const settings = kind === "factions" ? factionForm.value : itemForm.value;
    let password: string | undefined;
    if (settings.enabled) {
      const answer = await ElMessageBox.prompt(
        t(
          kind === "factions"
            ? "启用后面板会自动转移超限阵营的玩家，可能影响正在进行的战斗。请输入主账号密码确认。"
            : "启用后，匹配禁用物品的新事件会触发强制击杀。击杀原因不能检测背包或穿戴；延迟推送可能影响已复活玩家。请核对映射并输入主账号密码确认。"
        ),
        t("确认自动操作"),
        {
          inputType: "password",
          confirmButtonText: t("确认并保存"),
          cancelButtonText: t("取消")
        }
      );
      password = answer.value;
    }
    const confirmation = {
      targetRevision: data.value.targetRevision,
      acknowledgeRisk: !!password,
      password
    };
    if (kind === "factions")
      await saveFactionRules({ ...factionForm.value, ...confirmation });
    else await saveItemRules({ ...itemForm.value, ...confirmation });
    // Reconcile the saved section only, preserving edits in the other tab.
    const result = await getGameRules();
    data.value = result;
    if (kind === "factions")
      factionForm.value = structuredClone(result.factions.settings);
    else itemForm.value = structuredClone(result.items.settings);
    ElMessage.success(t("设置已保存"));
  });
}
function addItem() {
  if (
    !itemForm.value ||
    !selectedItem.value ||
    itemForm.value.items.some(i => i.itemId === selectedItem.value)
  )
    return;
  itemForm.value.items.push({ itemId: selectedItem.value, killCauses: [] });
  selectedItem.value = "";
}
let timer: ReturnType<typeof setInterval> | undefined;
let visible = true;
function poll() {
  if (visible && !document.hidden && !busy.value) void run(() => load());
}
function visibilityChanged() {
  if (!document.hidden) poll();
}
onMounted(() => {
  // Mount numeric controls after loading so their initial accessibility state is correct.
  void run(() => load()).finally(() => {
    initialized.value = true;
  });
  timer = setInterval(poll, 5000);
  document.addEventListener("visibilitychange", visibilityChanged);
});
onActivated(() => {
  visible = true;
  if (data.value) poll();
});
onDeactivated(() => {
  visible = false;
});
onUnmounted(() => {
  clearInterval(timer);
  document.removeEventListener("visibilitychange", visibilityChanged);
});
</script>

<template>
  <main class="game-rules-page">
    <header class="page-header">
      <div>
        <h2>{{ t("阵营与装备限制") }}</h2>
        <p>{{ t("面板自动规则，不修改游戏服务器配置文件") }}</p>
      </div>
      <el-button :loading="busy" @click="run(() => load())">{{
        t("刷新")
      }}</el-button>
    </header>
    <el-alert
      type="warning"
      :closable="false"
      :title="
        t(
          '试验性功能：阵营人数控制及武器、装备限制仍在验证中。出现误判、数据异常或执行问题时，请及时停用并反馈。'
        )
      "
      class="my-3"
    />
    <el-alert
      v-if="error"
      type="error"
      :closable="false"
      :title="error"
      class="my-3"
    />
    <el-alert
      v-if="!owner"
      type="info"
      :closable="false"
      :title="t('仅主账号可配置自动规则，子用户只能查看。')"
    />
    <template v-if="initialized && data && factionForm && itemForm">
      <el-alert
        v-if="!data.historyEnabled"
        type="warning"
        :closable="false"
        :title="t('历史采样未启用，自动规则不能启动。')"
      />
      <el-tabs v-model="tab" class="my-4">
        <el-tab-pane :label="t('阵营人数控制')" name="factions">
          <el-alert
            type="info"
            :closable="false"
            :title="
              t(
                '每 5 秒检查一次。0 人表示关闭阵营，-1 表示不限；只在开放阵营之间平衡，目标人数相同时随机选择。每次转移一人，并遵守等待与操作间隔。'
              )
            "
          />
          <el-card shadow="never" class="my-4">
            <template #header>{{ t("50V50 双阵营模式") }}</template>
            <div class="flex flex-wrap items-end gap-3">
              <el-form label-position="top" :disabled="!owner || busy">
                <el-form-item :label="t('关闭的阵营')" class="!mb-0">
                  <el-select v-model="excludedFaction" class="!w-64">
                    <el-option
                      v-for="faction in FACTIONS"
                      :key="faction.name"
                      :label="faction.name"
                      :value="faction.name"
                    />
                  </el-select>
                </el-form-item>
              </el-form>
              <el-button
                :disabled="!owner || busy"
                @click="applyFiftyVsFifty"
                >{{ t("填入 50V50 草稿") }}</el-button
              >
            </div>
            <p class="mt-3 text-sm text-gray-500">
              {{
                t(
                  "选定阵营上限为 0，其余两个阵营各 50 人，最大人数差为 1。只修改当前草稿，保存并确认启用后生效。"
                )
              }}
            </p>
            <p class="mt-2 text-sm text-gray-500">
              {{
                t(
                  "由面板定期转移，不会在游戏选阵营界面直接禁选。关闭阵营的玩家不受最低在线人数门槛限制；开放阵营都满员时暂停转移，不踢出玩家。"
                )
              }}
            </p>
            <el-button
              v-if="owner"
              class="mt-3"
              type="warning"
              :disabled="busy || shuffle?.phase === 'running'"
              @click="previewShuffle"
              >{{ t("预览随机打乱双方") }}</el-button
            >
            <p class="mt-2 text-sm text-gray-500">
              {{
                t(
                  "使用已保存的两个开放阵营，随机重分在线玩家并尽量均分。需先保存规则；预览不执行转移。"
                )
              }}
            </p>
            <el-button
              v-if="shuffle"
              link
              class="mt-2"
              @click="shuffleVisible = true"
              >{{ t(shufflePhases[shuffle.phase]) }}</el-button
            >
          </el-card>
          <el-alert
            v-if="data.factions.blocked || data.factions.requiresRearm"
            type="warning"
            :closable="false"
            :title="
              t(
                '规则已暂停：结果不确定或服务器目标变化，请核查后重新保存启用。'
              )
            "
            class="my-3"
          />
          <div class="faction-grid">
            <el-card
              v-for="faction in FACTIONS"
              :key="faction.name"
              shadow="never"
              :style="{ borderTop: `3px solid ${faction.color}` }"
            >
              <h3
                class="flex items-center gap-2"
                :style="{ color: faction.color }"
              >
                <FactionEmblem
                  :faction="faction.code"
                  class="h-9 w-9 shrink-0"
                />
                {{ faction.name }}
              </h3>
              <p>
                {{ t("在线人数") }}：{{
                  data.observation.observedAt
                    ? data.observation.counts[faction.name]
                    : "—"
                }}
              </p>
              <el-form label-position="top" :disabled="!owner || busy">
                <el-form-item :label="t('允许加入')">
                  <el-switch
                    :model-value="factionForm.limits[faction.name] !== 0"
                    :aria-label="t('允许加入 {p0}', { p0: faction.name })"
                    @change="setFactionOpen(faction.name, Boolean($event))"
                  />
                </el-form-item>
                <el-form-item :label="t('阵营人数上限')"
                  ><el-input-number
                    v-model="factionForm.limits[faction.name]"
                    :min="-1"
                    :max="1000"
                /></el-form-item>
              </el-form>
            </el-card>
          </div>
          <el-form
            label-position="top"
            :disabled="!owner || busy"
            class="control-grid"
          >
            <el-form-item :label="t('启用阵营控制')"
              ><el-switch v-model="factionForm.enabled"
            /></el-form-item>
            <el-form-item :label="t('同时限制阵营人数差')"
              ><el-switch v-model="factionForm.balanceEnabled"
            /></el-form-item>
            <el-form-item :label="t('最大阵营人数差')"
              ><el-input-number
                v-model="factionForm.maxDifference"
                :disabled="!factionForm.balanceEnabled"
                :min="1"
                :max="1000"
            /></el-form-item>
            <el-form-item :label="t('控制最低在线人数')"
              ><el-input-number
                v-model="factionForm.minimumPlayers"
                :min="0"
                :max="1000"
            /></el-form-item>
            <el-form-item :label="t('超限持续时间（秒）')"
              ><el-input-number
                v-model="factionForm.stableSeconds"
                :min="0"
                :max="300"
            /></el-form-item>
            <el-form-item :label="t('转移间隔（秒）')"
              ><el-input-number
                v-model="factionForm.cooldownSeconds"
                :min="5"
                :max="600"
            /></el-form-item>
          </el-form>
          <p>
            {{ t(data.factions.active ? "自动控制已启用" : "自动控制未启用") }}
            ·
            {{ t(states[data.observation.state] || data.observation.state) }} ·
            {{ formatDate(data.observation.observedAt) }}
          </p>
          <el-button
            v-if="owner"
            type="primary"
            :loading="busy"
            @click="save('factions')"
            >{{ t("保存阵营规则") }}</el-button
          >
        </el-tab-pane>
        <el-tab-pane :label="t('武器与装备禁用')" name="items">
          <el-alert
            type="warning"
            :closable="false"
            :title="
              t(
                '官方玩家名单不提供背包或穿戴装备。击杀原因仅能检测已产生击杀的武器；装备检测需要事件生产端额外发送 itemUsed，不代表服务器已支持。'
              )
            "
          />
          <el-alert
            v-if="!data.feedConfigured"
            type="error"
            :closable="false"
            :title="t('游戏事件推送未配置，物品规则不能启动。')"
            class="my-3"
          />
          <el-alert
            v-if="data.items.blocked || data.items.requiresRearm"
            type="warning"
            :closable="false"
            :title="
              t(
                '规则已暂停：结果不确定或服务器目标变化，请核查后重新保存启用。'
              )
            "
            class="my-3"
          />
          <p>
            {{ t("装备扩展事件最近接收") }}：{{
              data.evidence.lastItemUsedAt
                ? formatDate(data.evidence.lastItemUsedAt)
                : t("尚无数据")
            }}
          </p>
          <el-form
            label-position="top"
            :disabled="!owner || busy"
            class="control-grid"
          >
            <el-form-item :label="t('启用禁用物品强制击杀')"
              ><el-switch v-model="itemForm.enabled"
            /></el-form-item>
            <el-form-item :label="t('同一玩家击杀间隔（秒）')"
              ><el-input-number
                v-model="itemForm.cooldownSeconds"
                :min="5"
                :max="600"
            /></el-form-item>
          </el-form>
          <div v-if="owner" class="item-picker">
            <el-select
              v-model="selectedItem"
              filterable
              clearable
              :disabled="busy"
              :placeholder="t('搜索并选择武器或装备')"
            >
              <el-option-group
                v-for="group in catalogGroups"
                :key="group.kind"
                :label="t(group.label)"
              >
                <el-option
                  v-for="item in group.items"
                  :key="item.id"
                  :value="item.id"
                  :label="itemLabel(item.id)"
                  :disabled="itemForm.items.some(i => i.itemId === item.id)"
                />
              </el-option-group>
            </el-select>
            <el-button :disabled="!selectedItem || busy" @click="addItem">{{
              t("添加禁用物品")
            }}</el-button>
          </div>
          <p>
            {{
              t(
                "击杀原因必须与实机 cause 精确对应，仅忽略大小写与首尾空格。不根据物品名称猜测；留空时只匹配 itemUsed 的 itemId。"
              )
            }}
          </p>
          <el-table :data="itemForm.items" class="my-3">
            <el-table-column :label="t('禁用物品')" min-width="230"
              ><template #default="scope">{{
                itemLabel(scope.row.itemId)
              }}</template></el-table-column
            >
            <el-table-column :label="t('实机击杀原因映射')" min-width="330">
              <template #default="scope">
                <el-select
                  v-model="scope.row.killCauses"
                  multiple
                  filterable
                  allow-create
                  :disabled="!owner || busy"
                  :placeholder="t('选择已收到的 cause 或输入精确值')"
                >
                  <el-option
                    v-for="cause in data.evidence.causes"
                    :key="cause.cause"
                    :value="cause.cause"
                    :label="`${cause.cause} (${cause.samples})`"
                  />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column v-if="owner" :label="t('操作')" width="90"
              ><template #default="scope"
                ><el-button
                  link
                  type="danger"
                  :disabled="busy"
                  @click="itemForm.items.splice(scope.$index, 1)"
                  >{{ t("移除") }}</el-button
                ></template
              ></el-table-column
            >
          </el-table>
          <p>
            <a
              href="https://wardogs.t0ki.cn/weapons.html"
              target="_blank"
              rel="noopener noreferrer"
              >{{ t("物品列表来源：WARDOGS 小助手") }}</a
            >
            · {{ t("数据日期") }}：{{ data.catalog.sourceUpdatedAt }} ·
            {{ t("同步日期") }}：{{ data.catalog.retrievedAt }}
          </p>
          <p>
            {{
              t(
                data.items.active
                  ? "物品规则已启用，等待匹配事件"
                  : "物品规则未启用"
              )
            }}
          </p>
          <el-button
            v-if="owner"
            type="primary"
            :loading="busy"
            @click="save('items')"
            >{{ t("保存物品规则") }}</el-button
          >
        </el-tab-pane>
      </el-tabs>
      <el-card shadow="never" class="my-4">
        <template #header>{{ t("自动处理记录") }}</template>
        <el-table
          :data="
            tab === 'factions' ? data.factions.receipts : data.items.receipts
          "
        >
          <el-table-column prop="steam_id" label="SteamID64" min-width="185" />
          <el-table-column :label="t('规则依据')" min-width="230"
            ><template #default="scope">{{
              receiptLabel(scope.row)
            }}</template></el-table-column
          >
          <el-table-column :label="t('结果')" width="100"
            ><template #default="scope">{{
              outcome(scope.row.outcome)
            }}</template></el-table-column
          >
          <el-table-column prop="error" :label="t('错误码')" min-width="150" />
          <el-table-column :label="t('时间')" min-width="185"
            ><template #default="scope">{{
              formatDate(scope.row.created_at)
            }}</template></el-table-column
          >
        </el-table>
      </el-card>
    </template>
    <el-dialog
      v-model="shuffleVisible"
      :title="t('随机打乱双方')"
      width="min(900px, 95vw)"
    >
      <template v-if="shuffle">
        <el-alert
          v-if="error"
          type="error"
          :closable="false"
          :title="error"
          class="my-3"
        />
        <el-alert
          type="warning"
          :closable="false"
          :title="
            t(
              '预览 60 秒内有效。执行期间自动平衡暂停；名单、阵营、对局或规则变化，以及失败或超时，均会停止后续操作。顶部停止连接可中止后续转移。'
            )
          "
        />
        <p class="my-3">
          {{ t(shufflePhases[shuffle.phase]) }} · {{ t("预览到期") }}：{{
            formatDate(shuffle.expires)
          }}
        </p>
        <p class="my-3">
          {{
            shuffleCounts.map(row => `${row.name}: ${row.count}`).join(" / ")
          }}
        </p>
        <el-alert
          v-if="shuffle.error"
          type="error"
          :closable="false"
          :title="shuffle.error"
          class="my-3"
        />
        <el-table :data="shuffle.plan" max-height="400">
          <el-table-column prop="name" :label="t('玩家')" min-width="150" />
          <el-table-column prop="steamId" label="SteamID64" min-width="180" />
          <el-table-column prop="from" :label="t('原阵营')" min-width="110" />
          <el-table-column prop="to" :label="t('目标阵营')" min-width="110" />
          <el-table-column :label="t('结果')" min-width="110"
            ><template #default="scope">{{
              outcome(scope.row.outcome)
            }}</template></el-table-column
          >
          <el-table-column prop="error" :label="t('错误码')" min-width="140" />
        </el-table>
      </template>
      <template #footer>
        <el-button @click="shuffleVisible = false">{{ t("关闭") }}</el-button>
        <el-button
          v-if="shuffle?.phase === 'preview'"
          type="danger"
          :loading="busy"
          @click="executeShuffle"
          >{{ t("输入密码并执行") }}</el-button
        >
        <el-button
          v-else-if="shuffle"
          :disabled="busy"
          @click="
            run(async () => {
              if (shuffle) shuffle = await getFactionShuffle(shuffle.id);
            })
          "
          >{{ t("刷新") }}</el-button
        >
      </template>
    </el-dialog>
  </main>
</template>

<style scoped>
.game-rules-page {
  padding: 24px;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}
.page-header p,
.game-rules-page > p {
  color: var(--el-text-color-secondary);
}
.faction-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin: 20px 0;
}
.control-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0 20px;
  margin-top: 20px;
}
.item-picker {
  display: flex;
  gap: 12px;
  margin: 16px 0;
}
.item-picker .el-select {
  max-width: 560px;
  flex: 1;
}
@media (max-width: 900px) {
  .control-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 600px) {
  .game-rules-page {
    padding: 12px;
  }
  .faction-grid,
  .control-grid {
    grid-template-columns: 1fr;
  }
  .item-picker {
    flex-direction: column;
  }
}
</style>
