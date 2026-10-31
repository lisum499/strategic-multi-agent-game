"""Hard military-capability rules derived from the 1.4 design."""
from __future__ import annotations

NUCLEAR_STATUSES = ('无', '有', '二次核打击')
REGIONS = ('region_1', 'region_2', 'region_3', 'region_4')


def nuclear_status(country: dict) -> str | None:
    value = country.get('nuclear_status')
    return value if isinstance(value, str) else None


def derive_nuclear_status(country_id: str, country: dict) -> str:
    conventional = float(country['conventional'])
    nuclear = float(country['nuclear'])
    if conventional < 40 and nuclear > 0:
        raise ValueError(f'{country_id} 常规力量低于40，只能发展常规力量')
    if nuclear <= 20:
        return '无'
    if nuclear <= 60:
        return '有'
    return '二次核打击'


def can_choose_nuclear_action(country: dict, action_level) -> bool:
    """Apply the public nuclear-capability decision bands."""
    level = float(action_level)
    status = nuclear_status(country)
    if status == '有':
        return 12.5 <= level <= 45.0
    if status == '二次核打击':
        return level >= 45.0
    return False


def validate_regions(state: dict) -> None:
    if 'distance_bands' in state:
        raise ValueError('distance_bands 已删除；请为每个国家设置 region')
    for country_id, country in state['countries'].items():
        if country.get('region') not in REGIONS:
            raise ValueError(f'{country_id}.region 必须是 {", ".join(REGIONS)} 之一')


def can_deliver(state: dict, source: str, target: str) -> bool:
    status = nuclear_status(state['countries'][source])
    same_region = state['countries'][source]['region'] == state['countries'][target]['region']
    if status == '有':
        return same_region
    return status == '二次核打击'


def has_second_strike(country: dict) -> bool:
    return nuclear_status(country) == '二次核打击' and float(country.get('nuclear', 0)) > 0
