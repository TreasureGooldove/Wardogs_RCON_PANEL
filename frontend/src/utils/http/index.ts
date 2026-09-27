import Axios, {
  type AxiosInstance,
  type AxiosRequestConfig,
  type CustomParamsSerializer
} from "axios";
import type {
  PureHttpError,
  RequestMethods,
  PureHttpResponse,
  PureHttpRequestConfig
} from "./types.d";
import { stringify } from "qs";
import { useUserStoreHook } from "@/store/modules/user";

const defaultConfig: AxiosRequestConfig = {
  timeout: 10000,
  withCredentials: true,
  headers: {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Requested-With": "XMLHttpRequest"
  },
  paramsSerializer: {
    serialize: stringify as unknown as CustomParamsSerializer
  }
};

class PureHttp {
  private static initConfig: PureHttpRequestConfig = {};
  private static axiosInstance: AxiosInstance = Axios.create(defaultConfig);

  constructor() {
    this.httpInterceptorsRequest();
    this.httpInterceptorsResponse();
  }

  private httpInterceptorsRequest(): void {
    PureHttp.axiosInstance.interceptors.request.use(
      config => {
        const request = config as PureHttpRequestConfig;
        if (typeof request.beforeRequestCallback === "function") {
          request.beforeRequestCallback(request);
        } else {
          PureHttp.initConfig.beforeRequestCallback?.(request);
        }
        return config;
      },
      error => Promise.reject(error)
    );
  }

  private httpInterceptorsResponse(): void {
    PureHttp.axiosInstance.interceptors.response.use(
      (response: PureHttpResponse) => {
        const config = response.config;
        if (typeof config.beforeResponseCallback === "function") {
          config.beforeResponseCallback(response);
        } else {
          PureHttp.initConfig.beforeResponseCallback?.(response);
        }
        return response.data;
      },
      (error: PureHttpError) => {
        error.isCancelRequest = Axios.isCancel(error);
        if (
          error.response?.status === 401 &&
          error.config?.url?.startsWith("/api/server/")
        ) {
          useUserStoreHook().clearSession();
          window.dispatchEvent(new Event("panel-session-expired"));
        }
        return Promise.reject(error);
      }
    );
  }

  public request<T>(
    method: RequestMethods,
    url: string,
    param?: AxiosRequestConfig,
    axiosConfig?: PureHttpRequestConfig
  ): Promise<T> {
    return PureHttp.axiosInstance.request({
      method,
      url,
      ...param,
      ...axiosConfig
    }) as Promise<T>;
  }

  public post<T, P>(
    url: string,
    params?: AxiosRequestConfig<P>,
    config?: PureHttpRequestConfig
  ): Promise<T> {
    return this.request<T>("post", url, params, config);
  }

  public get<T, P>(
    url: string,
    params?: AxiosRequestConfig<P>,
    config?: PureHttpRequestConfig
  ): Promise<T> {
    return this.request<T>("get", url, params, config);
  }
}

export const http = new PureHttp();
