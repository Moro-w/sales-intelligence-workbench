"""Local feedback from the existing validators, not from acceptance answer files."""
from .contracts import SECTIONS, MARKERS, exact_evidence, evidence_sentence, validate_board
from app.text_storage import TextError


def board_feedback(payload, units):
    if not isinstance(payload, dict) or not isinstance(payload.get('sections'), dict):
        return [{'code': 'INVALID_OUTPUT'}]
    by_id = {u['id']: u for u in units}
    errors = []
    for section, items in payload['sections'].items():
        if not isinstance(items, list): continue
        for number, item in enumerate(items):
            candidate = {'sections': {s: [] for s in SECTIONS}}
            candidate['sections'][section] = [item]
            try:
                validate_board(candidate, units)
            except TextError as exc:
                issue = {'section': section, 'item': number, 'code': exc.code}
                if exc.code == 'FIDELITY_QUALIFIERS':
                    required = {word: 0 for word in MARKERS}
                    for raw in item['evidence']:
                        ref = exact_evidence(raw, by_id)
                        contexts = [ref['quote'], evidence_sentence(ref, by_id[ref['unit_id']])]
                        if section in ('预算与时间', '已达成事项'):
                            contexts.append(by_id[ref['unit_id']]['text'])
                        for context in contexts:
                            for word in MARKERS: required[word] = max(required[word], context.count(word))
                    issue['missing_markers'] = {word: {'required_count': n, 'actual_count': item['text'].count(word)}
                                                for word,n in required.items() if item['text'].count(word) < n}
                errors.append(issue)
    return errors
