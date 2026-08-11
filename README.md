# proven

[![ci](https://github.com/Dowgg18/unhealthy-until-proven/actions/workflows/ci.yml/badge.svg)](https://github.com/Dowgg18/unhealthy-until-proven/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-3776AB)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-MIT-2d6a4f)](LICENSE)
[![dependencies](https://img.shields.io/badge/runtime%20dependencies-none-CDC733)](pyproject.toml)

**A health check has three outcomes, not two.**

```
MEASURED   we asked, and we got an answer
STALE      we have an answer, but it is old enough to distrust
UNKNOWN    we could not find out
```

Almost every monitoring bug is the third one being quietly folded into one of the
first two. Fold it into healthy and a dead dependency shows green. Fold it into
down and a DNS blip pages someone at 3am. They are different facts, and only the
caller can decide which risk it wants.

`proven` makes that decision impossible to skip.

```python
from proven import probe, persist, load, is_healthy, DOWN

status = await probe(lambda: provider.session_state(id), name="session")
await persist(store, id, status)          # no-op if UNKNOWN. Deliberately.

status = await load(store, id, max_age=300)
if not is_healthy(status, unknown_as=DOWN, predicate=lambda s: s == "open"):
    stop_routing_to(id)
```

There is no `.ok`, no `__bool__`, and `unknown_as` has no default. If you want a
boolean, you say what an unverifiable check means to you — which is exactly the
decision that keeps getting skipped.

---

## Where this came from

A dashboard in a production system reported a messaging channel as **Connected**
for two months after the channel had died. Nobody was being careless. Four
separate mechanisms, each individually defensible, stacked into one lie.

### 1. The ratchet

```python
state = await provider.get_state(session_id)
if state == "open":
    await db.update(session_id, status="connected")
return state
```

Read it again for what it does *not* do. Nothing is written when the answer is
anything other than `"open"`. The column only ever moves toward `connected`, and
once it arrives it can never leave.

Both halves sound like good practice in isolation — "only write when you have
something good to record", "read the stored status instead of another round
trip". Together they build a mechanism that climbs monotonically to green.

**The guard:** `persist()` takes a `Health`, not a value, so there is no shape of
call that writes only the happy branch. And it is a no-op for `UNKNOWN` — "I could
not check" is not a fact about the subject and must never be recorded as one.

### 2. The error returned as a value, and never read

```ts
const { data } = await db.from("config").select("enabled, mode")
const enabled = data?.enabled !== false     // ships green when the query failed
```

The column did not exist. PostgREST returned error `42703` in the `error` half of
the tuple, which that file never reads — zero occurrences of the word `error` in
283 lines. `data` becomes `null`, `data?.enabled` becomes `undefined`, and
`undefined !== false` is `true`. The failure rendered as a healthy badge.

Three mistakes compose in one expression, and no type system objects, because
every step is legal.

There is a nasty inversion here worth naming: the *narrow projection*
(`select("enabled, mode")`, the practice usually taught as correct) is what broke
on schema drift, while `select("*")` — the sloppy habit — would have survived it.

**The guard:** `from_result(data, error)` takes both halves, so the error is an
argument rather than something you can forget to read.

### 3. The optimistic default at the call site

Every real incident here happened at the call site, not in the probe. The probes
were fine. So the fix cannot be a better probe — the type has to make the wrong
thing unwritable:

```python
>>> bool(Health.unknown("provider unreachable"))
TypeError: Health has no truth value on purpose. Collapsing it silently is the
bug this library exists to prevent — use is_healthy(h, unknown_as=...) and say
what an unverifiable check should count as.
```

### 4. Stale data read as current

A stored value is a claim about the past. Drop the timestamp and `connected`
outlives the connection.

```python
health = await load(store, key, max_age=300)
health.describe()      # 'connected [stale, 5184000s ago]'
```

`load` refuses to return a bare value, and treats *never written* as `UNKNOWN`
rather than healthy. An empty column is the absence of an observation.

---

## The rollup nobody gets right

`all` and `any` behave differently under `UNKNOWN`, and it is easy to swap them.

**`all_of` — one unknown contaminates the whole answer.** "Is every dependency
up?" cannot be answered while one is unreachable, no matter how many confirmed
healthy ones sit beside it.

**`any_of` — a confirmed pass wins.** "Is at least one replica serving?" is
answered the moment one says yes. But with no confirmed pass and at least one
unknown, the answer is `UNKNOWN`, not `False`.

The system this came from wrote it by hand as

```
connected > 0 ? "ok" : unreachable ? "unknown" : "down"
```

which is `any_of` logic applied to an `all_of` question. It reported a healthy
fleet whenever a single member was reachable, while the rest were unverifiable.

```python
verdict = all_of(members, lambda s: s == "open")
verdict.confidence      # Confidence.UNKNOWN
verdict.reason          # '1 of 3 could not be verified: session: timed out after 5s'
```

An empty collection has no default either. `all_of([])` is `UNKNOWN` until you
pass `empty_is=`, because "no dependencies configured" looks healthy and usually
is not.

---

## Install and run

```bash
pip install -e ".[dev]"
pytest
```

No runtime dependencies. Python 3.11+.

## What this is not

- **Not a metrics backend.** It produces a value; exporting it is your job.
- **Not a paging integration.** `NotVerifiable` exists so you can route "could
  not check" differently from "down", and that is where its opinion stops.
- **Not an uptime monitor.** There is no scheduler here. `probe` runs when you
  call it.
- **Not a circuit breaker.** No state machine, no half-open, no backoff.

## Limits, honestly

- `stale_as` defaults to `UP` in `is_healthy`, because a stale reading is still a
  reading and most callers want last-known-state. That is a default, so it is a
  place where the library relaxes its own rule. `unknown_as` never does.
- `probe` takes a `timeout` parameter, which ruff's `ASYNC109` discourages. Kept
  on purpose: making the bound opt-in would put the most dangerous configuration
  one forgotten wrapper away. The reasoning is in the code, next to the `noqa`.
- Freshness is wall-clock. Across machines with drifting clocks, a `STALE`
  verdict is only as good as your NTP.

MIT licensed.
