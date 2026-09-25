"""`VstimdClient`, whichever vstimd-client is installed.

vstimd-client renamed `Connection` to `VstimdClient` after 0.3.0a1, which is
the release `rig_versions.toml` pins; `make e2e-local` installs the checkout,
which has only the new name. This is the one place that knows both, and it
goes when the pin moves past the rename.
"""

try:
    from vstimd import VstimdClient
except ImportError:  # vstimd-client 0.3.0a1
    from vstimd import Connection as VstimdClient  # type: ignore[no-redef]

__all__ = ["VstimdClient"]
