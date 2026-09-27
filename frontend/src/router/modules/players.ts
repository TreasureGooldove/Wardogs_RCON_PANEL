const Layout = () => import("@/layout/index.vue");

export default {
  path: "/players",
  name: "PlayersRoot",
  component: Layout,
  redirect: "/players/index",
  meta: {
    icon: "ri/team-line",
    title: "在线玩家",
    rank: 1
  },
  children: [
    {
      path: "/players/index",
      name: "Players",
      component: () => import("@/views/players/index.vue"),
      meta: { title: "在线玩家" }
    }
  ]
} satisfies RouteConfigsTable;
