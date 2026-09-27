const Layout = () => import("@/layout/index.vue");

export default {
  path: "/help",
  name: "HelpRoot",
  component: Layout,
  redirect: "/help/index",
  meta: { icon: "ri/question-line", title: "帮助", rank: 12 },
  children: [
    {
      path: "/help/index",
      name: "Help",
      component: () => import("@/views/help/index.vue"),
      meta: { title: "帮助" }
    }
  ]
} satisfies RouteConfigsTable;
