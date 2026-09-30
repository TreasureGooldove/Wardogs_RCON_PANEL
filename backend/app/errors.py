"""Stable, redacted panel errors."""

import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


_ERRORS: dict[str, tuple[int, str]] = {
    "updater_unavailable": (503, "宿主机更新程序未启用，请按部署教程安装更新服务"),
    "update_busy": (409, "已有更新任务正在执行"),
    "update_not_available": (409, "没有可校验的新版本安装包"),
    "not_authenticated": (401, "请先登录管理面板"),
    "permission_denied": (403, "当前账号没有此操作权限"),
    "invalid_credentials": (401, "用户名或密码错误"),
    "invalid_subuser": (400, "子用户资料无效"),
    "subuser_not_found": (404, "子用户不存在"),
    "username_exists": (409, "用户名已存在"),
    "rate_limited": (429, "请求过于频繁，请稍后重试"),
    "rcon_unconfigured": (503, "服务器连接尚未配置"),
    "rcon_auth_failed": (502, "服务器连接认证失败"),
    "rcon_unavailable": (503, "服务器当前不可达"),
    "rcon_timeout": (504, "读取服务器数据超时"),
    "rcon_rate_limited": (429, "服务器请求过于频繁"),
    "route_unsupported": (501, "服务器不支持此查询"),
    "invalid_upstream": (502, "服务器返回的数据无法解析"),
    "invalid_settings": (400, "服务器设置无效"),
    "settings_bearer_required": (400, "更换服务器地址时必须填写新的 Bearer"),
    "settings_key_unavailable": (503, "服务器设置加密密钥未配置"),
    "settings_public_http_disabled": (403, "部署未允许公网 HTTP RCON"),
    "stale_server_target": (409, "服务器设置已变化，请刷新玩家名单后重试"),
    "config_conflict": (409, "服务器配置版本已变化，请刷新后重试"),
    "config_interface_inconsistent": (503, "官方接口存在问题：配置返回内容无法核对一致，已禁用配置功能及相关整份配置写入"),
    "invalid_config": (400, "服务器配置文档无效"),
    "reserved_exists": (409, "该玩家已在预留位名单中"),
    "reserved_missing": (404, "该玩家不在预留位名单中"),
    "write_disabled": (403, "服务器管理操作尚未启用"),
    "invalid_moderation_target": (400, "玩家标识无效"),
    "invalid_moderation_reason": (400, "管理原因无效"),
    "player_not_online": (409, "该玩家当前不在线，请刷新名单后重试"),
    "invalid_selection": (400, "服务器选项无效"),
    "invalid_steam_ids": (400, "SteamID 列表无效"),
    "steam_unconfigured": (503, "Steam 资料查询尚未配置"),
    "steam_auth_failed": (502, "Steam 资料查询认证失败"),
    "steam_rate_limited": (429, "Steam 查询过于频繁，请稍后重试"),
    "steam_unavailable": (503, "Steam 资料服务当前不可达"),
    "steam_timeout": (504, "Steam 资料查询超时"),
    "steam_bad_response": (502, "Steam 资料无法解析"),
    "action_unsupported": (501, "服务器不支持此管理操作"),
    "action_rejected": (502, "服务器拒绝了管理操作"),
    "action_uncertain": (502, "结果不确定，请在 RCON 原管理界面核查后再决定是否重试"),
}

logger = logging.getLogger("wardogs.panel")


class PanelError(Exception):
    def __init__(self, code: str, status_code: int | None = None) -> None:
        if code not in _ERRORS:
            raise ValueError("unknown panel error code")
        self.code = code
        self.status_code = _ERRORS[code][0] if status_code is None else status_code
        self.message = _ERRORS[code][1]
        super().__init__(code)


def install_error_handlers(app: FastAPI) -> None:
    def response_for(request: Request, exc: PanelError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None) or str(uuid4())
        # Log only bounded internal metadata. Never log an upstream URL/body or a token.
        logger.info("request_id=%s code=%s status=%d", request_id, exc.code, exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "requestId": request_id},
            headers={"X-Request-ID": request_id},
        )

    @app.exception_handler(PanelError)
    async def panel_error_handler(request: Request, exc: PanelError) -> JSONResponse:
        return response_for(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, _exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default validation body can include the submitted password.
        if request.url.path == "/api/auth/login":
            code = "invalid_credentials"
        elif request.url.path == "/api/server/settings":
            code = "invalid_settings"
        elif request.url.path.startswith("/api/subusers"):
            code = "invalid_subuser"
        elif request.url.path.startswith("/api/server/config"):
            code = "invalid_config"
        elif request.url.path.startswith("/api/server/reserved-slots"):
            code = "invalid_moderation_target"
        elif request.url.path == "/api/steam/profiles":
            code = "invalid_steam_ids"
        elif request.url.path.startswith("/api/rules") or request.url.path.startswith("/api/warmup"):
            code = "invalid_selection"
        elif request.url.path == "/api/server/warnings":
            code = "invalid_moderation_reason" if request.method == "POST" else "invalid_moderation_target"
        elif request.url.path.startswith("/api/server/catalog/maps/") or request.url.path in {
            "/api/server/audit", "/api/server/unbans", "/api/server/kills",
            "/api/server/messages", "/api/server/factions", "/api/server/broadcast",
            "/api/server/match/end", "/api/server/match/restart", "/api/server/match/map",
            "/api/server/world/lighting",
        }:
            code = "invalid_selection"
        elif request.url.path in {"/api/server/bans", "/api/server/kicks"} or (
            request.url.path.startswith("/api/server/players/")
            and request.url.path.endswith("/kick")
        ):
            code = "invalid_moderation_reason"
        else:
            code = "route_unsupported"
        return response_for(request, PanelError(code))
