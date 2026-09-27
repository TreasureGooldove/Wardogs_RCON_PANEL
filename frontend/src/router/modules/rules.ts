const Layout = () => import("@/layout/index.vue");

export default {
  path: "/rules",
  name: "RulesRoot",
  component: Layout,
  redirect: "/rules/index",
  meta: { icon: "ri/file-list-3-line", title: "服规播报", rank: 6 },
  children: [
    {
      path: "/rules/index",
      name: "Rules",
      component: () => import("@/views/rules/index.vue"),
      meta: { title: "服规播报", roles: ["owner", "subuser"] }
    }
  ]
} satisfies RouteConfigsTable;
