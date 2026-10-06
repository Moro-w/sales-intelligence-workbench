"""Full coverage planning, conservative fidelity checks and exact evidence spans."""
import hashlib
import json
import re
from collections import Counter
from app.text_storage import TextError

VERSION = 1
RULESET_VERSION = 4  # Increment for planning/validation changes, independently of output schema.
SECTIONS = ('会议概况', '客户需求与产品适配', '预算与时间', '决策与采购流程', '已达成事项', '待确认问题', '后续行动')
KINDS = ('reported', 'sales_claim', 'tentative', 'conflict', 'agreed', 'ai_suggestion')
MARKERS = ('不', '未', '没', '无', '可能', '大概', '约', '预计', '估计', '如果', '只有', '除非',
           '前提', '取决于', '待确认', '待验证', '尚需', '再考虑', '仅', '暂', '至少', '最多', '应该', '才', '拟', '只是', '初步', '倾向', '意向', '考虑', '建议')
NUMBERS = re.compile(r'\d+(?:[.,]\d+)*|[零〇一二两三四五六七八九十百千万亿]+(?:元|万|亿|天|周|月|年|个|份|人|成)')


def fail(code='INVALID_OUTPUT', message='模型结果结构不完整，未保存为成功。'):
    raise TextError(code, message, 422)


def utf16(text):
    return len(text.encode('utf-16-le')) // 2


def plan(source):
    units, offset = [], 0
    for line_no, line in enumerate(source.splitlines(keepends=True), 1):
        while line:
            cut = min(len(line), 700)
            if cut < len(line):
                breaks = [m.end() for m in re.finditer(r'[。！？；，\s]', line[:cut]) if m.end() >= 350]
                if breaks: cut = breaks[-1]
            part, line = line[:cut], line[cut:]
            units.append({'id': f'u{len(units):06d}', 'text': part, 'line': line_no,
                          'start_utf16': offset, 'end_utf16': offset + utf16(part)})
            offset += utf16(part)
    if ''.join(u['text'] for u in units) != source or offset != utf16(source):
        fail('COVERAGE_ERROR', '原文切分覆盖检查失败，已停止处理。')
    chunks, current, size = [], [], 0
    for unit in units:
        if not unit['text'].strip(): continue  # blanks are retained deterministically in the final transcript
        if current and (size + len(unit['text']) > 2200 or len(current) >= 12):
            chunks.append(current); current, size = [], 0
        current.append(unit); size += len(unit['text'])
    if current: chunks.append(current)
    return {'version': VERSION, 'units': units, 'chunks': chunks}


def extraction_batches(chunks):
    """Shared by the worker and offline budget planner; never truncates the source."""
    units = [unit for chunk in chunks for unit in chunk]
    return [units] if units and sum(len(u['text']) for u in units) <= 20000 else chunks


def object_keys(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys): fail()


