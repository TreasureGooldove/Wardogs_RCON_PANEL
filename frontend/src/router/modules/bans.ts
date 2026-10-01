const Layout = () => import("@/layout/index.vue");

export default {
  path: "/bans",
  name: "BansRoot",
  component: Layout,
  redirect: "/bans/index",
  meta: { icon: "ri/forbid-line", title: "封禁管理", rank: 7.5 },
  children: [
    {
      path: "/bans/index",
      name: "BanManagement",
      component: () => import("@/views/bans/index.vue"),
      meta: { title: "封禁管理", roles: ["owner", "subuser"] }
    }
  ]
} satisfies RouteConfigsTable;
