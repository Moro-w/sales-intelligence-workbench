"""MockTransport only; new product scope cannot spend old/other product grants."""
import json
import time
import httpx
import pytest
from app.text_processing.model import BudgetedModel,atomic_json
from app.text_storage import TextError

@pytest.fixture
def grant(tmp_path):
    path=tmp_path/'meeting-model-grant.json'
    atomic_json(path,{'enabled':True,'batch_id':'offline-scope','api_key':'offline-scope-only','owner':'offline',
        'model':'deepseek-flash','source_hashes':['a'*64],'expires_at':time.time()+3600,'max_requests':5,'max_input':100000,
        'max_output':45000,'max_microyuan':600000,'input_price_microyuan':2,'output_price_microyuan':8})
    return path

def call(model,stage):return model.call([{'role':'user','content':'offline'}],'offline','a'*64,stage,0)

@pytest.mark.parametrize('stage',['board_draft','board_merge','clean_draft'])
def test_old_grant_cannot_enable_new_pipeline(grant,stage):
    calls=[];model=BudgetedModel(grant,httpx.MockTransport(lambda r:calls.append(r)))
    with pytest.raises(TextError) as exc:call(model,stage)
    assert exc.value.code=='PRODUCT_NOT_AUTHORIZED' and not calls
    assert not grant.with_name('meeting-model-ledger.json').exists()

def test_board_only_authorization_does_not_allow_clean_or_legacy(grant):
    data=json.loads(grant.read_text());data.update(processing_protocol='sales-board-first-v1',products=['board']);atomic_json(grant,data)
    calls=[]
    def respond(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"sections":{}}'}}],
                                       'usage':{'prompt_tokens':10,'completion_tokens':5}})
    model=BudgetedModel(grant,httpx.MockTransport(respond))
    for stage in ['clean_draft','cleaning','extracting']:
        with pytest.raises(TextError) as exc:call(model,stage)
        assert exc.value.code=='PRODUCT_NOT_AUTHORIZED'
    assert not calls
    call(model,'board_draft');call(model,'board_merge')
    assert len(calls)==2
    ledger=json.loads(grant.with_name('meeting-model-ledger.json').read_text())
    assert [r['stage'] for r in ledger['requests']]==['board_draft','board_merge']
