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
  saveFactionRules,
  saveItemRules,
  type GameRulesView,
  type FactionSettings,
  type ItemSettings,
  type RuleReceipt
} from "@/api/gameRules";
import { getApiErrorMessage } from "@/api/errors";
import { useUserStoreHook } from "@/store/modules/user";
import { FACTIONS } from "@/utils/factions";
import { formatDate, locale, t } from "@/i18n";

defineOptions({ name: "GameRules" });
const data = ref<GameRulesView>();
const factionForm = ref<FactionSettings>();
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
  disabled: "自动控制已关闭",
  within_limits: "人数符合限制",
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
        processing: "处理中"
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
  // Refresh observations without discarding unsaved form edits.
  if (reset || !factionForm.value)
    factionForm.value = structuredClone(result.factions.settings);
  if (reset || !itemForm.value)
    itemForm.value = structuredClone(result.items.settings);
}
async function save(kind: "factions" | "items") {
  await run(async () => {
    if (!data.value || !factionForm.value || !itemForm.value) return;
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
                '每 5 秒检查一次。目标阵营人数相同时随机选择；人数上限为 0 表示不限。每次只转移一人，并遵守等待与操作间隔。'
              )
            "
          />
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
              <h3 :style="{ color: faction.color }">{{ faction.name }}</h3>
              <p>
                {{ t("在线人数") }}：{{
                  data.observation.observedAt
                    ? data.observation.counts[faction.name]
                    : "—"
                }}
              </p>
              <el-form label-position="top" :disabled="!owner || busy">
                <el-form-item :label="t('阵营人数上限')"
                  ><el-input-number
                    v-model="factionForm.limits[faction.name]"
                    :min="0"
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
