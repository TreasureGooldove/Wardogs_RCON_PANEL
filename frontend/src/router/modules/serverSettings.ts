const Layout = () => import("@/layout/index.vue");

export default {
  path: "/server-settings",
  name: "ServerSettingsRoot",
  component: Layout,
  redirect: "/server-settings/index",
  meta: {
    icon: "ri/settings-3-line",
    title: "服务器设置",
    rank: 5
  },
  children: [
    {
      path: "/server-settings/index",
      name: "ServerSettings",
      component: () => import("@/views/server-settings/index.vue"),
      meta: { title: "服务器设置", roles: ["owner"] }
    }
  ]
} satisfies RouteConfigsTable;
