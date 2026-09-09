"""The trusted executor: the only thing in the platform allowed to run a job.

A ROOT-OWNED SYSTEMD SERVICE ON THE HOST since 2026-09-10, listening on a unix
socket at /run/anastasia/executord.sock. It was a container holding
``/var/run/docker.sock``, and the socket was the problem: any process that
could reach that container could ask the daemon for a container mounting the
host's root filesystem. Now no container holds a socket Django can see, and
what web and celery are given is a socket that speaks nine verbs and refuses
everything else.

Deliberately **nothing else** — no database, no Vault key, no SECRET_KEY, no
Django. That emptiness is what makes trusting it defensible: compromising it
yields compute, not data.

Every module here must import with no Django configured. ``toto.anastasia``'s
Django-free core (``limits``, ``families``, ``choices``) is importable from
here; ``models``, ``services`` and ``conf`` are NOT, and a test enforces it —
in a subprocess, with a ``find_spec`` blocker that is proven to bite before
anything else is asserted.

The executor keeps **no database of its own**. Its runtime index is rebuilt
from container labels and cgroup directories at boot, which is what makes
"destroy every runner and the executor itself" a survivable event rather than
a data loss: the durable record was always the caller's rows.
"""
