<script setup lang="ts">
import { onMounted, ref } from "vue";
import { t } from "@/i18n";
import {
  getHealth,
  getServerId,
  getSponsor,
  getBotApiStatus,
  type BotApiStatus,
  type BotGatewayState,
  type HealthResponse,
  type ServerIdResponse,
  type SponsorResponse
} from "@/api/diagnostics";
import { getApiErrorMessage } from "@/api/errors";
import { formatObservedAt } from "@/api/snapshot";

defineOptions({ name: "ServerDiagnostics" });

const health = ref<HealthResponse | null>(null);
const serverId = ref<ServerIdResponse | null>(null);
const sponsor = ref<SponsorResponse | null>(null);
const loading = ref(false);
const errors = ref<Record<string, string>>({});
const botApi = ref<BotApiStatus | null>(null);
function botState(state: BotGatewayState) {
  const labels: Record<BotGatewayState, string> = {
    available: "入口鉴权通过",
    auth_rejected: "入口拒绝鉴权",
    http_error: "入口返回异常状态",
    invalid_response: "入口响应无法确认",
    unreachable: "入口暂时无法连接",
    unconfigured: "尚未配置密钥"
  };
  return t(labels[state]);
}

async function refresh() {
  if (loading.value) return;
  loading.value = true;
  errors.value = {};
  botApi.value = null;
  const result = await Promise.allSettled([
    getHealth(),
    getServerId(),
    getSponsor(),
    getBotApiStatus()
  ]);
  if (result[0].status === "fulfilled") health.value = result[0].value;
  else errors.value.health = getApiErrorMessage(result[0].reason);
  if (result[1].status === "fulfilled") serverId.value = result[1].value;
  else errors.value.serverId = getApiErrorMessage(result[1].reason);
  if (result[2].status === "fulfilled") sponsor.value = result[2].value;
  else errors.value.sponsor = getApiErrorMessage(result[2].reason);
  if (result[3].status === "fulfilled") botApi.value = result[3].value;
  else errors.value.botApi = getApiErrorMessage(result[3].reason);
  loading.value = false;
}

onMounted(refresh);
</script>

