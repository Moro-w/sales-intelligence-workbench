#!/usr/bin/env python3
"""Inspect model-test material; activation is an explicit post-user-approval operation.

Default: offline dry-run, never reads a model credential or sends a request.
--activate must only be used AFTER the user approves these exact materials and caps.
"""
import argparse
import hashlib
import json
import os
import re
import fcntl
from pathlib import Path
import secrets
import sys
import time
import urllib.request

from local_webui import ROOT, RUNTIME, private_json, read_state, owned_process


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
sys.path.insert(0, str(ROOT / 'Tingji'))
from app.text_processing.contracts import plan, extraction_batches
from app.text_storage import validate_source


def inspect(paths, products=('board','clean')):
    if not products or any(p not in ('board','clean') for p in products):raise RuntimeError('Invalid product scope.')
    materials=[]
    for supplied in paths:
        path=Path(supplied).resolve()
        rel=path.relative_to(ROOT.resolve()).parts if path.is_relative_to(ROOT.resolve()) else ()
        allowed=path.name.endswith('_原稿.md') or (len(rel)>=3 and rel[0]=='测试材料' and rel[-2]=='输入')
        if not rel or path.suffix.lower()!='.md' or not allowed or any('验收' in part or '核对' in part for part in rel):
            raise RuntimeError('Only project-local *_原稿.md or 测试材料/*/输入/*.md is allowed; never send the answer checklist.')
        raw=path.read_bytes()
        text,_=validate_source(path.name,raw,path.stem)
        planning=plan(text);chunks=len(planning['chunks']);extracts=len(extraction_batches(planning['chunks']))
        materials.append({'path':str(path.relative_to(ROOT.resolve())), 'sha256':hashlib.sha256(raw).hexdigest(),
                          'bytes':len(raw),'characters':len(text),'chunks':chunks,'extract_batches':extracts,'products':list(products),'review_requests':0,'minimum_requests':(extracts+int(extracts>1) if 'board' in products else 0)+(chunks if 'clean' in products else 0)})
    if not materials:raise RuntimeError('No source materials supplied.')
    if sum(m['minimum_requests'] for m in materials)>96:
        raise RuntimeError('Material exceeds this batch request plan; reassess before requesting approval, never truncate.')
    return materials


CAPS = {
    'full': {'max_requests':120, 'max_input':500000, 'max_output':125000, 'max_microyuan':2000000},
    'pilot': {'max_requests':24, 'max_input':200000, 'max_output':50000, 'max_microyuan':800000},
}


def archive_closed_batch():
    """Explicit new-batch operation only; never reset/reopen an active or incomplete ledger."""
    gpath=RUNTIME/'meeting-model-grant.json';lpath=RUNTIME/'meeting-model-ledger.json'
    grant=json.loads(gpath.read_text());ledger=json.loads(lpath.read_text())
    bid=grant.get('batch_id','')
    if (grant.get('enabled') is not False or 'api_key' in grant or ledger.get('halted') is not True
        or ledger.get('batch_id')!=bid or not re.fullmatch(r'[a-f0-9]{32}',bid)):
        raise RuntimeError('Only a fully closed matching batch without a credential copy may be archived.')
    archive=RUNTIME/'meeting-model-batches'/bid
    archive.mkdir(parents=True,exist_ok=False,mode=0o700)
    # Both closed records are copied durably before active paths are removed. Partial failure stays closed.
    for source, name in ((gpath,'grant.json'),(lpath,'ledger.json')):
        with open(archive/name,'xb',opener=lambda p,f:os.open(p,f,0o600)) as output:
            output.write(source.read_bytes());output.flush();os.fsync(output.fileno())
    gpath.unlink();lpath.unlink()


