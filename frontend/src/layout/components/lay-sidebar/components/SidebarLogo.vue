<script setup lang="ts">
import { getTopMenu } from "@/router/utils";
import { useNav } from "@/layout/hooks/useNav";

defineProps({
  collapse: Boolean
});

const { title } = useNav();
</script>

<template>
  <div class="sidebar-logo-container" :class="{ collapses: collapse }">
    <transition name="sidebarLogoFade">
      <router-link
        v-if="collapse"
        key="collapse"
        :title="title"
        class="sidebar-logo-link"
        :to="getTopMenu()?.path ?? '/'"
      >
        <span class="brand-mark" aria-hidden="true">W</span>
        <span class="sidebar-title">{{ title }}</span>
      </router-link>
      <router-link
        v-else
        key="expand"
        :title="title"
        class="sidebar-logo-link"
        :to="getTopMenu()?.path ?? '/'"
      >
        <span class="brand-mark" aria-hidden="true">W</span>
        <span class="sidebar-title">{{ title }}</span>
      </router-link>
    </transition>
  </div>
</template>

<style lang="scss" scoped>
.sidebar-logo-container {
  position: relative;
  width: 100%;
  height: 48px;
  overflow: hidden;

  .sidebar-logo-link {
    display: flex;
    flex-wrap: nowrap;
    align-items: center;
    height: 100%;
    padding-left: 10px;

    .brand-mark {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 32px;
      width: 32px;
      height: 32px;
      border-radius: 8px;
      background: #1d4ed8;
      color: white;
      font-size: 18px;
      font-weight: 700;
    }

    .sidebar-title {
      display: inline-block;
      height: 32px;
      margin: 2px 0 0 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      font-size: 18px;
      font-weight: 600;
      line-height: 32px;
      color: var(--pure-theme-sub-menu-active-text);
      white-space: nowrap;
    }
  }
}
</style>
