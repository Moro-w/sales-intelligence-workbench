"""Independent board/clean products on Tingji's existing queue and storage primitives.
Legacy files are read-only. Draft publication is NOT semantic acceptance.
"""
import time
import uuid
from app import text_storage as store, storage
from .jobs import Jobs, read_json, lease
from .contracts import plan, extraction_batches, fingerprint, object_keys, SECTIONS, fail
from .model import atomic_json
from .semantic import validate_clean_candidate
from .semantic_prompts import messages, POLISH
from .board_first import PROTOCOL, PROMPT, CLEAN_PROMPT, build_draft

PRODUCTS=('board','clean')
ACTIVE=('queued','running')
# Serial workflow: one confirmation -> clean the full transcript -> extract the board FROM the clean
# transcript (raw clauses remain the only citable evidence) -> publish. No raw-only fallback.
WORKFLOW='clean-then-board-v1'
ENGINE=fingerprint([PROTOCOL,PROMPT,CLEAN_PROMPT,POLISH,'numeric-speaker-validation-2','products-2-bounded-merge',WORKFLOW,'bracket-speakers-1'])


def clean_rows(value,chunk):
    """Structure is strict; a few unreliable rows keep the raw text and are marked for review."""
    rows=value.get('rows') if isinstance(value,dict) and set(value)=={'rows'} else None
    if (not isinstance(rows,list) or len(rows)!=len(chunk)
        or any(not isinstance(r,dict) or r.get('id')!=u['id'] for r,u in zip(rows,chunk))):
        fail('COVERAGE_ERROR','整理稿单元不完整或顺序错误。')
    out=[];kept=[]
    for row,unit in zip(rows,chunk):
        try:out.extend(validate_clean_candidate({'rows':[row]},[unit]))
        except store.TextError as exc:
            kept.append({'unit_id':unit['id'],'line':unit['line'],'code':exc.code,'message':exc.message})
    nonblank=sum(1 for u in chunk if u['text'].strip())
    if len(kept)*4>max(1,nonblank):
        fail('CLEAN_QUALITY',f'本片段有{len(kept)}/{nonblank}段未通过数字、发言人或扩写校验，未保存为清洗成果；可重试当前片段。')
    return out,kept


def board_shape(value):
    if not isinstance(value,dict) or set(value)-{'sections','coverage'}:
        raise store.TextError('INVALID_OUTPUT','看板结构无效。')
    object_keys(value.get('sections'),SECTIONS)
    if any(not isinstance(items,list) or len(items)>120 for items in value['sections'].values()):
        raise store.TextError('INVALID_OUTPUT','看板事项列表结构无效。')
    return value

def board_payload(value):
    """Accept the seven sections returned without the 'sections' wrapper; content is untouched."""
    if isinstance(value,dict) and value and set(value)==set(SECTIONS):value={'sections':value}
    return board_shape(value)


