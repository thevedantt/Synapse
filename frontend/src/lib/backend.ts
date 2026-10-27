import { auth } from "@/lib/auth/server";

const json = (detail: string, status: number) => Response.json({ detail }, { status });
const UNAVAILABLE = "Synapse is temporarily unavailable. Please try again in a moment.";

/** Call the FastAPI backend as the signed-in user (JWT in Authorization header). */
export async function backendFetch(path: string, init: RequestInit = {}) {
  const { data: session } = await auth.getSession();
  if (!session?.user) return json("Not signed in", 401);

  const { data, error } = await auth.token();
  if (error || !data?.token) return json("Your session has expired. Please sign in again.", 401);

  let res: Response;
  try {
    res = await fetch(`${process.env.BACKEND_URL}${path}`, {
      ...init,
      headers: {
        ...init.headers,
        "Content-Type": "application/json",
        Authorization: `Bearer ${data.token}`,
      },
      cache: "no-store",
    });
  } catch (e) {
    console.error(`[synapse] backend unreachable at ${process.env.BACKEND_URL}${path}:`, e);
    return json(UNAVAILABLE, 503);
  }
  // Pass the backend's body through; replace a non-JSON body (e.g. a crash page) with a message.
  const text = await res.text();
  try {
    JSON.parse(text);
  } catch {
    console.error(`[synapse] backend sent non-JSON (${res.status}) for ${path}:`, text.slice(0, 500));
    return json(UNAVAILABLE, 502);
  }
  return new Response(text, { status: res.status, headers: { "Content-Type": "application/json" } });
}
