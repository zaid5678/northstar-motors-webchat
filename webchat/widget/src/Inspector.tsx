import type { Inspection } from "./api";

/** Development only: the server has no such data unless its flag is on. */
export function Inspector({ data }: { data: Inspection }) {
  return (
    <section className="inspector" aria-label="Developer inspector">
      <strong>Inspector</strong>
      {(["session", "pending", "tool_calls", "verdicts"] as const).map((name) => (
        <details key={name} open>
          <summary>{name.replace("_", " ")}</summary>
          <pre>{JSON.stringify(data[name], null, 1)}</pre>
        </details>
      ))}
    </section>
  );
}
