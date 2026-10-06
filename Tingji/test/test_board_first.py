"""Offline only: synthetic contract cases, not customer-material answer fixtures."""
import copy
import json
import time
from unittest.mock import patch
import pytest
from app import storage,text_storage as store
from app.text_processing.contracts import plan,SECTIONS,fingerprint
from app.text_processing.board_first import build_draft,source_anchors,numeric_values,board_messages,supported_numbers
from app.text_processing.products import Products
from app.text_processing.model import atomic_json

OWNER='offline';MID='d'*32
RAW='说话人1：预算五到七万元，未批准。\n权限需要核对。\n'

def candidate(raw=RAW):
    aa=source_anchors(plan(raw)['units']);budget=[a['id'] for a in aa if a['line']==1]
    rows={s:[] for s in SECTIONS}
    rows['预算与时间']=[{'id':'budget','topic':'budget','label':'预算','speaker':aa[0]['speaker'],
        'text':'预算5—7万元，未批准。','kind':'tentative','evidence':budget,'conditions':[],'history':[]}]
    return {'sections':rows}

class FakeModel:
    def __init__(self,raw=RAW):self.raw=raw;self.calls=[];self.fail=None;self.invalid=None;self.prompts={}
    def status(self,*a):return {'enabled':True,'reason':None,'batch_id':'offline-batch'}
    def call(self,messages,owner,sha,stage,index):
        self.calls.append((stage,index));data=json.loads(messages[1]['content']);self.prompts[stage]=messages
        if self.fail==(stage,index):raise store.TextError('MODEL_TIMEOUT','离线模拟超时。')
        if self.invalid is not None:return copy.deepcopy(self.invalid)
        if stage=='clean_draft':return {'rows':[{'id':u['id'],'text':u['text']} for u in data['target']]}
        return candidate(self.raw)

def wait(jobs):
    for _ in range(300):
        if not jobs.active:return
        time.sleep(.01)
    raise AssertionError('offline worker did not finish')

def clean_first(jobs,model):
    """The board is extracted from a complete clean transcript; prepare it, then reset call counts."""
    jobs.start(OWNER,MID,'clean');wait(jobs)
    assert jobs.view(OWNER,MID)['clean']['processing_complete'] is True
    model.calls.clear()

@pytest.fixture
def setup(tmp_path):
    storage.set_data_dir(tmp_path);store.import_markdown(OWNER,MID,'synthetic.md',RAW.encode(),'离线工程样本')
    model=FakeModel();jobs=Products(model);clean_first(jobs,model)
    yield jobs,model
    jobs.shutdown()

@pytest.mark.parametrize('left,right',[('五到七万元','5—7万元'),('二十五个人','25人'),('1,250元','1250元'),('十二万条','120000条'),('负二万元','-20000元'),('三万个','30000个')])
def test_numeric_equivalence_is_explicit_not_a_literal_word_match(left,right):
    assert numeric_values(left)==numeric_values(right)

def test_units_and_magnitudes_are_not_erased():
    assert numeric_values('20万元')!=numeric_values('20元')
    assert numeric_values('7家')!=numeric_values('7人')
    assert numeric_values('-2%')!=numeric_values('2%')
    assert numeric_values('2万个')!=numeric_values('2万元')
    assert numeric_values('5万美元')!=numeric_values('5万元')

@pytest.mark.parametrize('text,source',[('5万元','5万美元'),('5万','5万，按美元计'),('人民币5万元','5万元，币种未提及')])
def test_currency_cannot_be_added_removed_or_switched(text,source):
    with pytest.raises(store.TextError):supported_numbers(text,[{'quote':source}])

def test_unlabelled_text_is_unknown_and_not_a_role_inference():
    units=plan('说话人1：我们再商量。\n预算：五万元。\n这个我回去问下审批人\n')['units']
    assert [a['speaker'] for a in source_anchors(units)]==['说话人1','未标明','未标明']
    payload=candidate();payload['sections']['预算与时间'][0]['speaker']='财务总监'
    with pytest.raises(store.TextError,match='尚无'):build_draft([payload],plan(RAW)['units'])

