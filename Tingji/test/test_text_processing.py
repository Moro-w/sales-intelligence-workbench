"""Offline deterministic contracts; deliberately NOT a real-model acceptance."""
import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from app import storage, text_storage as store
from app.text_main import create_app
from app.text_processing.contracts import *
from app.text_processing.jobs import Jobs, lease
from app.text_processing.model import atomic_json

MID='a'*32
RAW='客户甲：预算大概20万，尚未批准，只有审批后才考虑试用😀。\n销售乙：这个能力可能支持，但仍待验证。\n'


class ScriptedModel:
    def __init__(self, fail_at=None, event=None):
        self.calls=[];self.fail_at=fail_at;self.event=event
    def status(self, *args):return {'enabled':True,'reason':None}
    def call(self, prompt, owner, source_hash, stage, number):
        self.calls.append((stage,number))
        if self.event:self.event.wait(3)
        if self.fail_at==(stage,number):
            self.fail_at=None
            raise store.TextError('MODEL_TIMEOUT','离线模拟超时',502)
        data=json.loads(prompt[1]['content'])
        if stage.startswith('auditing_'):
            from app.text_processing.semantic import CLEAN_CHECKS, BOARD_CHECKS, GLOBAL_CHECKS
            keys=BOARD_CHECKS if stage=='auditing_board' else CLEAN_CHECKS
            global_keys=GLOBAL_CHECKS if stage=='auditing_board' else ('full_coverage',)
            return {'checks':[{'id':i,**{k:True for k in keys},'issues':[]} for i in data['ids']],
                    'global':{**{k:True for k in global_keys},'issues':[]}}
        if stage in ('cleaning','clean_draft'):return {'rows':[{'id':u['id'],'text':u['text'].strip()} for u in data['target']]}
        if isinstance(data['source'],dict):
            rows=[dict(zip(data['source']['columns'],row)) for row in data['source']['rows']]
            if rows and 'anchors' in rows[0]:  # clean-based board input: raw clauses are the citable evidence
                rows=[{'id':aid,'unit_id':r['unit_id'],'speaker':r['speaker'],'text':quote} for r in rows for aid,quote in r['anchors']]
            data['source']=rows
        sections={s:[] for s in SECTIONS};coverage=[]
        for uid in data['target_unit_ids']:
            refs=[a for a in data['source'] if a['unit_id']==uid];quote=''.join(a['text'] for a in refs)
            fid='fact-'+uid
            sections['客户需求与产品适配'].append({'id':fid,'topic':'need','speaker':refs[0]['speaker'],
                'text':quote,'kind':'sales_claim' if quote.startswith('销售') else 'reported',
                'evidence':[a['id'] for a in refs],'conditions':[],'history':[]})
            coverage.append({'unit_id':uid,'facts':[fid],'omitted':''})
        return {'sections':sections,'coverage':coverage}


@pytest.fixture
def source(tmp_path):
    storage.set_data_dir(tmp_path)
    store.import_markdown('user-a',MID,'会议.md',RAW.encode(),'测试')
    return tmp_path


def wait(jobs):
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        if not jobs.active:return
        time.sleep(.01)
    raise AssertionError('offline worker did not finish')


def test_all_text_covered_including_long_line_emoji_and_empty_lines():
    raw=('首😀\r\n\n'+'超长行😀不能丢失条件。'*3000+'\n尾部未批准20万\n')
    p=plan(raw)
    assert ''.join(u['text'] for u in p['units'])==raw
    encoded=raw.encode('utf-16-le')
    for unit in p['units']:
        assert encoded[unit['start_utf16']*2:unit['end_utf16']*2].decode('utf-16-le')==unit['text']
    assert p['units'][-1]['text']=='尾部未批准20万\n'
    assert all(len(c)<=12 and sum(len(u['text']) for u in c)<=2200 for c in p['chunks'])


@pytest.mark.parametrize('change,code',[
    (lambda p:p['rows'].pop(),'COVERAGE_ERROR'),
    (lambda p:p['rows'].reverse(),'COVERAGE_ERROR'),
    (lambda p:p['rows'][0].update(text=p['rows'][0]['text']+'补写内容'*80),'FIDELITY_EXPANSION'),
    (lambda p:p['rows'][0].update(text='客户甲：预算大概30万，尚未批准，只有审批后才考虑试用😀。'),'FIDELITY_NUMBERS'),
    (lambda p:p['rows'][0].update(text='客户甲：预算大概20万，已批准，只有审批后才考虑试用😀。'),'FIDELITY_QUALIFIERS'),
    (lambda p:p['rows'][0].update(text='客户丙：预算大概20万，尚未批准，只有审批后才考虑试用😀。'),'FIDELITY_SPEAKER'),
])
def test_clean_rejects_omission_order_numbers_negation_and_speaker(change,code):
    chunk=plan(RAW)['chunks'][0]
    payload={'rows':[{'id':u['id'],'text':u['text'].strip()} for u in chunk]}
    change(payload)
    with pytest.raises(store.TextError) as error:validate_clean(payload,chunk)
    assert error.value.code==code


