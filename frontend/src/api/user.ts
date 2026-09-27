import { http } from "@/utils/http";

export interface LoginRequest {
  username: string;
  password: string;
}

export interface AdminView {
  id: string;
  username: string;
  role: "owner" | "subuser";
  canKick: boolean;
  canBan: boolean;
  permissions: string[];
}

export const login = (data: LoginRequest) =>
  http.request<AdminView>("post", "/api/auth/login", { data });

export const logout = () => http.request<void>("post", "/api/auth/logout");

export const getCurrentAdmin = () =>
  http.request<AdminView>("get", "/api/auth/me");
