"""mutagen — LLM-driven mutation testing for pytest suites."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mutagen-cli")
except PackageNotFoundError:  # pragma: no cover - source checkout, never installed
    __version__ = "0+unknown"
