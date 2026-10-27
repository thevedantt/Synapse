import { backendFetch } from "@/lib/backend";

// Forwards /api/admin/* to the backend's /admin/* (backend checks the user is an admin).
async function forward(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const query = new URL(request.url).search;
  const body = ["GET", "DELETE"].includes(request.method) ? undefined : await request.text();
  return backendFetch(`/admin/${path.map(encodeURIComponent).join("/")}${query}`, {
    method: request.method,
    body,
  });
}

export { forward as GET, forward as PUT, forward as DELETE };
