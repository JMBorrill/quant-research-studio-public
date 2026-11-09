// A modal built on the native <dialog>, which keeps focus inside it and closes on Escape. However it
// closes (Escape, Cancel, or its parent removing it after Create), focus returns to what opened it.
import { useEffect, useId, useRef, type ReactNode } from 'react';

export function Dialog({ title, onClose, wide, children }: { title: string; onClose: () => void; wide?: boolean; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef(document.activeElement as HTMLElement | null); // captured before the dialog takes focus
  const titleId = useId();
  useEffect(() => {
    if (!ref.current?.open) ref.current?.showModal(); // React runs effects twice in development
    return () => opener.current?.focus();
  }, []);
  return (
    <dialog ref={ref} className={wide ? 'dialog dialog--wide' : 'dialog'} aria-labelledby={titleId} onClose={onClose}>
      <h2 id={titleId}>{title}</h2>
      {children}
    </dialog>
  );
}
