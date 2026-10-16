export class ApiError extends Error {
  constructor(
    message: string,
    public readonly code = "NETWORK_ERROR",
  ) {
    super(message);
  }
}
export function serverAddress(raw: string): string {
  const url = new URL(raw.trim());
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    throw new ApiError(
      "服务器地址必须是 HTTP(S) 地址，且不能包含账号、Key、查询参数或片段。",
      "INVALID_SERVER",
    );
  }
  return url.href.replace(/\/$/, "");
}
export class Api {
  constructor(
    public readonly base: string,
    private readonly key: string,
    private readonly log: (message: string) => void,
  ) {}
  redact(value: string): string {
    return this.key ? value.split(this.key).join("[REDACTED]") : value;
  }
  async request<T>(
    path: string,
    init: RequestInit = {},
    binary = false,
  ): Promise<T> {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 120_000);
    try {
      const headers = new Headers(init.headers);
      headers.set("Authorization", `Bearer ${this.key}`);
      const response = await fetch(`${this.base}${path}`, {
        ...init,
        headers,
        signal: controller.signal,
        credentials: "omit",
      });
      this.log(`${init.method ?? "GET"} ${path} → ${response.status}`);
      if (!response.ok) {
        if (response.status === 401)
          throw new ApiError(
            "API Key 不正确或已失效，请检查后重新连接。",
            "UNAUTHORIZED",
          );
        const body = await response.json().catch(() => ({}));
        const code: string = body.error?.code ?? `HTTP_${response.status}`;
        const message =
          response.status === 410
            ? "素材或结果已过期，请重新上传并生成。"
            : (body.error?.message ??
              `请求失败（${response.status}），请重试。`);
        throw new ApiError(this.redact(message), code);
      }
      return (binary ? await response.blob() : await response.json()) as T;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(
        "无法连接服务器或请求超时，请检查地址、网络与服务器的跨域设置。",
      );
    } finally {
      window.clearTimeout(timeout);
    }
  }
  json<T>(path: string, body: unknown): Promise<T> {
    return this.request(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify(body),
    });
  }
  async download(path: string, filename: string): Promise<void> {
    const blob = await this.request<Blob>(path, {}, true);
    const url = URL.createObjectURL(blob);
    try {
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
    } finally {
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  }
}
