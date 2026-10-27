import { backendFetch } from "@/lib/backend";

export async function POST(request: Request) {
  return backendFetch("/chat", { method: "POST", body: await request.text() });
}
