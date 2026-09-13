// Shared fetch wrapper — replicates frontend/app.js apiFetch semantics:
// JSON headers, FastAPI `detail` error extraction, 204 -> null.

export async function apiFetch<T = unknown>(
  url: string,
  options: RequestInit = {}
): Promise<T> {
  const { headers, ...rest } = options;
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json", ...headers },
    ...rest,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const err = await resp.json();
      detail = err.detail || detail;
    } catch {
      // ignore parse failures — fall back to statusText
    }
    throw new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail)
    );
  }
  if (resp.status === 204) return null as T;
  return resp.json() as Promise<T>;
}
