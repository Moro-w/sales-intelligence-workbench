"""No network: projection preserves exact source, old polish checkpoints retain provenance."""
import json
import time
import pytest
from app import storage, text_storage as store
from app.text_processing.contracts import SECTIONS, plan, fingerprint
from app.text_processing.projection import validate_projection
from app.text_processing.jobs import Jobs, LEGACY_ENGINE
from app.text_processing.model import atomic_json
from test.test_text_processing import ScriptedModel, wait


def payload(refs,kind='tentative',relation='none',section='预算与时间'):
    return {'sections': {s: [{'kind':kind,'relation':relation,'evidence':refs}] if s==section else [] for s in SECTIONS}}


def test_projection_expands_negative_prefix_and_keeps_multisource():
    units=plan('客户：😀预算初估20万，不支持API。\n客户：更正预算为12万，尚未批准。\n')['units']
    refs=[{'unit_id':u['id'],'quote':u['text'].strip(),'occurrence':0} for u in units]
    result=validate_projection(payload(refs,relation='correction'),units)
    item=result['预算与时间'][0]
    assert '不支持API' in item['text'] and '尚未批准' in item['text']
    assert len(item['evidence'])==2
    selected=validate_projection(payload([{'unit_id':u['id']} for u in units],relation='correction'),units)
    assert selected==result
    with pytest.raises(store.TextError):validate_projection(payload([{'unit_id':'u999999'}]),units)
    short=[dict(refs[0],quote='支持API')]
    out=validate_projection(payload(short,section='客户需求与产品适配'),units)
    assert '不支持API' in out['客户需求与产品适配'][0]['text']
    with pytest.raises(store.TextError):validate_projection(payload(refs,kind='agreed',section='已达成事项'),units)


def test_v3_does_not_reuse_legacy_clean_as_semantically_reviewed(tmp_path):
    storage.set_data_dir(tmp_path);mid='a'*32;raw='客户：预算20万尚未批准。'
    record,_=store.import_markdown('user-a',mid,'demo.md',raw.encode(),'demo')
    model=ScriptedModel(fail_at=('extracting',0));jobs=Jobs(model);jobs.start('user-a',mid);wait(jobs);jobs.shutdown()
    directory=store.meeting_dir('user-a',mid);job=json.loads((directory/'job.json').read_text())
    old_hash=fingerprint([plan(raw),LEGACY_ENGINE,job['source_sha256']]);job['plan_hash']=old_hash
    atomic_json(directory/'job.json',job)
    cp=directory/'checkpoints/cleaning-00000.json';saved=json.loads(cp.read_text());saved['plan_hash']=old_hash;atomic_json(cp,saved)
    model=ScriptedModel();jobs=Jobs(model)
    before=cp.read_bytes()
    with pytest.raises(store.TextError) as error:jobs.start('user-a',mid)
    assert error.value.code=='PROCESSING_VERSION_CHANGED'
    assert model.calls==[]
    assert cp.read_bytes()==before
    assert json.loads(cp.read_text())['payload']==saved['payload']
    jobs.shutdown()


def test_whole_source_classification_once_keeps_all_chunks(tmp_path):
    storage.set_data_dir(tmp_path);mid='b'*32
    raw=('客户：预算20万尚未批准。\n销售：能力待验证。\n')*35
    store.import_markdown('user-a',mid,'long.md',raw.encode(),'完整稿')
    model=ScriptedModel();jobs=Jobs(model);jobs.start('user-a',mid);wait(jobs)
    view=jobs.view('user-a',mid)
    assert view['job']['state']=='succeeded'
    assert len(plan(raw)['chunks'])>1
    assert [c for c in model.calls if c[0]=='extracting']==[('extracting',0)]
    cited={r['unit_id'] for items in view['result']['sections'].values() for i in items for r in i['evidence']}
    assert cited=={u['id'] for u in plan(raw)['units'] if u['text'].strip()}
    assert view['job']['completed_extract']==len(plan(raw)['chunks'])
    assert (store.meeting_dir('user-a',mid)/'checkpoints/extracting-00000.json').exists()
    calls=list(model.calls);jobs.shutdown();jobs=Jobs(model)
    assert jobs.view('user-a',mid)['result']==view['result']
    jobs.start('user-a',mid);assert model.calls==calls
    jobs.shutdown()


def test_correction_needs_explicit_source_not_just_multiple_references():
    units=plan('甲：原先说月底上线。\n乙：我没说一定在月底上线。我说的是月底可以看看试用条件。')['units']
    refs=[{'unit_id':u['id']} for u in units]
    assert len(validate_projection(payload(refs,relation='correction'),units)['预算与时间'][0]['evidence'])==2
    other=plan('甲：讨论预算。\n乙：这是另一个议题。')['units']
    with pytest.raises(store.TextError) as error:
        validate_projection(payload([{'unit_id':u['id']} for u in other],relation='correction'),other)
    assert error.value.code=='INVALID_RELATION'
