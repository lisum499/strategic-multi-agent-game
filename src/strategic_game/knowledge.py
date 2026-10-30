"""Private national profile tags; definitions are research context, not rules."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

DICTIONARY_PATH = Path(__file__).parent / 'knowledge' / 'label_dictionary.json'


def load_dictionary():
    raw = DICTIONARY_PATH.read_bytes()
    dictionary = json.loads(raw.decode('utf-8'))
    return dictionary, hashlib.sha256(raw).hexdigest()


def describe_tags(tags, dictionary, digest):
    """Resolve only explicitly configured tags from a single dictionary snapshot."""
    if not isinstance(tags, dict):
        raise ValueError('tags 必须是维度到标签值的对象')
    dimensions = dictionary['dimensions']
    explanations = {}
    for key, value in tags.items():
        if key not in dimensions:
            raise ValueError(f'未知标签维度: {key}')
        info = dimensions[key]
        if key == 'ideology' and type(value) is int and 1 <= value <= 5:
            entry = info['values']['侧重程度']
        elif isinstance(value, str) and value in info['values'] and value != '侧重程度':
            entry = info['values'][value]
        else:
            raise ValueError(f'标签取值无效: {key}={value!r}')
        explanations[key] = {'label': info['label'], 'value': value, 'definition': entry['definition']}
    return {'version': dictionary['version'], 'status': dictionary['status'],
            'sha256': digest, 'scope': dictionary['scope'], 'dimensions': explanations}
