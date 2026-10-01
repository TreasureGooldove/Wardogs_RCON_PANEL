<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { ElMessageBox } from "element-plus";
import { http } from "@/utils/http";
import { t } from "@/i18n";
import { useUserStoreHook } from "@/store/modules/user";
import { getApiErrorMessage } from "@/api/errors";
import { formatObservedAt } from "@/api/snapshot";
interface Awards {
  enabled: boolean;
  titles: { kills: string; cash_gain: string; deaths: string };
  targetRevision: string;
  previousMatch: { id: string; map: string; end_reason: string } | null;
  previewMessages: string[];
  board: {
    title: string;
    value: number | null;
    unit: string;
    winners: { steamId: string; name: string }[];
    tiedPlayers: number;
  }[];
  jobs: {
    old_match: string;
    outcome: string;
    created_at: string;
    error: string | null;
    parts: { message: string; outcome: string }[];
  }[];
}
const user = useUserStoreHook();
const canEdit = computed(
  () => user.role === "owner" || user.permissions.includes("broadcast")
);
const data = ref<Awards>();
const enabled = ref(false);
const titles = ref({ kills: "击杀王", cash_gain: "许家印", deaths: "区王" });
const error = ref("");
const busy = ref(false);
async function refresh() {
  busy.value = true;
  error.value = "";
  try {
    data.value = await http.request<Awards>("get", "/api/community/awards");
    enabled.value = data.value.enabled;
    titles.value = { ...data.value.titles };
  } catch (e) {
    error.value = getApiErrorMessage(e);
  } finally {
    busy.value = false;
  }
}
async function save() {
  if (!data.value) return;
  busy.value = true;
  error.value = "";
  try {
    let password: string | undefined;
    if (enabled.value) {
      const answer = await ElMessageBox.prompt(
        t(
          "开启后，每次观测到新一局会自动全服播报上一局榜单。确认前不会发送测试公告。"
        ),
        t("启用开局榜单播报"),
        {
          inputType: "password",
          inputPlaceholder: t("输入当前管理员密码"),
          inputValidator: value => !!value || t("请输入当前管理员密码"),
          confirmButtonText: t("确认"),
          cancelButtonText: t("取消")
        }
      );
      password = answer.value;
    }
    data.value = await http.request<Awards>("put", "/api/community/awards", {
      data: {
        enabled: enabled.value,
        titles: titles.value,
        targetRevision: data.value.targetRevision,
        ...(password ? { password } : {})
      }
    });
  } catch (e) {
    if (e !== "cancel" && e !== "close") error.value = getApiErrorMessage(e);
  } finally {
    busy.value = false;
  }
}
onMounted(refresh);
</script>
<template>
  <el-card shadow="never"
    ><template #header>{{ t("开局榜单播报") }}</template
    ><el-alert
      :title="
        t(
          '观测到新一局时播报上一局的击杀王、许家印、区王。现金按该局观测到的正向增加量估算，不计初始余额；消费不抵扣，补偿也可能计入。并列时共同获称号，零值或未知不授称号。'
        )
      "
      :closable="false"
    /><el-alert
      class="mt-2"
      :title="
        t(
          '对局边界与榜单都是观测值，可能遗漏采样之间的数据。采集断档或面板重启不补播，发送超时不重试。默认关闭，开启需要全服公告权限与密码确认。'
        )
      "
      type="warning"
      :closable="false"
    /><el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div class="my-4 flex flex-wrap gap-3">
      <el-switch
        v-model="enabled"
        :disabled="!canEdit || busy"
        :active-text="t('启用')"
        :inactive-text="t('关闭')"
      /><el-button v-if="canEdit" :loading="busy" @click="save">{{
        t("保存播报设置")
      }}</el-button
      ><el-button :loading="busy" @click="refresh">{{ t("刷新") }}</el-button>
    </div>
    <el-form label-position="top" class="grid gap-3 sm:grid-cols-3">
      <el-form-item :label="t('击杀最高称号')"
        ><el-input
          v-model="titles.kills"
          maxlength="24"
          show-word-limit
          :disabled="!canEdit || busy"
      /></el-form-item>
      <el-form-item :label="t('现金增加最高称号')"
        ><el-input
          v-model="titles.cash_gain"
          maxlength="24"
          show-word-limit
          :disabled="!canEdit || busy"
      /></el-form-item>
      <el-form-item :label="t('死亡最高称号')"
        ><el-input
          v-model="titles.deaths"
          maxlength="24"
          show-word-limit
          :disabled="!canEdit || busy"
      /></el-form-item>
    </el-form>
    <p class="mb-3">
      {{ t("称号可自定义；修改后点击保存播报设置，不会立即发送公告。") }}
    </p>
    <template v-if="data"
      ><p>
        {{ t("上一局地图") }}：{{
          data.previousMatch?.map || t("暂无完整观测对局")
        }}
      </p>
      <el-table :data="data.board"
        ><el-table-column prop="title" :label="t('称号')" /><el-table-column
          :label="t('玩家')"
          ><template #default="{ row }"
            >{{
              row.winners.map((p: { name: string }) => p.name).join("、") ||
              t("暂无获奖玩家")
            }}<span v-if="row.tiedPlayers > 1">
              ({{ t("并列") }} {{ row.tiedPlayers }})</span
            ></template
          ></el-table-column
        ><el-table-column :label="t('观测数值')"
          ><template #default="{ row }"
            >{{ row.value ?? t("未知") }} · {{ t(row.unit) }}</template
          ></el-table-column
        ></el-table
      >
      <h3 class="my-3">{{ t("公告预览（仅预览）") }}</h3>
      <p
        v-for="(message, index) in data.previewMessages"
        :key="index"
        class="my-2 break-all"
      >
        {{ message }}
      </p>
      <el-table class="mt-4" :data="data.jobs"
        ><el-table-column :label="t('时间')"
          ><template #default="{ row }">{{
            formatObservedAt(row.created_at)
          }}</template></el-table-column
        ><el-table-column prop="outcome" :label="t('结果')" /><el-table-column
          prop="error"
          :label="t('错误码')"
        /><el-table-column :label="t('公告及结果')"
          ><template #default="{ row }"
            ><p v-for="(part, index) in row.parts" :key="index">
              {{ part.message }} · {{ part.outcome }}
            </p></template
          ></el-table-column
        ></el-table
      ></template
    ></el-card
  >
</template>
