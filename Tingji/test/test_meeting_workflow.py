"""Offline workflow/edit/reference tests. Synthetic fixtures, never acceptance facts."""
import copy
import json
import pytest
from fastapi.testclient import TestClient
from app import storage,text_storage as store
from app.text_main import create_app
from app.text_processing.editing import save,draft,decide
from app.text_processing.presentation import completion,sales_card
from app.text_processing.reference import snapshot
from app.text_processing.products import Products
from test.test_board_first import FakeModel,wait,OWNER,MID,RAW


@pytest.fixture
def setup(tmp_path):
    """Fresh import: nothing cleaned yet, so the full serial workflow is exercised."""
    storage.set_data_dir(tmp_path);store.import_markdown(OWNER,MID,'synthetic.md',RAW.encode(),'离线工程样本')
    model=FakeModel();jobs=Products(model)
    yield jobs,model
    jobs.shutdown()


def edit_body(record,product='board'):
    patch={'processed':'人工整理：预算五到七万元，未批准。\n权限需要核对。'} if product=='clean' else {'fields':[{'id':record['board']['sections']['预算与时间'][0]['id'],'label':'人工预算备注','text':'待财务确认预算，未批准。','conditions':['不可视为采购承诺'],'history':[]}]}
    return {'product':product,'base_version':record[product]['version'],'patch':patch,'draft_version':None}


def test_single_confirmation_cleans_then_extracts_board_and_repeated_submission_does_not_regenerate(setup):
    jobs,model=setup;started=jobs.start_all(OWNER,MID)
    assert started['workflow']=='clean-then-board-v1'
    wait(jobs)
    assert model.calls==[('clean_draft',0),('board_draft',0)], 'board must be extracted after the clean transcript'
    record=jobs.view(OWNER,MID)
    assert record['board']['based_on_clean_version']==record['clean']['version']
    assert completion(jobs.view(OWNER,MID))['all_generated'] is True
    jobs.start_all(OWNER,MID);wait(jobs)
    assert len(model.calls)==2


def test_first_confirmation_preflights_clean_scope_before_any_model_call(setup):
    jobs,model=setup
    model.product_status=lambda owner,sha,product,protocol:{'enabled':product=='board','code':'PRODUCT_NOT_AUTHORIZED','reason':'清洗未授权'}
    with pytest.raises(store.TextError) as exc:jobs.start_all(OWNER,MID)
    assert exc.value.code=='PRODUCT_NOT_AUTHORIZED'
    assert model.calls==[] and jobs.state(OWNER,MID)['state']=='idle'


def test_failed_clean_shows_real_state_and_never_falls_back_to_raw_board(setup):
    jobs,model=setup;model.fail=('clean_draft',0);jobs.start_all(OWNER,MID);wait(jobs)
    result=jobs.view(OWNER,MID)
    assert result['board'] is None and result['clean'] is None, 'no silent raw-only board'
    assert result['products']['board']['state']=='waiting' and result['products']['clean']['state']=='failed'
    assert completion(result)['state']=='failed' and not completion(result)['all_generated']
    jobs.start_all(OWNER,MID);wait(jobs);assert model.calls==[('clean_draft',0)]
    model.fail=None;jobs.start(OWNER,MID,'clean');wait(jobs)
    assert model.calls==[('clean_draft',0),('clean_draft',0),('board_draft',0)]
    assert completion(jobs.view(OWNER,MID))['all_generated']


def test_board_start_failure_after_clean_is_visible(setup):
    jobs,model=setup
    model.product_status=lambda owner,sha,product,protocol:{'enabled':True,'reason':None,'batch_id':'offline-batch'}
    real=jobs.start
    def refuse_board(owner,mid,product='board',*a,**k):
        if product=='board':raise store.TextError('BUDGET_EXHAUSTED','离线模拟：额度不足。',409)
        return real(owner,mid,product,*a,**k)
    jobs.start=refuse_board
    jobs.start_all(OWNER,MID);wait(jobs);state=jobs.state(OWNER,MID,'board')
    assert state['state']=='failed' and '看板未能自动开始' in state['error']['message']
    assert jobs.view(OWNER,MID)['clean']['processing_complete'] is True and model.calls==[('clean_draft',0)]


