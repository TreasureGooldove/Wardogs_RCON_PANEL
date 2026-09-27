const Layout = () => import("@/layout/index.vue");

export default {
  path: "/diagnostics",
  name: "DiagnosticsRoot",
  component: Layout,
  redirect: "/diagnostics/index",
  meta: { icon: "ri/heartbeat-line", title: "服务器诊断", rank: 10 },
  children: [
    {
      path: "/diagnostics/index",
      name: "Diagnostics",
      component: () => import("@/views/diagnostics/index.vue"),
      meta: { title: "服务器诊断" }
    }
  ]
} satisfies RouteConfigsTable;
