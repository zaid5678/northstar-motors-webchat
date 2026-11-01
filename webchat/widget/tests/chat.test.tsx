import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Api, ChatMessage, Transcript, View } from "../src/api";
import { Chat } from "../src/Chat";

const collecting: View = {
  status: "collecting",
  message: "",
  prompts: [],
  notices: [],
  block: null,
  reference: null,
  can_check_again: false,
  fields: [
    { name: "email", label: "Email", kind: "email", options: [], value: null },
    {
      name: "department",
      label: "Department",
      kind: "select",
      options: [{ value: "sales", label: "Sales" }],
      value: null,
    },
  ],
};
const confirming: View = {
  ...collecting,
  status: "confirming",
  fields: [],
  block: {
    title: "Request a callback",
    rows: [{ label: "About", value: "Finance" }],
    warnings: ["Once cancelled, this time may not be available again."],
    token: "t",
    expires_at: "",
    button: "Confirm",
  },
};
const idle: View = { ...collecting, status: "idle", fields: [] };
const reply: ChatMessage = {
  role: "assistant",
  text: "Hello",
  vehicles: [
    {
      id: "veh-001",
      title: "2020 BMW 1 Series",
      price: "£18,500",
      availability: "available",
      url: "http://localhost:4173/?vehicle=veh-001",
      image: "http://localhost:4010/assets/vehicles/veh-001.jpg",
    },
  ],
  notices: ["Finance is subject to status."],
};

function fakeApi(overrides: Partial<Record<keyof Api, unknown>> = {}, saved: Partial<Transcript> = {}): Api {
  const transcript: Transcript = {
    messages: [],
    pending: false,
    last_event_id: 0,
    view: idle,
    actions: [{ id: "callback", label: "Request a callback" }],
    ...saved,
  };
  return {
    begin: vi.fn().mockResolvedValue(undefined),
    transcript: vi.fn().mockResolvedValue(transcript),
    state: vi.fn().mockResolvedValue(transcript.view),
    send: vi.fn(),
    resume: vi.fn(),
    press: vi.fn(),
    fill: vi.fn(),
    identity: vi.fn(),
    confirm: vi.fn(),
    checkAgain: vi.fn(),
    inspect: vi.fn().mockResolvedValue(null),
    ...overrides,
  } as unknown as Api;
}

async function openPanel(): Promise<void> {
  await userEvent.click(await screen.findByRole("button", { name: /Open chat/ }));
}

async function noViolations(container: Element): Promise<void> {
  const results = await axe(container);
  expect(results.violations).toEqual([]);
}

afterEach(cleanup);

describe("accessibility of every state", () => {
  it("closed", async () => {
    const { container } = render(<Chat api={fakeApi()} />);
    await screen.findByRole("button", { name: "Open chat" });
    await noViolations(container);
  });

  it("loading", async () => {
    const api = fakeApi({ begin: vi.fn(() => new Promise(() => undefined)) });
    const { container } = render(<Chat api={api} />);
    await openPanel();
    expect(screen.getByRole("status")).toHaveProperty("textContent", "Loading…");
    await noViolations(container);
  });

  it("open with messages, cards, and notices", async () => {
    const { container } = render(
      <Chat api={fakeApi({}, { messages: [{ role: "user", text: "hi" }, reply] })} />,
    );
    await openPanel();
    await screen.findByText("2020 BMW 1 Series");
    await noViolations(container);
  });

  it("unread", async () => {
    const api = fakeApi({ begin: vi.fn().mockResolvedValue(undefined) }, {
      messages: [{ role: "user", text: "hi" }],
      pending: true,
    });
    (api.resume as ReturnType<typeof vi.fn>).mockResolvedValue(reply);
    const { container } = render(<Chat api={api} />);
    await screen.findByRole("button", { name: "Open chat, 1 unread" });
    await noViolations(container);
  });

  it("unavailable", async () => {
    const api = fakeApi({ begin: vi.fn().mockRejectedValue(new Error("down")) });
    const { container } = render(<Chat api={api} />);
    await openPanel();
    await screen.findByRole("button", { name: "Try again" });
    await noViolations(container);
  });

  it("error", async () => {
    const api = fakeApi({ send: vi.fn().mockRejectedValue(new Error("down")) });
    const { container } = render(<Chat api={api} />);
    await openPanel();
    await userEvent.type(await screen.findByRole("textbox", { name: "Your message" }), "hi{Enter}");
    await screen.findByRole("alert");
    await noViolations(container);
  });

  it("form", async () => {
    const { container } = render(<Chat api={fakeApi({}, { view: collecting })} />);
    await openPanel();
    await screen.findByLabelText("Email");
    await noViolations(container);
  });

  it("confirmation", async () => {
    const { container } = render(<Chat api={fakeApi({}, { view: confirming })} />);
    await openPanel();
    await screen.findByRole("button", { name: "Confirm" });
    await noViolations(container);
  });
});

