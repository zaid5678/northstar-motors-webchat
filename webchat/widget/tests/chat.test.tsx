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
  hidden: false,
  resume: "",
  fields: [
    { name: "email", label: "Email", kind: "email", options: [], value: null, required: true },
    {
      name: "department",
      label: "Department",
      kind: "select",
      options: [{ value: "sales", label: "Sales" }],
      value: null,
      required: true,
    },
    { name: "preferred_time", label: "Best time to call", kind: "text", options: [], value: null, required: false },
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
    continueForm: vi.fn(),
    cancelTask: vi.fn(),
    newChat: vi.fn(),
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

  it("form put aside", async () => {
    const view = { ...collecting, hidden: true, resume: "Continue your callback request" };
    const { container } = render(<Chat api={fakeApi({}, { view })} />);
    await openPanel();
    await screen.findByRole("button", { name: "Continue your callback request" });
    await noViolations(container);
  });
});

describe("a new question puts the form aside (D-034)", () => {
  const aside = { ...collecting, hidden: true, resume: "Continue your callback request" };

  it("hides the form after a reply, and offers to continue", async () => {
    const answer: ChatMessage = { role: "assistant", text: "We open at 9." };
    const api = fakeApi(
      { send: vi.fn().mockResolvedValue(answer), state: vi.fn().mockResolvedValue(aside) },
      { view: collecting },
    );
    render(<Chat api={api} />);
    await openPanel();
    await screen.findByLabelText("Email");
    await userEvent.type(screen.getByRole("textbox", { name: "Your message" }), "when do you open?{Enter}");
    await screen.findByText("We open at 9.");
    expect(screen.queryByLabelText("Email")).toBeNull();
    expect(screen.getByRole("button", { name: "Continue your callback request" })).toBeTruthy();
  });

  it("brings the form back on Continue", async () => {
    const continueForm = vi.fn().mockResolvedValue(collecting);
    render(<Chat api={fakeApi({ continueForm }, { view: aside })} />);
    await openPanel();
    await userEvent.click(await screen.findByRole("button", { name: "Continue your callback request" }));
    await screen.findByLabelText("Email");
    expect(continueForm).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Continue your callback request" })).toBeNull();
  });

  it("hides a confirmation card too", async () => {
    const view = { ...confirming, hidden: true, resume: "Continue your callback request" };
    render(<Chat api={fakeApi({}, { view })} />);
    await openPanel();
    await screen.findByRole("button", { name: "Continue your callback request" });
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  });

  it("keeps the message box ready while the form is aside", async () => {
    render(<Chat api={fakeApi({}, { view: aside })} />);
    await openPanel();
    const box = await screen.findByRole("textbox", { name: "Your message" });
    await waitFor(() => expect(document.activeElement === box || box.matches(":focus")).toBe(true));
  });
});

