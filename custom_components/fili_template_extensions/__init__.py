import logging
from homeassistant.core import HomeAssistant
from homeassistant.helpers import template

_LOGGER = logging.getLogger(__name__)
DOMAIN = "fili_template_extensions" 

# https://github.com/zvldz/ha_custom_filters/blob/master/custom_components/custom_filters/__init__.py
_TemplateEnvironment = template.TemplateEnvironment

def setup(hass: HomeAssistant, config: dict):
    """Set up the component."""

    def is_available(value):
        """Check if the provided state string(s) are valid."""
        invalid_states = {"unknown", "unavailable", "none", "null"}
        
        # 1. Convert input into a list for consistent processing
        if isinstance(value, (list, set, tuple)):
            states_to_check = value
        else:
            states_to_check = [value]
            
        for s in states_to_check:
            # Handle cases where a state might be None or not a string
            str_state = str(s).lower() if s is not None else "none"
            
            if str_state in invalid_states:
                return False
                
        return True

    # Register the filter
    # template.TemplateEnvironment.filters["is_available"] = is_available
    # template.TemplateEnvironment.tests["available"] = is_available

    if "template.environment" in hass.data:
        hass.data["template.environment"].filters["is_available"] = is_available
        hass.data["template.environment"].tests["available"] = is_available
        _LOGGER.warning("Template Environment is available")
    else:
        _LOGGER.error("Template Environment is NOT available")
    
    _LOGGER.info("Filter 'is_available' has been injected into the Template Environment")

    return True