def test_unreliable_clean_rows_keep_original_and_are_marked(tmp_path):
    raw=''.join(f'说话人1：第{i}段，预算{i}万元。\n' for i in range(1,9))
    storage.set_data_dir(tmp_path);store.import_markdown(OWNER,MID,'synthetic.md',raw.encode(),'离线工程样本')
    class OneBadRow(FakeModel):
        def call(self,messages,owner,sha,stage,index):
            value=super().call(messages,owner,sha,stage,index)
            if stage=='clean_draft':value['rows'][1]['text']=value['rows'][1]['text'].replace('2万元','20万元')
            return value
    model=OneBadRow(raw);jobs=Products(model)
    try:
        jobs.start(OWNER,MID,'clean');wait(jobs);clean=jobs.view(OWNER,MID)['clean']
        assert clean['processing_complete'] is True and '20万元' not in clean['processed']
        assert [r['cleaned'] for r in clean['rows']][1] is None
        assert any(i['code']=='CLEAN_ROW_KEPT_ORIGINAL' and '第2行' in i['message'] for i in clean['issues'])
    finally:jobs.shutdown()


def test_mostly_unreliable_clean_chunk_fails_instead_of_passing_raw_as_clean(tmp_path):
    raw=''.join(f'说话人1：第{i}段，预算{i}万元。\n' for i in range(1,9))
    storage.set_data_dir(tmp_path);store.import_markdown(OWNER,MID,'synthetic.md',raw.encode(),'离线工程样本')
    class ManyBad(FakeModel):
        def call(self,messages,owner,sha,stage,index):
            value=super().call(messages,owner,sha,stage,index)
            for row in value.get('rows',[])[:4]:row['text']=row['text']+'另加100万元。'
            return value
    model=ManyBad(raw);jobs=Products(model)
    try:
        jobs.start(OWNER,MID,'clean');wait(jobs)
        state=jobs.state(OWNER,MID,'clean')
        assert state['state']=='failed' and state['error']['code']=='CLEAN_QUALITY'
        assert jobs.view(OWNER,MID)['clean'] is None
    finally:jobs.shutdown()


def test_cards_keep_qualifiers_and_never_use_import_date_as_meeting_date(setup):
    jobs,_=setup;jobs.start_all(OWNER,MID);wait(jobs);record=jobs.view(OWNER,MID)
    item=record['board']['sections']['预算与时间'][0];item['conditions']=[{'text':'税费另待确认'}]
    card=sales_card(record)
    assert '未批准' in card['obstacle'] and '税费另待确认' in card['obstacle']
    assert card['meeting_date'] is None
    record['source_text']='会议时间：2026-10-05 10:00\n'+RAW
    assert sales_card(record)['meeting_date']=='2026-10-05'


def test_human_edit_versions_survive_reopen_and_preserve_original_and_evidence(setup):
    jobs,model=setup;jobs.start_all(OWNER,MID);wait(jobs);record=jobs.view(OWNER,MID)
    original=store.meeting_dir(OWNER,MID)/'original.md';before=original.read_bytes()
    body=edit_body(record);saved_draft=save(jobs,OWNER,MID,body,draft_only=True)
    assert jobs.view(OWNER,MID)['edit_drafts']['board']['patch']==body['patch']
    assert jobs.view(OWNER,MID)['board']['version']==record['board']['version']
    body['draft_version']=saved_draft['draft_version'];saved=save(jobs,OWNER,MID,body)
    assert saved['origin']=='human' and saved['version']!=body['base_version'] and draft(jobs,OWNER,MID,'board') is None
    field=saved['sections']['预算与时间'][0]
    assert field['human_edited'] and not field['human_verified'] and field['speaker']=='未标明'
    assert field['evidence']==record['board']['sections']['预算与时间'][0]['evidence']
    assert original.read_bytes()==before and len(model.calls)==2
    from app.text_processing.products import Products
    reopened=Products(model)
    try:assert reopened.view(OWNER,MID)['board']==saved
    finally:reopened.shutdown()