@pytest.mark.parametrize('change',[{'text':'已批准预算900万元。'},{'topic':[]},{'speaker':{'guessed':'unknown'}}])
def test_partial_bad_field_does_not_erase_good_field_or_publish_bad_assertion(change):
    payload=candidate();bad=copy.deepcopy(payload['sections']['预算与时间'][0]);bad.update(id='bad',**change)
    payload['sections']['预算与时间'].append(bad)
    result=build_draft([payload],plan(RAW)['units']);items=result['sections']['预算与时间']
    assert result['usable_fields']==1 and result['complete'] is False
    assert items[1]['review_state']=='blocked' and '900' not in items[1]['text']
    assert items[1]['evidence'] and all(i['human_verified'] is False for i in items)
    assert any(i['severity']=='blocking' for i in result['issues'])

def test_missing_source_is_never_fuzzily_repaired():
    payload=candidate();payload['sections']['预算与时间'][0]['evidence']=['nonexistent']
    with pytest.raises(store.TextError) as error:build_draft([payload],plan(RAW)['units'])
    assert error.value.code=='NO_USABLE_BOARD'

def test_full_source_is_sent_even_when_target_is_a_fragment():
    raw='开头😀。\n'+'中间内容。\n'*600+'结尾不能承诺上线。'
    units=plan(raw)['units'];data=json.loads(board_messages(units,units[:1])[1]['content'])
    text=''.join(r[3] for r in data['source']['rows'])
    assert '开头😀' in text and '结尾不能承诺上线' in text
    assert {r[1] for r in data['source']['rows']}=={u['id'] for u in units}

def test_board_requires_complete_clean_and_is_extracted_from_it(tmp_path):
    storage.set_data_dir(tmp_path);store.import_markdown(OWNER,MID,'synthetic.md',RAW.encode(),'离线工程样本')
    class CleaningModel(FakeModel):
        def call(self,messages,owner,sha,stage,index):
            value=super().call(messages,owner,sha,stage,index)
            if stage=='clean_draft':
                for row in value['rows']:row['text']=row['text'].replace('权限需要核对。','权限还需要再核对。')
            return value
    model=CleaningModel();jobs=Products(model);directory=store.meeting_dir(OWNER,MID)
    before={p.name:p.read_bytes() for p in directory.iterdir() if p.is_file()}
    try:
        with pytest.raises(store.TextError) as error:jobs.start(OWNER,MID,'board')
        assert error.value.code=='CLEAN_REQUIRED' and not model.calls
        model.fail=('clean_draft',0);jobs.start(OWNER,MID,'clean');wait(jobs)
        assert jobs.state(OWNER,MID,'clean')['state']=='failed'
        with pytest.raises(store.TextError) as error:jobs.start(OWNER,MID,'board')
        assert error.value.code=='CLEAN_REQUIRED' and jobs.view(OWNER,MID)['board'] is None
        model.fail=None;jobs.start(OWNER,MID,'clean');wait(jobs);clean=jobs.view(OWNER,MID)['clean']
        assert '再核对' in clean['processed']
        jobs.start(OWNER,MID,'board');wait(jobs);record=jobs.view(OWNER,MID)
        data=json.loads(model.prompts['board_draft'][1]['content'])
        cols=data['source']['columns'];rows=[dict(zip(cols,r)) for r in data['source']['rows']]
        assert '清洗' in model.prompts['board_draft'][0]['content']
        assert any('再核对' in r['cleaned'] for r in rows), 'board input must be the cleaned transcript'
        assert all(r['clean_state']=='cleaned' for r in rows)
        anchor_quotes=' '.join(q for r in rows for _,q in r['anchors'])
        assert '再核对' not in anchor_quotes and '权限需要核对' in anchor_quotes, 'evidence stays raw'
        board=record['board']
        assert board['extraction_input']=='clean' and board['based_on_clean_version']==clean['version']
        assert board['state']=='draft' and not board['human_verified']
        assert all(e['quote'] in RAW for items in board['sections'].values() for f in items for e in f['evidence'])
        for name,raw in before.items():assert (directory/name).read_bytes()==raw
        assert model.calls==[('clean_draft',0),('clean_draft',0),('board_draft',0)]
    finally:jobs.shutdown()

