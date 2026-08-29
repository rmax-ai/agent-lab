from agentlab.onboarding import __version__


def test_onboarding_importable() -> None:
    assert __version__ == "0.1.0"
