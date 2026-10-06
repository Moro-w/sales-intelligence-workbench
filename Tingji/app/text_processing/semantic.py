"""V3: concise assertions are separate from immutable evidence and semantic review.

Deterministic checks establish structure/provenance, NOT semantic truth. A separate
bounded review request must pass before publishing; real acceptance remains required.
"""
import re
from .contracts import (SECTIONS, object_keys, text_value, fail, utf16,
                        exact_evidence, evidence_sentence, fingerprint)

PROTOCOL = 'atomic-facts-v3'
_UNIT = r'(?:万元|亿元|万条|万次|元|万|亿|人|个|份|天|周|个月|月|年|席|次|条|家|点|小时|%|％)'
_QUANTITY = re.compile(r'(?:\d+(?:[,，]\d{3})*(?:\.\d+)?|[零〇一二两三四五六七八九十百千]{2,}|[零〇一二两三四五六七八九](?='+_UNIT+r'))(?:\s*'+_UNIT+r')?')


def quantities(text):
    """Conservative quantities including units: 20万 != 20元; repeated mentions may collapse."""
    values = set()
    for match in _QUANTITY.finditer(text.replace('首年', '第一年')):
        value = re.sub(r'\s|[,，]', '', match.group())
        value = re.sub(r'([万亿])元$', r'\1', value)
        values.add(value)
    return values


def scoped_qualifiers(text, refs):
    # Check only selected assertion/condition clauses, never unrelated sentences in a paragraph.
    families = (('不支持','无法支持','尚不支持'),
                ('未批准','没批准','未获批','尚未获批','还没批','未经批准'),
                ('不同意','未同意','没同意','没有同意'), ('不允许','禁止','不得'))
    source = ' '.join(r['quote'] for r in refs)
    for family in families:
        if any(word in source for word in family) and not any(word in text for word in family):
            fail('FIDELITY_QUALIFIERS', '选中信息点的否定/审批状态不可从简洁结论中消失。')
TOPICS = {
    'meeting': ('会议概况',), 'organization': ('会议概况',),
    'need': ('客户需求与产品适配',), 'capability': ('客户需求与产品适配',),
    'budget': ('预算与时间',), 'quote': ('预算与时间',), 'timing': ('预算与时间',),
    'decision': ('决策与采购流程',), 'procurement': ('决策与采购流程',),
    'agreement': ('已达成事项',), 'open_question': ('待确认问题',), 'action': ('后续行动',),
}
KINDS = ('reported', 'sales_claim', 'tentative', 'conflict', 'agreed', 'declined')
CLEAN_CHECKS = ('meaning', 'qualifiers', 'attribution', 'order', 'appropriate_editing')
BOARD_CHECKS = ('supported', 'atomic', 'classification', 'commitment_scope',
                'complete_conditions', 'current_vs_history', 'concise')
GLOBAL_CHECKS = ('full_coverage', 'cross_source_links', 'no_parallel_obsolete_claims',
                 'no_invented_facts', 'usable_not_transcript')


def speaker_prefix(text):
    # Bracketed role labels (【销售】) are real speaker tags in raw transcripts.
    match = re.match(r'^(?:#{1,6}\s*)?【[^】\n]{1,20}】', text)
    if match: return match.group(0)
    # Prefer the actual speech colon, not the colon inside a timestamp.
    match = re.match(r'^(?:#{1,6}\s*)?([^\n：]{1,60})：', text)
    if not match:
        match = re.match(r'^([^\n:]{1,45}):(?=\s|[^0-9])', text)
    return match.group(0) if match else ''


def anchors(units):
    """Exact non-empty clause spans, including complete sentence context. No fuzzy search."""
    first = {}
    for u in units:
        first.setdefault(u['line'], u)
    result = []
    for u in units:
        raw = u['text']; start = 0
        boundaries = []
        for m in re.finditer(r'[。！？!?；;，,]', raw):
            i = m.start()
            if raw[i] in ',，' and i and i+1 < len(raw) and raw[i-1].isdigit() and raw[i+1].isdigit():
                continue
            boundaries.append(m.end())
        for stop in boundaries + [len(raw)]:
            part = raw[start:stop]
            offset = start + len(part) - len(part.lstrip())
            quote = part.strip(); start = stop
            if not quote: continue
            ref = {'unit_id': u['id'], 'line': u['line'], 'quote': quote,
                   'start_utf16': u['start_utf16'] + utf16(raw[:offset]),
                   'end_utf16': u['start_utf16'] + utf16(raw[:offset+len(quote)])}
            prefix = speaker_prefix(first[u['line']]['text'])
            ref.update(id=f'a{len(result):06d}', speaker=prefix.rstrip('：:') or '未标明',
                       context_quote=evidence_sentence(ref, u))
            result.append(ref)
    return result


