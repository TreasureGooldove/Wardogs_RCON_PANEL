export interface UpdaterState {
  agentAvailable: boolean;
  agent?: { status: string; lastSeenAt?: string; ageSeconds?: number };
}

export function updaterNotice(state: UpdaterState | null, failed = false) {
  if (failed)
    return {
      type: "error" as const,
      title: "无法读取更新服务状态",
      detail:
        "状态接口请求失败，暂时无法判断更新服务是否可用；请检查面板连接后重试。"
    };
  if (!state)
    return {
      type: "info" as const,
      title: "正在检测宿主机更新服务",
      detail: "版本检查与安装服务分开检测，请稍候。"
    };
  if (state.agentAvailable)
    return {
      type: "success" as const,
      title: "宿主机更新服务可用",
      detail: "一键安装会短暂重启面板；后台自动安装仍需单独启用。"
    };
  const notices: Record<string, { title: string; detail: string }> = {
    missing: {
      title: "未检测到更新服务心跳",
      detail:
        "可能尚未安装宿主机更新服务，或容器与服务未使用同一数据目录。检查版本和手动下载仍可使用。"
    },
    stale: {
      title: "更新服务心跳已过期",
      detail:
        "检查宿主机 service 和 timer；若已有更新任务正在执行，请先查看日志，不要重复提交。"
    },
    unreadable: {
      title: "面板无法读取更新服务心跳",
      detail: "请检查共享数据目录的挂载与权限，不要将数据目录开放给所有用户。"
    },
    invalid: {
      title: "更新服务心跳格式无效",
      detail:
        "请检查宿主机更新服务日志和版本；心跳文件应由服务生成，不要手动伪造。"
    },
    clock_skew: {
      title: "更新服务心跳时间异常",
      detail: "心跳时间晚于面板时间，请检查宿主机时间同步。"
    }
  };
  return {
    type: "warning" as const,
    ...(notices[state.agent?.status ?? ""] ?? {
      title: "宿主机更新服务暂不可用",
      detail: "当前版本未提供详细原因，请按部署教程检查服务、共享目录和心跳。"
    })
  };
}
