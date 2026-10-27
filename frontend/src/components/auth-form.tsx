"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { authClient } from "@/lib/auth/client";

export function AuthForm({ mode }: { mode: "sign-in" | "sign-up" }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const isSignUp = mode === "sign-up";

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const email = String(form.get("email"));
    const password = String(form.get("password"));
    setPending(true);
    setError(null);
    try {
      const { error } = isSignUp
        ? await authClient.signUp.email({ email, password, name: String(form.get("name")) })
        : await authClient.signIn.email({ email, password });
      if (error) setError(error.message ?? "Something went wrong");
      else router.push("/chat");
    } catch {
      setError("Something went wrong");
    } finally {
      setPending(false);
    }
  }

  async function google() {
    setError(null);
    const { error } = await authClient.signIn.social({ provider: "google", callbackURL: "/chat" });
    if (error) setError(error.message ?? "Google sign-in failed");
  }

  return (
    <main className="flex flex-1 items-center justify-center p-4 sm:p-6">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-2xl">
            Syn<span className="text-vibe-purple">apse</span>
          </CardTitle>
          <CardDescription>
            {isSignUp ? "Create your account" : "Sign in to your account"}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Button variant="outline" onClick={google} type="button">
            Continue with Google
          </Button>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <div className="h-px flex-1 bg-border" /> or <div className="h-px flex-1 bg-border" />
          </div>
          <form onSubmit={onSubmit} className="flex flex-col gap-3">
            {isSignUp && <Input name="name" placeholder="Full name" required />}
            <Input name="email" type="email" placeholder="Email" required autoComplete="email" />
            <Input
              name="password"
              type="password"
              placeholder="Password (min 8 characters)"
              minLength={8}
              required
              autoComplete={isSignUp ? "new-password" : "current-password"}
            />
            <Button type="submit" disabled={pending}>
              {pending ? "Please wait…" : isSignUp ? "Create account" : "Sign in"}
            </Button>
            {error && (
              <p role="alert" className="text-sm text-vibe-purple">
                {error}
              </p>
            )}
          </form>
        </CardContent>
        <CardFooter className="text-sm text-muted-foreground">
          {isSignUp ? "Already have an account? " : "New here? "}
          <Link
            href={isSignUp ? "/auth/sign-in" : "/auth/sign-up"}
            className="ml-1 text-vibe-purple hover:underline"
          >
            {isSignUp ? "Sign in" : "Create an account"}
          </Link>
        </CardFooter>
      </Card>
    </main>
  );
}
