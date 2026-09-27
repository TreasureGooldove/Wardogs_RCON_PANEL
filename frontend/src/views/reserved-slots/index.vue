<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
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

defineOptions({ name: "ReservedSlots" });

const snapshot = ref<ReservedSlots | null>(null);
const userStore = useUserStoreHook();
const canManage = computed(() => userStore.role === "owner" || userStore.permissions.includes("reserved"));
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

onMounted(refresh);
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
