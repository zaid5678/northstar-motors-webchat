import { useCallback, useEffect, useRef, useState } from "react";
import { onThisHost } from "./api";
import type { Api, Block, Button, ChatMessage, Inspection, View } from "./api";
import { Form } from "./Form";
import { Inspector } from "./Inspector";

const STAGES: Record<string, string> = {
  understanding: "Understanding your message…",
  checking: "Checking with the dealership…",
  writing: "Writing a reply…",
};
const LOADING = "Loading…";
const WAITING = "Working on your last message…";
// Once a write has settled, its result is in the saved transcript.
const SETTLED = new Set(["completed", "failed", "unknown", "two_bookings"]);
// A new chat would lose the result, or the Check again button, so it waits (D-036).
const SENDING = new Set(["processing", "unknown"]);

type Boot = "loading" | "ready" | "unavailable";

function search(name: string): string | null {
  return new URL(window.location.href).searchParams.get(name);
}

function Actions({ actions, onPress }: { actions: Button[]; onPress: (action: Button) => void }) {
  return (
    <div className="actions">
      {actions.map((action) => (
        <button className="action" type="button" key={action.id} onClick={() => onPress(action)}>
          {action.label}
        </button>
      ))}
    </div>
  );
}

function Message({ message, onPress }: { message: ChatMessage; onPress?: (action: Button) => void }) {
  return (
    <>
      {/* Text only, never HTML: model output must not be able to run in the page. */}
      <div className={`bubble ${message.role}`}>{message.text}</div>
      {message.notices?.map((notice) => <div className="notice" key={notice}>{notice}</div>)}
      {message.vehicles?.map((card) => (
        <a className="card" href={onThisHost(card.url, window.location.hostname)} key={card.id}>
          {card.image && <img src={onThisHost(card.image, window.location.hostname)} alt="" />}
          <span>
            <strong>{card.title}</strong>
            {card.price} · {card.availability}
          </span>
        </a>
      ))}
      {onPress && message.actions && message.actions.length > 0 && (
        <Actions actions={message.actions} onPress={onPress} />
      )}
    </>
  );
}

function Confirmation({ block, busy, onConfirm }: { block: Block; busy: boolean; onConfirm: () => void }) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), [block.token]);
  return (
    <div className="block">
      <h2 ref={heading} tabIndex={-1}>{block.title}</h2>
      <dl>
        {block.rows.map((row) => (
          <div key={row.label}>
            <dt>{row.label}</dt>
            <dd>{row.value}</dd>
          </div>
        ))}
      </dl>
      {block.warnings.map((warning) => <p className="warning" key={warning}>{warning}</p>)}
      <button className="primary" type="button" disabled={busy} onClick={onConfirm}>{block.button}</button>
    </div>
  );
}

