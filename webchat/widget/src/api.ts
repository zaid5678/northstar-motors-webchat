// Talks to the webchat server. The session is an HTTP-only cookie, and no key ever reaches here.

export interface Card {
  id: string;
  title: string;
  price: string;
  availability: string;
  url: string;
  image: string | null;
}

export interface Button {
  id: string;
  label: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  notices?: string[];
  actions?: Button[];
  vehicles?: Card[];
}

export interface Field {
  name: string;
  label: string;
  kind: "text" | "email" | "tel" | "number" | "textarea" | "select";
  options: { value: string; label: string }[];
  value: string | null;
}

export interface Block {
  title: string;
  rows: { label: string; value: string }[];
  warnings: string[];
  token: string;
  expires_at: string;
  button: string;
}

export interface View {
  status: string;
  message: string;
  block: Block | null;
  prompts: string[];
  notices: string[];
  fields: Field[];
  reference: string | null;
  can_check_again: boolean;
}

export interface Transcript {
  messages: ChatMessage[];
  pending: boolean;
  last_event_id: number;
  actions: Button[];
  view: View;
}

export type Inspection = Record<string, unknown>;
type OnStage = (stage: string) => void;

class Tracker {
  lastId = 0;
}

export class Api {
  constructor(private readonly base: string) {}

  private request(path: string, init?: RequestInit): Promise<Response> {
    return fetch(`${this.base}${path}`, { credentials: "include", ...init });
  }

  private post(path: string, body?: unknown): Promise<Response> {
    return this.request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  }

  private async json<T>(response: Response): Promise<T> {
    if (!response.ok) throw new Error(`request failed: ${response.status}`);
    return response.json();
  }

  async begin(): Promise<void> {
    await this.json(await this.post("/api/session"));
  }

  async transcript(): Promise<Transcript> {
    return this.json(await this.request("/api/chat/transcript"));
  }

  async state(): Promise<View> {
    return this.json(await this.request("/api/conversation/state"));
  }

  async press(id: string): Promise<ChatMessage> {
    const reply = await this.json<Omit<ChatMessage, "role">>(
      await this.post("/api/chat/action", { id }),
    );
    return { role: "assistant", ...reply };
  }

  async fill(name: string, value: string | number): Promise<View> {
    return this.json(await this.post("/api/chat/slot", { name, value }));
  }

  async identity(values: Record<string, string>): Promise<View> {
    return this.json(await this.post("/api/chat/identity", values));
  }

  async confirm(token: string): Promise<View> {
    return this.json(await this.post("/api/confirmations", { token }));
  }

  async checkAgain(): Promise<View> {
    return this.json(await this.post("/api/confirmations/check-again"));
  }

  /** Only answers when the server has the inspector switched on. */
  async inspect(): Promise<Inspection | null> {
    const response = await this.request("/api/dev/inspect");
    return response.ok ? response.json() : null;
  }

  /** Reads server-sent events from a fetch body, keeping the last ID so it can resume. */
  private async read(response: Response, onStage: OnStage, tracker: Tracker): Promise<ChatMessage> {
    if (!response.ok || !response.body) throw new Error("no stream");
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) throw new Error("the stream ended without a reply");
      buffer += value;
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const id = /^id: (\d+)$/m.exec(block)?.[1];
        if (id) tracker.lastId = Number(id);
        const event = /^event: (.+)$/m.exec(block)?.[1];
        const data = /^data: (.+)$/m.exec(block)?.[1];
        if (!event || !data) continue;
        if (event === "progress") onStage(JSON.parse(data).stage);
        if (event === "reply") return { role: "assistant", ...JSON.parse(data) };
        if (event === "error") throw new Error("the turn failed");
      }
    }
  }

  async resume(after: number, onStage: OnStage): Promise<ChatMessage> {
    const response = await this.request("/api/chat/stream", {
      headers: { "Last-Event-ID": String(after) },
    });
    return this.read(response, onStage, new Tracker());
  }

  async send(text: string, pageVehicle: string | null, onStage: OnStage): Promise<ChatMessage> {
    const tracker = new Tracker();
    const response = await this.post("/api/chat/stream", { text, page_vehicle: pageVehicle });
    try {
      return await this.read(response, onStage, tracker);
    } catch (error) {
      // The server has the turn once an event has arrived, so pick up where the stream stopped.
      if (tracker.lastId === 0) throw error;
      return this.resume(tracker.lastId, onStage);
    }
  }
}
