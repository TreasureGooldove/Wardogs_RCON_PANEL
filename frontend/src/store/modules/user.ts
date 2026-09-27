import { defineStore } from "pinia";
import { isAxiosError } from "axios";
import { store, router, routerArrays } from "../utils";
import type { userType } from "../utils";
import {
  type AdminView,
  type LoginRequest,
  login,
  logout,
  getCurrentAdmin
} from "@/api/user";
import { useMultiTagsStoreHook } from "./multiTags";
import { usePermissionStoreHook } from "./permission";

export const useUserStore = defineStore("panel-user", {
  state: (): userType => ({
    adminId: "",
    sessionInvalidated: false,
    username: "",
    role: null,
    canKick: false,
    canBan: false,
    permissions: [],
    nickname: "",
    avatar: ""
  }),
  actions: {
    setAdmin(admin: AdminView) {
      this.adminId = admin.id;
      this.username = admin.username;
      this.role = admin.role;
      this.canKick = admin.canKick;
      this.canBan = admin.canBan;
      this.permissions = admin.permissions;
      this.sessionInvalidated = false;
    },
    clearSession() {
      this.adminId = "";
      this.sessionInvalidated = true;
      this.username = "";
      this.role = null;
      this.canKick = false;
      this.canBan = false;
      this.permissions = [];
      this.nickname = "";
      this.avatar = "";
      usePermissionStoreHook().clearAllCachePage();
      useMultiTagsStoreHook().handleTags("equal", [...routerArrays]);
    },
    async ensureSession(): Promise<boolean> {
      if (this.sessionInvalidated) return false;
      if (this.adminId) return true;
      try {
        this.setAdmin(await getCurrentAdmin());
        return true;
      } catch (error) {
        if (isAxiosError(error) && error.response?.status === 401) {
          this.clearSession();
        }
        return false;
      }
    },
    async loginByUsername(data: LoginRequest): Promise<AdminView> {
      const admin = await login(data);
      this.setAdmin(admin);
      return admin;
    },
    async logOut(): Promise<void> {
      try {
        await logout();
      } catch (error) {
        if (!isAxiosError(error) || error.response?.status !== 401) throw error;
      }
      this.clearSession();
      await router.replace("/login");
    }
  }
});

export function useUserStoreHook() {
  return useUserStore(store);
}
