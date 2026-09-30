<script setup lang="ts">
import { t } from "@/i18n";
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  createSubuser,
  getSubusers,
  resetSubuserPassword,
  updateSubuser,
  type Subuser,
  type UpdateSubuser
} from "@/api/subusers";
import { getApiErrorMessage } from "@/api/errors";
import { formatObservedAt } from "@/api/snapshot";
import { useUserStoreHook } from "@/store/modules/user";

defineOptions({ name: "Subusers" });

const userStore = useUserStoreHook();
const rows = ref<Subuser[]>([]);
const loading = ref(false);
const saving = ref(false);
const pendingId = ref<string | null>(null);
const error = ref("");
const createVisible = ref(false);
const permissionVisible = ref(false);
const permissionAccount = ref<Subuser | null>(null);
const selectedPermissions = ref<string[]>([]);
const generatedPassword = ref("");
const passwordWasGenerated = ref(false);
const permissionOptions = [
  ["unban", t("解除封禁")],
  ["kill", t("击杀角色")],
  ["message", t("私聊玩家")],
  ["warning", t("警告玩家")],
  ["changeFaction", t("切换阵营")],
  ["broadcast", t("全服公告")],
  ["changeMap", t("切换地图")],
  ["endMatch", t("结束比赛")],
  ["restartMatch", t("重开比赛")],
  ["setLighting", t("切换光照")],
  ["reserved", t("管理预留位")],
  ["config", t("编辑服务器配置")],
  ["rules", t("管理服规播报")]
] as const;
const createForm = reactive({
  username: "",
  password: "",
  passwordConfirm: "",
  canKick: false,
  canBan: false,
  permissions: [] as string[]
});

function clearCreateForm() {
  createForm.username = "";
  createForm.password = "";
  createForm.passwordConfirm = "";
  createForm.canKick = false;
  createForm.canBan = false;
  createForm.permissions = [];
  passwordWasGenerated.value = false;
}