def test_stale_version_and_stale_draft_cannot_overwrite(setup):
    jobs,_=setup;jobs.start_all(OWNER,MID);wait(jobs);body=edit_body(jobs.view(OWNER,MID))
    first=save(jobs,OWNER,MID,body,draft_only=True)
    with pytest.raises(store.TextError) as exc:save(jobs,OWNER,MID,body)
    assert exc.value.code=='DRAFT_CHANGED'
    body['draft_version']=first['draft_version'];save(jobs,OWNER,MID,body)
    with pytest.raises(store.TextError) as exc:save(jobs,OWNER,MID,body)
    assert exc.value.code=='RESULT_CHANGED'


def test_edit_cannot_forge_source_or_promote_partial_clean(setup):
    jobs,_=setup;jobs.start_all(OWNER,MID);wait(jobs);record=jobs.view(OWNER,MID)
    body=edit_body(record);body['patch']['fields'][0]['evidence']=[{'quote':'fake'}]
    with pytest.raises(store.TextError) as exc:save(jobs,OWNER,MID,body)
    assert exc.value.code=='INVALID_EDIT'
    saved=save(jobs,OWNER,MID,edit_body(record,'clean'))
    assert 'rows' not in saved and saved['source_mapping_state']=='human_edit_not_revalidated'
    assert saved['processed'].startswith('人工整理') and not saved['human_verified']


def test_regeneration_keeps_manual_version_until_explicit_comparison_decision(setup):
    jobs,_=setup;jobs.start_all(OWNER,MID);wait(jobs);record=jobs.view(OWNER,MID)
    manual=save(jobs,OWNER,MID,edit_body(record))
    jobs.start(OWNER,MID,'board',True,manual['version']);wait(jobs)
    record=jobs.view(OWNER,MID);suggestion=record['candidates']['board']
    assert record['board']['version']==manual['version'] and suggestion['version']!=manual['version']
    decide(jobs,OWNER,MID,{'product':'board','base_version':manual['version'],'candidate_version':suggestion['version'],'adopt':True})
    assert jobs.view(OWNER,MID)['board']['version']==suggestion['version']
    assert (jobs.directory(OWNER,MID,'board')/'versions'/f"{manual['version']}.json").exists()


def test_reference_reads_saved_versions_not_draft_and_cannot_cross_owner(setup):
    jobs,model=setup;jobs.start_all(OWNER,MID);wait(jobs);record=jobs.view(OWNER,MID)
    save(jobs,OWNER,MID,edit_body(record),draft_only=True)
    value=snapshot(jobs,OWNER,MID)
    assert value['original']==RAW and value['versions']['board']==record['board']['version']
    assert '人工预算备注' not in json.dumps(value,ensure_ascii=False)
    with pytest.raises(store.TextError) as exc:snapshot(jobs,'other-owner',MID)
    assert exc.value.code=='NOT_FOUND' and len(model.calls)==2


def test_autosaved_edit_is_not_overwritten_by_regeneration(setup):
    jobs,_=setup;jobs.start_all(OWNER,MID);wait(jobs);record=jobs.view(OWNER,MID)
    save(jobs,OWNER,MID,edit_body(record),draft_only=True)
    jobs.start(OWNER,MID,'board',True,record['board']['version']);wait(jobs)
    updated=jobs.view(OWNER,MID)
    assert updated['board']['version']==record['board']['version'] and updated['candidates']['board']
    assert updated['edit_drafts']['board']['base_version']==record['board']['version']


