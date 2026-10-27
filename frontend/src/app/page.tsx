import Link from "next/link";

import { Button } from "@/components/ui/button";

export default function Home() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-6 p-8 text-center">
      <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
        Syn<span className="text-vibe-purple">apse</span>
      </h1>
      <p className="max-w-md text-muted-foreground">
        Ask questions about your company data in plain English. Synapse finds the right
        tools, queries the data you&apos;re allowed to see, and explains the results.
      </p>
      <div className="flex w-full max-w-xs flex-col gap-3 sm:w-auto sm:max-w-none sm:flex-row">
        <Button nativeButton={false} render={<Link href="/chat" />}>Open chat</Button>
        <Button variant="outline" nativeButton={false} render={<Link href="/auth/sign-up" />}>
          Create account
        </Button>
      </div>
    </main>
  );
}
