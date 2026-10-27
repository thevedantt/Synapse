/** fetch + JSON that never crashes on an empty/non-JSON body. Throws Error(detail) on failure. */
export async function fetchJson<T = unknown>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    // fall through: treated as an error below if the status is bad
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    const msg = Array.isArray(detail)
      ? detail.map((d: { msg?: string }) => d.msg).join(", ")
      : typeof detail === "string"
        ? detail
        : "Something went wrong. Please try again.";
    throw new Error(msg);
  }
  return data as T;
}
