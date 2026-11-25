export interface SampleItem {
  id: number;
  title: string;
}

export function createSampleHandler() {
  const items: SampleItem[] = [{ id: 1, title: "Starter API Health Check" }];

  return async function fetchHandler(req: Request): Promise<Response> {
    const url = new URL(req.url);

    if (req.method === "GET" && url.pathname === "/health") {
      return Response.json({ status: "ok", ready: true });
    }

    if (req.method === "GET" && url.pathname === "/items") {
      return Response.json({ items });
    }

    if (req.method === "POST" && url.pathname === "/items") {
      const body = (await req.json().catch(() => null)) as { title?: unknown } | null;
      if (!body || typeof body.title !== "string" || body.title.trim().length < 2) {
        return Response.json({ error: "INVALID_TITLE" }, { status: 400 });
      }
      const next: SampleItem = { id: items.length + 1, title: body.title.trim() };
      items.push(next);
      return Response.json({ item: next }, { status: 201 });
    }

    return Response.json({ error: "NOT_FOUND" }, { status: 404 });
  };
}

if (import.meta.main) {
  const port = Number(process.env.PORT ?? 3099);
  const server = Bun.serve({ port, fetch: createSampleHandler() });
  console.log(`Sample API listening on http://localhost:${server.port}`);
}
