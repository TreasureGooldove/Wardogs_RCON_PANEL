import { http } from "@/utils/http";

export interface Subuser {
  id: string;
  username: string;
  role: "subuser";
  canKick: boolean;
  canBan: boolean;
  permissions: string[];
  disabled: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface CreateSubuser {
  username: string;
  password: string;
  canKick: boolean;
  canBan: boolean;
  permissions: string[];
}

export interface UpdateSubuser {
  canKick?: boolean;
  canBan?: boolean;
  permissions?: string[];
  disabled?: boolean;
}

export const getSubusers = () =>
  http.request<Subuser[]>("get", "/api/subusers");

export const createSubuser = (data: CreateSubuser) =>
  http.request<Subuser>("post", "/api/subusers", { data });

export const updateSubuser = (id: string, data: UpdateSubuser) =>
  http.request<Subuser>("patch", `/api/subusers/${encodeURIComponent(id)}`, {
    data
  });

export const resetSubuserPassword = (id: string, password: string) =>
  http.request<void>(
    "post",
    `/api/subusers/${encodeURIComponent(id)}/reset-password`,
    { data: { password } }
  );