def test_invalid_response_has_no_artifact_and_explicit_retry_preserves_error(setup):
    jobs,model=setup;model.invalid={'sections':{'broken':[]}}
    jobs.start(OWNER,MID);wait(jobs)
    assert jobs.state(OWNER,MID)['state']=='failed' and jobs.view(OWNER,MID)['board'] is None
    assert list(jobs.directory(OWNER,MID,'board').glob('rejected/*.json'))
    model.invalid=None;jobs.start(OWNER,MID);wait(jobs)
    assert jobs.state(OWNER,MID)['last_error']['code']=='INVALID_OUTPUT'
    assert jobs.view(OWNER,MID)['board'] and len(model.calls)==2

def test_versions_immutable_and_regeneration_requires_matching_base(setup):
    jobs,model=setup;jobs.start(OWNER,MID);wait(jobs)
    prior=jobs.view(OWNER,MID)['board'];p=jobs.directory(OWNER,MID,'board')/'versions'/f"{prior['version']}.json";before=p.read_bytes()
    with pytest.raises(store.TextError):jobs.start(OWNER,MID,regenerate=True,base_version='wrong')
    assert len(model.calls)==1
    jobs.start(OWNER,MID,regenerate=True,base_version=prior['version']);wait(jobs)
    assert p.read_bytes()==before and len(model.calls)==2
    assert jobs.view(OWNER,MID)['board']['version']!=prior['version']

def test_reopen_never_calls_model_and_checks_artifact_hash(setup):
    jobs,model=setup;jobs.start(OWNER,MID);wait(jobs);saved=jobs.view(OWNER,MID)['board'];jobs.shutdown()
    reopened=Products(model)
    try:
        assert reopened.view(OWNER,MID)['board']==saved and len(model.calls)==1
        pointer=reopened.directory(OWNER,MID,'board')/'current.json';value=json.loads(pointer.read_text());value['sha256']='wrong';atomic_json(pointer,value)
        with pytest.raises(store.TextError) as error:reopened.view(OWNER,MID)
        assert error.value.code=='RESULT_CHANGED' and len(model.calls)==1
    finally:reopened.shutdown()

def test_clean_resume_reuses_checkpoint_and_keeps_board_bytes(tmp_path):
    raw=RAW*35;storage.set_data_dir(tmp_path);store.import_markdown(OWNER,MID,'synthetic.md',raw.encode(),'多片离线样本')
    model=FakeModel(raw);jobs=Products(model)
    try:
        model.fail=('clean_draft',1);jobs.start(OWNER,MID,'clean');wait(jobs)
        cp=jobs.directory(OWNER,MID,'clean')/'checkpoints/00000.json';before=cp.read_bytes()
        assert jobs.view(OWNER,MID)['clean']['processing_complete'] is False
        with pytest.raises(store.TextError) as error:jobs.start(OWNER,MID,'board')
        assert error.value.code=='CLEAN_REQUIRED', 'a partial clean draft is never board input'
        model.fail=None;jobs.start(OWNER,MID,'clean');wait(jobs)
        assert model.calls.count(('clean_draft',0))==1 and cp.read_bytes()==before
        jobs.start(OWNER,MID,'board');wait(jobs);board=jobs.view(OWNER,MID)['board']
        assert board and board['based_on_clean_version']==jobs.view(OWNER,MID)['clean']['version']
    finally:jobs.shutdown()

