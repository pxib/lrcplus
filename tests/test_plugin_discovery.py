from core.plugin_manager import PluginManager


def _create_plugin(folder):
    folder.mkdir(parents=True)
    (folder / "plugin.py").write_text(
        "class TestPlugin:\n"
        "    name = 'Test Plugin'\n"
        "\n"
        "PLUGIN_CLASS = TestPlugin\n",
        encoding="utf-8",
    )


def test_discovery_loads_core_community_and_legacy_plugins(tmp_path):
    plugins_dir = tmp_path / "plugins"
    _create_plugin(plugins_dir / "core" / "official_plugin")
    _create_plugin(plugins_dir / "community" / "community_plugin")
    _create_plugin(plugins_dir / "legacy_plugin")

    manager = PluginManager()
    catalog = manager.discover_plugins(plugins_dir)

    assert {
        (plugin["plugin_id"], plugin["category"])
        for plugin in catalog
    } == {
        ("official_plugin", "Core"),
        ("community_plugin", "Community"),
        ("legacy_plugin", "Community"),
    }

    manager.discover_and_load(plugins_dir)

    assert set(manager.plugins) == {
        "official_plugin",
        "community_plugin",
        "legacy_plugin",
    }
    manager.unload_all()


def test_disabled_core_plugin_loads_but_disabled_community_plugin_does_not(tmp_path):
    plugins_dir = tmp_path / "plugins"
    _create_plugin(plugins_dir / "core" / "official_plugin")
    _create_plugin(plugins_dir / "community" / "community_plugin")

    manager = PluginManager(
        disabled_plugins={"official_plugin", "community_plugin"}
    )
    manager.discover_and_load(plugins_dir)

    assert set(manager.plugins) == {"official_plugin"}
    manager.unload_all()