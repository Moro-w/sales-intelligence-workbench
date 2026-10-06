"""Explicit human edits. No model, no mutation of original or generated versions."""
import copy
import time
import uuid
from app import text_storage as store
from .contracts import SECTIONS, fingerprint
from .jobs import lease, read_json
from .model import atomic_json


def text(value,limit=12000):
    if not isinstance(value,str) or not value.strip() or len(value)>limit or any(ord(c)<32 and c not in '\n\r\t' for c in value):
        raise store.TextError('INVALID_EDIT','编辑内容为空、过长或含非法字符。')
    return value


def payload(current,patch):
    if not isinstance(patch,dict):raise store.TextError('INVALID_EDIT','编辑格式无效。')
    result=copy.deepcopy(current)
    if current['product']=='clean':
        if set(patch)!={'processed'}:raise store.TextError('INVALID_EDIT','仅可编辑清洗稿正文。')
        result['processed']=text(patch['processed'],store.MAX_SOURCE_BYTES)
        result['unprocessed_units']=[r['id'] for r in current.get('rows',[]) if r.get('cleaned') is None and r['text'].strip()] or current.get('unprocessed_units',[])
        result.pop('rows',None)
        result['source_mapping_state']='human_edit_not_revalidated'
    else:
        if set(patch)!={'fields'} or not isinstance(patch['fields'],list) or len(patch['fields'])>500:
            raise store.TextError('INVALID_EDIT','看板编辑格式无效。')
        lookup={f['id']:(s,f) for s,items in result['sections'].items() for f in items};seen=set()
        for row in patch['fields']:
            if not isinstance(row,dict) or set(row)-{'id','section','label','text','conditions','history','delete'}:
                raise store.TextError('INVALID_EDIT','不能写入或伪造原文依据。')
            fid=row.get('id')
            if fid is not None:
                if not isinstance(fid,str) or fid not in lookup or fid in seen:raise store.TextError('INVALID_EDIT','事项编号重复或不存在。')
                seen.add(fid);section,field=lookup[fid]
                if row.get('delete') is True:
                    result['sections'][section].remove(field);continue
            else:
                section=row.get('section')
                if section not in SECTIONS:raise store.TextError('INVALID_EDIT','请选择已有信息区。')
                field={'id':'human-'+uuid.uuid4().hex,'topic':'human','evidence':[],'conditions':[],'history':[]}
                result['sections'][section].append(field)
            values={'label':text(row.get('label') or '人工补充',120),'text':text(row.get('text'))}
            for key in ('conditions','history'):
                entries=row.get(key,[])
                if not isinstance(entries,list) or len(entries)>50:raise store.TextError('INVALID_EDIT','条件或历史格式无效。')
                values[key]=[text(v) for v in entries]
            unchanged=(fid is not None and all(field.get(k,'')==values[k] for k in ('label','text')) and all([v['text'] for v in field.get(k,[])]==values[k] for k in ('conditions','history')))
            if unchanged:continue
            field.update(label=values['label'],text=values['text'],human_edited=True,human_verified=False,
                         kind='human_added',speaker='未标明',review_state='pending',issues=[{'code':'HUMAN_EDIT','message':'人工修改；原有依据仅供复核，未验证修改后结论。'}])
            for key in ('conditions','history'):field[key]=[{'text':v,'evidence':[]} for v in values[key]]
        result['usable_fields']=sum(len(v) for v in result['sections'].values())
    result.update(human_edited=True,complete=False,human_verified=False,parent_version=current['version'])
    return result


def draft(jobs,owner,mid,product):
    directory=jobs.directory(owner,mid,product);path=directory/'draft.json'
    if not path.exists():return None
    value=read_json(path)
    if value.get('source_sha256')!=store.get_meeting(owner,mid)['meta']['source_sha256'] or value.get('product')!=product or fingerprint(value.get('patch'))!=value.get('patch_sha256'):
        raise store.TextError('RESULT_CHANGED','编辑草稿校验失败，未继续使用。',409)
    return value


