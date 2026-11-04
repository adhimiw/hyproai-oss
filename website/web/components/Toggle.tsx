interface ToggleProps {
  id: string;
  label: string;
  hint?: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}

/** A real, labelled checkbox — the native control, accented to the theme. */
export default function Toggle({
  id,
  label,
  hint,
  checked,
  disabled,
  onChange,
}: ToggleProps) {
  return (
    <div className="toggle-row">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <label htmlFor={id}>
        {label}
        {hint && <span className="toggle-hint">{hint}</span>}
      </label>
    </div>
  );
}
