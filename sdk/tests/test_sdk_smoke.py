from agentlab.sdk import __version__


def test_sdk_importable() -> None:
    assert __version__ == "0.1.0"
