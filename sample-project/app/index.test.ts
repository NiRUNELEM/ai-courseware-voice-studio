import { describe, expect, it } from "bun:test";
import { createSampleHandler } from "./index";

describe("Sample Starter API", () => {
  it("returns 200 OK on GET /health", async () => {
    const handler = createSampleHandler();
    const res = await handler(new Request("http://localhost/health"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "ok", ready: true });
  });

  it("creates and lists items cleanly and rejects invalid title with 400", async () => {
    const handler = createSampleHandler();

    const bad = await handler(
      new Request("http://localhost/items", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ title: "" }),
      }),
    );
    expect(bad.status).toBe(400);

    const created = await handler(
      new Request("http://localhost/items", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ title: "Automated Testing Sample" }),
      }),
    );
    expect(created.status).toBe(201);

    const list = await handler(new Request("http://localhost/items"));
    const data = (await list.json()) as { items: Array<{ id: number; title: string }> };
    expect(data.items.length).toBe(2);
  });
});
