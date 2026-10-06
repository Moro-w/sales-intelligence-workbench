"""No real network: exercise the exact production budget/HTTP adapter using MockTransport."""
import json
import time
import httpx
import pytest
from app.text_processing.model import BudgetedModel, atomic_json, OUTPUT_LIMIT
from app.text_storage import TextError


@pytest.fixture
def grant(tmp_path):
    path=tmp_path/'meeting-model-grant.json'
    value={'enabled':True,'batch_id':'offline-batch-001','api_key':'offline-not-a-real-key','owner':'user-a',
           'model':'deepseek-flash','source_hashes':['b'*64],'expires_at':time.time()+3600,
           'max_requests':4,'max_input':100000,'max_output':40000,'max_microyuan':500000,
           'input_price_microyuan':2,'output_price_microyuan':8}
    atomic_json(path,value)
    return path


def invoke(model):return model.call([{'role':'user','content':'PRIVATE TEST CONTENT'}],'user-a','b'*64,'cleaning',0)
def ledger(path):return json.loads(path.with_name('meeting-model-ledger.json').read_text())
def reply(usage=None,finish='stop',content='{"rows":[]}'):
    return {'choices':[{'finish_reason':finish,'message':{'content':content}}], 'usage':usage if usage is not None else {'prompt_tokens':20,'completion_tokens':10}}


def test_invalid_response_saved_privately_with_key_redacted_without_refunding_failure(grant):
    value=reply(content='{bad JSON offline-not-a-real-key')
    with pytest.raises(TextError) as error:invoke(BudgetedModel(grant,httpx.MockTransport(lambda _:httpx.Response(200,json=value))))
    assert error.value.code=='MODEL_INVALID_RESPONSE'
    path=next(grant.parent.glob('meeting-model-responses/*/0001.json'))
    assert path.stat().st_mode & 0o777==0o600
    assert 'offline-not-a-real-key' not in path.read_text() and '[REDACTED_API_KEY]' in path.read_text()
    assert ledger(grant)['requests'][0]['charged_output']==OUTPUT_LIMIT


def test_disabled_does_not_send_even_with_http_transport():
    calls=[];model=BudgetedModel(transport=httpx.MockTransport(lambda request:calls.append(request)))
    with pytest.raises(TextError) as e:invoke(model)
    assert e.value.code=='MODEL_NOT_AUTHORIZED' and calls==[]


def test_reservation_is_durable_before_network_and_ledger_contains_no_secrets(grant):
    def transport(request):
        assert ledger(grant)['requests'][0]['status']=='reserved'
        assert request.url=='https://api.deepseek.com/chat/completions'
        data=json.loads(request.content)
        assert data['thinking']=={'type':'disabled'} and data['max_tokens']==OUTPUT_LIMIT
        return httpx.Response(200,json=reply())
    assert invoke(BudgetedModel(grant,httpx.MockTransport(transport)))=={'rows':[]}
    text=grant.with_name('meeting-model-ledger.json').read_text()
    assert 'PRIVATE TEST CONTENT' not in text and 'offline-not-a-real-key' not in text
    row=ledger(grant)['requests'][0]
    assert row['charged_input']==20 and row['charged_output']==10 and row['status']=='ok'


@pytest.mark.parametrize('change,code',[
    ({'source_hashes':['c'*64]},'MATERIAL_NOT_AUTHORIZED'),
    ({'owner':'user-b'},'MATERIAL_NOT_AUTHORIZED'),
    ({'expires_at':0},'MODEL_NOT_AUTHORIZED'),
    ({'enabled':False},'MODEL_NOT_AUTHORIZED'),
    ({'model':'other'},'MODEL_NOT_AUTHORIZED'),
    ({'max_input':10},'BUDGET_EXHAUSTED'),
    ({'max_output':10},'BUDGET_EXHAUSTED'),
    ({'max_microyuan':1},'BUDGET_EXHAUSTED')])
def test_preflight_guards_before_network(grant,change,code):
    value=json.loads(grant.read_text());value.update(change);atomic_json(grant,value)
    calls=[]
    model=BudgetedModel(grant,httpx.MockTransport(lambda request:calls.append(request)))
    with pytest.raises(TextError) as e:invoke(model)
    assert e.value.code==code and calls==[]