def test_repeat_quote_uses_explicit_occurrence_and_utf16():
    units=plan('😀待确认；待确认\n')['units'];u=units[0]
    ref=exact_evidence({'unit_id':u['id'],'quote':'待确认','occurrence':1},{u['id']:u})
    assert ref['start_utf16']==6
    assert ref['end_utf16']==9
    for bad in [{'unit_id':u['id'],'quote':'确认通过','occurrence':0},{'unit_id':'u999999','quote':'待确认','occurrence':0},{'unit_id':u['id'],'quote':'待确认','occurrence':2}]:
        with pytest.raises(store.TextError) as e:exact_evidence(bad,{u['id']:u})
        assert e.value.code=='INVALID_SOURCE'


def board_payload(unit,section='预算与时间'):
    result={'sections':{s:[] for s in SECTIONS}}
    result['sections'][section]=[{'text':unit['text'].strip(),'kind':'tentative','evidence':[{'unit_id':unit['id'],'quote':unit['text'].strip(),'occurrence':0}]}]
    return result


def test_board_requires_all_seven_sections_and_real_sources():
    chunk=plan(RAW)['chunks'][0];payload=board_payload(chunk[0])
    assert len(validate_board(payload,chunk))==7
    del payload['sections']['后续行动']
    with pytest.raises(store.TextError) as e:validate_board(payload,chunk)
    assert e.value.code=='INVALID_OUTPUT'


@pytest.mark.parametrize('variant,code',[('fake_quote','INVALID_SOURCE'),('no_source','INVALID_SOURCE'),('condition_cut','FIDELITY_QUALIFIERS'),('invent_number','FIDELITY_NUMBERS')])
def test_board_rejects_unverifiable_or_overstated_fields(variant,code):
    chunk=plan(RAW)['chunks'][0];p=board_payload(chunk[0]);item=p['sections']['预算与时间'][0]
    if variant=='fake_quote':item['evidence'][0]['quote']='预算已经批准'
    elif variant=='no_source':item['evidence']=[]
    elif variant=='condition_cut':item.update(text='预算20万');item['evidence'][0]['quote']='预算大概20万'
    else:item['text']+=' 下月采购100套'
    with pytest.raises(store.TextError) as e:validate_board(p,chunk)
    assert e.value.code==code


def test_sales_claim_and_conditional_commitment_cannot_be_upgraded():
    chunk=plan(RAW)['chunks'][0]
    p=board_payload(chunk[1],'客户需求与产品适配')
    item=p['sections']['客户需求与产品适配'][0];item['kind']='reported'
    with pytest.raises(store.TextError) as e:validate_board(p,chunk)
    assert e.value.code=='INVALID_ATTRIBUTION'
    item['kind']='sales_claim'
    assert validate_board(p,chunk)['客户需求与产品适配'][0]['kind']=='sales_claim'
    p=board_payload(chunk[0],'已达成事项');p['sections']['已达成事项'][0]['kind']='agreed'
    with pytest.raises(store.TextError) as e:validate_board(p,chunk)
    assert e.value.code=='INVALID_COMMITMENT'


@pytest.mark.parametrize('raw,quote',[
    ('销售：😀暂时不支持API。','支持API'),
    ('客户：只有审批后才考虑试用。','考虑试用'),
    ('客户：可能延期上线。','上线'),
])
def test_quote_cannot_hide_its_own_sentence_negation_or_condition(raw,quote):
    units=plan(raw)['units'];payload=board_payload(units[0],'客户需求与产品适配')
    item=payload['sections']['客户需求与产品适配'][0]
    item['text']=quote;item['evidence'][0]['quote']=quote
    with pytest.raises(store.TextError) as e:validate_board(payload,units)
    assert e.value.code=='FIDELITY_QUALIFIERS'
    assert validate_board(board_payload(units[0],'客户需求与产品适配'),units)


def test_obvious_business_omission_is_not_reported_as_complete():
    chunk=plan(RAW)['chunks'][0]
    payload={'sections':{s:[] for s in SECTIONS}}
    with pytest.raises(store.TextError) as e:validate_board(payload,chunk,required_units=chunk)
    assert e.value.code=='INCOMPLETE_EXTRACTION'


