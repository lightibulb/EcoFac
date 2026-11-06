"""DEF-22: Render ephemeral-storage warning."""

from ecofac.main import ephemeral_storage_warning


def test_ephemeral_warning_on_render() -> None:
    env = {"RENDER": "true"}
    msg = ephemeral_storage_warning(env.get)
    assert msg is not None
    assert "persistent" in msg.lower()


def test_no_warning_off_render() -> None:
    assert ephemeral_storage_warning({}.get) is None


def test_no_warning_with_persistent_path() -> None:
    env = {"RENDER": "true", "ECOFAC_DB_PATH": "/data/db.sqlite"}
    assert ephemeral_storage_warning(env.get) is None
