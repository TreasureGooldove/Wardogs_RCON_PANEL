const Layout = () => import("@/layout/index.vue");

export default {
  path: "/catalog",
  name: "CatalogRoot",
  component: Layout,
  redirect: "/catalog/index",
  meta: {
    icon: "ri/book-open-line",
    title: "参考目录",
    rank: 3
  },
  children: [
    {
      path: "/catalog/index",
      name: "Catalog",
      component: () => import("@/views/catalog/index.vue"),
      meta: { title: "参考目录" }
    }
  ]
} satisfies RouteConfigsTable;
