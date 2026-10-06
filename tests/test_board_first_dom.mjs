// Offline native DOM contracts; no business-quality or browser acceptance claim.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../AionUi/package.json',import.meta.url));
const {JSDOM}=require('jsdom');
const html=readFileSync(new URL('../Tingji/static/meeting.html',import.meta.url),'utf8');
const script=readFileSync(new URL('../Tingji/static/text-results.js',import.meta.url),'utf8');
function fixture(){
 const text='说话人1：预算待确认😀。\n未标明身份，负责人也未确定。\n';const first=text.indexOf('\n')+1;
 const ref={quote:'预算待确认😀。',start_utf16:5,end_utf16:first-1,line:1,role:'当前结论'};
 const sections={'会议概况':[],'客户需求与产品适配':[],'预算与时间':[{id:'f1',topic:'budget',label:'预算',speaker:'说话人1',kind:'tentative',text:'预算待确认。',evidence:[ref],conditions:[],history:[],review_state:'pending'}],'决策与采购流程':[],'已达成事项':[],'待确认问题':[],'后续行动':[]};
 return {meta:{title:'离线样本',source_sha256:'offline'},source_text:text,source_index:{segments:[{line:1,start_utf16:0,end_utf16:first},{line:2,start_utf16:first,end_utf16:text.length}]},
  products:{board:{state:'draft',done:1,total:1,gate:{enabled:false,reason:'等待新授权'}},clean:{state:'failed',done:0,total:1,error:{message:'清洗超时'},gate:{enabled:false,reason:'清洗未授权'}}},
  board:{sections,version:'v1',origin:'model',issues:[],processing_complete:true,state:'draft'},clean:null,result:null,legacy_result:null};
}
function mount(data){
 const dom=new JSDOM(html,{runScripts:'outside-only',url:'http://sales-agent.localhost:25849/'});const w=dom.window,d=w.document,requests=[];
 w.setTimeout=()=>1;w.clearTimeout=()=>{};w.confirm=()=>true;w.apiFetch=async(path,options)=>{requests.push({path,options});return {ok:false,json:async()=>({detail:'离线模拟预算拒绝'})};};
 w.HTMLElement.prototype.scrollIntoView=function(){this.dataset.scrolled='yes';};
 const nodes=data.source_index.segments.map(s=>{const n=d.createElement('div');n.append(d.createElement('span'),d.createElement('span'));n.lastElementChild.textContent=data.source_text.slice(s.start_utf16,s.end_utf16).trimEnd();d.getElementById('transcript').append(n);return n;});
 data.job=data.products.board;data.model_gate=data.products.board.gate;
 w.TextEncoder=TextEncoder;
 for(const name of ['text-edit.js','text-export.js'])w.eval(readFileSync(new URL('../Tingji/static/'+name,import.meta.url),'utf8'));
 w.confirmTextAction=async()=>true;w.eval(script);w.setupTextProcessing('a'.repeat(32),data,nodes);return {dom,w,d,requests,nodes};
}
test('clean failure does not disable board or budget click',()=>{
 const {dom,d,nodes,requests}=mount(fixture());assert.equal(d.querySelector('#tab-summary.active')!==null,true);
 assert.equal(d.querySelector('.text-field-value').textContent,'预算待确认。');d.querySelector('.text-field-value').click();
 assert.equal(nodes[0].querySelector('mark')?.textContent,'预算待确认😀。');assert.equal(requests.length,0);
 assert.equal(d.getElementById('text-clean-btn').disabled,true);dom.window.close();
});
test('sample banner and all cost buttons remain disabled even with an enabled gate',()=>{
 const data=fixture();data.meta.sample=true;data.board.origin='sample';data.products.board.gate.enabled=true;
 const {dom,d,requests}=mount(data);assert.match(d.querySelector('.text-sample-banner').textContent,/静态数据，非AI生成/);
 d.getElementById('text-start-btn').click();assert.equal(requests.length,0);assert.equal(d.getElementById('text-start-btn').disabled,true);dom.window.close();
});
test('source pane starts hidden, clicking a field opens the exact original pane, and it can collapse again',()=>{
 const {dom,d}=mount(fixture());assert.equal(d.querySelector('.text-source-hidden')!==null,true,'board-first: original opens on demand');
 d.querySelector('.text-field-value').click();assert.equal(d.querySelector('.text-source-hidden'),null);assert.equal(d.querySelectorAll('#transcript').length,1);
 assert.equal(d.querySelector('.text-board-field').open,true,'the clicked item stays expanded');
 d.querySelector('.text-source-tools button').click();assert.equal(d.querySelector('.text-source-hidden')!==null,true);d.querySelector('.text-source-tools button').click();
 const width=d.querySelector('input[type=range]');width.value='48';width.dispatchEvent(new dom.window.Event('input'));
 assert.equal(d.querySelector('.text-result-split').style.getPropertyValue('--source-width'),'48%');dom.window.close();
});
test('generation error and budget blocker have distinct visible explanations',()=>{
 const data=fixture();data.board=null;data.products.board={state:'failed',done:0,total:1,last_error:{code:'MODEL_INVALID_RESPONSE',message:'模型格式无法解析'},error:{code:'BUDGET_EXHAUSTED',message:'预算不足'},gate:{enabled:false,reason:'下一请求将超过授权上限'}};
 const {dom,d}=mount(data);assert.match(d.getElementById('polish-warning-text').textContent,/格式无法解析.*超过授权上限/);
 assert.equal(d.querySelectorAll('.text-board-section').length,0);assert.match(d.querySelector('.text-task-details').textContent,/上次失败.*本次失败/s);dom.window.close();
});
test('request refusal is not overwritten by the normal status on finally',async()=>{
 const data=fixture();data.products.board.gate.enabled=true;
 const {dom,d,requests}=mount(data);await d.getElementById('text-start-btn').onclick();
 assert.equal(requests.length,1);assert.equal(JSON.parse(requests[0].options.body).product,'board');assert.equal(JSON.parse(requests[0].options.body).base_version,'v1');
 assert.match(d.getElementById('polish-warning-text').textContent,/离线模拟预算拒绝/);dom.window.close();
});
test('five deliverables enable only the corresponding saved product',()=>{
 const {dom,d}=mount(fixture());const options=[...d.getElementById('export-format').options];assert.equal(options.length,6);
 assert.equal(options.find(o=>o.value==='board-md').disabled,false);
 for(const value of ['board-jpg','board-pdf'])assert.equal(options.find(o=>o.value===value).disabled,false);
 for(const value of ['clean-md','clean-pdf'])assert.equal(options.find(o=>o.value===value).disabled,true);
 assert.equal(d.querySelector('[data-tab=processed]').disabled,true);dom.window.close();
});
test('first action requests both products once and hides separate initial buttons',async()=>{
 const data=fixture();data.board=null;for(const p of ['board','clean'])data.products[p]={state:'idle',gate:{enabled:true}};
 const {dom,d,requests}=mount(data);assert.equal(d.getElementById('text-start-btn').hidden,true);assert.equal(d.getElementById('text-clean-btn').hidden,true);
 await d.getElementById('text-start-all-btn').onclick();assert.equal(requests.length,1);assert.equal(JSON.parse(requests[0].options.body).product,'both');dom.window.close();
});
test('board-only authorization cannot enable the initial both-product action',()=>{
 const data=fixture();data.board=null;data.products.board={state:'idle',gate:{enabled:true}};data.products.clean={state:'idle',gate:{enabled:false}};
 const {dom,d,requests}=mount(data);d.getElementById('text-start-all-btn').click();assert.equal(requests.length,0);assert.equal(d.getElementById('text-start-all-btn').disabled,true);dom.window.close();
});
test('editing posts a draft and a saved version, never the original or a model request',async()=>{
 const data=fixture(),{dom,w,d,requests}=mount(data);w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
 let next=data;w.apiFetch=async(path,options)=>{requests.push({path,options});if(path.endsWith('/draft'))return {ok:true,json:async()=>({draft_version:'d1'})};if(path.endsWith('/save')){next=structuredClone(data);next.board.version='v2';next.board.sections['预算与时间'][0].text='人工备注，未批准。';return {ok:true,json:async()=>next.board};}return {ok:true,json:async()=>next};};
 [...d.querySelectorAll('button')].find(b=>b.textContent==='编辑看板').click();const input=d.querySelectorAll('dialog textarea')[1];input.value='人工备注，未批准。';input.dispatchEvent(new w.Event('input',{bubbles:true}));
 await [...d.querySelectorAll('dialog button')].find(b=>b.textContent==='保存版本').onclick();
 assert.equal(requests.filter(r=>r.path.endsWith('/draft')).length,1);assert.equal(requests.filter(r=>r.path.endsWith('/save')).length,1);
 assert.equal(requests.some(r=>r.path.endsWith('/process')||r.path.endsWith('/source')),false);assert.equal(d.querySelector('.text-field-value').textContent,'人工备注，未批准。');assert.equal(d.querySelector('dialog'),null);dom.window.close();
});
test('Markdown export identifies partial clean and neutralizes embedded HTML/images',async()=>{
 const data=fixture();data.clean={version:'clean-v2',processing_complete:false,processed_batches:1,total_batches:2,rows:[{text:'已整理',cleaned:'整理稿'},{text:'<script>bad()</script> ![image](https://invalid.test/image)',cleaned:null}]};
 const {dom,w,d,requests}=mount(data);let blob;
 w.Blob=Blob;w.URL.createObjectURL=value=>{blob=value;return 'blob:offline';};w.HTMLAnchorElement.prototype.click=function(){};
 d.getElementById('export-format').value='clean-md';d.getElementById('export-btn').click();
 const md=await blob.text();assert.match(md,/clean-v2/);assert.match(md,/部分草稿：仅完成 1\/2/);assert.match(md,/未清洗 · 保留原文/);
 assert.equal(md.includes('<script>'),false);assert.equal(md.includes('![image]'),false);assert.match(md,/&lt;script&gt;/);assert.equal(requests.length,0);dom.window.close();
});

