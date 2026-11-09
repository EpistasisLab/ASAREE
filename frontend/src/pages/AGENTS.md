# Page instructions

Read [frontend instructions](../../AGENTS.md) before editing pages. For canvas/page
integration, also read [protocol instructions](../components/protocol/AGENTS.md).

## Experiments list

- `ExperimentsPage.tsx` is a container list: retain its responsive card/tile grid rather
  than converting it to a dense records table.
- Keep titles truncated and descriptions `line-clamp-3`; expose full text via `title`.
  Include compact metadata badges/date. Full descriptions belong in the opened experiment.
- Preserve search, design/progress filters, sorting, and pagination together: filter and
  sort before slicing pages, reset/clamp page selection when filters change, and distinguish
  unavailable progress from zero progress. Fetch maintained summaries for the visible page;
  a progress filter needs summaries for the full matching set before pagination. Avoid
  fetching full run evidence to render list metadata.
- Summary strips must show real aggregates, such as scored progress; avoid repeating the
  list count or fields already shown on every tile.
- Preserve design-type icons, the glowing top accent strip, relative dates with exact-date
  hover text, short monospace IDs, faint watermark icons, and hover lift/glow/arrow effects.
  Icons and arrows follow the card accent as specified in the frontend instructions.
- Experiment navigation follows the linked protocol instructions; keep per-experiment
  content in the canvas panel/top bar.