def text_value(value, limit=5000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit: fail()
    return value


def preserve_sensitive(original, cleaned, *, numbers=True):
    # This is a conservative rejection guard, NOT a semantic correctness proof.
    if numbers and Counter(NUMBERS.findall(original)) != Counter(NUMBERS.findall(cleaned)):
        fail('FIDELITY_NUMBERS', '整理结果改变了数字或数量，已停止；可重试当前片段。')
    if any(cleaned.count(word) < original.count(word) for word in MARKERS):
        fail('FIDELITY_QUALIFIERS', '结果可能遗漏否定、条件或不确定表述，已停止；可重试当前片段。')


def validate_clean(payload, units):
    object_keys(payload, ('rows',))
    rows = payload['rows']
    if not isinstance(rows, list) or len(rows) != len(units):
        fail('COVERAGE_ERROR', '整理结果漏段或重复，未保存为成功。')
    result = []
    for row, unit in zip(rows, units):
        object_keys(row, ('id', 'text'))
        if row['id'] != unit['id']: fail('COVERAGE_ERROR', '整理结果顺序或来源不一致。')
        cleaned = text_value(row['text'], 2500)
        original = unit['text'].strip()
        preserve_sensitive(original, cleaned)
        prefix = re.match(r'^(?:#{1,6}\s*)?[^\n:：]{1,45}[:：]', original)
        if prefix and not cleaned.startswith(prefix.group()):
            fail('FIDELITY_SPEAKER', '说话人或发言标记发生变化，已停止。')
        if len(original) > 80 and len(cleaned) < len(original) * .55:
            fail('FIDELITY_LENGTH', '整理稿异常缩短，可能被写成摘要，已停止。')
        if len(cleaned) > max(len(original) * 1.4, len(original) + 30):
            fail('FIDELITY_EXPANSION', '整理稿异常扩写，可能新增信息，已停止。')
        result.append({'id': unit['id'], 'text': cleaned})
    return result


def exact_evidence(ref, units):
    object_keys(ref, ('unit_id', 'quote', 'occurrence'))
    unit = units.get(ref['unit_id']) if isinstance(ref['unit_id'], str) else None
    quote = text_value(ref['quote'], 2000)
    occurrence = ref['occurrence']
    if not unit or type(occurrence) is not int or occurrence < 0: fail('INVALID_SOURCE', '出处不属于本片段。')
    positions, start = [], 0
    while True:
        pos = unit['text'].find(quote, start)
        if pos < 0: break
        positions.append(pos); start = pos + 1
    if occurrence >= len(positions): fail('INVALID_SOURCE', '模型引文与原始稿不一致，未生成错误定位。')
    pos = positions[occurrence]
    a = unit['start_utf16'] + utf16(unit['text'][:pos])
    return {'unit_id': unit['id'], 'line': unit['line'], 'quote': quote,
            'start_utf16': a, 'end_utf16': a + utf16(quote)}


def evidence_sentence(ref, unit):
    # A quote of “支持API” inside “不支持API” must not hide its surrounding negation.
    raw = unit['text']
    pos = len(raw.encode('utf-16-le')[:(ref['start_utf16'] - unit['start_utf16']) * 2].decode('utf-16-le'))
    stop = pos + len(ref['quote'])
    punctuation = '。！？!?\n'
    left = max(raw.rfind(char, 0, pos) for char in punctuation) + 1
    if raw[stop - 1] not in punctuation:
        ends = [raw.find(char, stop) for char in punctuation]
        stop = min((end + 1 for end in ends if end >= 0), default=len(raw))
    return raw[left:stop]


def validate_board(payload, allowed_units, required_units=None):
    object_keys(payload, ('sections',))
    sections = payload['sections']
    object_keys(sections, SECTIONS)
    by_id = {u['id']: u for u in allowed_units}
    result = {s: [] for s in SECTIONS}
    for section in SECTIONS:
        items = sections[section]
        if not isinstance(items, list) or len(items) > 200: fail()  # full-source classification, still bounded
        for item in items:
            object_keys(item, ('text', 'kind', 'evidence'))
            text = text_value(item['text'], 2500)
            if item['kind'] not in KINDS: fail()
            refs = item['evidence']
            if not isinstance(refs, list) or len(refs) > 12: fail()
            if not refs and item['kind'] != 'ai_suggestion':
                fail('INVALID_SOURCE', '会议事实缺少原文依据，未保存为成功。')
            evidence = [exact_evidence(ref, by_id) for ref in refs]
            # Qualifiers in quoted evidence must remain visible in the field itself.
            # Full source context is always available alongside the field.
            for ref in evidence:
                preserve_sensitive(ref['quote'], text, numbers=False)
                preserve_sensitive(evidence_sentence(ref, by_id[ref['unit_id']]), text, numbers=False)
            original_numbers = Counter(NUMBERS.findall(' '.join(r['quote'] for r in evidence)))
            if item['kind'] != 'ai_suggestion' and any(n not in original_numbers for n in NUMBERS.findall(text)):
                fail('FIDELITY_NUMBERS', '看板出现无对应引文的数字，已停止。')
            if section in ('预算与时间', '已达成事项'):
                for ref in refs:
                    preserve_sensitive(by_id[ref['unit_id']]['text'], text, numbers=False)
            if item['kind'] == 'agreed' and any(any(word in r['quote'] for word in ('可能', '未', '没', '如果', '只有', '大概', '考虑', '建议', '初步')) for r in evidence):
                fail('INVALID_COMMITMENT', '有条件或未确定的表述不能标为无条件明确约定。')
            if section == '客户需求与产品适配' and item['kind'] == 'reported' and any(
                re.match(r'^(?:#{1,6}\s*)?(?:销售|售前|供应商|厂商)[^:：\n]{0,30}[:：]', by_id[r['unit_id']]['text'])
                and re.search(r'能力|支持|兼容|实现|上线|集成|接口|部署|性能|满足', by_id[r['unit_id']]['text']) for r in refs):
                fail('INVALID_ATTRIBUTION', '销售自述的产品能力需保留归属，不可包装为已核实事实。')
            if section == '已达成事项' and item['kind'] not in ('agreed', 'tentative', 'conflict'):
                fail('INVALID_COMMITMENT', '未明确同意的信息不能归为已达成事项。')
            result[section].append({'text': text, 'kind': item['kind'], 'evidence': evidence,
                                    'human_verified': False})
    if required_units is not None:
        cited = {ref['unit_id'] for items in result.values() for item in items for ref in item['evidence']}
        business = re.compile(r'预算|采购|审批|试用|需求|上线|接口|痛点|权限|待确认|待验证|负责人|承诺|同意|不支持')
        if any(business.search(u['text']) and u['id'] not in cited for u in required_units):
            fail('INCOMPLETE_EXTRACTION', '看板遗漏含业务信息的原文单元，已停止；成功整理稿仍保留。')
    return result


def assemble(planning, clean_parts, board_parts, source_sha):
    clean_map = {row['id']: row['text'] for part in clean_parts for row in part}
    expected = {u['id'] for u in planning['units'] if u['text'].strip()}
    if set(clean_map) != expected: fail('COVERAGE_ERROR', '整理稿未覆盖全部原文。')
    transcript = '\n'.join(clean_map[u['id']] if u['text'].strip() else '' for u in planning['units'])
    sections = {s: [] for s in SECTIONS}
    for section in SECTIONS:
        seen = {}
        for part in board_parts:
            for item in part[section]:
                key = (item['kind'], item['text'])
                if item.get('protocol') == 'atomic-facts-v3':
                    key += (fingerprint([item['speaker'], item['topic'], item['conditions'], item['history'], item['evidence']]),)
                if key not in seen:
                    value = dict(item, evidence=list(item['evidence']))
                    value['id'] = 'f-' + hashlib.sha256((section + '\0' + '\0'.join(key)).encode()).hexdigest()[:20]
                    seen[key] = value; sections[section].append(value)
                else:
                    for ref in item['evidence']:
                        if ref not in seen[key]['evidence']: seen[key]['evidence'].append(ref)
    # No model reducer: do not lose cross-chunk disagreements, conditional statements or tail facts.
    return {'schema_version': VERSION, 'source_sha256': source_sha, 'processed': transcript,
            'clean_rows': [dict(u, cleaned=clean_map.get(u['id'], u['text'])) for u in planning['units']],
            'sections': sections, 'human_verified': False}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
