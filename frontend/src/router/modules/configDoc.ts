const Layout = () => import("@/layout/index.vue");

export default {
  path: "/config-doc",
  name: "ConfigDocRoot",
  component: Layout,
  redirect: "/config-doc/index",
  meta: {
    icon: "ri/file-settings-line",
    title: "配置文件",
    rank: 9
  },
  children: [
    {
      path: "/config-doc/index",
      name: "ConfigDoc",
      component: () => import("@/views/config-doc/index.vue"),
      meta: { title: "配置文件", roles: ["owner", "subuser"], keepAlive: false }
    }
  ]
} satisfies RouteConfigsTable;