def activate(materials, profile='full', new_batch=False, products=('board','clean')):
    if not products or any(p not in ('board','clean') for p in products):raise RuntimeError('Invalid approved product scope.')
    if any(m.get('products',list(products))!=list(products) for m in materials):raise RuntimeError('Material plan and approved product scope differ.')
    grant_path=RUNTIME/'meeting-model-grant.json'
    ledger_path=RUNTIME/'meeting-model-ledger.json'
    if profile not in CAPS or (profile=='pilot' and len(materials)!=1):
        raise RuntimeError('Pilot profile permits exactly one approved complete source.')
    if (grant_path.exists() or ledger_path.exists()) and not new_batch:
        raise RuntimeError('Existing meeting authorization/ledger retained; do not silently reset or reopen it.')
    # Only this project's existing login/provider; no old project scanning or key migration.
    if not owned_process(read_state()):raise RuntimeError('The current project WebUI is not running; no credential request sent.')
    access=json.loads((RUNTIME/'local-access.json').read_text())
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    base='http://127.0.0.1:25849'
    def request(path,data=None,token=None):
        headers={'Host':'sales-agent.localhost:25849','Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        with opener.open(req,timeout=10) as response:return json.load(response)
    login=request('/login',{'username':access['username'],'password':access['password']})
    token=login.get('token')
    if not isinstance(token,str):raise RuntimeError('Project login failed; details withheld.')
    status=request('/api/meeting-assistant/status',token=token)
    if status.get('processing_protocol')!='sales-board-first-v1':
        raise RuntimeError('Meeting worker is not running the new information-point protocol; restart this project sidecar before activation.')
    if len(products)==2 and status.get('workflow')!='clean-then-board-v1':
        raise RuntimeError('Serial clean-then-board workflow is not deployed; no provider credentials requested.')
    providers=request('/api/providers',token=token)
    # Keep response contract strict; no guessing or dumping a credential-bearing response.
    rows=providers.get('data')
    if not isinstance(rows,list):raise RuntimeError('Provider response contract changed; inspect server code, not secret output.')
    # Explicit provider id wins; otherwise exactly one enabled DeepSeek provider must exist (never guess between several).
    wanted=os.environ.get('MEETING_PROVIDER_ID')
    selected=[p for p in rows if p.get('id')==wanted] if wanted else [
        p for p in rows if p.get('enabled') is True and (p.get('base_url') or '').rstrip('/')=='https://api.deepseek.com/v1']
    if len(selected)!=1:raise RuntimeError('Exactly one enabled DeepSeek provider is required (or set MEETING_PROVIDER_ID); none selected silently.')
    provider=selected[0]
    if (provider.get('base_url','').rstrip('/')!='https://api.deepseek.com/v1' or not provider.get('api_key')
        or provider.get('enabled') is not True or 'deepseek-flash' not in provider.get('models',[])
        or provider.get('model_enabled',{}).get('deepseek-flash',True) is False):
        raise RuntimeError('Provider configuration differs from approved DeepSeek endpoint; reassess without printing it.')
    # This ID was verified by the actual authenticated gateway and integration tests.
    # Confirm the login token subject in server-provided login user metadata before enabling.
    user=login.get('user',{})
    owner=user.get('id')
    if owner!='system_default_user':raise RuntimeError('Current login user does not match the approved project owner.')
    value={'enabled':True,'batch_id':secrets.token_hex(16),'api_key':provider['api_key'],'owner':owner,
           'model':'deepseek-flash','source_hashes':list(dict.fromkeys(m['sha256'] for m in materials)),
           'expires_at':time.time()+24*3600,**CAPS[profile],
           'input_price_microyuan':2,'output_price_microyuan':8,
           'processing_protocol':'sales-board-first-v1','products':list(products)}
    with open(RUNTIME/'meeting-model-budget.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if grant_path.exists() or ledger_path.exists():
            if not new_batch:raise RuntimeError('Existing batch retained.')
            archive_closed_batch()
        private_json(grant_path,value)
    print(f"Approved {profile} batch activated: <={value['max_microyuan']/1000000:g} CNY, <={value['max_requests']} requests including reviews/retries. Credentials not displayed.")


def close_batch():
    path=RUNTIME/'meeting-model-grant.json'
    # Same ledger lock as the worker: wait for any in-flight call to settle first.
    import fcntl
    with open(RUNTIME/'meeting-model-budget.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if path.exists():
            grant=json.loads(path.read_text());grant['enabled']=False;grant.pop('api_key',None)
            private_json(path,grant)
        ledger_path=RUNTIME/'meeting-model-ledger.json'
        if ledger_path.exists():
            value=json.loads(ledger_path.read_text());value['halted']=True
            value['closed_reason']='Batch complete; explicit new authorization required'
            private_json(ledger_path,value)
    print('Meeting batch closed, API key copy removed, ledger retained; no automatic reuse.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files',nargs='*')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--activate',action='store_true',help='Only after explicit user confirmation of the exact materials and fee cap')
    mode.add_argument('--close',action='store_true',help='Close this batch and remove its private credential copy')
    parser.add_argument('--pilot',action='store_true',help='One complete source; <=0.8 CNY, <=24 total requests')
    parser.add_argument('--product',choices=['board','clean','both'],default='both',help='Explicitly approved product scope; default automatic board + clean')
    parser.add_argument('--new-batch',action='store_true',help='Only after new explicit approval: archive a fully closed old batch, never reopen it')
    args=parser.parse_args();os.umask(0o077)
    if args.new_batch and not args.activate:raise RuntimeError('--new-batch requires explicitly approved activation')
    if args.close:return close_batch()
    products=('board','clean') if args.product=='both' else (args.product,)
    materials=inspect(args.files,products)
    profile='pilot' if args.pilot else 'full';caps=CAPS[profile]
    if args.pilot and len(materials)!=1:raise RuntimeError('Pilot must use exactly one complete source, not extra files.')
    if sum(m['minimum_requests'] for m in materials)>int(caps['max_requests']*.8):
        raise RuntimeError('Minimum plan leaves insufficient request allowance for this profile; reassess, never truncate.')
    print(json.dumps({'materials':materials,'minimum_requests':sum(m['minimum_requests'] for m in materials),
                      'proposed_max_cny':caps['max_microyuan']/1000000,'proposed_max_requests_including_retries':caps['max_requests'],
                      'profile':profile,'mode':'activation' if args.activate else 'offline-plan'},ensure_ascii=False,indent=2))
    if args.activate:activate(materials,profile,args.new_batch,products)


if __name__=='__main__':
    try:main()
    except Exception:
        # Never leak urllib errors/credential-bearing response bodies or exception reprs.
        raise SystemExit('Batch preparation failed; no credentials displayed. Inspect configuration and material contracts locally.') from None
