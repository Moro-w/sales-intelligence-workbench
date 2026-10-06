#!/usr/bin/env python3
"""Explicit offline UI sample. Not test material, not a model result, never uses a key."""
from pathlib import Path
import json
import sys
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Tingji'))
from app import storage,text_storage as store
from app.text_processing.products import Products
from app.text_processing.model import atomic_json
from app.text_processing.contracts import plan,SECTIONS
from app.text_processing.board_first import source_anchors,build_draft

SOURCE='''【界面样例：手工静态展示，非真实客户会议，非AI生成结果】
刘经理：我们是虚构的南桥零售，目前36家门店，年底可能再开4家，还没批准。
刘经理：我们想换订货和库存系统，门店现在反复在群里报数，盘点差异要手工核对。
刘经理：预算先按三十万元估吧。
刘经理：不是三十万元，预算改为二十二到二十六万元，这是首年软件和实施的内部估算，还没批，税费是否包含要问财务。
销售林越：我们可以提供订货和库存同步接口，断网后的补传能力要通过演示验证，不承诺现在已经满足。
嗯先别接生产系统 可以考虑拿两家门店做试点 但这只是评估意向啊 不是同意购买
刘经理：补充一下刚才的试点条件，最多2家门店、5个测试账号，只用虚构商品资料，不接生产ERP，库存差异核对通过后再评估是否扩大。
刘经理：采购要先做业务试点评估，再让IT看安全和接口，财务审批预算以后走合同审查，最终签字人还没定。
刘经理：我同意先准备演示资料，但没有同意采购，也没有确定正式上线日期。
销售林越：我负责在周四下班前把演示安排和接口清单发给刘经理。
那个历史库存资料先谁来准备 这个还没定 下次碰头再确认一下
'''

class NoModel:
    def status(self,*args):return {'enabled':False,'reason':'静态界面样例，无模型调用。'}
    def call(self,*args,**kwargs):raise AssertionError('UI sample must never call a model')


def sample_payload():
    units=plan(SOURCE)['units'];aa=source_anchors(units)
    def refs(line,*parts):
        found=[a['id'] for a in aa if a['line']==line and (not parts or any(p in a['quote'] for p in parts))]
        assert found,(line,parts)
        return found
    def condition(text,line,*parts):return {'text':text,'evidence':refs(line,*parts)}
    sections={s:[] for s in SECTIONS}
    def add(section,topic,label,text,speaker,line,*,parts=(),kind='reported',conditions=None,history=None):
        sections[section].append({'id':f'sample-{sum(map(len,sections.values()))}', 'topic':topic,'label':label,'text':text,'speaker':speaker,
            'kind':kind,'evidence':refs(line,*parts),'conditions':conditions or [],'history':history or []})
    add('会议概况','meeting','客户','南桥零售（虚构客户）','刘经理',2,parts=('我们是',))
    add('会议概况','organization','门店规模','现有36家门店；年底可能新增4家，尚未批准。','刘经理',2)
    add('客户需求与产品适配','need','订货与库存','希望更换订货与库存系统，减少群内反复报数和手工核对盘点差异。','刘经理',3)
    add('客户需求与产品适配','capability','接口与离线补传','供应商自述可提供订货与库存同步接口；离线补传能否满足需求待演示验证。','销售林越',6,kind='sales_claim')
    add('预算与时间','budget','当前预算','首年软件及实施估算22—26万元，未获批。','刘经理',5,parts=('预算改为','首年','还没批'),kind='tentative',
        conditions=[condition('税费是否包含，待财务确认。',5,'税费')],
        history=[{'text':'原先按30万元估算，已更正。','evidence':refs(4),'correction':refs(5,'预算改为')}])
    add('预算与时间','timing','上线日期','正式上线日期尚未确定。','刘经理',10,parts=('上线日期',),kind='tentative')
    add('决策与采购流程','procurement','采购步骤','业务试点评估 → IT安全及接口评估 → 财务预算审批 → 合同审查；最终签字人未定。','刘经理',9)
    add('待确认问题','agreement','有条件的试点评估意向','可以考虑试点，尚未同意购买；须满足下列全部前提。','未标明',7,kind='tentative',conditions=[
        condition('最多2家门店、5个测试账号。',8,'2家','5个'),condition('仅用虚构商品资料，不接生产ERP。',8,'虚构','生产ERP'),
        condition('库存差异核对通过后，再评估是否扩大。',8,'库存差异')])
    add('已达成事项','agreement','准备演示','刘经理明确同意先准备演示资料，仅限这一准备事项。','刘经理',10,parts=('同意先准备',),kind='agreed')
    add('待确认问题','open_question','资料负责人','历史库存资料由谁准备，尚未确定，留待下次沟通。','未标明',12,kind='tentative')
    add('后续行动','action','林越 · 周四下班前','林越在周四下班前把演示安排和接口清单发给刘经理。','销售林越',11)
    return build_draft([{'sections':sections}],units)


def main():
    manifest=ROOT/'.runtime/evidence/board-first-sample.json'
    if manifest.exists():
        print('Sample already installed; retained without overwriting.');return
    storage.set_data_dir(str(ROOT/'.runtime/tingji'))
    owner='system_default_user';mid=uuid.uuid4().hex
    payload=sample_payload()
    assert not [i for i in payload['issues'] if i['severity']=='blocking'],payload['issues']
    meta,_=store.import_markdown(owner,mid,'界面样例_非验收材料.md',SOURCE.encode(),'界面样例｜南桥零售 · 订货与库存沟通')
    meta['sample']=True;atomic_json(store.meeting_dir(owner,mid)/'meta.json',meta)
    jobs=Products(NoModel())
    try:
        payload.update(processing_complete=True,processed_batches=0,total_batches=0)
        board=jobs.publish(owner,mid,'board',payload,meta['source_sha256'],origin='sample')
        atomic_json(manifest,{'meeting_id':mid,'version':board['version'],'source_sha256':meta['source_sha256'],
                  'url':f'http://sales-agent.localhost:25849/#/meetings/{mid}','model_calls':0,'origin':'static-ui-sample'})
    finally:jobs.shutdown()
    print(json.dumps({'sample_meeting_id':mid,'fields':payload['usable_fields'],'model_calls':0},ensure_ascii=False))

if __name__=='__main__':main()