def test_restart_marks_active_product_interrupted_without_billing(setup):
    jobs,model=setup;jobs.start(OWNER,MID);wait(jobs);jobs.shutdown()
    path=jobs.directory(OWNER,MID,'board')/'job.json';state=json.loads(path.read_text());state['state']='running';atomic_json(path,state)
    reopened=Products(model)
    try:
        assert reopened.state(OWNER,MID)['state']=='interrupted' and len(model.calls)==1
        assert reopened.view(OWNER,MID)['board']
    finally:reopened.shutdown()

def test_static_sample_is_blocked_even_with_enabled_test_model(setup):
    jobs,model=setup;path=store.meeting_dir(OWNER,MID)/'meta.json';meta=json.loads(path.read_text());meta['sample']=True;atomic_json(path,meta)
    with pytest.raises(store.TextError) as error:jobs.start(OWNER,MID)
    assert error.value.code=='SAMPLE_ONLY' and not model.calls

def test_long_board_has_one_bounded_merge_and_failed_merge_can_resume(tmp_path):
    raw=RAW+'较长但无新增业务的口头重复。'*1500
    storage.set_data_dir(tmp_path);store.import_markdown(OWNER,MID,'synthetic.md',raw.encode(),'长稿离线结构样本')
    model=FakeModel(raw);jobs=Products(model)
    try:
        clean_first(jobs,model);model.fail=('board_merge',0)
        jobs.start(OWNER,MID);wait(jobs)
        state=jobs.state(OWNER,MID);before=list(model.calls)
        assert state['state']=='failed' and state['done']==state['total']-1
        assert jobs.view(OWNER,MID)['board']['processing_complete'] is False
        model.fail=None;jobs.start(OWNER,MID);wait(jobs)
        assert model.calls==before+[('board_merge',0)]
        assert jobs.view(OWNER,MID)['board']['processing_complete'] is True
        assert jobs.state(OWNER,MID)['state']=='draft'
    finally:jobs.shutdown()


def test_budget_refusal_keeps_generation_cause_and_gate_requires_new_batch(setup):
    jobs,model=setup;model.product_status=lambda *a:model.status()
    model.invalid={'wrong':'format'};jobs.start(OWNER,MID);wait(jobs)
    with patch.object(model,'call',side_effect=store.TextError('BUDGET_EXHAUSTED','下一请求超预算')):
        jobs.start(OWNER,MID);wait(jobs)
    state=jobs.overview(OWNER,MID)['board']
    assert state['last_error']['code']=='INVALID_OUTPUT'
    assert state['error']['code']=='BUDGET_EXHAUSTED' and state['gate']['enabled'] is False
    model.product_status=lambda *a:{'enabled':True,'reason':None,'batch_id':'new-offline-batch'}
    assert jobs.overview(OWNER,MID)['board']['gate']['enabled'] is True


def test_failed_regeneration_cannot_replace_complete_clean_with_partial(tmp_path):
    raw=RAW*35;storage.set_data_dir(tmp_path);store.import_markdown(OWNER,MID,'synthetic.md',raw.encode(),'多片保存检查')
    model=FakeModel(raw);jobs=Products(model)
    try:
        jobs.start(OWNER,MID,'clean');wait(jobs);saved=jobs.view(OWNER,MID)['clean']
        assert saved['processing_complete'] is True
        model.fail=('clean_draft',1)
        jobs.start(OWNER,MID,'clean',regenerate=True,base_version=saved['version']);wait(jobs)
        assert jobs.state(OWNER,MID,'clean')['state']=='failed'
        assert jobs.view(OWNER,MID)['clean']==saved
        assert len(list(jobs.directory(OWNER,MID,'clean').glob('versions/*.json')))==2
    finally:jobs.shutdown()


def test_disk_failure_status_is_kept_without_rebilling(setup):
    jobs,model=setup
    with patch('app.text_processing.products.atomic_json',side_effect=OSError('offline disk fault')):
        with pytest.raises(OSError):jobs.start(OWNER,MID)
    assert not jobs.active and not model.calls
    jobs.start(OWNER,MID);wait(jobs)
    assert jobs.view(OWNER,MID)['board'] and len(model.calls)==1


