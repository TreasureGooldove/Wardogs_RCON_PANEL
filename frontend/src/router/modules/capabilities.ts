const Layout = () => import("@/layout/index.vue");

export default {
  path: "/capabilities",
  name: "CapabilitiesRoot",
  component: Layout,
  redirect: "/capabilities/index",
  meta: {
    icon: "ri/radar-line",
    title: "服务器能力",
    rank: 2
  },
  children: [
    {
      path: "/capabilities/index",
      name: "Capabilities",
      component: () => import("@/views/capabilities/index.vue"),
      meta: { title: "服务器能力" }
    }
  ]
} satisfies RouteConfigsTable;
