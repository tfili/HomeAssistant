"""Custom Jinja template extensions for Home Assistant.

STAGED FILE -- move it into place, then restart Home Assistant:
    mv /config/www/fili_template_extensions_init.py \
       /config/custom_components/fili_template_extensions/__init__.py

Provides:
  * filter and test ``is_available`` -- False when a state is one of the "bad"
    sentinel values. Accepts a single value or a list/set/tuple, so one call
    can vet several entities at once.
  * globals ``warm_white_rgb`` / ``cool_white_rgb`` -- shared colour constants.

Usage from a template::

    {{ states('sensor.foo') | is_available }}        # filter form
    {% if states('sensor.foo') is available %}       # test form
    {{ [states('sensor.a'), states('sensor.b')] | is_available }}
    {{ warm_white_rgb }}                             # -> [245, 215, 160]

HOW THIS WORKS, AND WHY THE OBVIOUS APPROACH CANNOT
---------------------------------------------------
Home Assistant has no public API for registering Jinja filters from a custom
integration. Template environments are built **lazily** and cached per-hass
under three private HassKeys (``template.environment`` plus _LIMITED and
_STRICT variants). Two things are therefore needed, and the first attempt at
this fix did only the first:

  1. Replace ``template.TemplateEnvironment`` with a factory at module import
     time, so every environment built *after* we load receives the extras.
  2. Sweep ``hass.data`` during ``setup()`` and inject into any environment
     that already exists -- Home Assistant renders templates during startup
     and may well have built and cached one before this integration loaded.
     (Observed on 2026-11-24: with only step 1 in place, the filter was still
     unknown to the environment that template rendering actually used.)

The sweep finds environments by ``isinstance`` against the real class rather
than by importing the private HassKeys, so upstream renaming those keys cannot
break it.

(For the record: the previous version of this file did
``from homeassistant.helpers import TemplateEnvironment`` and then iterated
``TemplateEnvironment._environments``. That import raises ImportError --
``homeassistant/helpers/__init__.py`` contains nothing but a docstring, and
the class actually lives in the ``homeassistant.helpers.template`` *package*,
at ``template/__init__.py`` line 847 as of core-2026.11.2. And
``_environments`` has never existed in Home Assistant at all. So the module
died at import and ``setup()`` never ran -- which is why this integration sat
in the "could not be set up" repair since 2026-05.)

Instead we replace ``template.TemplateEnvironment`` with a factory at MODULE
IMPORT time. Every environment Home Assistant builds afterwards is created
through that factory and receives the extras. This is the approach used by
zvldz/ha_custom_filters, which this integration's original comment referenced.

Two consequences worth knowing:
  * A Home Assistant **restart** is required for changes to this file to take
    effect. Reloading does not re-import the module, so the patch is not
    re-applied.
  * The patch is global, so the factory must never raise. The injection is
    wrapped in try/except and the environment is returned either way: a fault
    here degrades to "the custom filters are missing", never to "every
    template in the instance fails to render".
"""

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import template

_LOGGER = logging.getLogger(__name__)

DOMAIN = "fili_template_extensions"

# Sentinel values meaning "this entity has no usable state". Deliberately the
# same four the original version used, so behaviour is unchanged. An empty
# string is arguably also unavailable but is NOT included, to avoid silently
# changing the semantics of existing templates.
INVALID_STATES = frozenset({"unknown", "unavailable", "none", "null"})

WARM_WHITE_RGB = [245, 215, 160]
COOL_WHITE_RGB = [200, 220, 255]


def is_available(value):
    """Return True only if every supplied state is a real, usable value.

    ``value`` may be a single state or a list/set/tuple of them; a group is
    available only if all of its members are. ``None`` is treated as the
    string "none" so it is caught like the textual sentinels.

    Note: an empty collection returns True (vacuously), matching the original
    implementation. Worth remembering if a template feeds this the result of
    an ``expand()`` that matched nothing.
    """
    values = value if isinstance(value, (list, set, tuple)) else [value]
    for item in values:
        text = "none" if item is None else str(item).strip().lower()
        if text in INVALID_STATES:
            return False
    return True


# Captured before patching so the factory can still build a stock environment.
_ORIGINAL_ENVIRONMENT = template.TemplateEnvironment


def _inject(env) -> bool:
    """Add our filter, test and globals to one Jinja environment.

    Never raises: this runs on the path that builds every template environment
    in the instance, so a fault here must degrade to "the custom filters are
    missing" rather than "no template renders".
    """
    try:
        env.filters["is_available"] = is_available
        env.tests["available"] = is_available
        env.globals["warm_white_rgb"] = WARM_WHITE_RGB
        env.globals["cool_white_rgb"] = COOL_WHITE_RGB
    except Exception:  # noqa: BLE001 - must never break template rendering
        _LOGGER.exception(
            "Could not inject template extensions into this environment; "
            "continuing without them so templates still render"
        )
        return False
    return True


def _make_environment(*args, **kwargs):
    """Build a normal TemplateEnvironment, then add our extras to it.

    Takes ``*args, **kwargs`` rather than naming the parameters so a future
    change to the upstream constructor signature cannot break this.
    """
    env = _ORIGINAL_ENVIRONMENT(*args, **kwargs)
    _inject(env)
    return env


# Step 1 -- patch once, so environments built after this import get the extras.
# On a fresh import TemplateEnvironment is the real class and the marker is
# absent; if this module is somehow re-imported, the marker on our own factory
# stops us wrapping a wrapper.
if not getattr(template.TemplateEnvironment, "_fili_patched", False):
    _make_environment._fili_patched = True
    template.TemplateEnvironment = _make_environment


def setup(hass: HomeAssistant, config: dict) -> bool:
    """Step 2 -- retrofit any environment that already existed.

    Home Assistant renders templates during startup, so by the time this
    integration is set up an environment may already be built and cached in
    ``hass.data``. The import-time factory cannot reach those, so sweep for
    them here. Identified by isinstance against the real class, which avoids
    depending on the private ``HassKey`` names.
    """
    patched = 0
    try:
        for value in list(hass.data.values()):
            if isinstance(value, _ORIGINAL_ENVIRONMENT) and _inject(value):
                patched += 1
    except Exception:  # noqa: BLE001 - setup must not fail over this
        _LOGGER.exception("Could not sweep hass.data for existing environments")

    _LOGGER.info(
        "fili_template_extensions active: filter+test 'is_available', globals "
        "'warm_white_rgb' and 'cool_white_rgb'; retrofitted %d pre-existing "
        "template environment(s)",
        patched,
    )
    return True