def wire_anchors(items):
    return [{'id': a['id'], 'unit_id': a['unit_id'], 'speaker': a['speaker'], 'text': a['quote']} for a in items]


def validate_clean_candidate(payload, units):
    object_keys(payload, ('rows',))
    rows = payload['rows']
    if not isinstance(rows, list) or len(rows) != len(units): fail('COVERAGE_ERROR', '整理稿单元不完整。')
    out = []
    for row, unit in zip(rows, units):
        object_keys(row, ('id', 'text'))
        if row['id'] != unit['id'] or not isinstance(row['text'], str): fail('COVERAGE_ERROR', '整理稿顺序错误。')
        original, cleaned = unit['text'].strip(), row['text'].strip()
        prefix = speaker_prefix(original)
        if prefix and not cleaned.startswith(prefix): fail('FIDELITY_SPEAKER', '不能变更发言人或已有时标。')
        # Repeated mentions may be deduplicated, not quantities or their written values.
        if quantities(original) != quantities(cleaned):
            fail('FIDELITY_NUMBERS', '数字或数量出现遗漏/新增，需重做该片段。')
        scoped_qualifiers(cleaned, [{'quote': original}])
        if len(cleaned) > max(len(original)*1.4, len(original)+30) or len(cleaned) > 2500:
            fail('FIDELITY_EXPANSION', '整理稿异常扩写。')
        out.append({'id': unit['id'], 'text': cleaned})
    return out


def _refs(ids, by_anchor):
    if not isinstance(ids, list) or not 1 <= len(ids) <= 32:
        fail('INVALID_SOURCE', '信息点必须包含有效来源。')
    if len({fingerprint(i) for i in ids}) != len(ids): fail('INVALID_SOURCE', '来源重复。')
    refs = []
    for selector in ids:
        if isinstance(selector, str): aid = selector
        elif isinstance(selector, dict):
            object_keys(selector, ('anchor_id','quote','occurrence')); aid = selector['anchor_id']
        else: fail('INVALID_SOURCE', '来源选择格式错误。')
        if not isinstance(aid,str) or aid not in by_anchor: fail('INVALID_SOURCE', '来源锚点不存在。')
        ref = dict(by_anchor[aid]); ref['anchor_id'] = ref.pop('id'); ref['selector'] = selector
        if isinstance(selector, dict):
            selected = exact_evidence({'unit_id':aid,'quote':selector['quote'],'occurrence':selector['occurrence']},
                                      {aid:{'id':aid,'line':ref['line'],'text':ref['quote'],'start_utf16':ref['start_utf16']}})
            pos = len(ref['quote'].encode('utf-16-le')[:(selected['start_utf16']-ref['start_utf16'])*2].decode('utf-16-le'))
            # A selected positive substring cannot hide an immediately preceding negation.
            neg = re.search(r'(?:尚未|还未|并不|没有|不得|不能|不|未|没)$',ref['quote'][:pos])
            if neg:
                selected['quote']=neg.group()+selected['quote'];selected['start_utf16']-=utf16(neg.group())
            ref.update(quote=selected['quote'],start_utf16=selected['start_utf16'],end_utf16=selected['end_utf16'])
        refs.append(ref)
    return sorted(refs, key=lambda r: r['start_utf16'])


def _numbers_supported(text, refs):
    available = quantities(' '.join(r['quote'] for r in refs))
    if not quantities(text).issubset(available):
        fail('FIDELITY_NUMBERS', '简洁结论含对应依据中没有的数字。')


