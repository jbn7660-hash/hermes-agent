"""Hermes-home path resolution extracted from ``gateway/run.py``.

Part of the hermes-monolith-split refactor (Phase 1).  Owns the module-level
``_hermes_home`` snapshot that was previously initialised inline at gateway
import time.  Eight gateway runner helpers (per Plan v3 audit) still close
over this value via gateway.run's module-global lookup; centralising it
here lets those helpers be moved out of ``gateway/run.py`` in a later
phase without each extraction needing its own ``get_hermes_home`` import.

The value is cached at this module's first import.  This matches the
previous behaviour: ``get_hermes_home()`` was called exactly once during
``gateway/run.py`` import, and ``_hermes_home`` was treated as a stable
snapshot thereafter.  Tests that need to override the path patch the
``gateway.run._hermes_home`` attribute (see
``tests/cli/test_personality_none.py`` and
``tests/gateway/test_internal_event_bypass_pairing.py``); that pattern
continues to work because ``gateway/run.py`` re-exports the name into
its own module namespace via ``from gateway._helpers.paths import
_hermes_home``, and the 50+ in-file consumers read from gateway.run's
binding rather than reaching into this module.
"""

from hermes_constants import get_hermes_home

_hermes_home = get_hermes_home()