export function Chat({ api }: { api: Api }) {
  const [open, setOpen] = useState(false);
  const [boot, setBoot] = useState<Boot>("loading");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [starters, setStarters] = useState<Button[]>([]);
  const [view, setView] = useState<View | null>(null);
  const [draft, setDraft] = useState("");
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const [unread, setUnread] = useState(0);
  const [busy, setBusy] = useState(false);
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [askingNew, setAskingNew] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const launcherRef = useRef<HTMLButtonElement>(null);
  const openRef = useRef(open);
  const wasOpen = useRef(false);
  const claimed = useRef(false); // Blocks a double click before React has re-rendered.
  openRef.current = open;

  const inspect = useCallback(async () => {
    if (search("inspector")) setInspection(await api.inspect());
  }, [api]);

  const received = useCallback(
    async (message: ChatMessage) => {
      // The reply appears last, so the box is ready for the next message as soon as it shows.
      try {
        setView(await api.state());
      } catch {
        // The reply still matters, and the view is read again after the next turn.
      }
      setMessages((current) => [...current, message]);
      if (!openRef.current) setUnread((count) => count + 1);
      void inspect();
    },
    [api, inspect],
  );

  const load = useCallback(async () => {
    setBoot("loading");
    setError("");
    try {
      await api.begin();
      const saved = await api.transcript();
      setMessages(saved.messages);
      setStarters(saved.actions);
      setView(saved.view);
      setBoot("ready");
      await inspect();
      if (saved.pending) {
        // A refresh during a reply: the turn carried on, so pick up its stream.
        setStage(WAITING);
        try {
          await received(
            await api.resume(saved.last_event_id, (name) => setStage(STAGES[name] ?? WAITING)),
          );
        } catch {
          // The turn may have finished without its stream event reaching us, so read what is saved.
          try {
            const again = await api.transcript();
            setMessages(again.messages);
            setView(again.view);
            if (again.pending) setError("We didn't get a reply to your last message. Please send it again.");
          } catch {
            setError("We didn't get a reply to your last message. Please send it again.");
          }
        } finally {
          setStage("");
        }
      }
    } catch {
      setBoot("unavailable");
    }
  }, [api, inspect, received]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (open) {
      setUnread(0);
      boxRef.current?.focus();
    } else if (wasOpen.current) {
      launcherRef.current?.focus();
    }
    wasOpen.current = open;
  }, [open]);

  // A disabled box cannot take focus, so someone who opened the chat during loading gets it now.
  useEffect(() => {
    const form = !view?.hidden && (view?.block || view?.fields.length);
    if (open && boot === "ready" && !form) boxRef.current?.focus();
  }, [open, boot, view]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [messages, stage, open, view]);

  const submit = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    setBusy(true);
    setError("");
    setDraft("");
    setStarters([]);
    setMessages((current) => [...current, { role: "user", text }]);
    try {
      const reply = await api.send(text, search("vehicle"), (name) => setStage(STAGES[name] ?? ""));
      await received(reply);
    } catch {
      setMessages((current) => current.slice(0, -1));
      setDraft(text);
      setError("We couldn't send that. Please try again.");
    } finally {
      setStage("");
      setBusy(false);
    }
  };

  const press = async (action: Button) => {
    if (busy) return;
    setBusy(true);
    setError("");
    setStarters([]);
    setMessages((current) => [...current, { role: "user", text: action.label }]);
    setStage(STAGES.checking);
    try {
      await received(await api.press(action.id));
    } catch {
      setMessages((current) => current.slice(0, -1));
      setError("We couldn't do that. Please try again.");
    } finally {
      setStage("");
      setBusy(false);
    }
  };

  const fillForm = async (values: Record<string, string>) => {
    if (!view) return;
    if (!Object.values(values).some((value) => value.trim())) {
      setError("Please fill in at least one field.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      let result = view;
      if (view.status === "verifying") {
        const details = { reference: "", last_name: "", registration: "", phone: "", ...values };
        result = await api.identity(details);
      } else {
        // Optional fields go first: the last required one is what issues the confirmation.
        const ordered = [...view.fields].sort((a, b) => Number(a.required) - Number(b.required));
        for (const field of ordered) {
          const value = values[field.name];
          if (value) result = await api.fill(field.name, field.kind === "number" ? Number(value) : value);
        }
      }
      setView(result);
      if (SETTLED.has(result.status)) setMessages((await api.transcript()).messages);
      await inspect();
    } catch {
      setError("We couldn't save that. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const bringBack = async () => {
    setBusy(true);
    setError("");
    try {
      setView(await api.continueForm());
    } catch {
      setError("We couldn't do that. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const startNewChat = async () => {
    setAskingNew(false);
    setBusy(true);
    setError("");
    try {
      await api.newChat();
      setDraft("");
      await load();
      boxRef.current?.focus();
    } catch {
      setError("We couldn't start a new chat. Please wait until your request has been sent.");
    } finally {
      setBusy(false);
    }
  };

  const cancelTask = async () => {
    setBusy(true);
    setError("");
    try {
      setView(await api.cancelTask());
      setMessages((await api.transcript()).messages);
      boxRef.current?.focus();
    } catch {
      setError("We couldn't cancel that. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const settle = async (write: () => Promise<View>) => {
    if (claimed.current) return;
    claimed.current = true;
    setBusy(true);
    setError("");
    setStage("Sending to the dealership…");
    try {
      const result = await write();
      const saved = await api.transcript();
      setMessages(saved.messages);
      setView(result);
      boxRef.current?.focus();
      await inspect();
    } catch {
      setError("We can't tell if that went through. Please don't try again yet.");
    } finally {
      setStage("");
      setBusy(false);
      claimed.current = false;
    }
  };

  if (!open) {
    return (
      <button
        ref={launcherRef}
        className="launcher"
        type="button"
        aria-label={unread ? `Open chat, ${unread} unread` : "Open chat"}
        onClick={() => setOpen(true)}
      >
        <span aria-hidden="true">💬</span>
        {unread > 0 && <span className="badge" aria-hidden="true">{unread}</span>}
      </button>
    );
  }

  const last = messages.length - 1;
  const fields = view?.fields ?? [];
  const aside = Boolean(view?.hidden);
  const showForm =
    !aside && fields.length > 0 && (view?.status === "collecting" || view?.status === "verifying");
  // Only before anything is sent. The server refuses a cancel after that anyway.
  const cancellable =
    aside || showForm || (view?.status === "confirming" && Boolean(view.block)) || view?.status === "locked";
  const statusText = boot === "loading" ? LOADING : stage;
  return (
    <div
      className="panel"
      role="dialog"
      aria-label="Northstar Motors chat"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          // Otherwise the website's vehicle dialog would close as well.
          event.preventDefault();
          event.stopPropagation();
          setOpen(false);
        }
      }}
    >
      <div className="header">
        <span>Northstar Motors</span>
        <button
          className="new-chat"
          type="button"
          disabled={busy || boot !== "ready" || SENDING.has(view?.status ?? "")}
          onClick={() => setAskingNew(true)}
        >
          New chat
        </button>
        <button className="close" type="button" aria-label="Close chat" onClick={() => setOpen(false)}>×</button>
      </div>
      {askingNew && (
        <div className="confirm-new" role="group" aria-label="Start a new chat?">
          <p>Start a new chat? This conversation will be cleared from this window.</p>
          <div className="actions">
            <button className="primary" type="button" onClick={() => void startNewChat()}>Start new chat</button>
            <button className="action" type="button" onClick={() => setAskingNew(false)}>Keep chatting</button>
          </div>
        </div>
      )}
      {boot === "unavailable" ? (
        <div className="unavailable">
          <p role="alert">Chat is unavailable right now.</p>
          <button className="primary" type="button" onClick={() => void load()}>Try again</button>
        </div>
      ) : (
        <>
          <div ref={logRef} className="log" role="log" aria-live="polite" aria-label="Conversation" tabIndex={0}>
            {boot === "ready" && messages.length === 0 && (
              <div className="bubble assistant">Hello! I can help you find a car, or book a test drive or service.</div>
            )}
            {messages.map((message, index) => (
              <Message key={index} message={message} onPress={index === last ? press : undefined} />
            ))}
            {starters.length > 0 && <Actions actions={starters} onPress={press} />}
            {view?.notices.map((notice) => <div className="notice" key={notice}>{notice}</div>)}
            {view?.status === "locked" && <div className="bubble assistant">{view.message}</div>}
            {(showForm || view?.status === "unknown") && view?.message && (
              <div className="bubble assistant">{view.message}</div>
            )}
            {showForm && view && (
              <Form key={fields.map((field) => field.name).join()} fields={fields} disabled={busy} onSubmit={fillForm} />
            )}
            {!aside && view?.status === "confirming" && view.block && (
              <Confirmation
                block={view.block}
                busy={busy}
                onConfirm={() => void settle(() => api.confirm(view.block!.token))}
              />
            )}
            {cancellable && view && (
              <div className="actions">
                {aside && (
                  <button className="action" type="button" disabled={busy} onClick={() => void bringBack()}>
                    {view.resume}
                  </button>
                )}
                <button className="action" type="button" disabled={busy} onClick={() => void cancelTask()}>
                  Cancel
                </button>
              </div>
            )}
            {view?.can_check_again && (
              <button className="primary" type="button" disabled={busy} onClick={() => void settle(() => api.checkAgain())}>
                Check again
              </button>
            )}
          </div>
          <div className="status" role="status">{statusText}</div>
          {error && <div className="error" role="alert">{error}</div>}
          <form
            className="composer"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <textarea
              ref={boxRef}
              aria-label="Your message"
              value={draft}
              disabled={boot !== "ready"}
              maxLength={2000}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void submit();
                }
              }}
            />
            <button className="send" type="submit" disabled={boot !== "ready"}>Send</button>
          </form>
          {inspection && <Inspector data={inspection} />}
        </>
      )}
    </div>
  );
}
