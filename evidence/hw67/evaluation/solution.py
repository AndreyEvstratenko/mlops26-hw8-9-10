"""Строгая оценка ответов и допуск кандидата без регрессии."""
import json
import math


def _index(items):
    if not isinstance(items, list) or not items:
        raise ValueError('Нужен непустой список записей')
    result = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Запись должна быть словарём')
        key = item.get('id')
        if not isinstance(key, str) or not key or key in result:
            raise ValueError('Нужны уникальные непустые строковые id')
        result[key] = item
    return result


def _slices(item):
    values = item.get('slices')
    if (not isinstance(values, list) or not values
            or any(not isinstance(s, str) or not s for s in values)
            or len(values) != len(set(values))):
        raise ValueError('Нужны уникальные именованные срезы')
    return values


def _summary(items):
    n = len(items)
    correct = sum(i['correct'] for i in items)
    valid = sum(i['valid'] for i in items)
    return dict(n=n, correct=correct, valid=valid,
                errors=sum(i['error'] is not None for i in items),
                accuracy=correct / n, format_rate=valid / n)


def _strict_object(pairs):
    """Не разрешать повторяющиеся ключи, скрываемые обычным json.loads."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Повторяющийся ключ JSON')
        result[key] = value
    return result


def score(rows, outputs):
    """Верните overall, slices и per_item. Не удаляйте плохие ответы из знаменателя.

    Строгий ответ: JSON с единственным строковым полем answer из choices.
    outputs: [{id, raw, error, latency_seconds}, ...].
    В overall и каждом срезе: n, correct, valid, errors, accuracy, format_rate.
    В per_item: id, raw, parsed, valid, correct, error, slices.
    Несовпадающие/дублирующиеся id и пустой набор должны вызвать ValueError.
    """
    tasks, answers = _index(rows), _index(outputs)
    if tasks.keys() != answers.keys():
        raise ValueError('ID заданий и ответов не совпадают')
    items = []
    for key, row in tasks.items():
        slices = _slices(row)
        choices = row.get('choices')
        if (not isinstance(choices, list) or not choices
                or any(not isinstance(c, str) for c in choices)
                or row.get('expected') not in choices):
            raise ValueError('Некорректные choices или expected')
        answer = answers[key]
        if 'raw' not in answer or 'error' not in answer:
            raise ValueError('В ответе нужны raw и error')
        raw, error = answer['raw'], answer['error']
        if error is not None and (not isinstance(error, str) or not error):
            raise ValueError('error должен быть непустой строкой или null')
        parsed, valid = None, False
        if error is None and isinstance(raw, str):
            try:
                parsed = json.loads(raw, object_pairs_hook=_strict_object)
                valid = (isinstance(parsed, dict) and set(parsed) == {'answer'}
                         and isinstance(parsed['answer'], str) and parsed['answer'] in choices)
            except (ValueError, RecursionError):
                parsed = None
        items.append(dict(id=key, raw=raw, parsed=parsed, valid=valid,
                          correct=valid and parsed['answer'] == row['expected'],
                          error=error, slices=slices))
    names = sorted({s for item in items for s in item['slices']})
    return dict(overall=_summary(items),
                slices={s: _summary([i for i in items if s in i['slices']]) for s in names},
                per_item=items)


def _validate_summary(actual, expected):
    if not isinstance(actual, dict):
        raise ValueError('Отсутствует сводка метрик')
    for key in ('n', 'correct', 'valid', 'errors'):
        if type(actual.get(key)) is not int or actual[key] != expected[key]:
            raise ValueError(f'Некорректный count: {key}')
    for key in ('accuracy', 'format_rate'):
        value = actual.get(key)
        if (type(value) not in (int, float) or not math.isfinite(value)
                or value != expected[key]):
            raise ValueError(f'Метрика {key} не соответствует counts')


def _validate_scores(report, minimum):
    if not isinstance(report, dict):
        raise ValueError('Отсутствует отчёт')
    items = _index(report.get('per_item'))
    if len(items) < minimum:
        raise ValueError(f'Нужно минимум {minimum} примеров')
    for item in items.values():
        _slices(item)
        if any(type(item.get(k)) is not bool for k in ('valid', 'correct')):
            raise ValueError('valid и correct должны быть булевыми')
        if any(k not in item for k in ('raw', 'parsed', 'error')):
            raise ValueError('Неполный per_item')
        if item['error'] is not None:
            raise ValueError('Ошибка генерации')
        if item['correct'] and not item['valid']:
            raise ValueError('Невалидный ответ помечен верным')
        if item['valid'] and (not isinstance(item['parsed'], dict)
                              or set(item['parsed']) != {'answer'}
                              or not isinstance(item['parsed']['answer'], str)):
            raise ValueError('Невалидный parsed')
    values = list(items.values())
    _validate_summary(report.get('overall'), _summary(values))
    groups = report.get('slices')
    names = {s for i in values for s in i['slices']}
    if not isinstance(groups, dict) or set(groups) != names:
        raise ValueError('Неполный набор срезов')
    for name in names:
        _validate_summary(groups[name], _summary([i for i in values if name in i['slices']]))
    return items


def quality_gate(baseline, candidate, critical_slices):
    """Верните {passed: True, ...} или вызовите ValueError с причиной отказа.

    Проверьте совместимость протокола/набора/baseline, метрики и counts,
    все id и срезы, общую accuracy/format_rate, критические срезы и behavior.
    Missing, NaN, пустой набор и ошибки генерации означают отказ.
    Пороги задаются до измерения; учебный допуск регрессии равен нулю.
    """
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        raise ValueError('Нужны два отчёта')
    for key in ('protocol_sha256', 'golden_sha256', 'baseline_spec_sha256'):
        value = baseline.get(key)
        if not isinstance(value, str) or not value or candidate.get(key) != value:
            raise ValueError(f'Несовместимый {key}')
    paired, behavior = [], []
    for report in (baseline, candidate):
        if report.get('status') != 'complete' or not isinstance(report.get('model'), dict) or not report['model'].get('kind'):
            raise ValueError('Отчёт не завершён или не указана модель')
        model = report['model']
        if model['kind'] == 'base':
            if not model.get('name') or not isinstance(model.get('revision'), str) or len(model['revision']) != 40:
                raise ValueError('База не закреплена')
        elif model['kind'] == 'adapter':
            if any(not model.get(k) for k in ('name', 'version', 'run_id', 'uri')):
                raise ValueError('Адаптер не закреплён')
        elif model['kind'] != 'fixture':
            raise ValueError('Неизвестный тип модели')
        paired.append(_validate_scores(report, 50))
        if len(report['slices']) < 3:
            raise ValueError('Нужны минимум три среза')
        behavior.append(_validate_scores(report.get('behavioral'), 3))
    for left, right in (paired, behavior):
        if left.keys() != right.keys() or any(set(left[k]['slices']) != set(right[k]['slices']) for k in left):
            raise ValueError('Наборы id или принадлежность срезам различаются')
    if (not isinstance(critical_slices, list) or not critical_slices
            or any(not isinstance(s, str) or s not in baseline['slices'] for s in critical_slices)):
        raise ValueError('Не заданы существующие критические срезы')
    for metric in ('accuracy', 'format_rate'):
        if candidate['overall'][metric] < baseline['overall'][metric]:
            raise ValueError(f'Регрессия общей {metric}')
    for name in critical_slices:
        if candidate['slices'][name]['accuracy'] < baseline['slices'][name]['accuracy']:
            raise ValueError(f'Регрессия критического среза {name}')
    if not all(i['correct'] for i in behavior[1].values()):
        raise ValueError('Не все behavioral-кейсы пройдены')
    return dict(passed=True, critical_slices=critical_slices,
                protocol_sha256=candidate['protocol_sha256'], model=candidate['model'])
