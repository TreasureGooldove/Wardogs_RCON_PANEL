import { t } from "@/i18n";
import { isAxiosError } from "axios";

export type ApiErrorCode =
  | "not_authenticated"
  | "permission_denied"
  | "invalid_credentials"
  | "invalid_subuser"
  | "subuser_not_found"
  | "username_exists"
  | "rate_limited"
  | "rcon_unconfigured"
  | "rcon_auth_failed"
  | "rcon_unavailable"
  | "rcon_timeout"
  | "rcon_rate_limited"
  | "route_unsupported"
  | "invalid_upstream"
  | "invalid_settings"
  | "settings_bearer_required"
  | "settings_key_unavailable"
  | "settings_public_http_disabled"
  | "write_disabled"
  | "updater_unavailable"
  | "update_busy"
  | "update_not_available"
  | "config_interface_inconsistent"
  | "invalid_moderation_target"
  | "player_not_online"
  | "invalid_moderation_reason"
  | "invalid_selection"
  | "action_unsupported"
  | "action_rejected"
  | "action_uncertain"
  | "stale_server_target";

export interface ApiErrorBody {
  code: ApiErrorCode;
  message: string;
  requestId: string;
}

const messages: Record<ApiErrorCode, string> = {
  not_authenticated: "登录已失效，请重新登录",
  permission_denied: "当前账号没有此操作权限",
  invalid_credentials: "账号或密码错误",
  invalid_subuser: "子用户资料无效，请检查用户名和密码",
  subuser_not_found: "子用户不存在，请刷新列表",
  username_exists: "用户名已存在，请更换用户名",
  rate_limited: "登录尝试过于频繁，请稍后重试",
  rcon_unconfigured: "服务器连接尚未配置",
  rcon_auth_failed: "服务器查询凭据无效，请联系管理员",
  rcon_unavailable: "暂时无法连接服务器",
  rcon_timeout: "服务器查询超时",
  rcon_rate_limited: "服务器查询过于频繁，请稍后重试",
  route_unsupported: "目标服务器不支持此项查询",
  invalid_upstream: "服务器返回的数据无法解析",
  invalid_settings: "服务器设置无效，请检查名称和 RCON 地址",
  settings_bearer_required: "更改 RCON 地址时必须重新填写 Bearer 密钥",
  settings_key_unavailable:
    "面板缺少保存密钥所需的服务器配置，请联系部署管理员",
  settings_public_http_disabled:
    "服务器部署策略未允许公网 HTTP RCON，请先配置 HTTPS 或私网通道",
  write_disabled: "服务器管理操作尚未启用",
  updater_unavailable: "宿主机更新程序未启用，请按部署教程安装更新服务",
  update_busy: "已有更新任务正在执行",
  update_not_available: "没有可校验的新版本安装包",
  config_interface_inconsistent:
    "官方接口存在问题：配置返回内容无法核对一致，仅禁用服务器配置页面写入",
  invalid_moderation_target: "玩家 SteamID 无效，请刷新名单后重试",
  player_not_online: "该玩家已不在线，请刷新玩家名单后重试",
  invalid_moderation_reason: "操作原因无效，请填写 1–200 个字符",
  invalid_selection: "服务器选项无效，请刷新当前数据后重新选择",
  action_unsupported: "目标服务器不支持此项管理操作",
  action_rejected: "目标服务器拒绝了此项管理操作",
  action_uncertain:
    "操作结果不确定，请到 RCON 原管理页核查；封禁要核查封禁列表，玩家离线不能证明封禁成功。勿立即重复操作",
  stale_server_target: "服务器设置已变更，请刷新玩家名单后重试"
};

export function getApiErrorCode(error: unknown): ApiErrorCode | null {
  if (!isAxiosError(error)) return null;
  const code = (error.response?.data as Partial<ApiErrorBody> | undefined)
    ?.code;
  return typeof code === "string" &&
    Object.prototype.hasOwnProperty.call(messages, code)
    ? (code as ApiErrorCode)
    : null;
}

export function getApiErrorMessage(error: unknown): string {
  const code = getApiErrorCode(error);
  if (code) return t(messages[code]);
  if (isAxiosError(error)) {
    if (!error.response) return t("无法连接面板服务，请检查本地网络");
    if (error.response.status === 401) return t("登录已失效，请重新登录");
    if (error.response.status === 429) return t("请求过于频繁，请稍后重试");
  }
  return t("请求失败，请稍后重试");
}
