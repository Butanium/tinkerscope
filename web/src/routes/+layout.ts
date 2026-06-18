// Client-rendered SPA: no SSR, no prerender. adapter-static emits a single
// index.html fallback and FastAPI serves it for every route.
export const ssr = false;
export const prerender = false;
