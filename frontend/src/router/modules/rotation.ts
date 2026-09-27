const Layout = () => import("@/layout/index.vue");

export default {
  path: "/rotation",
  name: "RotationRoot",
  component: Layout,
  redirect: "/rotation/index",
  meta: {
    icon: "ri/repeat-line",
    title: "地图轮换",
    rank: 4
  },
  children: [
    {
      path: "/rotation/index",
      name: "Rotation",
      component: () => import("@/views/rotation/index.vue"),
      meta: { title: "地图轮换" }
    }
  ]
} satisfies RouteConfigsTable;
