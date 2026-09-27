const Layout = () => import("@/layout/index.vue");

export default {
  path: "/subusers",
  name: "SubusersRoot",
  component: Layout,
  redirect: "/subusers/index",
  meta: {
    icon: "ri/group-line",
    title: "子用户",
    rank: 6
  },
  children: [
    {
      path: "/subusers/index",
      name: "Subusers",
      component: () => import("@/views/subusers/index.vue"),
      meta: { title: "子用户", roles: ["owner"] }
    }
  ]
} satisfies RouteConfigsTable;
