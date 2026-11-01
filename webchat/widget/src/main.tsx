import { createRoot } from "react-dom/client";
import { Api } from "./api";
import { Chat } from "./Chat";
import { styles } from "./styles";

// Captured now, because `currentScript` is only set while this file first runs.
const script = document.currentScript as HTMLScriptElement | null;
const base = script ? new URL(script.src).origin : window.location.origin;

const host = document.createElement("northstar-chat");
const shadow = host.attachShadow({ mode: "open" });
const style = document.createElement("style");
style.textContent = styles;
const mount = document.createElement("div");
shadow.append(style, mount);

/**
 * `showModal` makes everything outside the dialog inert, so the chat moves into an open dialog and
 * back again (D-015). The website itself does not change.
 */
function place(): void {
  const target = document.querySelector("dialog[open]") ?? document.body;
  if (host.parentElement === target) return;
  const focused = shadow.activeElement as HTMLElement | null;
  target.append(host);
  focused?.focus();
}

place();
new MutationObserver(place).observe(document.documentElement, {
  subtree: true,
  attributes: true,
  attributeFilter: ["open"],
});
createRoot(mount).render(<Chat api={new Api(base)} />);
