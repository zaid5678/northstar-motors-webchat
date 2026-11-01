import { afterEach, describe, expect, it, vi } from "vitest";
import { Api } from "../src/api";

function stream(...frames: string[]): Response {
  return new Response(frames.join(""), { headers: { "Content-Type": "text/event-stream" } });
}

afterEach(() => vi.unstubAllGlobals());

describe("resuming a stream", () => {
  it("sends the last event ID, so an old reply is not replayed", async () => {
    const fetcher = vi.fn().mockResolvedValue(stream('id: 8\nevent: reply\ndata: {"text":"new"}\n\n'));
    vi.stubGlobal("fetch", fetcher);
    await new Api("http://chat.test").resume(7, () => undefined);
    expect(fetcher.mock.calls[0][1].headers["Last-Event-ID"]).toBe("7");
  });

  it("continues from the ID it last saw when a stream drops", async () => {
    const dropped = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('id: 5\nevent: progress\ndata: {"stage":"checking"}\n\n'));
      },
      pull(controller) {
        controller.error(new TypeError("network dropped"));
      },
    });
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response(dropped))
      .mockResolvedValueOnce(stream('id: 6\nevent: reply\ndata: {"text":"done"}\n\n'));
    vi.stubGlobal("fetch", fetcher);
    const reply = await new Api("http://chat.test").send("hi", null, () => undefined);
    expect(reply.text).toBe("done");
    expect(fetcher.mock.calls[1][1].headers["Last-Event-ID"]).toBe("5");
  });
});