describe("forms", () => {
  it("marks only the required fields as required", async () => {
    render(<Chat api={fakeApi({}, { view: collecting })} />);
    await openPanel();
    expect((await screen.findByLabelText("Email") as HTMLInputElement).required).toBe(true);
    expect((screen.getByLabelText("Best time to call") as HTMLInputElement).required).toBe(false);
  });

  it("says so when an all-optional form is sent empty", async () => {
    const optionalOnly: View = {
      ...collecting,
      fields: [{ name: "notes", label: "Notes", kind: "text", options: [], value: null, required: false }],
    };
    const fill = vi.fn();
    render(<Chat api={fakeApi({ fill }, { view: optionalOnly })} />);
    await openPanel();
    await userEvent.click(await screen.findByRole("button", { name: "Continue" }));
    expect((await screen.findByRole("alert")).textContent).toContain("at least one");
    expect(fill).not.toHaveBeenCalled();
  });

  it("sends optional fields before the required ones", async () => {
    const order: string[] = [];
    const fill = vi.fn(async (name: string) => {
      order.push(name);
      return collecting;
    });
    render(<Chat api={fakeApi({ fill }, { view: collecting })} />);
    await openPanel();
    await userEvent.type(await screen.findByLabelText("Email"), "a@example.com");
    await userEvent.type(screen.getByLabelText("Best time to call"), "mornings");
    await userEvent.selectOptions(screen.getByLabelText("Department"), "sales");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(order).toEqual(["preferred_time", "email", "department"]));
  });

  it("shows a verified booking once it is in the transcript", async () => {
    const verifying: View = { ...collecting, status: "verifying", fields: [
      { name: "reference", label: "Booking reference", kind: "text", options: [], value: null, required: true },
    ] };
    const done: View = { ...idle, status: "completed", message: "Full service at Northstar Manchester" };
    const identity = vi.fn().mockResolvedValue(done);
    const transcript = vi
      .fn()
      .mockResolvedValueOnce({ messages: [], pending: false, last_event_id: 0, view: verifying, actions: [] })
      .mockResolvedValue({ messages: [{ role: "assistant", text: done.message }], pending: false, last_event_id: 0, view: done, actions: [] });
    render(<Chat api={fakeApi({ identity, transcript }, { view: verifying })} />);
    await openPanel();
    await userEvent.type(await screen.findByLabelText("Booking reference"), "WORK-10001");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText("Full service at Northstar Manchester")).toBeTruthy();
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

  it("shows the saved reply when a resumed stream ends without one", async () => {
    const resume = vi.fn().mockRejectedValue(new Error("the stream ended without a reply"));
    const transcript = vi
      .fn()
      .mockResolvedValueOnce({
        messages: [{ role: "user", text: "hi" }], pending: true, last_event_id: 3, view: idle, actions: [],
      })
      .mockResolvedValue({
        messages: [{ role: "user", text: "hi" }, { role: "assistant", text: "Saved reply" }],
        pending: false, last_event_id: 4, view: idle, actions: [],
      });
    render(<Chat api={fakeApi({ resume, transcript })} />);
    await openPanel();
    expect(await screen.findByText("Saved reply")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
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

describe("cancelling an unfinished task (D-035)", () => {
  const cancelled: Transcript = {
    messages: [{ role: "assistant", text: "That's cancelled, and nothing was sent to the dealership." }],
    pending: false,
    last_event_id: 0,
    view: { ...idle, status: "abandoned" },
    actions: [],
  };
  const cancel = () => screen.findByRole("button", { name: "Cancel" });

  it("cancels a form and says so", async () => {
    const cancelTask = vi.fn().mockResolvedValue({ ...idle, status: "abandoned" });
    const transcript = vi
      .fn()
      .mockResolvedValueOnce({ messages: [], pending: false, last_event_id: 0, view: collecting, actions: [] })
      .mockResolvedValue(cancelled);
    render(<Chat api={fakeApi({ cancelTask, transcript }, { view: collecting })} />);
    await openPanel();
    await userEvent.click(await cancel());
    await screen.findByText("That's cancelled, and nothing was sent to the dealership.");
    expect(cancelTask).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText("Email")).toBeNull();
  });

  it("is offered with a confirmation card", async () => {
    render(<Chat api={fakeApi({}, { view: confirming })} />);
    await openPanel();
    await screen.findByRole("button", { name: "Confirm" });
    expect(await cancel()).toBeTruthy();
  });

  it("is offered beside Continue when the form is aside", async () => {
    const view = { ...collecting, hidden: true, resume: "Continue your callback request" };
    render(<Chat api={fakeApi({}, { view })} />);
    await openPanel();
    await screen.findByRole("button", { name: "Continue your callback request" });
    expect(await cancel()).toBeTruthy();
  });

  it("is offered when locked out", async () => {
    const view = { ...idle, status: "locked", message: "Too many attempts." };
    render(<Chat api={fakeApi({}, { view })} />);
    await openPanel();
    expect(await cancel()).toBeTruthy();
  });

  it.each(["idle", "processing", "unknown", "completed"])("is not offered when %s", async (status) => {
    render(<Chat api={fakeApi({}, { view: { ...idle, status, message: "x" } })} />);
    await openPanel();
    await screen.findByRole("textbox", { name: "Your message" });
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  });

  it("has no accessibility problems beside a form", async () => {
    const { container } = render(<Chat api={fakeApi({}, { view: collecting })} />);
    await openPanel();
    await cancel();
    await noViolations(container);
  });
});

describe("starting a new chat (D-036)", () => {
  const old: Transcript = {
    messages: [{ role: "assistant", text: "Here are the hybrids." }],
    pending: false,
    last_event_id: 0,
    view: idle,
    actions: [],
  };
  const fresh: Transcript = { ...old, messages: [] };
  const newChatButton = () => screen.findByRole("button", { name: "New chat" });

  it("asks first, then clears the conversation", async () => {
    const newChat = vi.fn().mockResolvedValue(undefined);
    const transcript = vi.fn().mockResolvedValueOnce(old).mockResolvedValue(fresh);
    render(<Chat api={fakeApi({ newChat, transcript })} />);
    await openPanel();
    await screen.findByText("Here are the hybrids.");
    await userEvent.click(await newChatButton());
    expect(newChat).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Start new chat" }));
    await waitFor(() => expect(screen.queryByText("Here are the hybrids.")).toBeNull());
    expect(newChat).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Start new chat" })).toBeNull();
  });

  it("can be called off", async () => {
    const newChat = vi.fn();
    render(<Chat api={fakeApi({ newChat }, old)} />);
    await openPanel();
    await userEvent.click(await newChatButton());
    await userEvent.click(screen.getByRole("button", { name: "Keep chatting" }));
    expect(newChat).not.toHaveBeenCalled();
    expect(screen.getByText("Here are the hybrids.")).toBeTruthy();
  });

  it.each(["processing", "unknown"])("is not offered while a request is %s", async (status) => {
    render(<Chat api={fakeApi({}, { view: { ...idle, status, message: "x" } })} />);
    await openPanel();
    expect((await newChatButton()).hasAttribute("disabled")).toBe(true);
  });

  it("explains when the server refuses", async () => {
    const newChat = vi.fn().mockRejectedValue(new Error("409"));
    render(<Chat api={fakeApi({ newChat }, old)} />);
    await openPanel();
    await userEvent.click(await newChatButton());
    await userEvent.click(screen.getByRole("button", { name: "Start new chat" }));
    expect((await screen.findByRole("alert")).textContent).toContain("couldn't start a new chat");
    expect(screen.getByText("Here are the hybrids.")).toBeTruthy();
  });

  it("has no accessibility problems while asking", async () => {
    const { container } = render(<Chat api={fakeApi({}, old)} />);
    await openPanel();
    await userEvent.click(await newChatButton());
    await screen.findByRole("button", { name: "Start new chat" });
    await noViolations(container);
  });
});
