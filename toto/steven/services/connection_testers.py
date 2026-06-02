"""Connection-test dispatch. Strategy implementations live in provider_strategies.py."""
from toto.steven.services.provider_strategies import REGISTRY


def test_connection(connector, api_key: str = "", timeout: int = 10) -> dict:
    strategy = REGISTRY.get(connector.provider)
    if strategy is None:
        return {
            "ok": False,
            "message": f"No strategy registered for provider {connector.provider!r}.",
        }
    return strategy.test(connector, api_key, timeout)