def test_failures_and_manual_retries_count_and_remain_reserved(grant):
    def timeout(request):raise httpx.ReadTimeout('must not expose raw response or key')
    model=BudgetedModel(grant,httpx.MockTransport(timeout))
    for _ in range(4):
        with pytest.raises(TextError) as e:invoke(model)
        assert e.value.code=='MODEL_TIMEOUT'
        assert 'must not expose' not in e.value.message
    state=ledger(grant)
    assert len(state['requests'])==4
    assert all(r['charged_input']==r['reserved_input'] and r['charged_output']==OUTPUT_LIMIT for r in state['requests'])
    with pytest.raises(TextError) as e:invoke(BudgetedModel(grant,httpx.MockTransport(timeout)))
    assert e.value.code=='BUDGET_EXHAUSTED'


@pytest.mark.parametrize('response,code,halted',[
    (reply({},'stop'),'MODEL_USAGE_MISSING',True),
    (reply({'prompt_tokens':0,'completion_tokens':0}),'MODEL_USAGE_MISSING',True),
    (reply({'prompt_tokens':999999,'completion_tokens':10}),'MODEL_USAGE_BOUND',True),
    (reply(finish='length'),'MODEL_TRUNCATED',False),
    ([], 'MODEL_INVALID_RESPONSE',False),
    (reply(content='{"rows":[],"rows":[]}'),'MODEL_INVALID_RESPONSE',False),
    (reply(content='{"value":NaN}'),'MODEL_INVALID_RESPONSE',False),
    (reply(content='{broken'),'MODEL_INVALID_RESPONSE',False)])
def test_bad_or_truncated_response_not_accepted(grant,response,code,halted):
    model=BudgetedModel(grant,httpx.MockTransport(lambda _:httpx.Response(200,json=response)))
    with pytest.raises(TextError) as e:invoke(model)
    assert e.value.code==code
    assert ledger(grant)['halted'] is halted
    assert ledger(grant)['requests'][0]['charged_output']==OUTPUT_LIMIT


def test_redirect_does_not_forward_credentials(grant):
    calls=[]
    def redirect(request):
        calls.append(str(request.url));return httpx.Response(302,headers={'location':'https://evil.invalid/key'})
    with pytest.raises(TextError) as e:invoke(BudgetedModel(grant,httpx.MockTransport(redirect)))
    assert e.value.code=='MODEL_HTTP_ERROR'
    assert calls==['https://api.deepseek.com/chat/completions']


def test_changed_batch_does_not_reset_old_ledger(grant):
    invoke(BudgetedModel(grant,httpx.MockTransport(lambda _:httpx.Response(200,json=reply()))))
    value=json.loads(grant.read_text());value['batch_id']='different-batch';atomic_json(grant,value)
    with pytest.raises(TextError) as e:invoke(BudgetedModel(grant))
    assert e.value.code=='BUDGET_CLOSED'


def test_cut_at_max_tokens_one_over_is_truncation_not_a_batch_halt(grant):
    response=reply({'prompt_tokens':20,'completion_tokens':OUTPUT_LIMIT+1},'length')
    with pytest.raises(TextError) as e:invoke(BudgetedModel(grant,httpx.MockTransport(lambda _:httpx.Response(200,json=response))))
    assert e.value.code=='MODEL_TRUNCATED' and ledger(grant)['halted'] is False
    assert ledger(grant)['requests'][0]['charged_output']==OUTPUT_LIMIT+1, 'charged in full, never refunded'


def test_unexplained_output_overrun_still_halts(grant):
    response=reply({'prompt_tokens':20,'completion_tokens':OUTPUT_LIMIT+500},'stop')
    with pytest.raises(TextError) as e:invoke(BudgetedModel(grant,httpx.MockTransport(lambda _:httpx.Response(200,json=response))))
    assert e.value.code=='MODEL_USAGE_BOUND' and ledger(grant)['halted'] is True


def test_board_stage_gets_larger_output_cap():
    from app.text_processing.model import STAGE_OUTPUT_LIMIT
    assert STAGE_OUTPUT_LIMIT['board_draft']>OUTPUT_LIMIT and 'clean_draft' not in STAGE_OUTPUT_LIMIT
