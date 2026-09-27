const Layout = () => import("@/layout/index.vue");

export default {
  path: "/preferences",
  name: "PreferencesRoot",
  component: Layout,
  redirect: "/preferences/index",
  meta: { icon: "ri/paint-brush-line", title: "界面设置", rank: 11 },
  children: [
    {
      path: "/preferences/index",
      name: "Preferences",
      component: () => import("@/views/preferences/index.vue"),
      meta: { title: "界面设置" }
    }
  ]
} satisfies RouteConfigsTable;
