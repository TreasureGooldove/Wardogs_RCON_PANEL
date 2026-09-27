const Layout = () => import("@/layout/index.vue");

export default {
  path: "/history",
  name: "HistoryRoot",
  component: Layout,
  redirect: "/history/players",
  meta: { icon: "ri/history-line", title: "历史记录", rank: 2 },
  children: [
    { path: "/history/players", name: "HistoryPlayers", component: () => import("@/views/history/index.vue"), meta: { title: "历史玩家" } },
    { path: "/history/matches", name: "HistoryMatches", component: () => import("@/views/history/index.vue"), meta: { title: "历史对局" } },
    { path: "/history/players/:steamId", name: "HistoryPlayerDetail", component: () => import("@/views/history/index.vue"), meta: { title: "玩家对局", showLink: false } },
    { path: "/history/matches/:matchId", name: "HistoryMatchDetail", component: () => import("@/views/history/index.vue"), meta: { title: "对局详情", showLink: false } }
  ]
} satisfies RouteConfigsTable;
