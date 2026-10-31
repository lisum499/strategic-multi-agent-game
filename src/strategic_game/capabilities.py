"""Hard military-capability rules derived from the 1.4 design."""
from __future__ import annotations

def can_choose_nuclear_action(country: dict, action_level) -> bool:
    """Derive the current action band directly from nuclear force."""
    level = float(action_level)
    nuclear = float(country.get('nuclear', 0))
    if nuclear <= 0:
        return False
    if nuclear <= 40:
        return 12.5 <= level < 45.0
    return level >= 12.5

def has_second_strike(country: dict) -> bool:
    return float(country.get('nuclear', 0)) >= 41
