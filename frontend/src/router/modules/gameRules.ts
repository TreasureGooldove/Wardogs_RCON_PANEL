const Layout = () => import("@/layout/index.vue");
export default {
  path: "/game-rules",
  name: "GameRulesRoot",
  component: Layout,
  redirect: "/game-rules/index",
  meta: { icon: "ri/equalizer-line", title: "阵营与装备限制", rank: 7.6 },
  children: [
    {
      path: "/game-rules/index",
      name: "GameRules",
      component: () => import("@/views/game-rules/index.vue"),
      meta: { title: "阵营与装备限制", roles: ["owner", "subuser"] }
    }
  ]
} satisfies RouteConfigsTable;
