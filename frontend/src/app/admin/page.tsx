"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { UserAvatar } from "@/components/user-avatar";
import { fetchJson } from "@/lib/fetch-json";

type Role = "admin" | "manager" | "analyst";
type AppUser = { email: string; name: string; role: Role; region: string | null; image?: string | null };
type Me = { name: string; role: string; image: string | null };
type AuditRow = {
  id: number;
  at: string;
  user_email: string;
  role: string;
  question: string;
  tool_calls: { name: string; input: { sql?: string } | null }[];
  status: "ok" | "blocked" | "error";
  detail: string | null;
};

const ROLES: Role[] = ["admin", "manager", "analyst"];
const REGIONS = ["North", "South", "East", "West"];
const selectClass =
  "h-8 rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const api = (path: string, init?: RequestInit): Promise<any> =>
  fetchJson(`/api/admin/${path}`, { ...init, headers: { "Content-Type": "application/json" } });

export default function AdminPage() {
  const [users, setUsers] = useState<AppUser[]>([]);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<AppUser>({ email: "", name: "", role: "analyst", region: null });

  const load = useCallback(
    () =>
      Promise.all([api("users"), api("audit?limit=50"), fetchJson<Me>("/api/me")])
        .then(([u, a, m]) => {
          setUsers(u);
          setAudit(a);
          setMe(m);
          setError(null);
        })
        .catch((e: Error) => setError(e.message)),
    [],
  );

  useEffect(() => {
    load();
  }, [load]);

  async function save(user: AppUser) {
    try {
      const { email, name, role, region } = user; // don't send the photo back
      await api("users", { method: "PUT", body: JSON.stringify({ email, name, role, region }) });
      await load();
      return true;
    } catch (e) {
      setError((e as Error).message);
      await load(); // revert the row to what the server has
      return false;
    }
  }

  async function remove(email: string) {
    if (!confirm(`Remove ${email}? They will lose access to Synapse.`)) return;
    try {
      await api(`users/${encodeURIComponent(email)}`, { method: "DELETE" });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-4 p-3 sm:gap-6 sm:p-6">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold sm:text-2xl">
          Syn<span className="text-vibe-purple">apse</span>{" "}
          <span className="text-muted-foreground">Admin</span>
        </h1>
        <div className="flex items-center gap-3">
          <Button variant="outline" size="sm" nativeButton={false} render={<Link href="/chat" />}>
            Back to chat
          </Button>
          {me && <UserAvatar name={me.name} image={me.image} />}
        </div>
      </header>

      {error && (
        <p role="alert" className="rounded-lg border border-vibe-purple px-4 py-2 text-sm">
          {error}
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Users</CardTitle>
          <CardDescription>
            Who can use Synapse and what data they see. Managers only see their region; analysts
            can&apos;t see customer emails.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <form
            className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center"
            onSubmit={async (e) => {
              e.preventDefault();
              if (await save(draft)) setDraft({ email: "", name: "", role: "analyst", region: null });
            }}
          >
            <Input
              className="w-full sm:w-56"
              type="email"
              placeholder="Email"
              required
              value={draft.email}
              onChange={(e) => setDraft({ ...draft, email: e.target.value })}
            />
            <Input
              className="w-full sm:w-44"
              placeholder="Name"
              required
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            />
            <select
              className={selectClass}
              value={draft.role}
              onChange={(e) => setDraft({ ...draft, role: e.target.value as Role })}
            >
              {ROLES.map((r) => (
                <option key={r} value={r} className="bg-grey-black">
                  {r}
                </option>
              ))}
            </select>
            {draft.role === "manager" && (
              <select
                className={selectClass}
                required
                value={draft.region ?? ""}
                onChange={(e) => setDraft({ ...draft, region: e.target.value || null })}
              >
                <option value="" className="bg-grey-black">
                  Region…
                </option>
                {REGIONS.map((r) => (
                  <option key={r} value={r} className="bg-grey-black">
                    {r}
                  </option>
                ))}
              </select>
            )}
            <Button type="submit">Add user</Button>
          </form>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-muted-foreground">
                <tr className="border-b">
                  <th className="py-2 pr-4 font-medium">Name</th>
                  <th className="hidden py-2 pr-4 font-medium md:table-cell">Email</th>
                  <th className="py-2 pr-4 font-medium">Access</th>
                  <th className="hidden md:table-cell" />
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.email} className="border-b last:border-0">
                    <td className="py-2 pr-4">
                      <div className="flex items-center gap-2">
                        <UserAvatar name={u.name} image={u.image} />
                        <div className="min-w-0">
                          <p>{u.name}</p>
                          <p className="truncate text-xs text-muted-foreground md:hidden">{u.email}</p>
                        </div>
                      </div>
                    </td>
                    <td className="hidden py-2 pr-4 text-muted-foreground md:table-cell">{u.email}</td>
                    <td className="py-2 pr-2 md:pr-4">
                      {/* Role + region side by side on desktop, stacked with Remove on phones. */}
                      <div className="flex flex-col items-start gap-1 md:flex-row md:items-center md:gap-2">
                        <select
                          className={selectClass}
                          value={u.role}
                          onChange={(e) => {
                            const role = e.target.value as Role;
                            save({ ...u, role, region: role === "manager" ? u.region ?? "North" : null });
                          }}
                        >
                          {ROLES.map((r) => (
                            <option key={r} value={r} className="bg-grey-black">
                              {r}
                            </option>
                          ))}
                        </select>
                        {u.role === "manager" ? (
                          <select
                            className={selectClass}
                            value={u.region ?? ""}
                            onChange={(e) => save({ ...u, region: e.target.value })}
                          >
                            {REGIONS.map((r) => (
                              <option key={r} value={r} className="bg-grey-black">
                                {r}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <span className="hidden text-xs text-muted-foreground md:inline">all regions</span>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          className="px-1 md:hidden"
                          onClick={() => remove(u.email)}
                        >
                          Remove
                        </Button>
                      </div>
                    </td>
                    <td className="hidden py-2 text-right md:table-cell">
                      <Button variant="ghost" size="sm" onClick={() => remove(u.email)}>
                        Remove
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Audit log</CardTitle>
          <CardDescription>Last 50 questions: who asked, what ran, what was blocked.</CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-muted-foreground">
              <tr className="border-b">
                <th className="hidden py-2 pr-4 font-medium md:table-cell">Time</th>
                <th className="py-2 pr-4 font-medium">User</th>
                <th className="py-2 pr-4 font-medium">Question</th>
                <th className="py-2 pr-4 font-medium">Result</th>
              </tr>
            </thead>
            <tbody>
              {audit.map((a) => (
                <tr key={a.id} className="border-b align-top last:border-0">
                  <td className="hidden py-2 pr-4 whitespace-nowrap text-muted-foreground md:table-cell">
                    {new Date(a.at).toLocaleString("en-IN")}
                  </td>
                  <td className="py-2 pr-4">
                    {a.user_email}
                    <div className="text-xs text-muted-foreground">{a.role}</div>
                    <div className="text-xs text-muted-foreground md:hidden">
                      {new Date(a.at).toLocaleString("en-IN")}
                    </div>
                  </td>
                  <td className="max-w-xs py-2 pr-4 break-words sm:max-w-md">
                    {a.question}
                    {a.tool_calls.some((c) => c.input?.sql) && (
                      <details className="mt-1">
                        <summary className="cursor-pointer text-xs text-vibe-purple">
                          SQL ({a.tool_calls.filter((c) => c.input?.sql).length})
                        </summary>
                        {a.tool_calls
                          .filter((c) => c.input?.sql)
                          .map((c, i) => (
                            <pre key={i} className="mt-1 overflow-x-auto rounded bg-muted p-2 text-xs">
                              {c.input!.sql}
                            </pre>
                          ))}
                      </details>
                    )}
                  </td>
                  <td className="py-2 pr-4">
                    <Badge variant={a.status === "ok" ? "secondary" : "outline"}>{a.status}</Badge>
                    {a.detail && <div className="mt-1 text-xs text-muted-foreground">{a.detail}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </main>
  );
}
