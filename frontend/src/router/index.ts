import { getConfig } from "@/config";
import { t } from "@/i18n";
import NProgress from "@/utils/progress";
import { buildHierarchyTree } from "@/utils/tree";
import { useUserStoreHook } from "@/store/modules/user";
import { usePermissionStoreHook } from "@/store/modules/permission";
import remainingRouter from "./modules/remaining";
import {
  ascending,
  getHistoryMode,
  initRouter,
  handleAliveRoute,
  formatTwoStageRoutes,
  formatFlatteningRoutes
} from "./utils";
import {
  type Router,
  type RouteRecordRaw,
  type RouteComponent,
  createRouter
} from "vue-router";

const modules: Record<string, any> = import.meta.glob(
  ["./modules/**/*.ts", "!./modules/**/remaining.ts"],
  { eager: true }
);
const routes = Object.values(modules).map(module => module.default);

export const constantRoutes: Array<RouteRecordRaw> = formatTwoStageRoutes(
  formatFlatteningRoutes(buildHierarchyTree(ascending(routes.flat(Infinity))))
);
export const constantMenus: Array<RouteComponent> = ascending(
  routes.flat(Infinity)
).concat(...remainingRouter);
export const remainingPaths = remainingRouter.map(route => route.path);

export const router: Router = createRouter({
  history: getHistoryMode(import.meta.env.VITE_ROUTER_HISTORY),
  routes: constantRoutes.concat(...(remainingRouter as any)),
  strict: true,
  scrollBehavior(_to, _from, savedPosition) {
    return savedPosition ?? { top: 0 };
  }
});

const loadedPaths = new Set<string>();

window.addEventListener("panel-session-expired", () => {
  const current = router.currentRoute.value;
  if (current.path !== "/login") {
    router.replace({
      path: "/login",
      query: { redirect: current.fullPath }
    });
  }
});

export function resetLoadedPaths() {
  loadedPaths.clear();
}

export function resetRouter() {
  usePermissionStoreHook().clearAllCachePage();
  resetLoadedPaths();
}

router.beforeEach(async (to, from) => {
  to.meta.loaded = loadedPaths.has(to.path);
  if (!to.meta.loaded) NProgress.start();

  if (to.meta?.keepAlive) {
    handleAliveRoute(to, "add");
    if (from.name === undefined || from.name === "Redirect") {
      handleAliveRoute(to);
    }
  }

  const title = to.meta.title as string | undefined;
  if (title) {
    document.title = getConfig().Title
      ? `${t(title)} | ${t(getConfig().Title)}`
      : t(title);
  }

  const userStore = useUserStoreHook();
  const authenticated = await userStore.ensureSession();
  if (to.path === "/login") {
    if (authenticated) {
      await initRouter(userStore.role ?? "subuser");
      return { path: "/welcome" };
    }
    return true;
  }

  if (!authenticated) {
    return { path: "/login", query: { redirect: to.fullPath } };
  }

  await initRouter(userStore.role ?? "subuser");
  if (to.meta.roles?.length && !to.meta.roles.includes(userStore.role ?? "")) {
    return { path: "/access-denied" };
  }
  return true;
});

router.afterEach(to => {
  loadedPaths.add(to.path);
  NProgress.done();
});

export default router;
