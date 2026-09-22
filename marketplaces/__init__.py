"""
Marketplace plugins. Each module in this package (other than base) that
defines a module-level `PLUGIN` is one marketplace; see base.py for the
contract and README.md for how to add one.
"""
import importlib
import pkgutil


def discover():
    """Every plugin in this package, keyed by its id."""
    plugins = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_") or info.name == "base":
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        plugin = getattr(module, "PLUGIN", None)
        if plugin is None:
            continue
        if plugin.id in plugins:
            raise RuntimeError(f"Two marketplace plugins share the id {plugin.id!r}.")
        plugins[plugin.id] = plugin
    return plugins
