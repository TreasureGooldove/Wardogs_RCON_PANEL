const Layout = () => import("@/layout/index.vue");
export default {
  path: "/kills",
  name: "KillsRoot",
  component: Layout,
  redirect: "/kills/index",
  meta: { icon: "ri/sword-line", title: "击杀记录", rank: 4.2 },
  children: [
    {
      path: "/kills/index",
      name: "KillHistory",
      component: () => import("@/views/kills/index.vue"),
      meta: { title: "击杀记录", roles: ["owner", "subuser"] }
    }
  ]
} satisfies RouteConfigsTable;
