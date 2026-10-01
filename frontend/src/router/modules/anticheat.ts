const Layout = () => import("@/layout/index.vue");
export default {
  path: "/anticheat",
  name: "AntiCheatRoot",
  component: Layout,
  redirect: "/anticheat/index",
  meta: { icon: "ri/shield-check-line", title: "反作弊（实验性）", rank: 7.7 },
  children: [
    {
      path: "/anticheat/index",
      name: "AntiCheat",
      component: () => import("@/views/anticheat/index.vue"),
      meta: { title: "反作弊（实验性）", roles: ["owner", "subuser"] }
    }
  ]
} satisfies RouteConfigsTable;
