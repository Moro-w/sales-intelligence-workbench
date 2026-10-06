"""Read-only sales-card and aggregate status projections; never model calls."""
import re
from datetime import date
from .products import PRODUCTS, ACTIVE


def completion(record):
    products=record['products']
    ready={p:bool(record.get(p) and record[p].get('processing_complete') is True) for p in PRODUCTS}
    running=any(products[p]['state'] in ACTIVE for p in PRODUCTS)
    failed=any(products[p]['state'] in ('failed','interrupted') for p in PRODUCTS)
    # A previous saved artifact does not hide a failed/active regeneration.
    state='processing' if running else 'partial' if failed and any(ready.values()) else 'failed' if failed else 'generated' if all(ready.values()) else 'partial' if any(record.get(p) for p in PRODUCTS) else 'idle'
    if record['meta'].get('sample'):state='sample'
    elif state=='generated' and any(record[p].get('human_edited') for p in PRODUCTS):state='saved'
    labels={'processing':'处理中','partial':'部分完成','failed':'处理未完成','generated':'看板已生成','saved':'已人工修改','sample':'工程样例 · 非AI生成','idle':'原稿已保存 · 尚未处理'}
    return {'state':state,'label':labels[state],'products_ready':ready,'all_generated':state=='generated','human_verified':False}


def sales_card(record):
    board=record.get('board') or {};sections=board.get('sections',{})
    fields=[f for rows in sections.values() for f in rows if f.get('review_state')!='blocked']
    def pick(topic):return next((f for f in fields if f.get('topic')==topic),None)
    def text(item):
        if not item:return ''
        # Never substring-cut a conclusion or separate its conditions to fit a card.
        chunks=[item['text']]+[c['text'] for c in item.get('conditions',[])]
        return ('人工修改，待核对：' if item.get('human_edited') else '')+'；'.join(chunks)
    needs=next((f for f in sections.get('客户需求与产品适配',[]) if f.get('topic')=='need' and f.get('review_state')!='blocked'),None)
    pending=[f for f in sections.get('待确认问题',[]) if f.get('review_state')!='blocked']
    budget=pick('budget');obstacles=[]
    # Surface unresolved budget qualifiers without inventing an approval state.
    if budget and (budget.get('kind') in ('tentative','conflict') or re.search(r'未|没|待|尚|不足|缺口|不含|超预算|需.*(?:确认|财务|审批)',text(budget))):
        obstacles.append(text(budget))
    if pending:obstacles.append(text(pending[0]))
    actions=[text(f) for f in sections.get('后续行动',[]) if f.get('review_state')!='blocked']
    dates=set()
    for match in re.finditer(r'^\s*(?:会议日期|会议时间)\s*[:：]\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?=\s|$)',record['source_text'],re.M):
        try:dates.add(date(*map(int,match.groups())).isoformat())
        except ValueError:pass
    meeting_date=next(iter(dates)) if len(dates)==1 else None
    # Short headline points for the card grid: model labels (numbers already source-checked), never cut text.
    def head(item):return (item.get('label') or item['text']) if item else ''
    def unapproved(item):return bool(item) and bool(re.search(r'不是已批|未批|没批|未审批|尚未审批|未获批',item['text']))
    need_points=[head(f) for f in sections.get('客户需求与产品适配',[]) if f.get('topic')=='need' and f.get('review_state')!='blocked'][:3]
    obstacle_points=[]
    if budget:obstacle_points.append(head(budget)+(' · 未审批' if unapproved(budget) else ''))
    obstacle_points+=[head(f) for f in pending][:3-len(obstacle_points)]
    action_items=[f for f in sections.get('后续行动',[]) if f.get('review_state')!='blocked']
    def act(f):
        due=f.get('due');base=head(f)
        return base+(' · '+due if due and due!='未明确' and due not in base else '')
    meeting=pick('meeting');customer=(meeting or {}).get('customer') or board.get('customer')
    todos=[{'label':head(f),'owner':f.get('owner') if f.get('owner')!='未明确' else '','due':f.get('due') if f.get('due')!='未明确' else '',
            'confirmed':f.get('kind')=='agreed'} for f in action_items]
    return {'todos':todos,'pending_count':len(pending),
            'need_count':sum(1 for f in sections.get('客户需求与产品适配',[]) if f.get('review_state')!='blocked'),
            'customer_name':customer if customer and customer!='未明确' else '',
            'need_points':need_points,'obstacle_points':obstacle_points,'next_points':[act(f) for f in action_items[:2]],
            'more_actions':max(0,len(action_items)-2),
            'customer':text(pick('meeting')),'need':text(needs),'obstacle':'；'.join(obstacles),
            'next_action':(actions[0]+(f'；另有{len(actions)-1}项行动，打开会议查看。' if len(actions)>1 else '') if actions else ''),'meeting_date':meeting_date,
            'date_label':meeting_date or '会议日期待核对','board_version':board.get('version')}