def test_tentative_trial_is_allowed_in_pending_but_not_in_agreements():
    from app.text_processing.board_first import build_draft,source_anchors
    from app.text_processing.contracts import plan,SECTIONS
    raw='说话人1：可以考虑试点，还没有同意采购。\n'
    units=plan(raw)['units'];refs=[a['id'] for a in source_anchors(units)]
    item={'id':'trial','topic':'agreement','speaker':'说话人1','label':'试点意向','text':'可考虑试点，未同意采购。','kind':'tentative','evidence':refs,'conditions':[],'history':[]}
    result={'sections':{s:[] for s in SECTIONS}};result['sections']['待确认问题']=[item]
    assert build_draft([result],units)['sections']['待确认问题'][0]['review_state']=='pending'
    result['sections']['待确认问题']=[];result['sections']['已达成事项']=[item]
    with pytest.raises(store.TextError) as exc:build_draft([result],units)
    assert exc.value.code=='NO_USABLE_BOARD'


def test_actual_http_default_starts_two_products_and_edit_routes_validate(tmp_path):
    model=FakeModel();app=create_app(tmp_path,'a'*64,model);headers={'x-tingji-service-token':'a'*64,'x-tingji-user-id':OWNER}
    store.import_markdown(OWNER,MID,'raw.md',RAW.encode(),'工程测试')
    with TestClient(app,headers=headers) as client:
        assert client.post(f'/api/meetings/{MID}/process',json={}).status_code==202
        wait(app.state.jobs);record=client.get(f'/api/meetings/{MID}').json();assert record['completion']['all_generated']
        body=edit_body(record)
        assert client.post(f'/api/meetings/{MID}/save',json=body,headers={'x-tingji-user-id':'another'}).status_code==404
        assert client.post(f'/api/meetings/{MID}/save',json={**body,'source':'malicious'}).json()['code']=='INVALID_EDIT'
        saved=client.post(f'/api/meetings/{MID}/save',json=body);assert saved.status_code==200 and saved.json()['human_edited']
        ref=client.get(f'/api/meetings/{MID}/reference').json();assert ref['versions']['board']==saved.json()['version']
        assert len(model.calls)==2


def test_card_points_are_short_labels_and_keep_unapproved_budget(setup):
    jobs,_=setup;jobs.start_all(OWNER,MID);wait(jobs);record=jobs.view(OWNER,MID)
    item=record['board']['sections']['预算与时间'][0];item['label']='预算5-7万元'
    card=sales_card(record)
    assert card['obstacle_points']==['预算5-7万元 · 未审批']
    assert all(len(p)<=40 for p in card['obstacle_points']+card['need_points']+card['next_points'])


def test_archive_only_tags_the_meeting_and_keeps_every_file(tmp_path):
    model=FakeModel();app=create_app(tmp_path,'a'*64,model);headers={'x-tingji-service-token':'a'*64,'x-tingji-user-id':OWNER}
    store.import_markdown(OWNER,MID,'raw.md',RAW.encode(),'工程测试')
    before=sorted(p.name for p in store.meeting_dir(OWNER,MID).iterdir())
    with TestClient(app,headers=headers) as client:
        assert client.post(f'/api/meetings/{MID}/archive',json={'archived':True}).json()=={'id':MID,'archived':True}
        assert client.get('/api/meetings').json()[0]['archived'] is True
        assert client.post(f'/api/meetings/{MID}/archive',json={'archived':'yes'}).status_code==400
        assert client.post(f'/api/meetings/{MID}/archive',json={'archived':False}).json()['archived'] is False
        assert client.get('/api/meetings').json()[0]['archived'] is False
        assert client.post(f'/api/meetings/{MID}/archive',json={'archived':True},headers={'x-tingji-user-id':'other'}).status_code==404
    assert sorted(p.name for p in store.meeting_dir(OWNER,MID).iterdir())==before and not model.calls
