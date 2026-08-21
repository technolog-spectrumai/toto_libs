"""The trusted manager: the only thing in the platform allowed to touch Docker.

Runs in its own container with the Docker socket and, deliberately, **nothing
else** — no database, no Vault key, no SECRET_KEY, no Django. That emptiness is
what makes handing it the socket defensible: compromising it yields compute,
not data.

Every module here must import with no Django configured. ``toto.anastasia``'s
Django-free core (``limits``, ``families``, ``choices``) is importable from
here; ``models``, ``services`` and ``conf`` are NOT, and a test enforces it.

The manager keeps **no database of its own**. Its runtime index is rebuilt from
Docker labels and cgroup directories at boot, which is what makes "destroy
every runner and the manager itself" a survivable event rather than a data
loss: the durable record was always the caller's rows.
"""