def test_ai_suggestion_distinct_and_no_source_is_not_fabricated():
    chunk=plan(RAW)['chunks'][0]
    p={'sections':{s:[] for s in SECTIONS}}
    p['sections']['待确认问题']=[{'text':'建议核对范围','kind':'ai_suggestion','evidence':[]}]
    assert validate_board(p,chunk)['待确认问题'][0]['evidence']==[]


def test_pipeline_persists_real_files_and_reopens_without_model(source):
    model=ScriptedModel();jobs=Jobs(model)
    jobs.start('user-a',MID);wait(jobs)
    record=jobs.view('user-a',MID)
    assert record['job']['state']=='succeeded'
    assert record['result']['processed'].splitlines()==RAW.splitlines()
    assert list(record['result']['sections'])==list(SECTIONS)
    count=len(model.calls);jobs.start('user-a',MID);assert len(model.calls)==count
    jobs.shutdown()
    reopened=Jobs()
    assert reopened.view('user-a',MID)['result']==record['result']
    assert reopened.model.status()['enabled'] is False
    reopened.shutdown()
    assert (store.meeting_dir('user-a',MID)/'original.md').read_bytes()==RAW.encode()


def test_failed_piece_preserved_and_retry_only_missing_chunks(tmp_path):
    storage.set_data_dir(tmp_path)
    raw=RAW*35;store.import_markdown('user-a',MID,'long.md',raw.encode(),'长稿')
    model=ScriptedModel(fail_at=('cleaning',1));jobs=Jobs(model)
    jobs.start('user-a',MID);wait(jobs)
    failed=jobs.view('user-a',MID)
    assert failed['job']['state']=='failed' and failed['job']['completed_clean']==1
    assert failed['job']['error']['code']=='MODEL_TIMEOUT'
    assert any(r['cleaned'] is None for r in failed['partial_clean'])
    assert failed['result'] is None
    jobs.start('user-a',MID);wait(jobs)
    assert jobs.view('user-a',MID)['job']['state']=='succeeded'
    assert model.calls.count(('cleaning',0))==1
    assert model.calls.count(('cleaning',1))==2
    assert '尾' not in jobs.view('user-a',MID)['processed'] # no invented sentinel
    jobs.shutdown()


def test_duplicate_clicks_only_one_job(source):
    event=threading.Event();model=ScriptedModel(event=event);jobs=Jobs(model)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _:jobs.start('user-a',MID),range(6)))
    event.set();wait(jobs)
    assert model.calls.count(('cleaning',0))==1
    assert jobs.state('user-a',MID)['attempt']==1
    jobs.shutdown()


def test_restart_marks_interrupted_without_starting_or_spending(source):
    path=store.meeting_dir('user-a',MID)/'job.json'
    atomic_json(path,{'schema_version':1,'state':'extracting'})
    model=ScriptedModel();jobs=Jobs(model)
    assert jobs.state('user-a',MID)['state']=='interrupted'
    assert model.calls==[]
    jobs.shutdown()


def test_recovery_does_not_steal_live_lease(source):
    directory=store.meeting_dir('user-a',MID)
    atomic_json(directory/'job.json',{'schema_version':1,'state':'cleaning'})
    with lease(directory):
        jobs=Jobs(ScriptedModel())
        assert jobs.state('user-a',MID)['state']=='cleaning'
    jobs.shutdown()


def test_corrupt_completed_result_fails_closed(source):
    jobs=Jobs(ScriptedModel());jobs.start('user-a',MID);wait(jobs)
    atomic_json(store.meeting_dir('user-a',MID)/'result.json',{'processed':'tampered'})
    with pytest.raises(store.TextError) as e:jobs.view('user-a',MID)
    assert e.value.code=='RESULT_CHANGED'
    jobs.shutdown()


def test_final_write_failure_keeps_checkpoints_and_retry_does_not_recall_model(source, monkeypatch):
    from app.text_processing import jobs as module
    original=module.atomic_json
    def fail_result(path,value):
        if path.name=='result.json':raise OSError('offline disk failure')
        return original(path,value)
    monkeypatch.setattr(module,'atomic_json',fail_result)
    model=ScriptedModel();jobs=Jobs(model);jobs.start('user-a',MID);wait(jobs)
    assert jobs.state('user-a',MID)['state']=='failed'
    assert not (store.meeting_dir('user-a',MID)/'result.json').exists()
    count=len(model.calls)
    monkeypatch.setattr(module,'atomic_json',original)
    jobs.start('user-a',MID);wait(jobs)
    assert jobs.state('user-a',MID)['state']=='succeeded'
    assert len(model.calls)==count
    jobs.shutdown()


