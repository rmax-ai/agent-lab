from agentlab.backend import __version__


def test_backend_importable() -> None:
    assert __version__ == "0.1.0"