<template>
  <div class="space-y-5 p-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-2xl font-semibold">{{ $t("服务器诊断") }}</h1>
        <p class="text-sm text-gray-500">
          {{ $t("只读查看健康状态、服务器标识与展示图片") }}
        </p>
      </div>
      <el-button :loading="loading" @click="refresh">{{
        $t("重新查询")
      }}</el-button>
    </div>
    <el-card shadow="never" data-testid="bot-api-status">
      <template #header>{{ $t("机器人 API 状态") }}</template>
      <el-alert
        v-if="errors.botApi"
        :title="errors.botApi"
        type="warning"
        :closable="false"
      />
      <el-empty v-if="!botApi" :description="$t('尚无 API 状态')" />
      <template v-else>
        <el-descriptions :column="1" border>
          <el-descriptions-item :label="$t('机器人 API 入口')"
            >{{ botApi.gatewayOrigin }}/api/bot</el-descriptions-item
          >
          <el-descriptions-item :label="$t('传输协议')">{{
            botApi.encrypted ? "HTTPS" : "HTTP"
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('版本')"
            >{{ botApi.panelVersion }} · API
            {{ botApi.apiVersion }}</el-descriptions-item
          >
          <el-descriptions-item :label="$t('查询时间')">{{
            formatObservedAt(botApi.observedAt)
          }}</el-descriptions-item>
        </el-descriptions>
        <div class="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
          <div
            v-for="credential in botApi.credentials"
            :key="credential.role"
            class="rounded border border-[var(--el-border-color-light)] p-4"
          >
            <h2 class="mb-3 font-semibold">
              {{
                credential.role === "read"
                  ? $t("只读机器人身份")
                  : $t("管理机器人身份")
              }}
            </h2>
            <el-tag
              :type="
                credential.state === 'available'
                  ? 'success'
                  : credential.state === 'unconfigured'
                    ? 'info'
                    : 'warning'
              "
              >{{ botState(credential.state) }}</el-tag
            >
            <p class="mt-3 text-sm">
              {{ $t("密钥配置") }}：{{
                credential.configured ? $t("已配置") : $t("未配置")
              }}
            </p>
            <p class="mt-1 text-sm">
              {{ $t("可用权限") }}：{{
                credential.configured
                  ? credential.role === "read"
                    ? $t("个人信息查询")
                    : $t("个人信息查询、玩家查找、封禁")
                  : "—"
              }}
            </p>
            <p class="mt-1 text-sm">
              HTTP {{ credential.httpStatus ?? "—" }} ·
              {{
                credential.latencyMs === null
                  ? "—"
                  : `${credential.latencyMs} ms`
              }}
            </p>
          </div>
        </div>
        <p class="mt-4 text-sm text-gray-500">
          {{
            $t(
              "从面板服务器探测入口鉴权，不执行封禁；结果不代表所有外部网络都能访问。"
            )
          }}
        </p>
        <el-alert
          v-if="!botApi.encrypted"
          class="mt-3"
          :title="$t('HTTP 入口明文传输 API Key 和查询数据，请留意数据安全。')"
          type="warning"
          :closable="false"
        />
      </template>
    </el-card>
    <div class="grid grid-cols-1 gap-5 lg:grid-cols-2">
      <el-card shadow="never">
        <template #header>{{ $t("RCON 健康状态") }}</template>
        <el-alert
          v-if="errors.health"
          :title="errors.health"
          type="warning"
          :closable="false"
        />
        <el-empty v-if="!health" :description="$t('尚无健康状态')" />
        <el-descriptions v-else :column="1" border>
          <el-descriptions-item :label="$t('报告状态')">{{
            health.reportedState ?? $t("未知")
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('运行时间')">{{
            health.uptimeSeconds === null
              ? $t("未知")
              : $t("{p0} 分钟", { p0: Math.floor(health.uptimeSeconds / 60) })
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('活动连接')">{{
            health.connections?.active ?? $t("未知")
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('游戏线程执行中')">{{
            health.gameThreadQueue?.inFlight ?? $t("未知")
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('游戏线程队列')">{{
            health.gameThreadQueue?.depth ?? $t("未知")
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('累计拒绝')">{{
            health.gameThreadQueue?.rejectedTotal ?? $t("未知")
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('查询时间')">{{
            formatObservedAt(health.observedAt)
          }}</el-descriptions-item>
        </el-descriptions>
      </el-card>
      <el-card shadow="never">
        <template #header>{{ $t("服务器资料") }}</template>
        <el-alert
          v-if="errors.serverId"
          :title="errors.serverId"
          type="warning"
          :closable="false"
        />
        <el-alert
          v-if="errors.sponsor"
          :title="errors.sponsor"
          type="warning"
          :closable="false"
          class="mt-2"
        />
        <el-descriptions :column="1" border>
          <el-descriptions-item label="ServerID">{{
            serverId?.serverId || $t("未设置或未知")
          }}</el-descriptions-item>
          <el-descriptions-item :label="$t('展示图片')">{{
            sponsor?.hasImage ? $t("已设置") : $t("未设置或未知")
          }}</el-descriptions-item>
        </el-descriptions>
        <img
          v-if="sponsor?.imageUrl"
          :src="sponsor.imageUrl"
          :alt="$t('服务器展示图片')"
          class="mt-4 max-h-64 max-w-full rounded border border-[var(--el-border-color-light)] object-contain"
        />
        <p class="mt-3 text-xs text-gray-500">
          {{ $t("展示图片地址可在“配置文件”的 ServerImageURL 中编辑。") }}
        </p>
      </el-card>
    </div>
  </div>
</template>
