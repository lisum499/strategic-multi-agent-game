"""Hard military-capability rules derived from the 1.4 design."""
from __future__ import annotations

from itertools import combinations

NUCLEAR_STATUSES = ('无', '有，但发动打击能力有限', '有', '二次核打击')
DISTANCE_BANDS = ('adjacent', 'regional', 'distant')
DISTANCE_RANK = {name: rank for rank, name in enumerate(DISTANCE_BANDS)}
DELIVERY_RANK = {'无': -1, '有，但发动打击能力有限': 0, '有': 1, '二次核打击': 2}


def nuclear_status(country: dict) -> str | None:
    value = country.get('nuclear_status')
    return value if isinstance(value, str) else None


def derive_nuclear_status(country_id: str, country: dict) -> str:
    conventional = float(country['conventional'])
    nuclear = float(country['nuclear'])
    if conventional < 40 and nuclear > 0:
        raise ValueError(f'{country_id} 常规力量低于40，只能发展常规力量')
    if nuclear == 0:
        return '无'
    if nuclear <= 20:
        return '有，但发动打击能力有限'
    if nuclear < 60:
        return '有'
    return '二次核打击'


def validate_distances(state: dict) -> None:
    countries = state['countries']
    matrix = state.get('distance_bands')
    if not isinstance(matrix, dict) or set(matrix) != set(countries):
        raise ValueError('distance_bands 必须包含全部国家')
    for source, target in combinations(sorted(countries), 2):
        a = matrix.get(source, {}).get(target)
        b = matrix.get(target, {}).get(source)
        if a not in DISTANCE_BANDS or b != a:
            raise ValueError(f'distance_bands.{source}.{target} 必须完整、合法且双向一致')
    for source, values in matrix.items():
        if not isinstance(values, dict) or set(values) != set(countries) - {source}:
            raise ValueError(f'distance_bands.{source} 必须恰好包含全部其他国家')


def can_deliver(state: dict, source: str, target: str) -> bool:
    status = nuclear_status(state['countries'][source])
    distance = state['distance_bands'][source][target]
    return status in DELIVERY_RANK and DELIVERY_RANK[status] >= DISTANCE_RANK[distance]


def has_second_strike(country: dict) -> bool:
    return nuclear_status(country) == '二次核打击' and float(country.get('nuclear', 0)) > 0
