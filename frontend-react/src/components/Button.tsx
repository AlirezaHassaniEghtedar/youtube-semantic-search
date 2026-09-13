import { useState, type ReactNode } from "react";

// Button with built-in loading state — replaces setButtonLoading from app.js.

interface ButtonProps {
  variant?: "primary" | "ghost" | "danger" | "warning";
  size?: "sm" | "md";
  type?: "button" | "submit";
  loading?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  className?: string;
  block?: boolean;
  children: ReactNode;
}

export function Button({
  variant = "primary",
  size = "md",
  type = "button",
  loading = false,
  disabled = false,
  onClick,
  className = "",
  block = false,
  children,
}: ButtonProps) {
  const cls = [
    "btn",
    `btn--${variant}`,
    size === "sm" ? "btn--sm" : "",
    block ? "btn--block" : "",
    loading ? "btn--loading" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      type={type}
      className={cls}
      onClick={onClick}
      disabled={disabled || loading}
    >
      <span className="btn__text">{children}</span>
      {loading && <span className="btn__spinner" aria-hidden="true" />}
    </button>
  );
}

// Convenience hook for buttons that trigger async work.
export function useAsyncAction() {
  const [loading, setLoading] = useState(false);

  async function run(fn: () => Promise<void>) {
    setLoading(true);
    try {
      await fn();
    } finally {
      setLoading(false);
    }
  }

  return { loading, run };
}