def candidate(jobs,owner,mid,product,source_sha):
    pointer=jobs.state(owner,mid,product).get('candidate')
    if not pointer:return None
    version=pointer.get('version','')
    if not isinstance(version,str) or not store.VALID_ID.fullmatch(version):raise store.TextError('RESULT_CHANGED','更新建议索引无效。',409)
    value=read_json(jobs.directory(owner,mid,product)/'versions'/f'{version}.json')
    if fingerprint(value)!=pointer.get('sha256') or value.get('source_sha256')!=source_sha or value.get('product')!=product:
        raise store.TextError('RESULT_CHANGED','更新建议校验失败。',409)
    return value


def decide(jobs,owner,mid,body):
    if not isinstance(body,dict) or set(body)!={'product','base_version','candidate_version','adopt'} or not isinstance(body['adopt'],bool):
        raise store.TextError('INVALID_EDIT','建议处理请求无效。')
    product=body['product'];directory=jobs.directory(owner,mid,product);source_sha=store.get_meeting(owner,mid)['meta']['source_sha256']
    with lease(directory):
        current=jobs.artifact(owner,mid,product,source_sha);suggestion=candidate(jobs,owner,mid,product,source_sha)
        if not current or current['version']!=body['base_version'] or not suggestion or suggestion['version']!=body['candidate_version']:
            raise store.TextError('RESULT_CHANGED','版本已变化，请重新比较。',409)
        if draft(jobs,owner,mid,product):raise store.TextError('DRAFT_CHANGED','有编辑草稿，请先保存再比较。',409)
        if body['adopt']:
            if not suggestion.get('processing_complete'):raise store.TextError('PROCESSING_INCOMPLETE','建议尚不完整，未替换当前版本。',409)
            atomic_json(directory/'current.json',{'version':suggestion['version'],'sha256':fingerprint(suggestion)})
        job=jobs.state(owner,mid,product);job['reviewed_candidate']=job.pop('candidate');job['candidate_decision']='adopted' if body['adopt'] else 'kept'
        atomic_json(directory/'job.json',job)
        return {'saved':True}


def save(jobs,owner,mid,body,*,draft_only=False):
    if not isinstance(body,dict) or set(body)-{'product','base_version','patch','draft_version'}:
        raise store.TextError('INVALID_EDIT','保存请求格式无效。')
    product=body.get('product');directory=jobs.directory(owner,mid,product)
    record=store.get_meeting(owner,mid)
    with lease(directory):
        current=jobs.artifact(owner,mid,product,record['meta']['source_sha256'])
        if not current:raise store.TextError('RESULT_NOT_READY','该产物尚未生成，不能用人工内容冒充生成结果。',409)
        if current['version']!=body.get('base_version'):raise store.TextError('RESULT_CHANGED','保存版本已变化，保留你的输入；请比较后再保存。',409)
        prior=draft(jobs,owner,mid,product)
        if (prior or {}).get('draft_version')!=body.get('draft_version'):raise store.TextError('DRAFT_CHANGED','另一处编辑已保存草稿，未覆盖。请重新打开比较。',409)
        if prior and prior.get('base_version')!=current['version']:
            raise store.TextError('DRAFT_CHANGED','草稿基于旧版本。请先比较或另存旧草稿，不能静默覆盖。',409)
        value=payload(current,body.get('patch'))
        if draft_only:
            saved={'product':product,'base_version':current['version'],'source_sha256':record['meta']['source_sha256'],
                   'patch':body['patch'],'patch_sha256':fingerprint(body['patch']),'draft_version':uuid.uuid4().hex,'saved_at':time.time()}
            atomic_json(directory/'draft.json',saved);return saved
        if product=='board':
            clean=jobs.artifact(owner,mid,'clean',record['meta']['source_sha256'])
            value['based_on_clean_version']=(clean or {}).get('version')
        result=jobs.publish(owner,mid,product,value,record['meta']['source_sha256'],origin='human')
        # Keep a recoverable draft history; the old draft is not a current edit.
        if prior:
            atomic_json(directory/'edit-history'/f"{prior['draft_version']}.json",prior)
            (directory/'draft.json').unlink()
        return result
