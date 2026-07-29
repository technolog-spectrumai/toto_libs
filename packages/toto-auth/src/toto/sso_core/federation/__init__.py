"""A provider and a consumer, federated in one Django process.

Production runs two hosts, two databases and two url trees: an OIDC provider
(``toto.sso_master``) and a consumer that delegates sign-in to it
(``toto.sso_client``). Nothing in the suite exercised that pair — ``sso_client``
had no tests at all — because a two-host flow looks like it needs two servers.

It does not. The two sides are separated here by URLCONF instead of by network,
and the consumer's outbound HTTP is dispatched straight into the provider's
views. Everything else is real: real authorization-code rows, real RS256 ID
tokens signed by a Gervazy-held key, real consent templates. Only the socket is
gone.

Lives in ``sso_core`` because that is the one auth app installed in *both*
modes, so both a provider host and a consumer host inherit the suite from the
wheel with no wiring of their own.

``toto`` is a PEP 420 namespace package, so the test runner cannot discover a
package label — name the modules, as the host gates already do for the
host-owned suites::

    DJANGO_SETTINGS_MODULE=toto.sso_core.federation.settings \\
        python -m django test \\
            toto.sso_core.federation.tests.test_auth_config \\
            toto.sso_core.federation.tests.test_federated_login \\
            toto.sso_core.federation.tests.test_claims_mapping

The clean-env gate runs exactly that via ``gate_federation_tests``.
"""
