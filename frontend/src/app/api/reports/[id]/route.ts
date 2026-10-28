import { backendFetch } from "@/lib/backend";

// Downloads a report as a .md file (backend checks the user owns it).
export async function GET(_request: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const res = await backendFetch(`/reports/${encodeURIComponent(id)}`);
  if (!res.ok) return new Response("Report not available.", { status: res.status });
  const { title, markdown } = await res.json();
  const filename = `${title.replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "-") || "report"}.md`;
  return new Response(markdown, {
    headers: {
      "Content-Type": "text/markdown; charset=utf-8",
      "Content-Disposition": `attachment; filename="${filename}"`,
    },
  });
}