def test_bracketed_role_labels_are_real_speakers_and_must_survive_cleaning():
    from app.text_processing.semantic import validate_clean_candidate
    units=plan('【销售】您好，预算两万。\n【客户】嗯，还没批。\n没有标签的一句。\n')['units']
    assert [a['speaker'] for a in source_anchors(units)]==['销售','销售','客户','客户','未标明']
    chunk=[u for u in units if u['text'].strip()]
    ok={'rows':[{'id':chunk[0]['id'],'text':'【销售】您好，预算两万。'},{'id':chunk[1]['id'],'text':'【客户】嗯，还没批。'},{'id':chunk[2]['id'],'text':'没有标签的一句。'}]}
    assert validate_clean_candidate(ok,chunk)
    swapped=copy.deepcopy(ok);swapped['rows'][1]['text']='【销售】嗯，还没批。'
    with pytest.raises(store.TextError) as error:validate_clean_candidate(swapped,chunk)
    assert error.value.code=='FIDELITY_SPEAKER'


def test_sections_without_wrapper_are_accepted_and_a_rejected_paid_response_is_reused(setup):
    jobs,model=setup;flat=candidate()['sections']
    model.invalid=flat
    jobs.start(OWNER,MID);wait(jobs)
    assert jobs.view(OWNER,MID)['board']['sections']['预算与时间'], 'wrapper-less sections are the same board'
    assert model.calls==[('board_draft',0)]


def test_old_wrapper_rejection_is_salvaged_without_a_new_model_call(setup):
    jobs,model=setup;flat=candidate()['sections']
    import app.text_processing.products as products
    real=products.board_payload
    products.board_payload=lambda v:products.board_shape(v)  # simulate the previous strict release
    try:
        model.invalid=flat;jobs.start(OWNER,MID);wait(jobs)
        assert jobs.state(OWNER,MID)['state']=='failed' and len(model.calls)==1
    finally:products.board_payload=real
    model.invalid={'sections':{'broken':[]}}  # any new call would fail; it must not be made
    jobs.start(OWNER,MID);wait(jobs)
    assert len(model.calls)==1 and jobs.view(OWNER,MID)['board']
    assert jobs.state(OWNER,MID)['salvaged_batches']==[0]


def test_count_from_bare_spoken_number_is_supported_but_money_stays_exact():
    supported_numbers('直营在岗师傅86人',[{'quote':'嗯八十六，'}])
    supported_numbers('同一次报修进两遍',[{'quote':'回头发现两遍，'}])
    for text,quote in [('预算9万元',[{'quote':'九，'}]),('预算8到10万元',[{'quote':'九到十二万，'}]),('87人',[{'quote':'八十六，'}])]:
        with pytest.raises(store.TextError):supported_numbers(text,quote)


def test_revalidate_republishes_saved_model_output_without_a_model_call(setup):
    jobs,model=setup;jobs.start(OWNER,MID);wait(jobs);first=jobs.view(OWNER,MID)['board']
    calls=len(model.calls);again=jobs.revalidate(OWNER,MID)
    assert len(model.calls)==calls and again['version']!=first['version']
    assert again['sections']==first['sections'] and jobs.view(OWNER,MID)['board']['version']==again['version']
    old=jobs.directory(OWNER,MID,'board')/'versions'/f"{first['version']}.json";assert old.exists()


