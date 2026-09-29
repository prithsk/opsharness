"""Find installed worlds.

A world package registers its Env class under the "opsharness.worlds" entry
point group in its pyproject.toml. Installing the package is enough for every
opsharness command to see it.
"""
import sys
from importlib.metadata import entry_points


def worlds():
    out = {}
    for ep in entry_points(group="opsharness.worlds"):
        try:
            out[ep.name] = ep.load()
        except Exception as e:  # a broken world should not take the others down
            print(f"warning: could not load world '{ep.name}': {e}", file=sys.stderr)
    return out


def world(name):
    found = worlds()
    if name not in found:
        raise SystemExit(f"no installed world named '{name}'. Installed: {sorted(found) or 'none'}")
    return found[name]
