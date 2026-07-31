// FieldError: an inline validation message shown beneath a form field.
//
// Paired with `parseFieldErrors` (src/api/errors.ts), which maps a 422 body's
// per-field validation messages to field names. Render one under each input,
// keyed by the field name, to surface server-side validation (422) precisely
// where the user can fix it. Renders nothing when there is no message.

interface FieldErrorProps {
  /** The message for this field, or null/undefined when the field is valid. */
  message?: string | null;
  /** Optional id so an input can reference it via aria-describedby. */
  id?: string;
  className?: string;
}

export default function FieldError({ message, id, className = "" }: FieldErrorProps) {
  if (!message) return null;
  return (
    <p id={id} className={`mt-1 text-xs text-red-600 ${className}`.trim()}>
      {message}
    </p>
  );
}
