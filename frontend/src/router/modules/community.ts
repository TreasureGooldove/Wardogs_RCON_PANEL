const Layout = () => import("@/layout/index.vue");
export default {
  path: "/community",
  name: "CommunityRoot",
  component: Layout,
  redirect: "/community/index",
  meta: { icon: "ri/team-line", title: "玩家工具", rank: 7.6 },
  children: [
    {
      path: "/community/index",
      name: "CommunityTools",
      component: () => import("@/views/community/index.vue"),
      meta: { title: "玩家工具", roles: ["owner", "subuser"] }
    }
  ]
} satisfies RouteConfigsTable;
