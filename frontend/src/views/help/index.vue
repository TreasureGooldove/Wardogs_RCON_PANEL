<script setup lang="ts">
import { t } from "@/i18n";
defineOptions({ name: "WardogsHelp" });

const questions = [
  {
    question: t("为什么有些操作显示不可用？"),
    answer: t(
      "面板按目标服务器的 /v1/capabilities 清单判断。不同版本开放的路由可能不同；能力信息过期时管理操作会暂停。"
    )
  },
  {
    question: t("配置修改后为什么游戏内还没有变化？"),
    answer: t(
      "配置文档写入后，部分选项可能要到下局或服务器重新读取时才生效。应用结果中的 outcomes 与 warnings 以游戏服务器返回为准。"
    )
  },
  {
    question: t("预留位为什么只显示 SteamID？"),
    answer: t(
      "RCON 原始列表只返回 SteamID64。Steam 公开昵称和头像由面板后端另行查询；关闭界面设置中的 Steam 资料开关后，只显示 SteamID。"
    )
  },
  {
    question: t("RCON 密码与面板登录密码是一回事吗？"),
    answer: t(
      "不是。RCON 密码是服务器 HTTP RCON 的全权限 Bearer，保存在面板服务端；面板账号密码只用于登录本面板。"
    )
  },
  {
    question: t("命令请求超时后能否直接重试？"),
    answer: t(
      "不能立即重试。服务器可能已经执行了写入，只是响应丢失。先查询目标状态、封禁列表或服务器事件，再决定下一步。"
    )
  }
];
</script>

<template>
  <div class="space-y-5 p-5">
    <div>
      <h1 class="text-2xl font-semibold">{{ $t("帮助") }}</h1>
      <p class="text-sm text-gray-500">{{ $t("常见问题与接口参考") }}</p>
    </div>
    <el-card shadow="never">
      <el-collapse>
        <el-collapse-item
          v-for="(item, index) in questions"
          :key="index"
          :title="item.question"
          :name="index"
        >
          <p class="text-sm leading-6">{{ item.answer }}</p>
        </el-collapse-item>
      </el-collapse>
    </el-card>
    <el-card shadow="never">
      <template #header>{{ $t("外部资料") }}</template>
      <div class="flex flex-wrap gap-3">
        <el-link
          href="http://rcon.wardogs.com/faq.html"
          target="_blank"
          type="primary"
          >{{ $t("参考面板 FAQ") }}</el-link
        >
        <el-link
          href="https://wardogs.tech/openapi.json"
          target="_blank"
          type="primary"
          >{{ $t("社区 RCON API 参考") }}</el-link
        >
      </div>
      <p class="mt-3 text-xs text-gray-500">
        {{ $t("社区资料可能随游戏版本变化；以当前服务器能力清单为准。") }}
      </p>
    </el-card>
  </div>
</template>