def test_customer_owner_due_shown_only_when_traceable_and_bad_label_falls_back():
    raw='【客户】我们是岚川设备服务，预算五到七万元，未批准。\n【销售】我周四下午五点前把方案发您。\n'
    units=plan(raw)['units'];aa=source_anchors(units)
    l1=[a['id'] for a in aa if a['line']==1];l2=[a['id'] for a in aa if a['line']==2]
    rows={s:[] for s in SECTIONS}
    rows['会议概况']=[{'id':'m','topic':'meeting','label':'客户与会议','speaker':'客户','text':'客户为岚川设备服务。','kind':'reported','evidence':l1[:1],'conditions':[],'history':[],'customer':'岚川设备服务'}]
    rows['预算与时间']=[{'id':'b','topic':'budget','label':'预算9万元','speaker':'客户','text':'预算五到七万元，未批准。','kind':'reported','evidence':l1,'conditions':[],'history':[]}]
    rows['后续行动']=[{'id':'a','topic':'action','label':'销售发方案','speaker':'销售','text':'销售周四下午五点前发方案。','kind':'agreed','evidence':l2,'conditions':[],'history':[],'owner':'销售','due':'周四下午五点前'},
                    {'id':'x','topic':'action','label':'销售回访','speaker':'销售','text':'销售发方案。','kind':'agreed','evidence':l2,'conditions':[],'history':[],'owner':'财务总监老王','due':'下周一'}]
    result=build_draft([{'sections':rows}],units);s=result['sections']
    assert s['会议概况'][0]['customer']=='岚川设备服务'
    assert s['预算与时间'][0]['label']=='预算与审批' and s['预算与时间'][0]['review_state']=='pending', 'wrong number in headline only drops the headline'
    assert s['后续行动'][0]['owner']=='销售' and s['后续行动'][0]['due']=='周四下午五点前'
    assert 'owner' not in s['后续行动'][1] and 'due' not in s['后续行动'][1], 'untraceable owner/due never shown'


def test_uncommitted_todo_is_shown_unconfirmed_and_duplicate_agreement_is_listed_once():
    raw='【销售】周四下午五点前发方案草案，一起发你。\n【客户】空表头可以，我明天下班前发你。\n'
    units=plan(raw)['units'];aa=source_anchors(units)
    l1=[a['id'] for a in aa if a['line']==1];l2=[a['id'] for a in aa if a['line']==2]
    rows={s:[] for s in SECTIONS}
    rows['后续行动']=[{'id':'a','topic':'action','label':'销售发方案草案','speaker':'销售','text':'销售周四下午五点前发方案草案。','kind':'agreed','evidence':l1,'conditions':[],'history':[]},
                    {'id':'b','topic':'action','label':'客户发空表头','speaker':'客户','text':'客户明天下班前发空表头。','kind':'agreed','evidence':l2,'conditions':[],'history':[]}]
    rows['已达成事项']=[{'id':'c','topic':'agreement','label':'客户同意发空表头','speaker':'客户','text':'客户同意明天下班前发空表头。','kind':'agreed','evidence':l2,'conditions':[],'history':[]}]
    s=build_draft([{'sections':rows}],units)['sections']
    assert [f['kind'] for f in s['后续行动']]==['tentative','agreed'] and all(f['review_state']=='pending' for f in s['后续行动'])
    assert s['已达成事项']==[], 'same words already listed as a to-do'


def test_customer_name_survives_a_blocked_overview_but_must_be_verbatim():
    raw='【客户】我们是岚川设备服务，预算五到七万元。\n'
    units=plan(raw)['units'];ids=[a['id'] for a in source_anchors(units)]
    rows={s:[] for s in SECTIONS};bad={'id':'m','topic':'meeting','label':'概况','speaker':'客户','text':'月单1800多。','kind':'reported','evidence':ids[:1],'conditions':[],'history':[],'customer':'岚川设备服务'}
    rows['会议概况']=[bad];rows['预算与时间']=[{'id':'b','topic':'budget','label':'预算','speaker':'客户','text':'预算五到七万元。','kind':'reported','evidence':ids,'conditions':[],'history':[]}]
    result=build_draft([{'sections':rows}],units)
    assert result['customer']=='岚川设备服务' and result['sections']['会议概况'][0]['review_state']=='blocked'
    rows['会议概况'][0]['customer']='岚川集团'
    assert build_draft([{'sections':rows}],units)['customer'] is None


def test_alternatives_and_word_digits():
    assert numeric_values('四还是两小时')==numeric_values('4小时或2小时')
    assert numeric_values('不能统一一条线')==set()
    assert numeric_values('二零二六年')!=set()