def validate_facts(payload, units, required_units=None, *, number_check=_numbers_supported, anchor_builder=anchors, topic_map=TOPICS):
    object_keys(payload, ('sections', 'coverage'))
    object_keys(payload['sections'], SECTIONS)
    by_anchor = {a['id']: a for a in anchor_builder(units)}
    sections = {s: [] for s in SECTIONS}; facts = {}
    for section, items in payload['sections'].items():
        if not isinstance(items, list) or len(items) > 120: fail()
        for item in items:
            object_keys(item, ('id', 'topic', 'speaker', 'text', 'kind', 'evidence', 'conditions', 'history'))
            fid = text_value(item['id'], 40)
            if fid in facts: fail('DUPLICATE_FACT', '信息点ID重复。')
            if not isinstance(item['topic'], str) or item['topic'] not in topic_map or section not in topic_map[item['topic']]:
                fail('FACT_SECTION', '信息点主题与七区位置不符。')
            text = text_value(item['text'], 120)
            if item['kind'] not in KINDS: fail()
            if section == '已达成事项' and item['kind'] not in ('agreed', 'declined', 'tentative', 'conflict'):
                fail('INVALID_COMMITMENT', '须明确区分同意、未同意和条件意向。')
            evidence = _refs(item['evidence'], by_anchor)
            speakers = {r['speaker'] for r in evidence}
            # '未标明' never guesses a person; it is honest for a fact synthesised from several speakers.
            if not isinstance(item['speaker'], str) or not (item['speaker'] in speakers or item['speaker'] == '未标明'):
                fail('INVALID_ATTRIBUTION', '不能推测或更换信息点的发言归属。')
            number_check(text, evidence)
            if not isinstance(item['conditions'], list) or len(item['conditions']) > 16: fail()
            conditions = []
            for condition in item['conditions']:
                object_keys(condition, ('text', 'evidence'))
                value = text_value(condition['text'], 120); refs = _refs(condition['evidence'], by_anchor)
                number_check(value, refs)
                conditions.append({'text': value, 'evidence': refs})
            if item['kind'] == 'agreed':
                scoped_source = ' '.join(r['quote'] for r in evidence)
                if re.search(r'可以考虑|可以评估|再考虑|只是意向|暂不承诺|未承诺|没承诺|还没答应|并未同意', scoped_source):
                    fail('INVALID_COMMITMENT', '意向/考虑不能标为已明确同意；请分别选择与表达实际约定范围。')
                affirmative = re.sub(r'不同意|未同意|没同意|没有同意|未确认|没确认|不确定|未确定|不能承诺|不承诺|未接受|没有答应', '', scoped_source)
                promise = (r'同意|确认|确定|答应|接受|承诺|我来|我负责|我们负责|我会|我们会|将于|(?:^|[：:，。】])(?:好|好的|行|没问题)[，。！\s]*$'
                           # Spoken first-person commitments and explicit acceptance, e.g. 我今天把邀请发你 / 空表头可以.
                           r'|(?:^|[】：:，。])(?:好|好的|行|可以)[，。！\s]|(?<![不没])可以[，。！]|(?<![不没])可以$'
                           r'|(?:我|我们)(?:今天|明天|后天|这边|马上|回去|周[一二三四五六日天]|下周)(?:(?!不|没|别)[^，。；]){0,12}(?:发|给|准备|安排|约|过一下|回复)')
                if not re.search(promise, affirmative):
                    fail('INVALID_COMMITMENT', '明确约定必须有对应的信息点级肯定依据，不能从整段主题推断。')
                # A faithful mixed statement (同意A，没同意B) needs the refusal in its own evidence.
                if re.search(r'不同意|未同意|没有同意|没同意', text) and not re.search(r'不同意|未同意|没有同意|没同意', ' '.join(r['quote'] for r in evidence)):
                    fail('INVALID_COMMITMENT', '未同意的事项必须与肯定约定分开表达。')
            scoped_qualifiers(text + ' ' + ' '.join(c['text'] for c in conditions), evidence)
            for condition in conditions: scoped_qualifiers(condition['text'], condition['evidence'])
            if not isinstance(item['history'], list) or len(item['history']) > 12: fail()
            history = []
            for old in item['history']:
                object_keys(old, ('text', 'evidence', 'correction'))
                old_text = text_value(old['text'], 120)
                old_refs = _refs(old['evidence'], by_anchor); correction = _refs(old['correction'], by_anchor)
                number_check(old_text, old_refs)
                if max(r['end_utf16'] for r in old_refs) > min(r['start_utf16'] for r in correction):
                    fail('INVALID_RELATION', '更正必须晚于历史说法。')
                actual = ' '.join(r['context_quote'] for r in correction)
                # An earlier explicit uncertainty that is later answered is also a superseded statement.
                resolved = re.search(r'不确定|不知道|说不清|待确认|还没定|没定|要翻|先不确定', ' '.join(r['quote'] for r in old_refs))
                if not resolved and not re.search(r'更正为|改成|改为|撤回|收回|不是.{0,50}是|没说.{0,50}说的是|不看了|别按|不按|不算|去掉|作废|改一下|以.{0,12}为准', actual):
                    fail('INVALID_RELATION', '缺少明确更正，不能把补充或保留更正的讨论当成改口。')
                if not any((r['start_utf16'] < e['end_utf16'] and e['start_utf16'] < r['end_utf16']) or r['unit_id'] == e['unit_id'] for r in correction for e in evidence):
                    fail('INVALID_RELATION', '当前口径必须引用实际更正。')
                history.append({'text': old_text, 'evidence': old_refs, 'correction': correction})
            # Scope is the selected assertion, NOT every negative word in its paragraph.
            if item['kind'] == 'agreed' and re.search(r'未同意|没同意|不同意|尚未同意|未决定|没有决定', text) and not re.search(r'不同意|未同意|没有同意|没同意', ' '.join(r['quote'] for r in evidence)):
                fail('INVALID_COMMITMENT', '未同意的事项不能标明确同意。')
            field = {'fact_id': fid, 'topic': item['topic'], 'speaker': item['speaker'], 'text': text,
                     'kind': item['kind'], 'conditions': conditions, 'history': history,
                     'evidence': evidence, 'human_verified': False, 'protocol': PROTOCOL}
            facts[fid] = field; sections[section].append(field)
    if len(facts) > 240: fail('READABILITY_LIMIT', '信息点过多，须检查重复和整段搬运。')
    # Old assertions may be inspected as history, but not independently re-published as current.
    historical = [r for f in facts.values() for h in f['history'] for r in h['evidence']]
    for field in facts.values():
        for ref in field['evidence']:
            if any(ref['start_utf16'] < h['end_utf16'] and h['start_utf16'] < ref['end_utf16'] for h in historical):
                fail('OBSOLETE_CURRENT_FACT', '历史说法不能同时作为当前有效结论。')
    coverage = payload['coverage']
    target = {u['id'] for u in (required_units if required_units is not None else units) if u['text'].strip()}
    if not isinstance(coverage, list): fail('COVERAGE_ERROR', '缺少信息覆盖说明。')
    seen = set()
    for row in coverage:
        object_keys(row, ('unit_id', 'facts', 'omitted'))
        uid = row['unit_id']
        if not isinstance(uid, str) or uid not in target or uid in seen: fail('COVERAGE_ERROR', '覆盖单元重复或错误。')
        seen.add(uid)
        if not isinstance(row['facts'], list) or any(not isinstance(f, str) or f not in facts for f in row['facts']): fail()
        if row['facts']:
            if row['omitted'] != '': fail()
            for fid in row['facts']:
                f = facts[fid]
                refs = f['evidence'] + [r for c in f['conditions'] for r in c['evidence']] + [r for h in f['history'] for key in ('evidence','correction') for r in h[key]]
                if uid not in {r['unit_id'] for r in refs}: fail('COVERAGE_ERROR', '覆盖声明没有实际来源支持。')
        else: text_value(row['omitted'], 120)  # Must be checked by the independent review, not trusted as proof.
    if seen != target: fail('COVERAGE_ERROR', '存在未核查的原文单元。')
    # Keep the combined click list while retaining the role of each reference.
    for field in facts.values():
        combined = [dict(r, role='当前结论') for r in field['evidence']]
        combined += [dict(r, role='限制条件') for c in field['conditions'] for r in c['evidence']]
        combined += [dict(r, role='历史说法') for h in field['history'] for r in h['evidence']]
        combined += [dict(r, role='明确更正') for h in field['history'] for r in h['correction']]
        unique = {}
        for ref in combined: unique.setdefault((ref['start_utf16'],ref['end_utf16'],ref['role']), ref)
        field['evidence'] = list(unique.values())
    return sections


