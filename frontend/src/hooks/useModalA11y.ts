import { useEffect, useRef } from "react";

/**
 * Accessibility helper for modal dialogs.
 *
 * Responsibilities :
 *   - Close on ESC key.
 *   - Focus trap : Tab and Shift+Tab cycle within the modal, never leaking
 *     focus back to the page behind.
 *   - Initial focus : the first focusable element inside the modal (or the
 *     container itself if none) receives focus on mount.
 *   - Restore focus : on unmount, the element that had focus before the
 *     modal opened gets it back.
 *
 * Caller pattern :
 *   const ref = useModalA11y(onClose);
 *   return (
 *     <div
 *       ref={ref}
 *       role="dialog"
 *       aria-modal="true"
 *       aria-labelledby="my-title"
 *       tabIndex={-1}
 *     >
 *       <h2 id="my-title">Title</h2>
 *       ...
 *     </div>
 *   );
 *
 * Why a hook instead of a wrapper component : the modals across the app
 * have wildly different markup (cyber-card, plain div, full-screen panel).
 * A wrapper would force a shared shell on all of them. The hook leaves the
 * markup to the caller and only handles the a11y behaviour.
 */
export function useModalA11y<T extends HTMLElement = HTMLDivElement>(
  onClose: () => void,
) {
  const ref = useRef<T | null>(null);
  // Snapshot of the previously focused element so we can return focus
  // when the modal closes — standard a11y pattern.
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const root = ref.current;
    if (root === null) return;

    // Initial focus: first focusable child, else the root itself.
    const focusables = getFocusable(root);
    if (focusables.length > 0) {
      focusables[0].focus();
    } else {
      // tabIndex=-1 on the root makes it focusable programmatically without
      // being part of the tab sequence.
      root.focus();
    }

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      if (root === null) return;
      const elems = getFocusable(root);
      if (elems.length === 0) {
        // Nothing focusable inside — trap focus on the root.
        e.preventDefault();
        root.focus();
        return;
      }
      const first = elems[0];
      const last = elems[elems.length - 1];
      const active = document.activeElement;
      if (e.shiftKey) {
        if (active === first || !root.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else if (active === last) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      // Restore previous focus on unmount.
      const prev = previouslyFocused.current;
      if (prev && typeof prev.focus === "function") {
        prev.focus();
      }
    };
  }, [onClose]);

  return ref;
}

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function getFocusable(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => {
      // Skip elements hidden via display:none / visibility:hidden.
      if (el.offsetParent === null && el.tagName !== "DIALOG") return false;
      return true;
    },
  );
}
