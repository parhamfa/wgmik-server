import * as React from "react";

export type LooseNumberInputOptions = {
  min?: number;
  max?: number;
  /** When the field is empty on blur (defaults to `min` if set, else last committed). */
  emptyFallback?: number;
  /** Applied on blur instead of min/max when you need custom clamping (e.g. scope rules). */
  transformOnBlur?: (n: number) => number;
};

/**
 * Avoids per-keystroke min/max clamping on controlled number inputs so multi-digit values
 * can be typed without intermediate digits snapping to the minimum. Clamps on blur.
 */
export function useLooseNumberInput(
  committed: number,
  commit: (n: number) => void,
  opts: LooseNumberInputOptions = {},
): {
  value: string;
  onFocus: () => void;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onBlur: () => void;
} {
  const [draft, setDraft] = React.useState<string | null>(null);
  const { min, max, emptyFallback, transformOnBlur } = opts;

  const value = draft !== null ? draft : String(committed);

  function onFocus() {
    setDraft(String(committed));
  }

  function onChange(e: React.ChangeEvent<HTMLInputElement>) {
    const s = e.target.value;
    setDraft(s);
    if (s === "") return;
    const n = Number(s);
    if (!Number.isFinite(n)) return;
    commit(n);
  }

  function onBlur() {
    let n: number;
    if (draft === null || draft === "") {
      n = emptyFallback !== undefined ? emptyFallback : min !== undefined ? min : committed;
    } else {
      n = Number(draft);
      if (!Number.isFinite(n)) n = committed;
    }
    if (transformOnBlur) {
      n = transformOnBlur(n);
    } else {
      if (min !== undefined) n = Math.max(min, n);
      if (max !== undefined) n = Math.min(max, n);
    }
    commit(n);
    setDraft(null);
  }

  return { value, onFocus, onChange, onBlur };
}
