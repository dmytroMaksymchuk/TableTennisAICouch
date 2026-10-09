import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

export function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

export function renderWithProviders(
  ui: ReactElement,
  opts: { route?: string; client?: QueryClient } = {},
) {
  const client = opts.client ?? makeClient();
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[opts.route ?? "/"]}>{ui}</MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

export const jsonResponse = (body: unknown, status = 200) =>
  Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );

type Handler = (url: string, init?: RequestInit) => Promise<Response> | undefined;

/** Stubs global fetch; the first handler returning a response wins. */
export function stubFetch(...handlers: Handler[]) {
  const fn = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    for (const h of handlers) {
      const r = h(url, init);
      if (r) return r;
    }
    return jsonResponse({ error: { code: "not_found", message: `unhandled ${url}` } }, 404);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
