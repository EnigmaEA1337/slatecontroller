import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Don't refetch when the user returns to the tab — `staleTime` handles
      // freshness, and dashboard widgets that need live data already
      // declare their own `refetchInterval`.
      refetchOnWindowFocus: false,
      // Made explicit for the audit trail (default is already false):
      // `refetchInterval` timers PAUSE when the browser tab is hidden and
      // resume when it's visible again. Avoids burning bandwidth on a
      // controller tab the user left in the background. Components that
      // need to keep polling even in background (very rare) can override
      // per-query with `refetchIntervalInBackground: true`.
      refetchIntervalInBackground: false,
      retry: 1,
      staleTime: 10_000,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {/* Outermost ErrorBoundary catches anything React-render that escapes
        the route-level boundaries. Pre-empts white-screen-of-death. */}
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
