const Layout = () => import("@/layout/index.vue");

export default {
  path: "/reserved-slots",
  name: "ReservedSlotsRoot",
  component: Layout,
  redirect: "/reserved-slots/index",
  meta: {
    icon: "ri/vip-crown-line",
    title: "预留位",
    rank: 8
  },
  children: [
    {
      path: "/reserved-slots/index",
      name: "ReservedSlots",
      component: () => import("@/views/reserved-slots/index.vue"),
      meta: { title: "预留位", roles: ["owner", "subuser"] }
    }
  ]
} satisfies RouteConfigsTable;
