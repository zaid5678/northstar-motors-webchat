import { FormEvent, useEffect, useRef, useState } from "react";
import type { Field } from "./api";

interface Props {
  fields: Field[];
  disabled: boolean;
  onSubmit: (values: Record<string, string>) => void;
}

export function Form({ fields, disabled, onSubmit }: Props) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map((field) => [field.name, field.value ?? ""])),
  );
  const first = useRef<HTMLDivElement>(null);

  useEffect(() => {
    first.current?.querySelector<HTMLElement>("input, select, textarea")?.focus();
  }, []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit(values);
  };

  return (
    <form className="form" onSubmit={submit}>
      <div ref={first}>
        {fields.map((field) => {
          const common = {
            required: field.required,
            value: values[field.name] ?? "",
            onChange: (event: { target: { value: string } }) =>
              setValues((current) => ({ ...current, [field.name]: event.target.value })),
          };
          return (
            <label className="field" key={field.name}>
              <span>{field.label}</span>
              {field.kind === "select" ? (
                <select {...common}>
                  <option value="">Choose…</option>
                  {field.options.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              ) : field.kind === "textarea" ? (
                <textarea {...common} rows={2} />
              ) : (
                <input {...common} type={field.kind} />
              )}
            </label>
          );
        })}
      </div>
      <button className="primary" type="submit" disabled={disabled}>Continue</button>
    </form>
  );
}
