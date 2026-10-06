"""V3 offline contracts on new engineering strings, not the independent answer checklist."""
import copy
import json
import pytest
from app import storage, text_storage as store
from app.text_processing.contracts import plan, SECTIONS, utf16, fingerprint
from app.text_processing.semantic import (anchors, validate_facts, validate_clean_candidate, validate_review,
                                          CLEAN_CHECKS, BOARD_CHECKS, GLOBAL_CHECKS, quantities)
from app.text_processing.jobs import Jobs, bound_review
from app.text_processing.semantic_prompts import messages
from test.test_text_processing import ScriptedModel, wait

RAW='甲：同意准备演示，未同意采购。\n甲：测试意向仅限虚构资料。\n乙：增加限制，审计检查通过才安排。\n财务：初估30万。\n财务：预算更正为18万，未批准。\n'


def setup_source(raw=RAW):
    units=plan(raw)['units'];a=anchors(units)
    def ref(text):return next(x['id'] for x in a if text in x['quote'])
    return units,a,ref


def fact(fid,text,refs,topic='agreement',kind='agreed',speaker='甲',conditions=None,history=None):
    return dict(id=fid,topic=topic,speaker=speaker,text=text,kind=kind,evidence=refs,
                conditions=conditions or [],history=history or [])


def document(units,items):
    from app.text_processing.semantic import TOPICS
    sections={s:[] for s in SECTIONS};a={v['id']:v for v in anchors(units)}
    for item in items:sections[TOPICS[item['topic']][0]].append(item)
    coverage=[]
    for u in units:
        if not u['text'].strip():continue
        hits=[]
        for item in items:
            refs=item['evidence']+[r for c in item['conditions'] for r in c['evidence']]+[r for h in item['history'] for key in ('evidence','correction') for r in h[key]]
            if any(a[r if isinstance(r,str) else r['anchor_id']]['unit_id']==u['id'] for r in refs):hits.append(item['id'])
        coverage.append({'unit_id':u['id'],'facts':hits,'omitted':'' if hits else '工程测试中不纳入本信息点，仍需全文审查判断'})
    return {'sections':sections,'coverage':coverage}


def test_positive_and_negative_scope_not_whole_paragraph():
    units,a,r=setup_source()
    items=[fact('yes','同意准备演示',[r('同意准备')]),fact('no','未同意采购',[r('未同意采购')],kind='declined')]
    out=validate_facts(document(units,items),units)['已达成事项']
    assert [i['kind'] for i in out]==['agreed','declined']
    assert len(out[0]['evidence'][0]['quote']) < len(units[0]['text'])
    assert '未同意采购' in out[0]['evidence'][0]['context_quote']
    assert out[0]['text']=='同意准备演示'  # context is not stuffed back into the summary


def test_compound_sentence_can_split_positive_scope_and_refusal_without_punctuation():
    units,a,r=setup_source('甲：同意准备演示但未同意采购。')
    def select(quote):return {'anchor_id':a[0]['id'],'quote':quote,'occurrence':0}
    items=[fact('yes','同意准备演示',[select('同意准备演示')]),
           fact('no','未同意采购',[select('未同意采购')],kind='declined')]
    out=validate_facts(document(units,items),units)['已达成事项']
    assert out[0]['kind']=='agreed' and out[1]['kind']=='declined'
    assert out[0]['evidence'][0]['quote']=='同意准备演示'
    bad=fact('bad','同意采购',[select('同意采购')])
    with pytest.raises(store.TextError) as e:validate_facts(document(units,[bad]),units)
    assert e.value.code=='INVALID_COMMITMENT'


def test_conditions_have_own_visible_text_and_multiple_precise_sources():
    units,a,r=setup_source()
    item=fact('trial','有意评估测试',[r('测试意向')],kind='tentative',conditions=[
        {'text':'仅用虚构资料','evidence':[r('测试意向')]},
        {'text':'审计检查通过才安排','evidence':[r('审计检查')]}])
    out=validate_facts(document(units,[item]),units)['已达成事项'][0]
    assert len(out['conditions'])==2
    assert {v['line'] for v in out['evidence']}=={2,3}
    assert {v['role'] for v in out['evidence']}=={'当前结论','限制条件'}


def current_budget():
    units,a,r=setup_source()
    item=fact('budget','当前初估18万，未批准',[r('更正为18'),r('未批准')],topic='budget',kind='tentative',speaker='财务',
              history=[{'text':'旧估30万','evidence':[r('初估30')],'correction':[r('更正为18')]}])
    return units,r,item


