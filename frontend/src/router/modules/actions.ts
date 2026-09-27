const Layout = () => import("@/layout/index.vue");

export default {
  path: "/actions",
  name: "ActionsRoot",
  component: Layout,
  redirect: "/actions/index",
  meta: {
    icon: "ri/command-line",
    title: "管理操作",
    rank: 7
  },
  children: [
    {
      path: "/actions/index",
      name: "Actions",
      component: () => import("@/views/actions/index.vue"),
      meta: { title: "管理操作", roles: ["owner", "subuser"] }
    }
  ]
} satisfies RouteConfigsTable;
