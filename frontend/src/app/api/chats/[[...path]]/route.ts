import { backendFetch } from "@/lib/backend";

// /api/chats and /api/chats/<id> -> backend /chats (backend only returns the user's own chats).
async function forward(
  request: Request,
  ctx: { params: Promise<{ path?: string[] }> },
) {
  const { path = [] } = await ctx.params;
  return backendFetch(["/chats", ...path.map(encodeURIComponent)].join("/"), {
    method: request.method,
  });
}

export { forward as GET, forward as DELETE };