def test_same_sentence_different_speakers_has_distinct_stable_field_ids():
    from app.text_processing.contracts import assemble
    raw='甲：可以考虑测试。\n乙：可以考虑测试。'
    units,a,r=setup_source(raw)
    items=[fact('one','可以考虑测试',[a[0]['id']],kind='tentative',speaker='甲'),
           fact('two','可以考虑测试',[a[1]['id']],kind='tentative',speaker='乙')]
    board=validate_facts(document(units,items),units);p=plan(raw)
    clean=[[{'id':u['id'],'text':u['text']} for u in c] for c in p['chunks']]
    result=assemble(p,clean,[board],'a'*64);rows=result['sections']['已达成事项']
    assert len(rows)==2 and rows[0]['id']!=rows[1]['id']
    assert [f['evidence'][0]['line'] for f in rows]==[1,2]
    assert assemble(p,clean,[board],'a'*64)==result


def test_current_budget_separate_from_history_and_old_not_parallel():
    units,r,item=current_budget()
    out=validate_facts(document(units,[item]),units)['预算与时间'][0]
    assert '30' not in out['text'] and '30' in out['history'][0]['text']
    assert any(v['role']=='历史说法' for v in out['evidence'])
    old=fact('old','初估30万',[r('初估30')],topic='budget',kind='tentative',speaker='财务')
    with pytest.raises(store.TextError) as e:validate_facts(document(units,[item,old]),units)
    assert e.value.code=='OBSOLETE_CURRENT_FACT'


@pytest.mark.parametrize('later',['请保留更正过程。','我补充审计条件。','准确点，这是参考材料。'])
def test_no_correction_from_meta_discussion_or_supplement(later):
    units,a,r=setup_source('甲：原计划测试。\n甲：'+later)
    item=fact('x',later,[a[-1]['id']],kind='tentative',history=[{'text':'原计划测试','evidence':[a[0]['id']],'correction':[a[-1]['id']]}])
    with pytest.raises(store.TextError) as e:validate_facts(document(units,[item]),units)
    assert e.value.code=='INVALID_RELATION'


def test_unicode_repeated_phrase_and_original_timestamp_exact():
    raw='甲（03:12）：🙂可以考虑测试。\n乙：可以考虑测试。\n'
    units,a,r=setup_source(raw)
    assert a[0]['speaker']=='甲（03:12）'
    assert a[1]['speaker']=='乙'
    for ref in a:
        assert raw.encode('utf-16-le')[ref['start_utf16']*2:ref['end_utf16']*2].decode('utf-16-le')==ref['quote']
    assert a[1]['start_utf16']==utf16(raw.splitlines(keepends=True)[0])


@pytest.mark.parametrize('mutation,code',[
    (lambda p:p['sections']['已达成事项'][0].update(text='x'*121),'INVALID_OUTPUT'),
    (lambda p:p['sections']['已达成事项'][0].update(speaker='猜测的人'),'INVALID_ATTRIBUTION'),
    (lambda p:p['sections']['已达成事项'][0].update(evidence=['a999999']),'INVALID_SOURCE'),
    (lambda p:p['sections']['已达成事项'][0].update(topic='organization'),'FACT_SECTION'),
    (lambda p:p.update(coverage=[]),'COVERAGE_ERROR'),
])
def test_invalid_fact_contracts(mutation,code):
    units,a,r=setup_source();p=document(units,[fact('x','同意准备演示',[r('同意准备')])]);mutation(p)
    with pytest.raises(store.TextError) as e:validate_facts(p,units)
    assert e.value.code==code


@pytest.mark.parametrize('raw',['甲：可以考虑测试。','甲：尚未同意采购。','甲：这个需求只是意向。'])
def test_clear_intent_or_refusal_cannot_be_upgraded_to_agreement(raw):
    units,a,r=setup_source(raw)
    item=fact('bad','明确同意测试',[a[0]['id']])
    with pytest.raises(store.TextError) as e:validate_facts(document(units,[item]),units)
    assert e.value.code=='INVALID_COMMITMENT'


def test_negative_assertion_cannot_be_stripped_in_concise_summary():
    units,a,r=setup_source('供应商：不支持批量导出。')
    item=fact('x','支持批量导出',[a[0]['id']],topic='capability',kind='sales_claim',speaker='供应商')
    with pytest.raises(store.TextError) as e:validate_facts(document(units,[item]),units)
    assert e.value.code=='FIDELITY_QUALIFIERS'


def test_polish_can_rephrase_qualifiers_and_remove_mechanical_repetition():
    raw='甲：嗯，预算大概20万，20万，领导还没批，审批过了才考虑测试。'
    units=plan(raw)['chunks'][0]
    p={'rows':[{'id':units[0]['id'],'text':'甲：预算约20万元，尚未获批；审批通过后才考虑测试。'}]}
    assert validate_clean_candidate(p,units)[0]['text']==p['rows'][0]['text']
    assert quantities('20万')!=quantities('20元')
    p['rows'][0]['text']=p['rows'][0]['text'].replace('20万元','20元')
    with pytest.raises(store.TextError) as e:validate_clean_candidate(p,units)
    assert e.value.code=='FIDELITY_NUMBERS'