test('board is one card: key facts on top, every section folded to one line and opened on demand',()=>{
 const data=fixture();const base=data.board.sections['预算与时间'][0];
 data.board.sections['客户需求与产品适配']=Array.from({length:7},(_,i)=>({...base,id:'n'+i,topic:'need',label:'需求'+i,text:'完整需求说明'+i,kind:'reported'}));
 data.board.sections['后续行动']=[{...base,id:'a1',topic:'action',label:'销售发材料',text:'销售周四下午五点前发材料。',kind:'agreed',owner:'销售',due:'周四下午五点前'},
   {...base,id:'b1',label:'下一步行动',text:'结论暂不展示，请核对依据或修复此事项。',review_state:'blocked',kind:'conflict'}];
 data.board.sections['会议概况']=[{...base,id:'m1',topic:'meeting',label:'沟通对象',text:'与岚川设备服务沟通。',kind:'reported',customer:'岚川设备服务'}];
 data.board.sections['预算与时间'][0]={...base,label:'第一年预算8-10万含税',text:'预算8到10万含税，不是已批。'};
 const {dom,d}=mount(data);
 assert.equal(d.querySelectorAll('.text-board-card').length,1,'a single card');
 assert.equal(d.querySelector('.text-board-customer').textContent,'岚川设备服务');
 const facts=[...d.querySelectorAll('.text-fact-row')].map(r=>r.textContent);
 assert.match(facts[0],/预算.*第一年预算8-10万含税 · 未审批/);assert.match(facts[2],/下一步.*销售发材料 · 周四下午五点前/);
 const sections=[...d.querySelectorAll('.text-board-section')];assert.equal(sections.length,7);
 assert.deepEqual(sections.slice(0,3).map(x=>x.querySelector('h3').textContent),['下一步','待确认','客户需求'],'sales priority order');
 assert.equal(sections.every(x=>!x.open),true,'all folded by default');
 const need=sections[2];assert.match(need.querySelector('.text-section-preview').textContent,/需求0 等7项/);
 const rows=[...need.querySelectorAll('.text-field-list > .text-board-field')];assert.equal(rows.filter(r=>!r.hidden).length,5);
 need.querySelector('.text-section-more').click();assert.equal(rows.filter(r=>!r.hidden).length,7);
 d.querySelectorAll('.text-fact-row')[2].click();assert.equal(sections[0].open,true);assert.equal(sections[0].querySelector('.text-board-field').open,true);
 assert.match(sections[0].querySelector('.text-field-plan').textContent,/负责人：销售 · 期限：周四下午五点前/);
 assert.match(sections[0].querySelector('.text-blocked-fields summary').textContent,/另有 1 条待人工补充/);
 assert.equal(d.querySelector('.text-key-data'),null);dom.window.close();
});
