"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { UserAvatar } from "@/components/user-avatar";
import { authClient } from "@/lib/auth/client";
import { fetchJson } from "@/lib/fetch-json";

type ToolCall = { name: string; input: Record<string, unknown> | null };
type Message = {
  role: "user" | "assistant";
  text: string;
  toolCalls?: ToolCall[];
  blocked?: boolean;
};
type Me = {
  name: string;
  email: string;
  role: string;
  region: string | null;
  image: string | null;
};
type ChatReply = {
  session_id: string;
  reply: string;
  tool_calls: ToolCall[];
  blocked: boolean;
};
type ChatSummary = { id: string; title: string; updated_at: string };
type Turn = {
  question: string;
  reply: string;
  tool_calls: ToolCall[];
  blocked?: boolean;
};

const SUGGESTIONS = [
  "Analyze last month's sales by region and channel",
  "What is our RTO rate for COD vs prepaid, and which courier is worst?",
  "Which products are out of stock or about to run out?",
];

export default function ChatPage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const loadChats = () =>
    fetchJson<ChatSummary[]>("/api/chats")
      .then(setChats)
      .catch(() => {}); // sidebar is optional; chat still works

  useEffect(() => {
    fetchJson<Me>("/api/me")
      .then((m) => {
        setMe(m);
        loadChats();
      })
      .catch((e: Error) => setAccessError(e.message));
  }, []);

  async function openChat(id: string) {
    setShowHistory(false);
    try {
      const chat = await fetchJson<{ turns: Turn[] }>(`/api/chats/${id}`);
      setSessionId(id);
      setMessages(
        chat.turns.flatMap((t): Message[] => [
          { role: "user", text: t.question },
          {
            role: "assistant",
            text: t.reply,
            toolCalls: t.tool_calls,
            blocked: t.blocked,
          },
        ]),
      );
    } catch {
      loadChats(); // it may have been deleted elsewhere
    }
  }

  function newChat() {
    setShowHistory(false);
    setSessionId(null);
    setMessages([]);
  }

  async function deleteChat(id: string) {
    await fetchJson(`/api/chats/${id}`, { method: "DELETE" }).catch(() => {});
    if (id === sessionId) newChat();
    loadChats();
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending]);

  async function send(text: string) {
    if (!text.trim() || pending) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text }]);
    setPending(true);
    try {
      const data = await fetchJson<ChatReply>("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: sessionId }),
      });
      setSessionId(data.session_id);
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          text: data.reply,
          toolCalls: data.tool_calls,
          blocked: data.blocked,
        },
      ]);
      setTimeout(loadChats, 1500); // the backend saves the chat just after replying
    } catch (e) {
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          text: `Error: ${(e as Error).message}`,
          blocked: true,
        },
      ]);
    } finally {
      setPending(false);
    }
  }

  async function signOut() {
    await authClient.signOut();
    router.push("/auth/sign-in");
  }

  return (
    <div className="flex h-dvh w-full">
      {/* Chat history: always visible on desktop, a slide-over on phones. */}
      {showHistory && (
        <div
          className="fixed inset-0 z-10 bg-black/60 md:hidden"
          onClick={() => setShowHistory(false)}
        />
      )}
      <aside
        className={`${showHistory ? "flex" : "hidden"} fixed inset-y-0 left-0 z-20 w-72 flex-col gap-2 border-r bg-background p-3 md:static md:flex md:w-64`}
      >
        <Button onClick={newChat}>New chat</Button>
        <nav className="flex flex-1 flex-col gap-1 overflow-y-auto">
          {chats.length === 0 && (
            <p className="px-2 py-4 text-sm text-muted-foreground">
              Your chats will appear here.
            </p>
          )}
          {chats.map((c) => (
            <div
              key={c.id}
              className={`group flex items-center rounded-lg text-sm hover:bg-muted ${
                c.id === sessionId ? "bg-muted" : ""
              }`}
            >
              <button
                className="min-w-0 flex-1 truncate px-2 py-2 text-left"
                onClick={() => openChat(c.id)}
              >
                {c.title}
              </button>
              <button
                aria-label={`Delete chat: ${c.title}`}
                className="px-2 py-2 text-muted-foreground hover:text-foreground md:opacity-0 md:group-hover:opacity-100"
                onClick={() => deleteChat(c.id)}
              >
                ×
              </button>
            </div>
          ))}
        </nav>
      </aside>

      <main className="mx-auto flex h-dvh w-full min-w-0 max-w-4xl flex-col">
        <header className="flex items-center justify-between gap-2 border-b px-3 py-3 sm:p-4">
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="md:hidden"
              onClick={() => setShowHistory(true)}
            >
              Chats
            </Button>
            <h1 className="text-xl font-semibold sm:text-2xl">
              Syn<span className="text-vibe-purple">apse</span>
            </h1>
          </div>
          <div className="flex items-center gap-2 sm:gap-3">
            {me && (
              <div className="flex items-center gap-2">
                <div className="hidden text-right text-sm sm:block">
                  <p>{me.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {me.role}
                    {me.region ? ` · ${me.region}` : ""}
                  </p>
                </div>
                <UserAvatar name={me.name} image={me.image} />
              </div>
            )}
            {me?.role === "admin" && (
              <Button
                variant="secondary"
                size="sm"
                nativeButton={false}
                render={<Link href="/admin" />}
              >
                Admin
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={signOut}>
              Sign out
            </Button>
          </div>
        </header>

        {accessError ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
            <p className="text-lg">Can&apos;t open chat right now.</p>
            <p className="text-sm text-muted-foreground">{accessError}</p>
          </div>
        ) : (
          <>
            <section className="flex flex-1 flex-col gap-4 overflow-y-auto p-3 sm:p-4">
              {messages.length === 0 && (
                <div className="m-auto flex max-w-md flex-col items-center gap-3 text-center">
                  <p className="text-muted-foreground">
                    Ask about sales, customers or products.
                  </p>
                  {SUGGESTIONS.map((s) => (
                    <Button
                      key={s}
                      variant="secondary"
                      className="h-auto w-full py-2 whitespace-normal"
                      onClick={() => send(s)}
                    >
                      {s}
                    </Button>
                  ))}
                </div>
              )}

              {messages.map((m, i) =>
                m.role === "user" ? (
                  <div
                    key={i}
                    className="max-w-[85%] self-end rounded-lg bg-primary px-4 py-2 break-words text-primary-foreground sm:max-w-[80%]"
                  >
                    {m.text}
                  </div>
                ) : (
                  <div
                    key={i}
                    className="flex max-w-full min-w-0 flex-col gap-2 self-start sm:max-w-[90%]"
                  >
                    {!!m.toolCalls?.length && (
                      <details className="rounded-lg border px-3 py-2 text-sm">
                        <summary className="cursor-pointer text-vibe-purple">
                          Used {m.toolCalls.length} tool call
                          {m.toolCalls.length > 1 ? "s" : ""}
                        </summary>
                        <ul className="mt-2 flex flex-col gap-2">
                          {m.toolCalls.map((c, j) => (
                            <li key={j}>
                              <Badge variant="secondary">{c.name}</Badge>
                              {typeof c.input?.sql === "string" && (
                                <pre className="mt-1 overflow-x-auto rounded bg-muted p-2 text-xs">
                                  {c.input.sql}
                                </pre>
                              )}
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                    <div
                      className={`prose-chat min-w-0 rounded-lg px-3 py-3 break-words sm:px-4 ${
                        m.blocked ? "border border-vibe-purple" : "bg-muted"
                      }`}
                    >
                      <ReactMarkdown>{m.text}</ReactMarkdown>
                    </div>
                  </div>
                ),
              )}

              {pending && (
                <div className="self-start rounded-lg bg-muted px-4 py-3 text-sm text-vibe-purple">
                  Thinking and querying data…
                </div>
              )}
              <div ref={bottomRef} />
            </section>

            <form
              onSubmit={(e) => {
                e.preventDefault();
                send(input);
              }}
              className="flex gap-2 border-t p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:p-4"
            >
              <Input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask Synapse anything…"
                maxLength={4000}
                disabled={pending}
              />
              <Button type="submit" disabled={pending || !input.trim()}>
                Send
              </Button>
            </form>
          </>
        )}
      </main>
    </div>
  );
}