def verdict(ids,board=False,failed=None):
    keys=BOARD_CHECKS if board else CLEAN_CHECKS
    value={'checks':[{'id':i,**{k:True for k in keys},'issues':[]} for i in ids],
           'global':{**{k:True for k in (GLOBAL_CHECKS if board else ('full_coverage',))},'issues':[]}}
    if failed:value['checks'][0][failed]=False;value['checks'][0]['issues']=['工程桩指出待修问题']
    return value


@pytest.mark.parametrize('criterion',CLEAN_CHECKS)
def test_clean_review_each_requirement_is_mandatory(criterion):
    with pytest.raises(store.TextError) as e:validate_review(verdict(['u'],failed=criterion),['u'])
    assert e.value.code=='SEMANTIC_REVIEW_REJECTED'


@pytest.mark.parametrize('criterion',BOARD_CHECKS)
def test_board_review_each_requirement_is_mandatory(criterion):
    with pytest.raises(store.TextError) as e:validate_review(verdict(['f'],True,criterion),['f'],board=True)
    assert e.value.code=='SEMANTIC_REVIEW_REJECTED'


@pytest.mark.parametrize('criterion',GLOBAL_CHECKS)
def test_global_review_includes_links_and_overall_usability(criterion):
    value=verdict(['f'],True);value['global'][criterion]=False
    with pytest.raises(store.TextError):validate_review(value,['f'],board=True)


def test_review_cannot_be_reused_for_a_different_candidate_or_incomplete_ids():
    with pytest.raises(store.TextError) as e:
        bound_review({'candidate_sha256':fingerprint('old'),'review':verdict(['u'])},'new',['u'])
    assert e.value.code=='CHECKPOINT_CORRUPT'
    with pytest.raises(store.TextError):validate_review(verdict(['u']),['u','missing'])
    p=verdict(['u']);p['checks'][0]['meaning']='true'
    with pytest.raises(store.TextError):validate_review(p,['u'])


def test_review_timeout_reuses_paid_candidate_and_preserves_original(tmp_path):
    storage.set_data_dir(tmp_path);mid='c'*32;raw='甲：预算20万未批准。'
    store.import_markdown('user-a',mid,'demo.md',raw.encode(),'工程例')
    model=ScriptedModel(fail_at=('auditing_clean',0));jobs=Jobs(model)
    jobs.start('user-a',mid);wait(jobs)
    assert jobs.state('user-a',mid)['completed_clean']==0
    assert jobs.view('user-a',mid)['result'] is None
    jobs.shutdown();jobs=Jobs(model);jobs.start('user-a',mid);wait(jobs)
    assert jobs.state('user-a',mid)['state']=='succeeded'
    assert model.calls.count(('cleaning',0))==1
    assert model.calls.count(('auditing_clean',0))==2
    d=store.meeting_dir('user-a',mid)
    assert (d/'original.md').read_bytes()==raw.encode()
    result=(d/'result.json').read_bytes();calls=list(model.calls);jobs.shutdown();jobs=Jobs(model)
    assert jobs.view('user-a',mid)['result']['processing_protocol']=='atomic-facts-v3'
    jobs.start('user-a',mid);assert model.calls==calls and (d/'result.json').read_bytes()==result
    jobs.shutdown()


def test_explicit_review_rejection_archives_then_retry_generates_new_candidate(tmp_path):
    class RejectOnce(ScriptedModel):
        rejected=False
        def call(self,prompt,*args):
            value=super().call(prompt,*args)
            if args[-2]=='auditing_clean' and not self.rejected:
                self.rejected=True;value['checks'][0]['appropriate_editing']=False
                value['checks'][0]['issues']=['有冗余但仅换行，需真正整理。']
            return value
    storage.set_data_dir(tmp_path);mid='d'*32
    store.import_markdown('user-a',mid,'demo.md','甲：预算20万未批准。'.encode(),'工程例')
    model=RejectOnce();jobs=Jobs(model);jobs.start('user-a',mid);wait(jobs)
    assert jobs.state('user-a',mid)['error']['code']=='SEMANTIC_REVIEW_REJECTED'
    assert jobs.state('user-a',mid)['completed_clean']==0
    assert list((store.meeting_dir('user-a',mid)/'rejected').glob('cleaning-*.json'))
    jobs.start('user-a',mid);wait(jobs)
    assert jobs.state('user-a',mid)['state']=='succeeded'
    assert model.calls.count(('cleaning',0))==2
    jobs.shutdown()


