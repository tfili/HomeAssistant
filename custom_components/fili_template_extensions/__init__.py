import logging
from homeassistant.core import HomeAssistant
from homeassistant.helpers import TemplateEnvironment

_LOGGER = logging.getLogger(__name__)
DOMAIN = "fili_template_extensions" 

# https://github.com/zvldz/ha_custom_filters/blob/master/custom_components/custom_filters/__init__.py
# _TemplateEnvironment = template.TemplateEnvironment

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

    # Constants
    def warm_white_rgb():
        return [245, 215, 160]
    
    def cool_white_rgb():
        return [200, 220, 255]

    # Patch the class-level globals so all instances pick it up
    for env in TemplateEnvironment._environments.values():
        env.filters["is_available"] = is_available
        env.tests["available"] = is_available
        env.globals["warm_white_rgb"] = warm_white_rgb
        env.globals["cool_white_rgb"] = cool_white_rgb

    _LOGGER.info("Filter 'is_available' has been injected into the Template Environment")
    _LOGGER.info("Constants 'warm_white_rgb' and 'cool_white_rgb' have been injected into the Template Environment")

    return True