describe("behaviour", () => {
  it("resumes from the last event ID after a refresh", async () => {
    const resume = vi.fn().mockResolvedValue(reply);
    render(
      <Chat
        api={fakeApi({ resume }, {
          messages: [{ role: "user", text: "hi" }],
          pending: true,
          last_event_id: 42,
        })}
      />,
    );
    await waitFor(() => expect(resume).toHaveBeenCalled());
    expect(resume.mock.calls[0][0]).toBe(42);
  });

  it("confirms exactly once on repeated clicks", async () => {
    let finish: (view: View) => void = () => undefined;
    const confirm = vi.fn(() => new Promise<View>((done) => (finish = done)));
    render(<Chat api={fakeApi({ confirm }, { view: confirming })} />);
    await openPanel();
    const button = await screen.findByRole("button", { name: "Confirm" });
    await userEvent.click(button);
    await userEvent.click(button);
    expect(confirm).toHaveBeenCalledTimes(1);
    await act(async () => finish(idle));
  });

  it("confirms once when two clicks land before a re-render", async () => {
    const confirm = vi.fn(() => new Promise<View>(() => undefined));
    render(<Chat api={fakeApi({ confirm }, { view: confirming })} />);
    await openPanel();
    const button = await screen.findByRole("button", { name: "Confirm" });
    act(() => {
      button.click();
      button.click();
    });
    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it("moves focus to the box once loading ends, for a customer who opened it early", async () => {
    let finish: () => void = () => undefined;
    const begin = vi.fn(() => new Promise<void>((done) => (finish = done)));
    render(<Chat api={fakeApi({ begin })} />);
    await openPanel();
    await act(async () => finish());
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Your message" })),
    );
  });

  it("accepts a second message as soon as a reply appears", async () => {
    const send = vi.fn().mockResolvedValue({ role: "assistant", text: "First reply" });
    render(<Chat api={fakeApi({ send })} />);
    await openPanel();
    const box = await screen.findByRole("textbox", { name: "Your message" });
    await userEvent.type(box, "one{Enter}");
    await screen.findByText("First reply");
    await userEvent.type(box, "two{Enter}");
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
  });

  it("keeps a reply if reading the state fails", async () => {
    const send = vi.fn().mockResolvedValue({ role: "assistant", text: "Kept" });
    const state = vi.fn().mockRejectedValue(new Error("down"));
    render(<Chat api={fakeApi({ send, state })} />);
    await openPanel();
    await userEvent.type(await screen.findByRole("textbox", { name: "Your message" }), "hi{Enter}");
    expect(await screen.findByText("Kept")).toBeTruthy();
  });

  it("offers a check when the outcome is unknown", async () => {
    const unknown = { ...idle, status: "unknown", message: "Still checking", can_check_again: true };
    render(<Chat api={fakeApi({}, { view: unknown as View })} />);
    await openPanel();
    expect(await screen.findByRole("button", { name: "Check again" })).toBeTruthy();
  });

  it("never renders model text as HTML", async () => {
    const message: ChatMessage = { role: "assistant", text: "<img src=x>" };
    const { container } = render(<Chat api={fakeApi({}, { messages: [message] })} />);
    await openPanel();
    await screen.findByText("<img src=x>");
    expect(container.querySelector(".bubble img")).toBeNull();
  });
});
