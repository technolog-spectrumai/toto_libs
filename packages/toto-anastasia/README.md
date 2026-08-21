# toto-anastasia

`toto-anastasia` is the **compute** distribution of the toto suite: reserved
capacity, mounted Compute Gears, and disposable runners. It is infrastructure,
not a product — it owns no documents, no datasets and no business workflows,
and it is the successor to the retired *placidia* host's compute role.

See `portal/anastasia.md` for the architecture this package implements.

## The model: conscious provisioning

Compute is not something a feature quietly grabs. It is something a user
reserves, mounts, watches, and then selects:

```text
pool → reserve → ComputeLease → mount → GearRuntime → select → Execution
```

Three concepts, kept separate on purpose:

| Concept | What it is |
|---|---|
| `ComputeLease` | reserved CPU / RAM / scratch / PIDs. **Deducted from the pool while idle** — that is what a reservation means. |
| `GearRuntime` | the mounted, alive, bounded environment: `READY` / `BUSY` / `DEGRADED` / `DEAD`, with live usage. |
| `Execution` | one heavy job — a disposable runner created inside the Gear and destroyed when it finishes. |

Mounting is a **separate act** from reserving, and unmounting does **not**
surrender the booking. A Gear stays mounted across many jobs until the user
unmounts it or the lease expires.

## What it does (functional)

- **A pool with honest arithmetic.** `ANASTASIA_POOL` is the total this
  deployment will hand out. Reservations are admitted against it in all four
  dimensions at once, and a refusal names every dimension that is short rather
  than making somebody discover them one retry at a time.
- **Two levels of the same algebra.** A Gear is a little pool: its live
  executions book against it exactly as its lease books against the host. That
  is what lets one Gear host several concurrent jobs while none of them can
  exceed what the user reserved.
- **A narrow logical API.** Callers name an *operation* — `render_pdf`,
  `compile_latex`, `normalize_media`, `run_ocr`, `start_python_runtime` — and
  supply *declared parameters*. There is no way to express an image, a mount, a
  flag, a capability, a command or privileged mode, because the vocabulary in
  `families.py` has no word for them.
- **Warm runtimes, inside a Gear only.** Nothing idles outside a reservation.
  Inside one, a user may keep runners warm (`warm_policy`) — the capacity is
  already reserved and already deducted, so warmth there costs the pool nothing
  it was not costing anyway. Only the `python` family is warmable, because only
  it has state worth keeping.
- **An append-only history**, refusals included: a history that only records
  what worked cannot answer "why can I not mount this".

## How it works (technical)

Two halves, deliberately separable:

- **The Django app** (`models`, `services`, `execute`, `checks`) owns the
  booking arithmetic and the durable record. These rows survive destroying
  every container — that is the campaign's central invariant.
- **The manager** (`toto.anastasia.manager`) is a small trusted process, the
  only thing in the platform allowed to talk to Docker. It must import with
  **no Django at all**, which is what makes it safe to hand it the socket;
  `tests/test_django_free.py` enforces that, and verifies its own guard bites
  before trusting what it lets through.

Between them sits `runtime.RuntimeBackend` — a stateless ABC with a safe
default (`NullRuntimeBackend`, which refuses to pretend a Gear is mounted when
nothing was mounted) and a lazily-resolved dotted path, so every service in
this package is testable with no Docker, no manager and no network.

### Enforcement

Runners are bounded by systemd slice nesting, so a runner is inside its Gear
*by construction* rather than by a check:

```text
anastasia.slice                  ← the pool ceiling
  anastasia-gear-<uuid>.slice    ← the Gear ceiling (CPUQuota/MemoryMax/TasksMax)
    docker-<runner>.scope        ← the execution
```

`Limits.memory_bytes` is `ram_mb + scratch_mb` on purpose: scratch is a tmpfs
(the deploy hosts run overlayfs on ext4, where `--storage-opt size=` is
unavailable) and tmpfs pages are charged to the cgroup that faults them in, so
a ceiling of RAM alone would let a full scratch OOM the compute beside it.

### Metering

One metric, `anastasia.execution` — a **rate limit on submissions**, not a
price on compute. Reserved capacity is held over time, which is a levy rather
than a per-request count; metering resources here as well would charge twice
for one thing. The reservation levy is a documented TODO.

## Installing

Add `toto-anastasia==<version>` to a host's `requirements.toto.txt`, put
`toto.anastasia` in `INSTALLED_APPS`, and set `ANASTASIA_POOL`. Without a pool
the app still loads and warns (`anastasia.W001`); every reservation is refused
with a sentence naming the setting, because an unconfigured pool must not
silently become an unbounded one.

Booking works with no manager deployed. Mounting does not — and says so.
