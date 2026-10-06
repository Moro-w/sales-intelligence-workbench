"""Isolated, no-model/no-ASR Tingji entry point behind the Core gateway."""
import hmac
from contextlib import asynccontextmanager
import os
from pathlib import Path
import re

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app import storage, text_storage as store
from app.text_processing.products import Products, WORKFLOW
from app.text_processing.board_first import PROTOCOL
from app.text_processing.model import BudgetedModel
from app.text_processing.presentation import completion, sales_card

STATIC = Path(__file__).resolve().parent.parent / 'static'
PREFIX = '/api/meeting-assistant'
ASSETS = {'style.css', 'meeting.css', 'common.js', 'app.js', 'meeting.js',
          'marked.min.js', 'text-mode.js', 'text-mode.css', 'text-results.js', 'text-edit.js', 'text-export.js'}


def create_app(data_dir=None, service_token=None, model=None):
    token = service_token or os.environ.get('TINGJI_SERVICE_TOKEN', '')
    if not re.fullmatch(r'[a-f0-9]{64}', token):
        raise RuntimeError('A private service token is required for text-only mode')
    root = data_dir or os.environ.get('TINGJI_DATA_DIR')
    if not root:
        raise RuntimeError('An explicit text-only data directory is required')
    storage.set_data_dir(str(root))
    jobs = Products(model or BudgetedModel(os.environ.get('TINGJI_MODEL_GRANT')))
    @asynccontextmanager
    async def lifespan(_app):
        yield
        jobs.shutdown()
    app = FastAPI(title='Tingji text-only', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.jobs = jobs

    @app.middleware('http')
    async def guard(request: Request, call_next):
        supplied = request.headers.get('x-tingji-service-token', '')
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            return JSONResponse({'code': 'UNAUTHORIZED', 'detail': '需要通过工作台访问。'}, status_code=401)
        owner = request.headers.get('x-tingji-user-id', '')
        if not owner or len(owner) > 128 or not owner.isascii():
            return JSONResponse({'code': 'UNAUTHORIZED', 'detail': '身份无效。'}, status_code=401)
        if request.method not in ('GET', 'POST'):
            return JSONResponse({'code': 'METHOD_NOT_ALLOWED', 'detail': '原始资料只读。'}, status_code=405)
        if request.method == 'POST':
            try:
                size = int(request.headers.get('content-length', '0'))
            except ValueError:
                size = 0
            if not 0 < size <= store.MAX_SOURCE_BYTES + 16384:
                return JSONResponse({'code': 'FILE_TOO_LARGE', 'detail': '上传体积无效或超过限制。'}, status_code=413)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(store.TextError)
    async def text_error(_request, exc):
        return JSONResponse({'code': exc.code, 'detail': exc.message}, status_code=exc.status)

    @app.exception_handler(OSError)
    async def storage_error(_request, _exc):
        return JSONResponse({'code': 'STORAGE_FAILED', 'detail': '保存或读取失败，请检查本机存储后重试。'}, status_code=500)

    def owner(request):
        return request.headers['x-tingji-user-id']

    def page(name):
        html = (STATIC / name).read_text()
        # Only known URL attributes in pinned upstream templates are adapted;
        # uploaded text never enters this template or a global text replacement.
        html = re.sub(r'<script src="/static/access\.js[^\"]*"></script>', '', html)
        def asset_url(match):
            attribute, asset = match.groups()
            if asset not in ASSETS:
                raise store.TextError('TEMPLATE_MISMATCH', '会议页面资源不完整，请检查安装。', 500)
            return f'{attribute}="{PREFIX}/ui/static/{asset}"'
        html = re.sub(r'\b(src|href)="/static/([^"?]+)(?:\?[^\"]*)?"', asset_url, html)
        html = re.sub(r'\bhref="/"', f'href="{PREFIX}/ui/"', html)
        html = html.replace('</head>', f'<link rel="stylesheet" href="{PREFIX}/ui/static/text-mode.css">\n<script src="{PREFIX}/ui/static/text-edit.js"></script>\n<script src="{PREFIX}/ui/static/text-export.js"></script>\n<script src="{PREFIX}/ui/static/text-results.js"></script>\n<script src="{PREFIX}/ui/static/text-mode.js"></script>\n</head>', 1)
        return HTMLResponse(html)

    @app.get('/api/status')
    def status(request: Request):
        gate = jobs.model.status(owner(request))
        return {'mode': 'text-only', 'processing_protocol': PROTOCOL, 'workflow': WORKFLOW, 'model_enabled': gate['enabled'], 'model_gate': gate, 'max_source_bytes': store.MAX_SOURCE_BYTES}

    @app.get('/ui/')
    def index():
        return page('index.html')

    @app.get('/ui/m/{meeting_id}')
    def detail_page(meeting_id: str, request: Request):
        store.get_meeting(owner(request), meeting_id)
        return page('meeting.html')

    @app.get('/ui/static/{name}')
    def static_file(name: str):
        if name not in ASSETS:
            raise store.TextError('NOT_FOUND', '资源不存在。', 404)
        return FileResponse(STATIC / name)

    @app.get('/api/meetings')
    def meetings(request: Request):
        items = store.list_meetings(owner(request), tolerate_corrupt=True)
        for item in items:
            item['archived']='archived' in (item.get('tags') or [])
            if item.get('error'): continue
            try:
                data = jobs.view(owner(request), item['id'])
                job = data['products']['board']; board = data['board']
                item['status'] = job['state']; item['processing'] = job
                item['status_label'] = ('界面样例 · 非AI结果' if item.get('sample') else
                    '看板草稿 · 待核对' if board else '清洗中 · 完成后自动生成看板' if job['state']=='waiting' and data['products']['clean']['state'] in ('queued','running') else
                    '清洗未完成 · 看板等待中' if job['state']=='waiting' else '看板生成中' if job['state'] in ('queued','running') else
                    '生成失败 · '+('可重试' if job['gate']['enabled'] else '等待开通') if job['state'] in ('failed','interrupted') else
                    '旧版结果 · 待重做' if data['legacy_result'] else
                    '旧流程失败 · '+('可单独生成看板' if job['gate']['enabled'] else '等待开通') if data['legacy_job'].get('error') else '原稿已保存 · 待生成看板')
                fields=[f for values in (board or {}).get('sections',{}).values() for f in values if f.get('review_state')!='blocked']
                item['key_data']=[{'label':label,'text':next((f['text'] for f in fields if f.get('topic')==topic),'未提取，待核对')}
                    for label,topic in [('规模','organization'),('预算','budget'),('下一步','action')]] if board else []
                item['summary'] = '；'.join(row['text'] for row in item['key_data'][1:])[:180]
                item['clean_state'] = data['products']['clean']['state']
                item['card'] = sales_card(data)
                item['completion'] = completion(data)
                item['product_states'] = {p:data['products'][p]['state'] for p in ('board','clean')}
                if board and not item.get('sample'):item['status_label']=item['completion']['label']
            except store.TextError as exc:
                item['status'] = 'failed'
                item['error'] = {'code': exc.code, 'message': exc.message}
        return items

    @app.post('/api/imports', status_code=201)
    async def upload(request: Request, file: UploadFile = File(...), title: str = Form(''), import_id: str = Form(...)):
        try:
            content = await file.read(store.MAX_SOURCE_BYTES + 1)
            meta, created = store.import_markdown(owner(request), import_id, file.filename, content, title)
        finally:
            await file.close()
        return JSONResponse({'meeting_id': meta['id'], 'status': meta['status'], 'created': created}, status_code=201 if created else 200)

    @app.get('/api/meetings/{meeting_id}')
    def detail(meeting_id: str, request: Request):
        record=jobs.view(owner(request), meeting_id)
        record['completion']=completion(record)
        return record

    @app.get('/api/meetings/{meeting_id}/job')
    def job_state(meeting_id: str, request: Request):
        products = jobs.overview(owner(request), meeting_id)
        return {'job': products['board'], 'products': products, 'model_gate': products['board']['gate']}

    @app.post('/api/meetings/{meeting_id}/process', status_code=202)
    async def start_processing(meeting_id: str, request: Request):
        try: body = await request.json()
        except ValueError: raise store.TextError('INVALID_REQUEST', '开始请求格式无效。') from None
        if not isinstance(body, dict) or set(body)-{'product','regenerate','base_version'} or not isinstance(body.get('regenerate',False),bool):
            raise store.TextError('INVALID_REQUEST', '开始请求格式无效。')
        if body.get('product','both')=='both':
            if body.get('regenerate') or body.get('base_version') is not None:
                raise store.TextError('INVALID_REQUEST','重新生成请分别选择看板或清洗稿。')
            return jobs.start_all(owner(request),meeting_id)
        return jobs.start(owner(request), meeting_id, body['product'], body.get('regenerate',False), body.get('base_version'))

    @app.get('/api/meetings/{meeting_id}/reference')
    def meeting_reference(meeting_id: str, request: Request):
        from app.text_processing.reference import snapshot
        return snapshot(jobs,owner(request),meeting_id)

    @app.post('/api/meetings/{meeting_id}/save')
    async def save_edit(meeting_id: str, request: Request):
        from app.text_processing.editing import save
        try:body=await request.json()
        except ValueError:raise store.TextError('INVALID_EDIT','保存请求格式无效。') from None
        return save(jobs,owner(request),meeting_id,body)

    @app.post('/api/meetings/{meeting_id}/archive')
    async def archive(meeting_id: str, request: Request):
        try:body=await request.json()
        except ValueError:raise store.TextError('INVALID_REQUEST','归档请求格式无效。') from None
        if not isinstance(body,dict) or set(body)!={'archived'}:raise store.TextError('INVALID_REQUEST','归档请求格式无效。')
        return store.set_archived(owner(request),meeting_id,body['archived'])

    @app.post('/api/meetings/{meeting_id}/draft')
    async def save_draft(meeting_id: str, request: Request):
        from app.text_processing.editing import save
        try:body=await request.json()
        except ValueError:raise store.TextError('INVALID_EDIT','草稿请求格式无效。') from None
        return save(jobs,owner(request),meeting_id,body,draft_only=True)

    @app.post('/api/meetings/{meeting_id}/suggestion')
    async def decide_suggestion(meeting_id: str, request: Request):
        from app.text_processing.editing import decide
        try:body=await request.json()
        except ValueError:raise store.TextError('INVALID_EDIT','建议请求格式无效。') from None
        return decide(jobs,owner(request),meeting_id,body)

    @app.get('/api/meetings/{meeting_id}/source')
    def original(meeting_id: str, request: Request):
        record = store.get_meeting(owner(request), meeting_id)
        return FileResponse(store.meeting_dir(owner(request), meeting_id) / 'original.md',
                            media_type='text/markdown; charset=utf-8', filename=record['meta']['filename'])

    return app
