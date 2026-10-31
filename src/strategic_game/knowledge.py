"""Tag descriptions: explicit source-grounded context, never game mechanics."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

DICTIONARY_PATH = Path(__file__).parent / 'knowledge' / 'label_dictionary.json'


def load_dictionary():
    raw = DICTIONARY_PATH.read_bytes()
    dictionary = json.loads(raw.decode('utf-8'))
    return dictionary, hashlib.sha256(raw).hexdigest()


def _references(value, country_ids, country_id, name):
    if not isinstance(value, list):
        raise ValueError(f'{name} 必须是国家 ID 数组')
    if country_ids is None:
        raise ValueError(f'{name} 需要场景国家 ID 集合以校验引用')
    valid = set(country_ids)
    result = []
    for target in value:
        if not isinstance(target, str) or target not in valid or target == country_id:
            raise ValueError(f'{name} 含无效的其他国家 ID: {target!r}')
        if target in result:
            raise ValueError(f'{name} 含重复国家 ID: {target}')
        result.append(target)
    return result


def _stakeholders(value, country_ids, country_id):
    if not isinstance(value, dict) or set(value) - {'allies', 'adversaries', 'others', 'description'}:
        raise ValueError('stakeholders 仅接受国家 ID 数组及 description 说明')
    result = {key: _references(ids, country_ids, country_id, f'stakeholders.{key}')
              for key, ids in value.items() if key != 'description'}
    if 'description' in value:
        text = value['description']
        if not isinstance(text, str) or not text.strip() or len(text) > 3000:
            raise ValueError('stakeholders.description 必须是非空文本（最多3000字）')
        result['description'] = text
    return result


def _geography(value, country_ids, country_id):
    if not isinstance(value, dict):
        raise ValueError('relative_geography 必须是其他国家 ID 到地理关系描述的对象')
    _references(list(value), country_ids, country_id, 'relative_geography')
    result = {}
    for target, description in value.items():
        if not isinstance(description, str) or not description.strip() or len(description) > 3000:
            raise ValueError(f'relative_geography.{target} 必须是非空地理描述文本（最多3000字）')
        result[target] = description
    return result


def _describe_value(key, value, info, country_ids=None, country_id=None):
    label = info['label']
    if key == 'stakeholders':
        value = _stakeholders(value, country_ids, country_id)
        entry = info
    elif key == 'relative_geography':
        value = _geography(value, country_ids, country_id)
        entry = info
    elif type(value) is int and 'scale' in info:
        entry = info['scale']
        if not entry.get('min', 1) <= value <= entry.get('max', 5):
            raise ValueError(f'标签取值无效: {key}={value!r}')
    elif key == 'ideology' and type(value) is int and 1 <= value <= 5:
        entry = info['values']['侧重程度']
    elif isinstance(value, str) and value in info.get('values', {}) and not (key == 'ideology' and value == '侧重程度'):
        entry = info['values'][value]
    else:
        raise ValueError(f'标签取值无效: {key}={value!r}')
    result = {'label': label, 'value': copy.deepcopy(value), 'definition': entry['definition']}
    if info.get('source'):
        result['source'] = info['source']
    # Keep applicable source qualifications, competing source interpretations,
    # and field-level definitions, rather than turning research hypotheses into rules.
    # Include the exact relevant source passages, with attribution; do not inject unrelated labels.
    excerpts = {}
    if info.get('original_excerpt'):
        excerpts['dimension'] = copy.deepcopy(info['original_excerpt'])
    if entry is not info and entry.get('original_excerpt'):
        excerpts['selected_value'] = copy.deepcopy(entry['original_excerpt'])
    if excerpts:
        result['original_excerpt'] = excerpts
    for name in ('source_conflicts', 'source_hypotheses', 'note'):
        if name in entry:
            result[name] = copy.deepcopy(entry[name])
        elif name in info:
            result[name] = copy.deepcopy(info[name])
    if entry is not info and info.get('definition'):
        result['dimension_definition'] = info['definition']
    return result


def _describe(tags, dimensions, dictionary, digest, *, country_ids=None, country_id=None):
    if not isinstance(tags, dict):
        raise ValueError('tags 必须是维度到标签值的对象')
    explanations = {}
    for key, value in tags.items():
        if key not in dimensions:
            raise ValueError(f'未知标签维度: {key}')
        explanations[key] = _describe_value(key, value, dimensions[key], country_ids, country_id)
    return {'version': dictionary['version'], 'status': dictionary['status'],
            'sha256': digest, 'scope': dictionary['scope'], 'dimensions': explanations}


def describe_tags(tags, dictionary, digest, country_ids=None, country_id=None):
    """Resolve only a country's explicitly configured tags; references remain private."""
    return _describe(tags, dictionary['dimensions'], dictionary, digest,
                     country_ids=country_ids, country_id=country_id)
