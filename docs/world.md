# Virtual Facility

`GET /world` is a data-driven visualization of the demo facility. Every
color, LED, and pulse reflects a real database state - not a scripted
animation.

## Purpose

This page exists to make the *engineering logic* visible end-to-end:

- A **rack** turns from green to amber when an incident fires on that
  meter.
- A **pulse** on the rack signals that the open-incident count changed
  between two polls.
- The **test drive** buttons call real endpoints and show the actual
  responses.
- The **Live events** panel shows the last 15 incidents and decisions,
  ordered by timestamp.

The visualization is *not* a simulation. It renders whatever the database
says.

## Backend contract

`GET /api/v1/world/state` returns a single snapshot:
- `buildings[]` - each with `meters[]`
- `meters[].latest_reading` - latest by `timestamp_utc`, including
  `is_valid` so the UI can distinguish "no reading" from "invalid
  reading" from "healthy reading"
- `meters[].open_incidents` - count of `DETECTED | UNDER_REVIEW |
  ACKNOWLEDGED`
- `meters[].total_incidents` - count of every incident status
- `totals.decisions` - grouped by status
- `totals.scenarios` - count
- `recent_events[]` - last 15 mixed incidents and decisions

### Query budget

Single snapshot cost: ~9 indexed `SELECT`s and one `ORDER BY ... LIMIT`.
Roughly 20 ms against the demo dataset (~20k readings, ~200 incidents,
~50 decisions). No decision-engine calls. No LLM.

## Frontend behavior

- **Polling**: every 5 s while `document.visibilityState === "visible"`.
  No WebSocket; the demo is single-process SQLite.
- **Two views**: `Data Center` (floor plan of racks) and `Building`
  (skyline of floors). Both use the same data.
- **Drawer**: click any rack/floor to see meter metadata, latest
  reading, incident counts, and recent events for that meter.
- **Animations**: `prefers-reduced-motion` disables pulses. Pulsing a
  rack only reflects a real change in `open_incidents` between polls;
  nothing is animated for decoration.
- **Test drive**: four buttons call real endpoints (`/scenarios/evaluate`,
  `/decisions/{id}/verify`, `/incidents/{id}/evidence`). Results are
  shown inline; failures are surfaced verbatim.

## What this page does not do

- It does not connect to a real BMS.
- It does not claim the visualization is "real-time" - it is a 5 s
  polling snapshot.
- It does not fabricate sensor values, sensor noise, or temperature
  readings that the backend does not model.
- It does not compute engineering outcomes in JavaScript - every number
  comes from `/api/v1/world/state` or one of the test-drive endpoints.

## Why this design

Three constraints drove the choices:

1. **No infrastructure additions** (per project rules) - no WebSocket,
   no Redis, no MQTT. Polling a small snapshot is the correct tool for
   a single-process SQLite demo.
2. **Honest visualization** - the page must not suggest that the demo
   has 3D thermal simulation, airflow, or equipment control when it
   does not. Racks are represented as tiles whose only semantics are
   "this meter is healthy / warning / alert / stale".
3. **Discoverability** - a hiring reviewer should be able to see the
   pipeline fire without reading code. Clicking "Evaluate +40% load"
   produces a real REJECT in the events panel with the actual
   precedence (`hard_limit_violation` or `reserve_violation`), which is
   the same path a production caller would hit.

See also:
- `docs/decision-center.md` - decision inspector and evidence
- `docs/scenario-intelligence.md` - scenario evaluation and analysis
