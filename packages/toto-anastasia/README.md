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

## The manager

`toto.anastasia.manager` is the trusted half — a small `http.server` process
that runs in its own container with the Docker socket and, deliberately,
nothing else. Start it with `python -m toto.anastasia.manager`; it refuses to
start without `ANASTASIA_SHARED_SECRET`, because an unauthenticated Docker
manager is a remote shell.

**It keeps no database.** Everything it needs is derivable from Docker labels
(`anastasia.gear`, `anastasia.exec`, `anastasia.warm`, `anastasia.deadline`),
the cgroup tree, and the staging directory layout. That is what makes "destroy
every runner, every scratch area and the manager itself" survivable rather than
a data loss — and it means a restarted manager rebuilds its view by looking, so
it cannot drift from reality. Timeouts are enforced from the `deadline` label
by the reconcile loop rather than by a thread per execution, for the same
reason: a thread dies with the manager and leaves its runner running forever.

| Concern | Where |
|---|---|
| HMAC over method + path + body digest + timestamp + nonce | `protocol.py` |
| Hardened tar in and out (no links, budgets, resolved-path containment) | `staging.py` |
| The Gear cgroup: systemd transient slice, cgroupfs fallback, honest null | `slices.py` |
| Every Docker flag a runner gets — assembled, never accepted | `containers.py` |
| Operation → argv, on the trusted side only | `runners.py` |
| Mount / execute / collect / unmount | `gears.py` |
| Deadlines, orphans, staging sweep, adoption | `reconcile.py` |
| Refuse new mounts under memory or disk pressure | `pressure.py` |

Two details worth knowing before changing any of it:

* **`StartTransientUnit` is asynchronous.** `ensure()` waits for the cgroup to
  actually carry the limits it asked for; returning early would let a runner
  launch into a slice whose ceiling systemd had not written yet.
* **systemd expands every dash into a level**, so
  `anastasia-gear-<hex>.slice` lives at
  `anastasia.slice/anastasia-gear.slice/anastasia-gear-<hex>.slice`. The
  obvious shallow path finds nothing and reports a healthy Gear as
  unmeasurable.

`ManagerRuntimeBackend` (in `manager_backend.py`) is the Django side of the
wire and the only module in the app that knows the manager exists. Point
`ANASTASIA_RUNTIME_BACKEND` at it once a manager is deployed.

## Tests

Run them by module name — `toto` is a PEP 420 namespace package, so directory
discovery cannot walk it:

```bash
python manage.py test toto.anastasia.tests.test_booking \
    toto.anastasia.tests.test_mount toto.anastasia.tests.test_execute \
    toto.anastasia.tests.test_limits toto.anastasia.tests.test_protocol \
    toto.anastasia.tests.test_manager toto.anastasia.tests.test_service \
    toto.anastasia.tests.test_django_free
```

`test_docker_integration` additionally needs a reachable Docker daemon and
skips itself without one. It is the only place the claims about confinement are
actually proven — that the memory ceiling OOM-kills, that the pids limit stops
a fork bomb, that the scratch tmpfs is hard, that a runner has no network, no
root, no credentials and no Docker socket.

## Installing

Add `toto-anastasia==<version>` to a host's `requirements.toto.txt`, put
`toto.anastasia` in `INSTALLED_APPS`, and set `ANASTASIA_POOL`. Without a pool
the app still loads and warns (`anastasia.W001`); every reservation is refused
with a sentence naming the setting, because an unconfigured pool must not
silently become an unbounded one.

Booking works with no manager deployed. Mounting does not — and says so.
