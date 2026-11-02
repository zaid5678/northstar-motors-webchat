export const styles = `
:host { all: initial; }
* { box-sizing: border-box; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.launcher, .panel { position: fixed; z-index: 2147483000; }
.launcher {
  right: 20px; bottom: 20px; width: 60px; height: 60px; border: 0; border-radius: 50%;
  background: #172329; color: #fff; font-size: 24px; cursor: pointer;
  box-shadow: 0 6px 20px rgba(0,0,0,.3);
}
.launcher:focus-visible, button:focus-visible, textarea:focus-visible, a:focus-visible, .log:focus-visible {
  outline: 3px solid #1f6feb; outline-offset: 2px;
}
.badge {
  position: absolute; top: -2px; right: -2px; min-width: 22px; height: 22px; padding: 0 6px;
  border-radius: 11px; background: #c62828; color: #fff; font-size: 13px; line-height: 22px;
}
.panel {
  right: 20px; bottom: 20px; width: 380px; height: min(620px, calc(100vh - 40px));
  display: flex; flex-direction: column; overflow: hidden; border-radius: 14px;
  background: #fff; color: #172329; box-shadow: 0 12px 40px rgba(0,0,0,.35);
}
.header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 16px; background: #172329; color: #fff; font-size: 16px; font-weight: 600;
}
.header > span { flex: 1; }
.new-chat {
  border: 1px solid rgba(255,255,255,.6); border-radius: 14px; background: none; color: inherit;
  font-size: 13px; padding: 4px 10px; cursor: pointer;
}
.new-chat:disabled { opacity: .45; cursor: default; }
.close { border: 0; background: none; color: inherit; font-size: 20px; cursor: pointer; padding: 4px 8px; }
.confirm-new { padding: 10px 14px; background: #eef2f4; border-bottom: 1px solid #d5dde1; font-size: 14px; }
.confirm-new p { margin: 0 0 8px; }
.log { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
.bubble { max-width: 88%; padding: 9px 12px; border-radius: 12px; font-size: 15px; line-height: 1.4; white-space: pre-wrap; overflow-wrap: anywhere; }
.user { align-self: flex-end; background: #1f6feb; color: #fff; }
.assistant { align-self: flex-start; background: #eef1f3; }
.notice { font-size: 13px; color: #47555c; border-left: 3px solid #9aa5ab; padding-left: 8px; }
.card { display: flex; gap: 10px; padding: 8px; border: 1px solid #d5dbde; border-radius: 10px; color: inherit; text-decoration: none; font-size: 14px; }
.card img { width: 76px; height: 56px; object-fit: cover; border-radius: 6px; }
.card strong { display: block; }
.actions { display: flex; flex-wrap: wrap; gap: 6px; }
.action { border: 1px solid #172329; border-radius: 16px; background: #fff; color: #172329; padding: 6px 12px; font-size: 14px; cursor: pointer; }
.status { min-height: 20px; padding: 0 14px; font-size: 13px; color: #47555c; }
.error { padding: 6px 14px; font-size: 14px; color: #b00020; }
.composer { display: flex; gap: 8px; padding: 10px 12px; border-top: 1px solid #d5dbde; }
textarea { flex: 1; resize: none; height: 44px; padding: 10px; border: 1px solid #9aa5ab; border-radius: 10px; font-size: 16px; }
.send { border: 0; border-radius: 10px; background: #172329; color: #fff; padding: 0 14px; font-size: 15px; cursor: pointer; }
.form, .block { display: flex; flex-direction: column; gap: 10px; padding: 12px; border: 1px solid #d5dbde; border-radius: 10px; }
.field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 8px; font-size: 14px; }
.field input, .field select, .field textarea { height: auto; padding: 9px; border: 1px solid #9aa5ab; border-radius: 8px; font-size: 16px; background: #fff; width: 100%; }
.block h2 { margin: 0; font-size: 16px; outline-offset: 2px; }
.block dl { margin: 0; }
.block dl div { display: flex; justify-content: space-between; gap: 12px; padding: 4px 0; font-size: 14px; }
.block dt { color: #47555c; }
.block dd { margin: 0; text-align: right; overflow-wrap: anywhere; }
.warning { margin: 0; font-size: 13px; color: #8a4b00; }
.primary { border: 0; border-radius: 10px; background: #172329; color: #fff; padding: 10px 14px; font-size: 15px; cursor: pointer; }
.primary:disabled, .send:disabled { opacity: .55; cursor: default; }
.unavailable { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; padding: 20px; text-align: center; }
.inspector { max-height: 180px; overflow: auto; padding: 8px 12px; border-top: 2px dashed #8a4b00; background: #fff8ec; font-size: 12px; }
.inspector pre { margin: 2px 0; white-space: pre-wrap; overflow-wrap: anywhere; }
button:focus-visible, select:focus-visible, input:focus-visible { outline: 3px solid #1f6feb; outline-offset: 2px; }
@media (max-width: 480px) {
  .panel { top: 0; right: 0; bottom: 0; left: 0; width: 100%; height: 100%; border-radius: 0; }
}
@media (prefers-reduced-motion: no-preference) { .bubble { transition: opacity .15s; } }
`;