def test_status_disk_failure_is_visible_and_recoverable_without_fake_running(source, monkeypatch):
    from app.text_processing import jobs as module
    original=module.atomic_json
    writes=0
    def fail_after_queue(path,value):
        nonlocal writes
        writes+=1
        if writes>1:raise OSError('offline disk full')
        return original(path,value)
    monkeypatch.setattr(module,'atomic_json',fail_after_queue)
    model=ScriptedModel();jobs=Jobs(model);jobs.start('user-a',MID);wait(jobs)
    assert jobs.state('user-a',MID)['state']=='failed'
    assert jobs.state('user-a',MID)['error']['code']=='PROCESSING_FAILED'
    assert model.calls==[]
    monkeypatch.setattr(module,'atomic_json',original)
    jobs.start('user-a',MID);wait(jobs)
    assert jobs.state('user-a',MID)['state']=='succeeded'
    jobs.shutdown()


def test_corrupt_checkpoint_never_silently_rebills(tmp_path):
    storage.set_data_dir(tmp_path);store.import_markdown('user-a',MID,'a.md',(RAW*35).encode(),'测试')
    model=ScriptedModel(fail_at=('cleaning',1));jobs=Jobs(model);jobs.start('user-a',MID);wait(jobs)
    path=store.meeting_dir('user-a',MID)/'checkpoints/cleaning-00000.json'
    saved=json.loads(path.read_text());saved['payload']['rows'][0]['text']='changed';atomic_json(path,saved)
    count=len(model.calls);jobs.start('user-a',MID);wait(jobs)
    assert jobs.state('user-a',MID)['error']['code']=='CHECKPOINT_CORRUPT'
    assert len(model.calls)==count
    jobs.shutdown()


def test_http_pipeline_and_reopen_use_real_saved_results(tmp_path):
    headers={'x-tingji-service-token':'a'*64,'x-tingji-user-id':'user-a'}
    model=ScriptedModel()
    app=create_app(tmp_path,'a'*64,model=model)
    with TestClient(app,headers=headers) as client:
        assert client.post('/api/imports',files={'file':('a.md',RAW.encode())},data={'import_id':MID}).status_code==201
        assert client.post(f'/api/meetings/{MID}/process',json={}).status_code==202
        wait(app.state.jobs)
        assert client.get(f'/api/meetings/{MID}/job').json()['job']['state']=='draft'
        record=client.get(f'/api/meetings/{MID}').json()
        assert record['clean']['processing_complete'] and record['products']['clean']['state']=='draft'
        assert model.calls==[('clean_draft',0),('board_draft',0)]
        saved=record['board'];assert saved['based_on_clean_version']==record['clean']['version']
        assert saved['human_verified'] is False
        assert saved['sections']['客户需求与产品适配'][0]['evidence'][0]['quote'] in RAW
        assert client.get(f'/api/meetings/{MID}',headers={'x-tingji-user-id':'user-b'}).status_code==404
    with TestClient(create_app(tmp_path,'a'*64),headers=headers) as client:
        assert client.get(f'/api/meetings/{MID}').json()['board']==saved


def test_corrupt_job_does_not_prevent_service_start_or_other_meetings(source):
    (store.meeting_dir('user-a',MID)/'job.json').write_text('["bad"]')
    jobs=Jobs()
    with pytest.raises(store.TextError) as e:jobs.state('user-a',MID)
    assert e.value.code=='CHECKPOINT_CORRUPT'
    jobs.shutdown()
    store.import_markdown('user-a','b'*32,'good.md',RAW.encode(),'另一场')
    with TestClient(create_app(source,'a'*64),headers={'x-tingji-service-token':'a'*64,'x-tingji-user-id':'user-a'}) as client:
        response=client.get('/api/meetings')
        assert response.status_code==200
        states={m['id']:m['status'] for m in response.json()}
        assert states=={MID:'failed','b'*32:'idle'}


def test_http_process_default_disabled_and_cross_user_denied(tmp_path):
    headers={'x-tingji-service-token':'a'*64,'x-tingji-user-id':'user-a'}
    with TestClient(create_app(tmp_path,'a'*64),headers=headers) as client:
        assert client.post('/api/imports',files={'file':('a.md',RAW.encode())},data={'import_id':MID}).status_code==201
        response=client.post(f'/api/meetings/{MID}/process',json={})
        assert response.status_code==409 and response.json()['code']=='MODEL_NOT_AUTHORIZED'
        assert client.post(f'/api/meetings/{MID}/process',json={'model':'evil'}).json()['code']=='INVALID_REQUEST'
        response=client.post(f'/api/meetings/{MID}/process',json={},headers={'x-tingji-user-id':'user-b'})
        assert response.status_code==404
        assert client.get(f'/api/meetings/{MID}/job').json()['job']['state']=='idle'