def validate_review(payload, ids, *, board=False):
    object_keys(payload, ('checks', 'global'))
    criteria = BOARD_CHECKS if board else CLEAN_CHECKS
    checks = payload['checks']
    if not isinstance(checks, list) or len(checks) != len(ids): fail('REVIEW_INCOMPLETE', '质量核查结果不完整。')
    seen = set(); rejected = False
    for check in checks:
        object_keys(check, ('id', *criteria, 'issues'))
        cid = check['id']
        if not isinstance(cid, str) or cid not in ids or cid in seen: fail('REVIEW_INCOMPLETE', '质量核查对象不完整。')
        seen.add(cid)
        if any(type(check[k]) is not bool for k in criteria): fail('REVIEW_INCOMPLETE', '核查判断须为布尔值。')
        if not isinstance(check['issues'], list) or any(not isinstance(i,str) or len(i)>500 for i in check['issues']): fail()
        rejected |= bool(check['issues']) or not all(check[k] for k in criteria)
    globals_ = GLOBAL_CHECKS if board else ('full_coverage',)
    object_keys(payload['global'], (*globals_, 'issues'))
    if any(type(payload['global'][k]) is not bool for k in globals_): fail('REVIEW_INCOMPLETE', '缺少全文核查结论。')
    issues = payload['global']['issues']
    if not isinstance(issues,list) or any(not isinstance(i,str) or len(i)>500 for i in issues): fail()
    if rejected or issues or not all(payload['global'][k] for k in globals_):
        fail('SEMANTIC_REVIEW_REJECTED', '内容或整体可用性核查未通过；已保留原因，未发布为完成。')
    return payload
