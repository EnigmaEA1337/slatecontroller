// Active device selection — used by the axios interceptor to attach
// `?device=<slug>` to backend requests. Null = "use the backend's default
// device" (omit the query param entirely). localStorage persists the
// choice across page reloads.
//
// Why a plain module + event listener rather than React context :
// the axios interceptor is registered once at module import time, BEFORE
// any React tree exists. It needs a synchronous getter that works
// outside React. The DevicePicker writes via setActiveDevice() which
// also dispatches a window event so React hooks can subscribe to
// changes.

const KEY = "slate.activeDevice";
const EVENT = "slate:active-device-changed";

export function getActiveDevice(): string | null {
  try {
    const v = localStorage.getItem(KEY);
    return v && v.trim() ? v.trim() : null;
  } catch {
    return null;
  }
}

export function setActiveDevice(slug: string | null): void {
  try {
    if (slug && slug.trim()) {
      localStorage.setItem(KEY, slug.trim());
    } else {
      localStorage.removeItem(KEY);
    }
  } catch {
    /* localStorage disabled — DevicePicker will still work in-memory
       for the session, just not persist across reloads. */
  }
  // Synchronous broadcast: same-tab listeners get notified now, and
  // cross-tab listeners get the native 'storage' event for free.
  window.dispatchEvent(new CustomEvent(EVENT, { detail: { slug } }));
}

export function subscribeActiveDevice(
  cb: (slug: string | null) => void,
): () => void {
  const handler = () => cb(getActiveDevice());
  window.addEventListener(EVENT, handler);
  // 'storage' fires for changes from OTHER tabs (same origin).
  window.addEventListener("storage", (e) => {
    if (e.key === KEY) handler();
  });
  return () => {
    window.removeEventListener(EVENT, handler);
    window.removeEventListener("storage", handler);
  };
}
