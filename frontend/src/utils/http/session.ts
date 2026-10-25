import type { PureHttpError } from "./types.d";

export function createSessionGuard(
  probe: () => Promise<unknown>,
  expire: () => void
) {
  let checking: Promise<void> | null = null;
  return async (error: PureHttpError) => {
    const code = (error.response?.data as { code?: string } | undefined)?.code;
    const url = error.config?.url ?? "";
    if (
      error.response?.status !== 401 ||
      !url.startsWith("/api/") ||
      url.startsWith("/api/auth/") ||
      ["invalid_credentials", "origin_mismatch"].includes(code ?? "")
    )
      return;
    if (!checking) {
      checking = (async () => {
        try {
          await probe();
        } catch (reason) {
          if ((reason as PureHttpError).response?.status === 401) expire();
        }
      })().finally(() => {
        checking = null;
      });
    }
    await checking;
  };
}