def test_board_rejection_keeps_accepted_clean_and_regenerates_only_board(tmp_path):
    class RejectBoardOnce(ScriptedModel):
        rejected=False
        def call(self,prompt,*args):
            value=super().call(prompt,*args)
            if args[-2]=='auditing_board' and not self.rejected:
                self.rejected=True;value['global']['cross_source_links']=False
                value['global']['issues']=['补充条件未归入相应信息点。']
            return value
    storage.set_data_dir(tmp_path);mid='e'*32
    store.import_markdown('user-a',mid,'demo.md','甲：预算20万未批准。'.encode(),'工程例')
    model=RejectBoardOnce();jobs=Jobs(model);jobs.start('user-a',mid);wait(jobs)
    state=jobs.state('user-a',mid)
    assert state['state']=='failed' and state['completed_clean']==1 and state['completed_extract']==0
    assert state['error']['issues']==['补充条件未归入相应信息点。']
    assert jobs.view('user-a',mid)['result'] is None
    jobs.start('user-a',mid);wait(jobs)
    assert jobs.state('user-a',mid)['state']=='succeeded'
    assert model.calls.count(('cleaning',0))==1 and model.calls.count(('auditing_clean',0))==1
    assert model.calls.count(('extracting',0))==2 and model.calls.count(('auditing_board',0))==2
    jobs.shutdown()


def test_no_forced_change_for_already_clear_source_but_negative_loss_is_blocked():
    units=plan('甲：预算20万，未批准。')['chunks'][0]
    p={'rows':[{'id':units[0]['id'],'text':units[0]['text']}]}
    assert validate_clean_candidate(p,units)
    p['rows'][0]['text']='甲：预算20万，已批准。'
    with pytest.raises(store.TextError) as e:validate_clean_candidate(p,units)
    assert e.value.code=='FIDELITY_QUALIFIERS'


def test_selector_quote_round_trip_does_not_expand_back_to_mixed_scope():
    from app.text_processing.jobs import board_candidate
    units,a,r=setup_source('甲：同意准备演示但未同意采购。')
    selector={'anchor_id':a[0]['id'],'quote':'同意准备演示','occurrence':0}
    raw=document(units,[fact('yes','同意准备演示',[selector])])
    validated=validate_facts(raw,units)
    combined=board_candidate([validated],[raw])
    again=validate_facts(combined,units)
    assert again['已达成事项'][0]['evidence'][0]['quote']=='同意准备演示'
    assert combined['sections']['已达成事项'][0]['evidence']==[selector]


def test_validation_feedback_identifies_rejected_field_without_rewriting_candidate():
    units,a,r=setup_source('甲：可以考虑测试。')
    candidate=document(units,[fact('x','同意测试',[a[0]['id']])]);before=copy.deepcopy(candidate)
    details=Jobs._validation_details(candidate,lambda p:validate_facts(p,units))
    assert details[0]['fact_id']=='x' and details[0]['code']=='INVALID_COMMITMENT'
    assert candidate==before
    assert set(details[0])=={'fact_id','section','code','message'}


def test_only_sources_and_candidates_in_review_no_external_checklist(tmp_path):
    units,a,r=setup_source()
    prompt=messages('auditing_board',source=a,candidate={'sections':{}},ids=[])
    body=json.loads(prompt[1]['content'])
    assert set(body)=={'protocol','candidate','review_schema','ids','source'}
    assert body['source']['columns']==['id','unit_id','speaker','text']
    assert all(len(row)==4 for row in body['source']['rows'])


@pytest.mark.parametrize('quote,ok',[('我今天把邀请发你微信，',True),('【客户】空表头可以，',True),('我明天下班前发你，',True),
    ('这个不可以，',False),('我今天不发，',False),('我们可以考虑一下。',False),('周五上午十点半呢，',False)])
def test_spoken_commitments_are_recognised_without_accepting_negation(quote,ok):
    from app.text_processing.board_first import source_anchors,supported_numbers,BOARD_TOPICS
    raw='说话人1：'+quote+'\n'
    units=plan(raw)['units'];refs=[a['id'] for a in source_anchors(units)]
    item={'id':'x','topic':'agreement','speaker':'说话人1','text':'约定事项。','kind':'agreed','evidence':refs,'conditions':[],'history':[]}
    payload={'sections':{s:[] for s in SECTIONS},'coverage':[]};payload['sections']['已达成事项']=[item]
    if ok:validate_facts(payload,units,[],number_check=supported_numbers,anchor_builder=source_anchors,topic_map=BOARD_TOPICS)
    else:
        with pytest.raises(store.TextError) as e:validate_facts(payload,units,[],number_check=supported_numbers,anchor_builder=source_anchors,topic_map=BOARD_TOPICS)
        assert e.value.code=='INVALID_COMMITMENT'
