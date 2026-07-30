"""Two toto instances, one process, TWO databases.

datalink's subject is rows moving between databases, so — unlike the SSO federation
suite this is modelled on — it cannot share one. With a single database the rows the
receiver is supposed to create already exist with the same primary keys, so every
stage is a no-op, create-versus-update is untestable, every reference resolves so an
unresolvable one is unreachable, and there is nothing to conflict. The suite would pass
while proving nothing.

So there are two aliases, `default` (the receiver, where a run executes) and `peer`
(the source, which only ever serves reads), and one context manager — `bridge.peer_side`
— that swaps the ROOT_URLCONF *and* the database together. One entry point on purpose:
taking the urlconf without the database is an invisible bug in which the receiver reads
its own rows through the peer's views and every test still passes.

What is real: the models, the migrations, the views, the templates, the credential
hashing, the serialisation. Only the socket is gone — the receiver's outbound reads are
dispatched straight into the peer's views by a loopback that returns exactly the dict
``toto.api.client.execute_api_request`` returns, truncation branch included.

No auth app is installed here, deliberately. datalink has nothing to do with sign-in,
and toto-base cannot depend on toto-auth. The side effect is useful: `auth_posture()`
reports "not federated", so the degraded path — where replicated people may never be
claimed by an account — is the path every test in this suite runs.

Run it by naming the modules, since `toto` is a PEP 420 namespace package and the test
runner cannot discover a package label:

    DJANGO_SETTINGS_MODULE=toto.datalink.federation.settings python -m django test \
        toto.datalink.federation.tests.test_registry
"""
