"""Hard military-capability rules derived from the 1.4 design."""
from __future__ import annotations

REGIONS = ('region_1', 'region_2', 'region_3', 'region_4')


def can_choose_nuclear_action(country: dict, action_level) -> bool:
    """Derive the current action band directly from nuclear force."""
    level = float(action_level)
    nuclear = float(country.get('nuclear', 0))
    if nuclear <= 0:
        return False
    if nuclear <= 40:
        return 12.5 <= level < 45.0
    return level >= 12.5


def validate_regions(state: dict) -> None:
    if 'distance_bands' in state:
        raise ValueError('distance_bands 已删除；请为每个国家设置 region')
    for country_id, country in state['countries'].items():
        if country.get('region') not in REGIONS:
            raise ValueError(f'{country_id}.region 必须是 {", ".join(REGIONS)} 之一')


def can_deliver(state: dict, source: str, target: str) -> bool:
    nuclear = float(state['countries'][source].get('nuclear', 0))
    same_region = state['countries'][source]['region'] == state['countries'][target]['region']
    if nuclear <= 0:
        return False
    if nuclear <= 40:
        return same_region
    return True


def has_second_strike(country: dict) -> bool:
    return float(country.get('nuclear', 0)) >= 41