class Products:
    def __init__(self,model):
        self.legacy=Jobs(model);self.model=model;self.pool=self.legacy.pool;self.lock=self.legacy.lock
        self.closing=self.legacy.closing;self.active=set();self.failed_states={}
        for path in storage.get_data_dir().glob('users/*/*/products/*/job.json'):
            if path.parent.name not in PRODUCTS:continue
            job=None
            try:
                with lease(path.parent):
                    job=read_json(path)
                    if job.get('state') in ACTIVE:
                        job.update(state='interrupted',error={'code':'SERVICE_RESTARTED','message':'任务中断，已保存片段可继续，不会自动重跑。'})
                        atomic_json(path,job)
            except store.TextError:continue
            except OSError:
                if isinstance(job,dict):self.failed_states[str(path)]=job

    def directory(self,owner,mid,product):
        if product not in PRODUCTS:raise store.TextError('INVALID_REQUEST','仅支持看板或清洗任务。')
        return store.meeting_dir(owner,mid)/'products'/product

    def state(self,owner,mid,product='board'):
        path=self.directory(owner,mid,product)/'job.json'
        if str(path) in self.failed_states:return dict(self.failed_states[str(path)])
        if not path.exists():return {'state':'idle','product':product,'done':0,'total':0,'error':None,'last_error':None,'attempt':0}
        job=read_json(path)
        if job.get('protocol')!=PROTOCOL or job.get('product')!=product or job.get('state') not in (*ACTIVE,'draft','failed','interrupted','waiting'):
            raise store.TextError('CHECKPOINT_CORRUPT','独立产物任务记录损坏。',409)
        return job

    def artifact(self,owner,mid,product,source_sha):
        directory=self.directory(owner,mid,product);pointer=directory/'current.json'
        if not pointer.exists():return None
        current=read_json(pointer);version=current.get('version','')
        if not isinstance(version,str) or not store.VALID_ID.fullmatch(version):raise store.TextError('RESULT_CHANGED','版本索引损坏。',409)
        value=read_json(directory/'versions'/f'{version}.json')
        if (fingerprint(value)!=current.get('sha256') or value.get('source_sha256')!=source_sha
            or value.get('product')!=product or value.get('version')!=version or value.get('protocol')!=PROTOCOL):
            raise store.TextError('RESULT_CHANGED','已保存产物校验失败，未继续展示。',409)
        return value

    def clean_source(self,owner,mid,source_sha,version=None):
        """The model clean version with per-unit mapping that the board is extracted from."""
        if version is None:
            current=self.artifact(owner,mid,'clean',source_sha)
            if current and current.get('rows') and current.get('processing_complete') is True:return current
            version=self.state(owner,mid,'clean').get('model_version')
        if not version:raise store.TextError('CLEAN_REQUIRED','销售看板从清洗稿提取，需先完成完整清洗稿。',409)
        if not isinstance(version,str) or not store.VALID_ID.fullmatch(version):raise store.TextError('RESULT_CHANGED','清洗版本索引损坏。',409)
        value=read_json(self.directory(owner,mid,'clean')/'versions'/f'{version}.json')
        if (value.get('source_sha256')!=source_sha or value.get('product')!='clean' or value.get('version')!=version
            or not isinstance(value.get('rows'),list) or value.get('processing_complete') is not True):
            raise store.TextError('CLEAN_REQUIRED','可用于提取的清洗稿不完整或校验失败，未生成看板。',409)
        return value

    def publish(self,owner,mid,product,payload,source_sha,*,origin='model',make_current=True):
        version=uuid.uuid4().hex;directory=self.directory(owner,mid,product)
        value={**payload,'version':version,'source_sha256':source_sha,'product':product,'origin':origin,
               'protocol':PROTOCOL,'state':'draft','human_verified':False,'saved_at':time.time()}
        version_path=directory/'versions'/f'{version}.json'
        if version_path.exists():raise store.TextError('RESULT_CHANGED','版本编号冲突，未覆盖已有成果。',409)
        atomic_json(version_path,value)
        if make_current:atomic_json(directory/'current.json',{'version':version,'sha256':fingerprint(value)})
        return value

    def gate(self,owner,record,product):
        if record['meta'].get('sample'):
            return {'enabled':False,'code':'SAMPLE_ONLY','reason':'界面样例为静态数据，不调用模型、不作为业务验收结果。'}
        if hasattr(self.model,'product_status'):
            gate=self.model.product_status(owner,record['meta']['source_sha256'],product,PROTOCOL)
            job=self.state(owner,record['meta']['id'],product)
            if (gate['enabled'] and (job.get('error') or {}).get('code')=='BUDGET_EXHAUSTED'
                and job.get('authorization_batch')==gate.get('batch_id')):
                return {**gate,'enabled':False,'code':'BUDGET_EXHAUSTED','reason':'AI 处理额度不足，已保留处理进度，开通后可继续。'}
            return gate
        return self.model.status(owner,record['meta']['source_sha256'])  # Offline test doubles only.

    def overview(self,owner,mid):
        record=store.get_meeting(owner,mid)
        return {p:{**self.state(owner,mid,p),'gate':self.gate(owner,record,p)} for p in PRODUCTS}

    def view(self,owner,mid):
        record=self.legacy.view(owner,mid)
        record['legacy_job']=record['job'];record['legacy_result']=record['result']
        record['products']=self.overview(owner,mid)
        for p in PRODUCTS:record[p]=self.artifact(owner,mid,p,record['meta']['source_sha256'])
        from .editing import draft,candidate
        record['edit_drafts']={p:draft(self,owner,mid,p) for p in PRODUCTS}
        record['candidates']={p:candidate(self,owner,mid,p,record['meta']['source_sha256']) for p in PRODUCTS}
        record['job']=record['products']['board'];record['model_gate']=record['job']['gate']
        return record

    def start_all(self,owner,mid):
        """One confirmation schedules both, without retrying an earlier failed job.

        Preflight every missing product before any work. A board-only test grant
        cannot accidentally start half of the promised first-run workflow.
        """
        record=store.get_meeting(owner,mid)
        with self.lock:
            needed=[p for p in PRODUCTS if self.state(owner,mid,p)['state']=='idle']
            for p in needed:
                gate=self.gate(owner,record,p)
                if not gate['enabled']:raise store.TextError(gate.get('code','MODEL_NOT_AUTHORIZED'),gate['reason'],409)
            if len(self.active)+len(needed)>8:raise store.TextError('QUEUE_FULL','等待任务较多，请稍后再试。',429)
            errors={};waiting=self.directory(owner,mid,'board')/'job.json'
            if 'clean' in needed and 'board' in needed:
                # Board waits for the full clean transcript; it is started by the clean worker.
                waiting.parent.mkdir(parents=True,exist_ok=True)
                with lease(waiting.parent):
                    atomic_json(waiting,{'protocol':PROTOCOL,'product':'board','state':'waiting','waiting_for':'clean',
                        'done':0,'total':0,'error':None,'last_error':None,'attempt':0,'updated_at':time.time()})
            first=['clean'] if 'clean' in needed else needed
            for p in first:
                try:self.start(owner,mid,p)
                except (store.TextError,OSError) as exc:
                    errors[p]={'code':getattr(exc,'code','STORAGE_FAILED'),'message':getattr(exc,'message','该产物未能开始，请单独重试。')}
                    if p=='clean' and 'board' in needed:
                        with lease(waiting.parent):waiting.unlink(missing_ok=True)
            return {'products':self.overview(owner,mid),'start_errors':errors,'workflow':WORKFLOW}

    def start(self,owner,mid,product='board',regenerate=False,base_version=None):
        record=store.get_meeting(owner,mid);directory=self.directory(owner,mid,product);key=(owner,mid,product)
        with self.lock:
            if key in self.active:return self.state(owner,mid,product)
            allowed=self.gate(owner,record,product)
            if not allowed['enabled']:raise store.TextError(allowed.get('code','MODEL_NOT_AUTHORIZED'),allowed['reason'],409)
            if self.closing.is_set():raise store.TextError('SERVICE_STOPPING','服务正在停止。',503)
            if len(self.active)>=8:raise store.TextError('QUEUE_FULL','等待任务较多，请稍后再试。',429)
            directory.mkdir(parents=True,exist_ok=True)
            with lease(directory):
                prior=self.state(owner,mid,product)
                if prior['state'] in ACTIVE:return prior
                artifact=self.artifact(owner,mid,product,record['meta']['source_sha256'])
                if regenerate and (artifact or {}).get('version')!=base_version:raise store.TextError('RESULT_CHANGED','保存版本已变化，请刷新后再操作。',409)
                if prior['state']=='draft' and not regenerate:return prior
                planning=plan(record['source_text']);clean_src=None
                if product=='board':
                    clean_src=self.clean_source(owner,mid,record['meta']['source_sha256'],None if regenerate else prior.get('clean_version'))
                ph=fingerprint([planning,ENGINE,product,record['meta']['source_sha256']]+([clean_src['version']] if clean_src else []))
                if not regenerate and prior.get('plan_hash',ph)!=ph and any((directory/'checkpoints').glob('*.json')):raise store.TextError('PROCESSING_VERSION_CHANGED','规则已变化，保留旧片段；请新建会议处理，不混用。',409)
                if regenerate or (prior.get('error') or {}).get('code')=='NO_USABLE_BOARD':
                    checkpoints=directory/'checkpoints'
                    paths=[checkpoints/'merge.json'] if not regenerate and (checkpoints/'merge.json').exists() else checkpoints.glob('*.json')
                    for path in paths:
                        # Preserve paid candidates before removing active checkpoint pointers.
                        saved=read_json(path)
                        atomic_json(directory/'rejected'/f'{time.time_ns()}-{path.name}',saved);path.unlink()
                batches=extraction_batches(planning['chunks']) if product=='board' else planning['chunks']
                job={**prior,'protocol':PROTOCOL,'product':product,'state':'queued','done':0,'total':len(batches)+(1 if product=='board' and len(batches)>1 else 0),'plan_hash':ph,
                     'source_sha256':record['meta']['source_sha256'],'attempt':prior['attempt']+1,'error':None,
                     'last_error':prior.get('error') or prior.get('last_error'),'authorization_batch':allowed.get('batch_id'),'updated_at':time.time()}
                job.pop('waiting_for',None)
                if clean_src:job['clean_version']=clean_src['version']
                atomic_json(directory/'job.json',job);self.failed_states.pop(str(directory/'job.json'),None);self.active.add(key)
            try:self.pool.submit(self._run,owner,mid,product,planning,job)
            except RuntimeError:
                self.active.discard(key);job.update(state='interrupted',error={'code':'SERVICE_STOPPING','message':'服务正在停止，未调用模型。'});atomic_json(directory/'job.json',job)
                raise store.TextError('SERVICE_STOPPING','服务正在停止，未调用模型。',503) from None
            return dict(job)

    def _publish_parts(self,owner,mid,product,planning,job,parts,kept=()):
        if product=='board':
            payload=build_draft(parts,planning['units'])
            payload.update(extraction_input='clean',based_on_clean_version=job.get('clean_version'))
        else:
            rows={row['id']:row['text'] for batch in parts for row in batch}
            payload={'processed':'\n'.join(rows.get(u['id'],u['text'].rstrip('\r\n')) for u in planning['units']),
                     'rows':[dict(u,cleaned=rows.get(u['id'])) for u in planning['units']],
                     'issues':[{'code':'CLEAN_REVIEW_PENDING','message':'整理草稿待核对；未整理片段仍保留原文。'}]}
            if kept:
                lines='、'.join(str(k['line']) for k in kept[:12])+('等' if len(kept)>12 else '')
                payload['issues'].append({'code':'CLEAN_ROW_KEPT_ORIGINAL','severity':'review',
                    'message':f'{len(kept)}段未能可靠清洗（数字、发言人或扩写校验未通过），已保留原文并标“未清洗”，含义待确认：第{lines}行。'})
                payload['kept_original_units']=list(kept)
        payload.update(processed_batches=job['done'],total_batches=job['total'],processing_complete=job['done']==job['total'])
        if product=='board' and job['done']<job['total']:
            payload['issues'].append({'code':'PROCESSING_INCOMPLETE','severity':'blocking','message':'全文处理或跨段合并尚未完成；当前仅为部分草稿。'})
        prior=self.artifact(owner,mid,product,job['source_sha256'])
        # An autosaved edit is also protected, even before explicit publication.
        has_edit_draft=(self.directory(owner,mid,product)/'draft.json').exists()
        make_current=not (prior and ((prior.get('processing_complete') and not payload['processing_complete']) or prior.get('human_edited') or has_edit_draft))
        result=self.publish(owner,mid,product,payload,job['source_sha256'],make_current=make_current)
        if not make_current:job['candidate']={'version':result['version'],'sha256':fingerprint(result)}
        if product=='clean' and payload['processing_complete']:job['model_version']=result['version']
        return result

    def revalidate(self,owner,mid,product='board'):
        """Re-check the saved model output with the current validators. Never calls a model;
        publishes a new version only when the saved checkpoints are complete and intact."""
        if product!='board':raise store.TextError('INVALID_REQUEST','仅看板支持重新校验。')
        record=store.get_meeting(owner,mid);directory=self.directory(owner,mid,product)
        with self.lock:
            if (owner,mid,product) in self.active:raise store.TextError('ALREADY_RUNNING','正在处理中。',409)
            with lease(directory):
                job=self.state(owner,mid,product)
                if job['state']!='draft':raise store.TextError('INVALID_REQUEST','只有已生成的看板可以重新校验。',409)
                planning=plan(record['source_text']);batches=extraction_batches(planning['chunks'])
                names=['merge.json'] if len(batches)>1 else [f'{i:05d}.json' for i in range(len(batches))]
                parts=[]
                for name in names:
                    saved=read_json(directory/'checkpoints'/name)
                    if fingerprint(saved.get('payload'))!=saved.get('sha256'):raise store.TextError('CHECKPOINT_CORRUPT','已保存片段校验失败。',409)
                    parts.append(board_payload(saved['payload']))
                result=self._publish_parts(owner,mid,product,planning,job,parts)
                job.update(revalidated_at=time.time(),revalidated_version=result['version'],updated_at=time.time())
                atomic_json(directory/'job.json',job)
                return result

    def _salvage(self,directory,job,index):
        for path in sorted((directory/'rejected').glob(f'*-{index}.json'),reverse=True):
            saved=read_json(path)
            if saved.get('plan_hash')!=job['plan_hash'] or saved.get('code')!='INVALID_OUTPUT':continue
            try:return board_payload(saved.get('payload'))
            except store.TextError:continue
        return None

    def _continue_board(self,owner,mid):
        """Clean finished: start the waiting board from it. Failure is shown, never hidden."""
        path=self.directory(owner,mid,'board')/'job.json'
        try:
            if self.state(owner,mid,'board')['state']!='waiting':return
            self.start(owner,mid,'board')
        except (store.TextError,OSError) as exc:
            try:
                with lease(path.parent):
                    job=read_json(path)
                    if job.get('state')=='waiting':
                        job.update(state='failed',error={'code':getattr(exc,'code','STORAGE_FAILED'),
                            'message':'清洗稿已完成，但看板未能自动开始：'+getattr(exc,'message','保存失败。')},updated_at=time.time())
                        atomic_json(path,job)
            except (store.TextError,OSError):pass

    def _run(self,owner,mid,product,planning,job):
        from .board_first import board_messages_from_clean
        directory=self.directory(owner,mid,product);parts=[];kept=[];chain=False
        try:
            with lease(directory):
                batches=extraction_batches(planning['chunks']) if product=='board' else planning['chunks']
                clean_input=self.clean_source(owner,mid,job['source_sha256'],job['clean_version'])['rows'] if product=='board' else None
                for index,chunk in enumerate(batches):
                    if self.closing.is_set():raise store.TextError('SERVICE_RESTARTED','任务已停止，保留成功片段。',409)
                    store.get_meeting(owner,mid)  # Re-check immutable source at each boundary.
                    job.update(state='running',done=index,updated_at=time.time());atomic_json(directory/'job.json',job)
                    cp=directory/'checkpoints'/f'{index:05d}.json'
                    if cp.exists():
                        saved=read_json(cp)
                        if saved.get('plan_hash')!=job['plan_hash'] or fingerprint(saved.get('payload'))!=saved.get('sha256'):
                            raise store.TextError('CHECKPOINT_CORRUPT','已保存片段校验失败，不自动重跑。',409)
                        value=saved['payload']
                    elif product=='board' and (salvaged:=self._salvage(directory,job,index)) is not None:
                        # A paid response rejected only for its outer wrapper is reused, never re-bought.
                        value=salvaged;job['salvaged_batches']=sorted(set(job.get('salvaged_batches',[]))|{index})
                        atomic_json(cp,{'payload':value,'sha256':fingerprint(value),'plan_hash':job['plan_hash']})
                    else:
                        prompt=board_messages_from_clean(planning['units'],chunk,clean_input) if product=='board' else messages('cleaning',target=chunk,context=(batches[index-1][-2:] if index else [])+(batches[index+1][:2] if index+1<len(batches) else []))
                        value=self.model.call(prompt,owner,job['source_sha256'],product+'_draft',index)
                        try:
                            if product=='board':
                                value=board_payload(value)
                            else:clean_rows(value,chunk)
                        except store.TextError as exc:
                            atomic_json(directory/'rejected'/f'{time.time_ns()}-{index}.json',{'payload':value,'code':exc.code,'plan_hash':job['plan_hash']});raise
                        atomic_json(cp,{'payload':value,'sha256':fingerprint(value),'plan_hash':job['plan_hash']})
                    if product=='board':parts.append(value)
                    else:
                        rows,bad=clean_rows(value,chunk);parts.append(rows);kept.extend(bad)
                    job['done']=index+1;atomic_json(directory/'job.json',job)
                if product=='board' and len(batches)>1:
                    if self.closing.is_set():raise store.TextError('SERVICE_RESTARTED','合并尚未开始，保留已保存片段。',409)
                    store.get_meeting(owner,mid)
                    merge_path=directory/'checkpoints'/'merge.json';binding=fingerprint([job['plan_hash'],parts])
                    if merge_path.exists():
                        saved=read_json(merge_path)
                        if saved.get('binding')!=binding or fingerprint(saved.get('payload'))!=saved.get('sha256'):
                            raise store.TextError('CHECKPOINT_CORRUPT','合并记录与片段不匹配，不自动重跑。',409)
                        merged=board_payload(saved['payload'])
                    else:
                        import json
                        prompt=board_messages_from_clean(planning['units'],planning['units'],clean_input)
                        prompt.append({'role':'user','content':json.dumps({'draft_parts':parts,'instruction':'对照全部原稿整合这些候选：同事项合并全部条件、当前口径与更正，消除跨片重复；不要逐片拼接。只返回最终sections，不能省略关键事实。'},ensure_ascii=False)})
                        merged=self.model.call(prompt,owner,job['source_sha256'],'board_merge',0)
                        try:merged=board_payload(merged)
                        except store.TextError as exc:
                            atomic_json(directory/'rejected'/f'{time.time_ns()}-merge.json',{'payload':merged,'code':exc.code,'plan_hash':job['plan_hash']});raise
                        atomic_json(merge_path,{'payload':merged,'sha256':fingerprint(merged),'binding':binding})
                    parts=[merged];job['done']=job['total']
                self._publish_parts(owner,mid,product,planning,job,parts,kept)
                job.update(state='draft',error=None,updated_at=time.time());atomic_json(directory/'job.json',job)
                chain=product=='clean' and bool(job.get('model_version'))
        except Exception as exc:
            error=exc if isinstance(exc,store.TextError) else store.TextError('PROCESSING_FAILED','生成或保存失败，已落盘片段保留。',500)
            if error.code=='ALREADY_RUNNING':return
            # Reacquire the product lease before failure publication. A recovering
            # process may have acquired it after the main work block unwound.
            try:
                with lease(directory):
                    current=read_json(directory/'job.json')
                    if current.get('attempt')!=job['attempt']:return
                    # Partial work never replaces a previously complete saved version.
                    if parts and job['done']<job['total'] and error.code not in ('SOURCE_CHANGED','INDEX_CHANGED','STORAGE_CORRUPT','RESULT_CHANGED','CHECKPOINT_CORRUPT'):
                        try:self._publish_parts(owner,mid,product,planning,job,parts,kept)
                        except (store.TextError,OSError):pass
                    job.update(state='interrupted' if error.code=='SERVICE_RESTARTED' else 'failed',
                               error={'code':error.code,'message':error.message},updated_at=time.time())
                    atomic_json(directory/'job.json',job)
            except store.TextError as saving_error:
                if saving_error.code!='ALREADY_RUNNING':
                    job.update(state='failed',error={'code':saving_error.code,'message':saving_error.message})
                    self.failed_states[str(directory/'job.json')]=dict(job)
            except OSError:
                job.update(state='failed',error={'code':error.code,'message':error.message})
                self.failed_states[str(directory/'job.json')]=dict(job)
        finally:
            with self.lock:self.active.discard((owner,mid,product))
        if chain:self._continue_board(owner,mid)

    def shutdown(self):self.legacy.shutdown()