function makePassword() {
  const alphabet =
    "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%";
  let result = "";
  do {
    const bytes = crypto.getRandomValues(new Uint8Array(24));
    result = Array.from(bytes, value => alphabet[value % alphabet.length]).join(
      ""
    );
  } while (
    !/[A-Z]/.test(result) ||
    !/[a-z]/.test(result) ||
    !/[0-9]/.test(result) ||
    !/[!@#$%]/.test(result)
  );
  createForm.password = result;
  createForm.passwordConfirm = result;
  passwordWasGenerated.value = true;
}

async function copyGeneratedPassword() {
  try {
    await navigator.clipboard.writeText(generatedPassword.value);
    ElMessage.success(t("密码已复制"));
  } catch {
    ElMessage.warning(t("复制失败，请手动复制密码"));
  }
}

function editPermissions(row: Subuser) {
  permissionAccount.value = row;
  selectedPermissions.value = [...row.permissions];
  permissionVisible.value = true;
}

async function savePermissions() {
  const row = permissionAccount.value;
  if (!row) return;
  await patchSubuser(
    row,
    { permissions: selectedPermissions.value },
    t("管理权限已更新")
  );
  if (!error.value) permissionVisible.value = false;
}

function openCreate() {
  clearCreateForm();
  createVisible.value = true;
}

async function load() {
  if (loading.value || userStore.role !== "owner") return;
  loading.value = true;
  error.value = "";
  try {
    rows.value = await getSubusers();
  } catch (reason) {
    error.value = getApiErrorMessage(reason);
  } finally {
    loading.value = false;
  }
}

async function create() {
  if (saving.value || userStore.role !== "owner") return;
  const username = createForm.username.trim();
  if (username.length < 3 || username.length > 64) {
    ElMessage.error(t("用户名需为 3–64 个字符"));
    return;
  }
  if (createForm.password.length < 12 || createForm.password.length > 256) {
    ElMessage.error(t("密码需为 12–256 个字符"));
    return;
  }
  if (createForm.password !== createForm.passwordConfirm) {
    ElMessage.error(t("两次输入的密码不一致"));
    return;
  }

  saving.value = true;
  error.value = "";
  try {
    const created = await createSubuser({
      username,
      password: createForm.password,
      canKick: createForm.canKick,
      canBan: createForm.canBan,
      permissions: createForm.permissions
    });
    rows.value = [...rows.value, created];
    if (passwordWasGenerated.value)
      generatedPassword.value = createForm.password;
    createVisible.value = false;
    clearCreateForm();
    ElMessage.success(t("子用户已创建"));
  } catch (reason) {
    error.value = getApiErrorMessage(reason);
  } finally {
    saving.value = false;
  }
}

async function patchSubuser(
  row: Subuser,
  update: UpdateSubuser,
  successMessage: string
) {
  if (pendingId.value || userStore.role !== "owner") return;
  pendingId.value = row.id;
  error.value = "";
  try {
    const updated = await updateSubuser(row.id, update);
    rows.value = rows.value.map(item => (item.id === row.id ? updated : item));
    ElMessage.success(successMessage + t("；该账号需重新登录"));
  } catch (reason) {
    error.value = getApiErrorMessage(reason);
  } finally {
    pendingId.value = null;
  }
}

async function setPermission(
  row: Subuser,
  permission: "canKick" | "canBan",
  enabled: boolean
) {
  if (pendingId.value) return;
  if (permission === "canBan" && enabled) {
    try {
      await ElMessageBox.confirm(
        t("允许“") +
          row.username +
          t("”永久封禁玩家？授予后该账号可对真实服务器执行封禁。"),
        t("确认封禁权限"),
        {
          type: "warning",
          confirmButtonText: t("确认授权"),
          cancelButtonText: t("取消")
        }
      );
    } catch {
      return;
    }
  }
  await patchSubuser(
    row,
    { [permission]: enabled },
    enabled ? t("管理权限已授予") : t("管理权限已取消")
  );
}

async function setEnabled(row: Subuser, enabled: boolean) {
  if (pendingId.value) return;
  if (!enabled) {
    try {
      await ElMessageBox.confirm(
        t("禁用“") +
          row.username +
          t("”后，该账号的现有登录会话会立即失效。是否继续？"),
        t("确认禁用子用户"),
        {
          type: "warning",
          confirmButtonText: t("确认禁用"),
          cancelButtonText: t("取消")
        }
      );
    } catch {
      return;
    }
  }
  await patchSubuser(
    row,
    { disabled: !enabled },
    enabled ? t("账号已启用") : t("账号已禁用")
  );
}

async function resetPassword(row: Subuser) {
  if (pendingId.value || userStore.role !== "owner") return;
  let password: string;
  try {
    const answer = await ElMessageBox.prompt(
      t("为“") +
        row.username +
        t("”设置新密码（12–256 个字符）。重设后该账号现有会话会立即失效。"),
      t("重设子用户密码"),
      {
        type: "warning",
        inputType: "password",
        inputPlaceholder: t("输入新密码"),
        inputValidator: value =>
          value.length >= 12 && value.length <= 256
            ? true
            : t("密码需为 12–256 个字符"),
        confirmButtonText: t("确认重设"),
        cancelButtonText: t("取消")
      }
    );
    password = answer.value;
  } catch {
    return;
  }
  pendingId.value = row.id;
  error.value = "";
  try {
    await resetSubuserPassword(row.id, password);
    ElMessage.success(t("密码已重设；该账号需使用新密码重新登录"));
  } catch (reason) {
    error.value = getApiErrorMessage(reason);
  } finally {
    password = "";
    pendingId.value = null;
  }
}

onMounted(load);
</script>

<template>
  <div class="p-5 space-y-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-2xl font-semibold">{{ $t("子用户") }}</h1>
        <p class="text-sm text-gray-500">
          {{ $t("按操作分别授权协作账号，默认只读") }}
        </p>
      </div>
      <div class="flex gap-2">
        <el-button :loading="loading" @click="load">{{
          $t("刷新列表")
        }}</el-button>
        <el-button type="primary" @click="openCreate">{{
          $t("新建子用户")
        }}</el-button>
      </div>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert
      :title="
        $t(
          '权限、启停或密码变更后，目标子用户的现有登录会话会失效，需要重新登录。'
        )
      "
      type="info"
      :closable="false"
    />

    <el-card shadow="never">
      <el-skeleton v-if="loading && rows.length === 0" :rows="5" animated />
      <el-empty v-else-if="rows.length === 0" :description="$t('暂无子用户')" />
      <el-table v-else :data="rows" row-key="id" border>
        <el-table-column
          prop="username"
          :label="$t('用户名')"
          min-width="150"
        />
        <el-table-column :label="$t('账号状态')" width="120">
          <template #default="{ row }">
            <el-switch
              :model-value="!row.disabled"
              :active-text="$t('启用')"
              :inactive-text="$t('禁用')"
              :disabled="Boolean(pendingId)"
              @change="setEnabled(row, Boolean($event))"
            />
          </template>
        </el-table-column>
        <el-table-column :label="$t('踢出权限')" width="120">
          <template #default="{ row }">
            <el-switch
              :model-value="row.canKick"
              :disabled="Boolean(pendingId)"
              @change="setPermission(row, 'canKick', Boolean($event))"
            />
          </template>
        </el-table-column>
        <el-table-column :label="$t('封禁权限')" width="120">
          <template #default="{ row }">
            <el-switch
              :model-value="row.canBan"
              :disabled="Boolean(pendingId)"
              @change="setPermission(row, 'canBan', Boolean($event))"
            />
          </template>
        </el-table-column>
        <el-table-column :label="$t('创建时间')" min-width="175">
          <template #default="{ row }">{{
            formatObservedAt(row.createdAt)
          }}</template>
        </el-table-column>
        <el-table-column :label="$t('管理')" width="130" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              :disabled="Boolean(pendingId)"
              @click="editPermissions(row)"
              >{{ $t("详细权限") }}</el-button
            >
            <el-button
              link
              type="primary"
              :disabled="Boolean(pendingId)"
              @click="resetPassword(row)"
              >{{ $t("重设密码") }}</el-button
            >
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="createVisible"
      :title="$t('新建子用户')"
      width="min(92vw, 520px)"
      :close-on-click-modal="false"
      @closed="clearCreateForm"
    >
      <el-form label-position="top" @submit.prevent="create">
        <el-form-item :label="$t('用户名')">
          <el-input
            v-model="createForm.username"
            maxlength="64"
            autocomplete="off"
            :placeholder="$t('3–64 个字符')"
          />
        </el-form-item>
        <el-form-item :label="$t('密码')">
          <el-button class="mb-2" @click="makePassword">{{
            $t("生成随机密码")
          }}</el-button>
          <el-input
            v-model="createForm.password"
            type="password"
            show-password
            autocomplete="new-password"
            :placeholder="$t('至少 12 个字符')"
          />
        </el-form-item>
        <el-form-item :label="$t('确认密码')">
          <el-input
            v-model="createForm.passwordConfirm"
            type="password"
            autocomplete="new-password"
            :placeholder="$t('再次输入密码')"
          />
        </el-form-item>
        <el-form-item :label="$t('可用权限')">
          <div class="flex flex-wrap gap-5">
            <el-checkbox v-model="createForm.canKick">{{
              $t("允许踢出玩家")
            }}</el-checkbox>
            <el-checkbox v-model="createForm.canBan">{{
              $t("允许永久封禁玩家")
            }}</el-checkbox>
          </div>
          <el-checkbox-group
            v-model="createForm.permissions"
            class="grid w-full grid-cols-2 gap-x-3"
          >
            <el-checkbox
              v-for="[key, label] in permissionOptions"
              :key="key"
              :label="key"
              >{{ label }}</el-checkbox
            >
          </el-checkbox-group>
          <div class="w-full text-xs text-gray-500">
            {{ $t("默认均不勾选，仅可查看服务器数据。") }}
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">{{ $t("取消") }}</el-button>
        <el-button type="primary" :loading="saving" @click="create">{{
          $t("创建")
        }}</el-button>
      </template>
    </el-dialog>
    <el-dialog
      v-model="permissionVisible"
      :title="$t('管理 {p0} 的权限', { p0: permissionAccount?.username || '' })"
      width="min(92vw, 520px)"
    >
      <el-checkbox-group
        v-model="selectedPermissions"
        class="grid grid-cols-2 gap-x-3"
      >
        <el-checkbox
          v-for="[key, label] in permissionOptions"
          :key="key"
          :label="key"
          >{{ label }}</el-checkbox
        >
      </el-checkbox-group>
      <template #footer
        ><el-button @click="permissionVisible = false">{{
          $t("取消")
        }}</el-button
        ><el-button
          type="primary"
          :loading="Boolean(pendingId)"
          @click="savePermissions"
          >{{ $t("保存权限") }}</el-button
        ></template
      >
    </el-dialog>
    <el-dialog
      :model-value="Boolean(generatedPassword)"
      :title="$t('新账号随机密码')"
      width="min(92vw, 480px)"
      :close-on-click-modal="false"
      @close="generatedPassword = ''"
    >
      <p class="mb-3 text-sm text-gray-500">
        {{ $t("请现在复制并安全交给子用户；关闭后面板不会再次显示。") }}
      </p>
      <el-input :model-value="generatedPassword" readonly />
      <template #footer
        ><el-button @click="copyGeneratedPassword">{{
          $t("复制密码")
        }}</el-button
        ><el-button type="primary" @click="generatedPassword = ''">{{
          $t("我已保存")
        }}</el-button></template
      >
    </el-dialog>
  </div>
</template>
