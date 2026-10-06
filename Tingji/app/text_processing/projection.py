"""Legacy V2 extractive projection, retained only for historical regression tests.
Not used by the V3 worker; exact evidence alone did not establish business quality.
"""
import re
from .contracts import (SECTIONS, object_keys, exact_evidence, evidence_sentence,
                        validate_board, fail)


def validate_projection(payload, units, required_units=None):
    object_keys(payload, ('sections',))
    object_keys(payload['sections'], SECTIONS)
    by_id = {u['id']: u for u in units}
    expanded = {'sections': {s: [] for s in SECTIONS}}
    for section, items in payload['sections'].items():
        if not isinstance(items, list) or len(items) > 200: fail()  # whole-meeting capacity; fidelity unchanged
        for item in items:
            object_keys(item, ('kind', 'relation', 'evidence'))
            relation = item['relation']
            if relation not in ('none', 'correction', 'supplement'): fail()
            refs = item['evidence']
            if not isinstance(refs, list) or not 1 <= len(refs) <= 12: fail('INVALID_SOURCE', '缺少有效来源。')
            normalized = []
            for ref in refs:
                if isinstance(ref, dict) and set(ref) == {'unit_id'}:
                    unit = by_id.get(ref['unit_id']) if isinstance(ref['unit_id'], str) else None
                    if unit is None: fail('INVALID_SOURCE', '选择了不存在的原文单元。')
                    normalized.append({'unit_id':unit['id'], 'quote':unit['text'].strip(), 'occurrence':0})
                else:
                    normalized.append(ref)  # strict quoted projection compatibility; validated below
            verified = [exact_evidence(r, by_id) for r in normalized]
            verified.sort(key=lambda r: r['start_utf16'])
            if relation != 'none' and len(verified) < 2: fail('INVALID_RELATION', '更正或补充需要多处原话。')
            if relation == 'correction' and not re.search(r'更正|纠正|撤回|改成|改为|不是.{0,40}是|收回|补充|限定|没说.{0,40}我说|准确点|准确说|撤掉|口径改', by_id[verified[-1]['unit_id']]['text']):
                fail('INVALID_RELATION', '更正关系缺少后文明确依据。')
            converted, texts = [], []
            for ref in verified:
                unit = by_id[ref['unit_id']]
                quote = unit['text'].strip() if section in ('预算与时间', '已达成事项') else evidence_sentence(ref, unit).strip()
                # Expansion is deterministic, including surrounding negation; never fuzzy matching.
                candidates = [m.start() for m in re.finditer(re.escape(quote), unit['text'])]
                start = len(unit['text'].encode('utf-16-le')[:(ref['start_utf16']-unit['start_utf16'])*2].decode('utf-16-le'))
                occurrence = next((n for n,p in enumerate(candidates) if p <= start and p+len(quote) >= start+len(ref['quote'])), None)
                if occurrence is None: fail('INVALID_SOURCE', '来源展开校验失败。')
                newref = {'unit_id':unit['id'], 'quote':quote, 'occurrence':occurrence}
                if newref in converted: continue
                converted.append(newref)
                first = min((u for u in units if u['line']==unit['line']),key=lambda u:u['start_utf16'])
                match = re.match(r'^([^：\n]{1,60})：', first['text'])
                speaker = re.sub(r'[（(]\d{1,2}:\d{2}[）)]','',match.group(1)) if match else ''
                texts.append((speaker+'：' if speaker and not quote.startswith(speaker+'：') else '')+quote)
            prefix = {'none':'', 'supplement':'多处原话共同说明（限制同时保留）：\n', 'correction':'前后更正：前文口径已被后文修正，以最后一处更正为准。\n'}[relation]
            text = prefix+'\n\n'.join(texts)
            expanded['sections'][section].append({'text':text,'kind':item['kind'],'evidence':converted})
    # Keep the existing fidelity/attribution/commitment checks; do not weaken them to accept a run.
    return validate_board(expanded, units, required_units)
