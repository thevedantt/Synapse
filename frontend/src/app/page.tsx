import { Badge } from "@/components/ui/badge";
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
import { Textarea } from "@/components/ui/textarea";

// Temporary theme preview page. Replace with the chat UI later.
const swatches = [
  { name: "Deep Purple", hex: "#5B0A70", className: "bg-deep-purple" },
  { name: "Vibe Purple", hex: "#AC58E9", className: "bg-vibe-purple" },
  { name: "Lavender", hex: "#DCC8EA", className: "bg-lavender" },
  { name: "Grey Black", hex: "#181818", className: "bg-grey-black border" },
  { name: "White", hex: "#FFFFFF", className: "bg-white" },
];

const tools = ["PostgreSQL", "Google Sheets", "Slack", "CRM"];

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-10 p-8">
      <header className="flex items-center justify-between">
        <h1 className="text-4xl font-semibold tracking-tight">
          Syn<span className="text-vibe-purple">apse</span>
        </h1>
        <Badge>MCP Agent</Badge>
      </header>

      <section className="grid grid-cols-2 gap-4 sm:grid-cols-5">
        {swatches.map((s) => (
          <div key={s.name} className="flex flex-col gap-2">
            <div className={`h-20 rounded-lg ${s.className}`} />
            <p className="text-sm font-medium">{s.name}</p>
            <p className="text-xs text-muted-foreground">{s.hex}</p>
          </div>
        ))}
      </section>

      <section className="flex flex-wrap gap-3">
        <Button>Primary</Button>
        <Button variant="secondary">Secondary</Button>
        <Button variant="outline">Outline</Button>
        <Button variant="ghost">Ghost</Button>
        <Button variant="destructive">Destructive</Button>
        <Button variant="link">Link</Button>
      </section>

      <section className="flex flex-wrap gap-2">
        <Badge>Default</Badge>
        <Badge variant="secondary">Secondary</Badge>
        <Badge variant="outline">Outline</Badge>
        <Badge variant="destructive">Destructive</Badge>
      </section>

      <section className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Chat preview</CardTitle>
            <CardDescription>How agent replies will look</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="self-end rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground">
              Analyze last month&apos;s sales and create a report.
            </div>
            <div className="self-start rounded-lg bg-muted px-4 py-2 text-sm">
              <span className="text-vibe-purple">calling postgres.run_sql…</span>
              <br />
              Revenue up 12% vs previous month. Report sent to Slack.
            </div>
          </CardContent>
          <CardFooter className="gap-2">
            <Input placeholder="Ask Synapse anything…" />
            <Button>Send</Button>
          </CardFooter>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Connected tools</CardTitle>
            <CardDescription>Discovered via MCP</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-wrap gap-2">
              {tools.map((t) => (
                <Badge key={t} variant="secondary">
                  {t}
                </Badge>
              ))}
            </div>
            <Textarea placeholder="System prompt…" />
          </CardContent>
          <CardFooter>
            <Button variant="outline" className="w-full">
              Add tool
            </Button>
          </CardFooter>
        </Card>
      </section>
    </main>
  );
}
