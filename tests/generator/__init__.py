"""Tests for the synthetic data generator (owner: Sarthak)."""

# The generator's current modules, named once for the layout tests. Discovery
# finds new modules automatically; this list only guards against discovery
# returning nothing and names the old top-level paths that must stay gone.
KNOWN_GENERATOR_MODULES: tuple[str, ...] = (
    "masters",
    "batches",
    "defects",
    "expected_findings",
    "approved_sample",
    "dataset_io",
    "cli",
    "catalog",
)
