import {
  type RouterHistory,
  type RouteRecordRaw,
  type RouteComponent,
  createWebHistory,
  createWebHashHistory
} from "vue-router";
import { router } from "./index";
import { isProxy, toRaw } from "vue";
import { useTimeoutFn } from "@vueuse/core";
import { cloneDeep, isAllEmpty } from "@pureadmin/utils";
import { buildHierarchyTree } from "@/utils/tree";
import { type menuType, routerArrays } from "@/layout/types";
import { useMultiTagsStoreHook } from "@/store/modules/multiTags";
import { usePermissionStoreHook } from "@/store/modules/permission";
import type { AdminView } from "@/api/user";

function handRank(routeInfo: any) {
  const { name, path, parentId, meta } = routeInfo;
  return isAllEmpty(parentId)
    ? isAllEmpty(meta?.rank) ||
        (meta?.rank === 0 && name !== "Home" && path !== "/")
    : false;
}

function ascending(arr: any[]) {
  arr.forEach((route, index) => {
    if (handRank(route)) route.meta.rank = index + 2;
  });
  return arr.sort((a, b) => a?.meta?.rank - b?.meta?.rank);
}

function filterTree(data: RouteComponent[]) {
  const tree = cloneDeep(data).filter(
    (route: any) => route.meta?.showLink !== false
  );
  tree.forEach((route: any) => {
    if (route.children) route.children = filterTree(route.children);
  });
  return tree;
}

function filterNoPermissionTree(data: RouteComponent[]) {
  return data.filter(
    (route: any) => !route.children || route.children.length > 0
  );
}

function getParentPaths(value: string, routes: RouteRecordRaw[], key = "path") {
  function find(items: RouteRecordRaw[], parents: string[]): string[] {
    for (const item of items) {
      if (item[key] === value) return parents;
      if (item.children?.length) {
        const result = find(item.children, [...parents, item.path]);
        if (result.length) return result;
      }
    }
    return [];
  }
  return find(routes, []);
}

function findRouteByPath(path: string, routes: RouteRecordRaw[]) {
  for (const item of routes) {
    if (item.path === path) return isProxy(item) ? toRaw(item) : item;
    if (item.children?.length) {
      const child = findRouteByPath(path, item.children);
      if (child) return child;
    }
  }
  return null;
}

/** 只使用本地静态菜单，业务路由由面板会话保护。 */
function initRouter(role: AdminView["role"]): Promise<typeof router> {
  if (usePermissionStoreHook().wholeMenus.length === 0) {
    usePermissionStoreHook().handleWholeMenus([], role);
  }
  if (!useMultiTagsStoreHook().getMultiTagsCache) {
    useMultiTagsStoreHook().handleTags("equal", [...routerArrays]);
  }
  return Promise.resolve(router);
}

function formatFlatteningRoutes(routesList: RouteRecordRaw[]) {
  if (routesList.length === 0) return routesList;
  let hierarchyList = buildHierarchyTree(routesList);
  for (let index = 0; index < hierarchyList.length; index++) {
    if (hierarchyList[index].children) {
      hierarchyList = hierarchyList
        .slice(0, index + 1)
        .concat(hierarchyList[index].children, hierarchyList.slice(index + 1));
    }
  }
  return hierarchyList;
}

function formatTwoStageRoutes(routesList: RouteRecordRaw[]) {
  if (routesList.length === 0) return routesList;
  const result: RouteRecordRaw[] = [];
  routesList.forEach(route => {
    if (route.path === "/") {
      result.push({
        component: route.component,
        name: route.name,
        path: route.path,
        redirect: route.redirect,
        meta: route.meta,
        children: []
      });
    } else {
      result[0]?.children.push({ ...route });
    }
  });
  return result;
}

function handleAliveRoute({ name }: ToRouteType, mode?: string) {
  const permissionStore = usePermissionStoreHook();
  if (mode === "add" || mode === "delete" || mode === "refresh") {
    permissionStore.cacheOperate({ mode, name });
    return;
  }
  permissionStore.cacheOperate({ mode: "delete", name });
  useTimeoutFn(() => permissionStore.cacheOperate({ mode: "add", name }), 100);
}

function getHistoryMode(routerHistory: string): RouterHistory {
  const [mode, base = ""] = routerHistory.split(",");
  return mode === "h5" ? createWebHistory(base) : createWebHashHistory(base);
}

/** 首期没有按钮级角色授权；这两个兼容接口均不授予权限。 */
function getAuths(): string[] {
  return [];
}

function hasAuth(_value: string | string[]): boolean {
  return false;
}

function getTopMenu(tag = false): menuType {
  const first = usePermissionStoreHook().wholeMenus[0] as any;
  const menu = (first?.children?.[0] ?? first) as menuType;
  if (tag && menu) useMultiTagsStoreHook().handleTags("push", menu);
  return menu;
}

export {
  hasAuth,
  getAuths,
  ascending,
  filterTree,
  initRouter,
  getTopMenu,
  getHistoryMode,
  getParentPaths,
  findRouteByPath,
  handleAliveRoute,
  formatTwoStageRoutes,
  formatFlatteningRoutes,
  filterNoPermissionTree
};
