# ASAREE frontend

React 19 + TypeScript + Vite, styled with Tailwind v4 and a small set of local
shadcn-style primitives over [Base UI](https://base-ui.com/) (`src/components/ui`).
The visual language is deliberate and documented in the repo root's
[`CLAUDE.md`](../CLAUDE.md) — read that before adding a page or component.

## How it runs: dev server only, no build step

This is served by the **Vite dev server in every environment there currently
is**, including under Docker. There is no production build in the deployment
path, and nothing anywhere reads a `dist/` directory.

`docker compose up` (from the repo root — see the root [`README.md`](../README.md))
starts the `asaree-frontend` service on <http://localhost:5173>:

- [`Dockerfile`](Dockerfile) installs `node_modules` at image-build time and its
  `CMD` is `npm run dev -- --host 0.0.0.0`. That's the whole image.
- [`compose.yml`](../compose.yml)'s `asaree-frontend` bind-mounts `./frontend`
  to `/app`, so the container compiles your working tree directly and HMR picks
  up host-side edits live. An anonymous volume on `/app/node_modules` keeps the
  container's own (Linux-built) modules from being shadowed by the host's.
- So `docker compose build` for this service means `npm install`, nothing more.
  Editing a `.tsx` needs **no rebuild and no restart** — only a dependency
  change (`package.json`) does.

`/api/*` is proxied to the backend by the dev server, not called cross-origin
(see [`vite.config.ts`](vite.config.ts)). Under compose the proxy target is
overridden to `http://asaree-app:8000` via `VITE_API_PROXY_TARGET`, because
`127.0.0.1` inside the frontend container is that container, not the backend's.

The backend does not serve the frontend: the app image copies only `src/` and
`scripts/`, and there is no `StaticFiles` mount. `:8000` is the API alone.

### `npm run build` and `dist/`

You almost certainly don't want `npm run build`. It's the stock Vite script and
nothing consumes its output — running it just leaves a stray, gitignored
`dist/` in your checkout that looks like it might be what the browser is
loading. It isn't, and once it goes stale it is an actively misleading thing to
debug against. If you find a `dist/` here, delete it.

The genuinely useful half of that script is the type-check in front of it, which
is worth running on its own before you commit (the dev server transpiles
*without* type-checking, so it will happily serve code `tsc` rejects):

```bash
docker compose exec asaree-frontend npx tsc -b   # types
docker compose exec asaree-frontend npm run lint # oxlint
```

`npm run build` would only start to matter if someone adds a production frontend
image — a multi-stage build emitting `dist/` behind nginx, with a real reverse
proxy replacing the Vite `/api` proxy. That doesn't exist today.

## Working on the host instead

Nothing stops you running the dev server directly (`npm install && npm run dev`),
which proxies `/api` to `http://127.0.0.1:8000` by default. Stop the
`asaree-frontend` container first — otherwise both want port 5173.
