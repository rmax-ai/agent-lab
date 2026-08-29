from agentlab.world import __version__


def test_world_importable() -> None:
    assert __version__ == "0.1.0"
