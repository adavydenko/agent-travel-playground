# Flat catalog and student MCP split

Date: 2026-10-22  
Status: draft for review  
Scope: config reshape, student MCP surface, RSVP; grader deferred

## Problem

The current config nests `flights` and `hotels` under each event and embeds a `request` brief. Student MCP mixes task prep (`inspect_task`), booking, and grading (`finish`). Agents can treat inspection/finish as an oracle, which is not a realistic booking workflow and undermines playground evaluation.

## Goals

1. Config describes a shared **world**: events, flights, hotels — not per-event puzzle packs with fixed trip requests.
2. Student agents **browse and book** (and RSVP); they do not receive grader feedback through MCP.
3. Operator issues a token (= attempt). Task text lives outside config (golden questions later).
4. Keep the model simple; defer automated grading.

## Non-goals (this pass)

- Admin/HTTP grade API or receipt/`finish` replacement
- Golden-question files
- Flight inventory / sold-out flags
- Seats counter on registration
- Clock times (document 14:00 check-in / 12:00 check-out as narrative defaults only)
- Admin agent for token issuance

## Config schema

Top-level:

```json
{
  "events": [],
  "flights": [],
  "hotels": []
}
```

### Event

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Unique within `events` |
| `name` | yes | Display title |
| `description` | yes | Free text for agents |
| `location` | yes | City/place; agents correlate hotels/flights to this |
| `start_date` | yes | `YYYY-MM-DD`, inclusive |
| `end_date` | yes | `YYYY-MM-DD`, inclusive; `start_date <= end_date` |
| `email_required` | no | Default `false`. When `true`, registration without email fails |

No `request` object. No nested catalogs.

### Flight

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Unique within `flights` |
| `origin` | yes | Must be comparable to event/hotel locations by string equality |
| `destination` | yes | |
| `depart_date` | yes | |
| `arrive_date` | yes | `arrive_date >= depart_date` |
| `price_rub` | yes | Non-negative integer |

Dropped vs today: `direction`, `available`. Listed flight ⇒ bookable. Omit flights that should not be offered.

### Hotel

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Unique within `hotels` |
| `name` | yes | |
| `location` | yes | Same vocabulary as `event.location` |
| `nightly_price_rub` | yes | Non-negative integer |
| `available_nights` | yes | List of `YYYY-MM-DD` night starts; missing night ⇒ unavailable |

### Validation at load

- Types, dates, money, uniqueness per list, chronological flight/event dates
- Do **not** require that every event has a solvable trip
- Shipped example config should remain solvable by construction (manual/author check + tests that complete a trip via tools)

### Example migration

Flatten today’s two events into global lists with globally unique IDs (e.g. `vvo-out-night`, `kzn-mayak`). Drop sold-out flight rows. Set `location` from former destinations. Add short `description` and `email_required: false`.

## Student MCP surface

### Removed

- `inspect_task`
- `finish`
- `start_task` (attempt begins with operator-issued token)

### Catalog (read)

- `list_events`
- `list_flights`
- `list_hotels`

v1 may return full lists. Optional filters (`location`, `origin`, `destination`, dates) are nice-to-have if cheap; not required for the first cut.

Catalog data comes from the attempt’s **config snapshot** (see Persistence).

### RSVP / conference registration

- `register_for_event(event_id, name, email?)` — one call = one attendance (one person)
- `list_registrations`
- `cancel_registration(registration_id)` — free, idempotent

Rules:

- `name` always required
- `email` optional unless the event has `email_required: true`
- Multiple registrations allowed (same or different events). Over-registration is **not** blocked by MCP; later grader / brief decides success (e.g. two attendances only if the user asked for two people)
- No `seats` field

### Booking

- `book_flight(flight_id)`
- `book_hotel(hotel_id, check_in, check_out)`
- `cancel_booking(booking_id)`
- `list_bookings`

Token scopes the attempt: **no `task_id` in tool arguments** if the bearer token already identifies the attempt (preferred). Preserve today’s idempotent re-book / safe re-cancel behavior where applicable.

Booking still validates local constraints only (unknown id, unavailable hotel nights, invalid date range). It does **not** check trip coherence or RSVP.

## Persistence and operator

- Operator issues a bearer token for a student/`agent_attempt` manually (existing admin flow)
- **Do not require** pinning an `event_id` on issue in this pass; the agent chooses via RSVP. Remove or ignore `issue --event` until a grader needs an expected event
- On issue (or first use), snapshot the full flat catalog `{events, flights, hotels}` into attempt state so mid-run config edits do not mutate an open attempt
- Attempt state holds `bookings` and `registrations` (+ existing quotas/journal patterns)
- Reset creates a new attempt opportunity; old journal retained
- No grade/report-of-pass in this pass beyond existing operational report/journal if still useful for operators

## Intended success rules (documentation only; grader later)

Not implemented now. For a future grader / golden briefs:

Physical/date constraints (dates-only model; 14:00 / 12:00 are narrative defaults):

- Cannot check in before arrival; cannot check out after leaving the city
- Cannot leave before arriving; cannot check out before check-in
- Cannot arrive after the event has started (outbound arrive before event start)
- Hotel stay should cover the event; flights should bracket the stay
- Hotel `location` matches event `location`; flight cities connect traveler to that location
- RSVP: expected number of attendances matches the brief; each attendance has required identity fields

## Docs and tests impact

- Update `docs/requirements.md` and README to the new contract
- Rewrite `config/events.json` example
- Update `service/catalog.py` models and loaders
- Rework MCP tools in `server.py` / `travel.py` / `store.py`
- Replace tests that assume nested catalogs, `inspect_task`, `finish`, and `request`
- Keep tests on temp DB/config; verify example config is usable via browse + register + book

## Open follow-ups

- Admin grade command / HTTP
- Golden question bank
- Optional list filters
- Passenger count on flights vs multiple RSVPs for multi-person trips
