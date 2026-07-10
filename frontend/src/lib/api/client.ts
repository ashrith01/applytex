export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") || "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((d: { msg?: string }) => d.msg || "").join("; ");
    }
  } catch {
    /* ignore */
  }
  return response.statusText || "Request failed";
}

export async function apiFetch<T>(
  path: string,
  init?: RequestInit & { profileId?: string },
): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.profileId) {
    headers.set("X-Profile-Id", init.profileId);
  }
  if (init?.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });

  if (!response.ok) {
    throw new ApiError(await parseError(response), response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export async function apiUpload<T>(
  path: string,
  formData: FormData,
  profileId?: string,
): Promise<T> {
  const headers = new Headers();
  if (profileId) headers.set("X-Profile-Id", profileId);
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    body: formData,
    headers,
  });
  if (!response.ok) {
    throw new ApiError(await parseError(response), response.status);
  }
  return response.json() as Promise<T>;
}
