// Real native templates/scripts in jsdom; no browser or real model acceptance claimed.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../AionUi/package.json',import.meta.url));
const {JSDOM}=require('jsdom');
const html=readFileSync(new URL('../Tingji/static/meeting.html',import.meta.url),'utf8');
const script=readFileSync(new URL('../Tingji/static/text-results.js',import.meta.url),'utf8');
const names=['会议概况','客户需求与产品适配','预算与时间','决策与采购流程','已达成事项','待确认问题','后续行动'];
function fixture(){
 const text='😀客户：可能预算20万，未批。\n客户：可能预算20万，未批。\n<script>window.bad=true</script>\n';
 const lines=text.split(/(?<=\n)/);let start=0;
 const segments=lines.map((line,i)=>{const s={line:i+1,start_utf16:start,end_utf16:start+line.length};start+=line.length;return s;});
 const quote='可能预算20万，未批。';const a=text.indexOf(quote),b=text.indexOf(quote,a+1);
 const evidence=[{quote,line:1,start_utf16:a,end_utf16:a+quote.length},{quote,line:2,start_utf16:b,end_utf16:b+quote.length}];
 const sections=Object.fromEntries(names.map(n=>[n,[]]));sections['预算与时间']=[{id:'field-1',text:'可能预算20万，未批。',kind:'tentative',evidence}];
 return {meta:{title:'离线DOM样本',source_sha256:'offline'},source_text:text,source_index:{segments},job:{state:'succeeded',total:1,completed_clean:1,completed_extract:1},
 model_gate:{enabled:false,reason:'未授权'},result:{sections,processed:'整理稿原样测试'}};
}
function mount(data){
 const dom=new JSDOM(html,{runScripts:'outside-only',url:'http://sales-agent.localhost:25849/api/meeting-assistant/ui/m/'+'a'.repeat(32)});
 const w=dom.window,timers=[],requests=[];
 w.setTimeout=(fn)=>{timers.push(fn);return timers.length;};w.clearTimeout=()=>{};
 w.HTMLElement.prototype.scrollIntoView=function(){this.dataset.scrolled='yes';};
 w.apiFetch=async(path,options)=>{requests.push({path,options});return {ok:true,json:async()=>({job:data.job,model_gate:data.model_gate})};};
 const d=w.document,transcript=d.getElementById('transcript');
 const nodes=data.source_index.segments.map(segment=>{
  const n=d.createElement('div');n.className='text-source-line';
  const number=d.createElement('span'),content=d.createElement('span');number.textContent=String(segment.line);
  content.textContent=data.source_text.slice(segment.start_utf16,segment.end_utf16).replace(/\r?\n$/,'');n.append(number,content);transcript.append(n);return n;
 });
 w.TextEncoder=TextEncoder;
 for(const name of ['text-edit.js','text-export.js'])w.eval(readFileSync(new URL('../Tingji/static/'+name,import.meta.url),'utf8'));
 w.confirmTextAction=async()=>true;w.eval(script);w.setupTextProcessing('a'.repeat(32),data,nodes);
 return {dom,w,d,nodes,timers,requests};
}
test('seven sections use the existing tabs and transcript, source remains safe text',()=>{
 const {dom,w,d,timers}=mount(fixture());
 assert.equal(d.querySelectorAll('.text-board-section').length,7);
 assert.equal(d.querySelectorAll('.text-empty-field').length,6);
 assert.equal(d.querySelector('#tab-summary .transcript')?.id,'transcript');
 assert.equal(d.querySelector('#transcript script'),null);assert.equal(w.bad,undefined);
 assert.equal(timers.length,1,'polling may refresh independent products/gate; must never start model work');dom.window.close();
});
test('field click and second source select distinct exact UTF-16 positions',()=>{
 const {dom,d,nodes}=mount(fixture());
 d.querySelector('.text-field-value').click();
 assert.equal(nodes[0].querySelector('mark')?.textContent,'可能预算20万，未批。');
 assert.equal(nodes[1].querySelector('mark'),null);
 d.querySelectorAll('.text-evidence-links button')[1].click();
 assert.equal(nodes[0].querySelector('mark'),null);
 assert.equal(nodes[1].querySelector('mark')?.textContent,'可能预算20万，未批。');
 assert.equal(nodes[1].dataset.scrolled,'yes');
 assert.match(d.querySelector('.text-source-hint').textContent,/2\/2处/);dom.window.close();
});
test('bad source range never triggers approximate search',()=>{
 const data=fixture();data.result.sections['预算与时间'][0].evidence[0].start_utf16=0;
 const {dom,d}=mount(data);d.querySelector('.text-field-value').click();
 assert.equal(d.querySelectorAll('mark').length,0);assert.match(d.querySelector('.text-source-hint').textContent,/出处不匹配/);dom.window.close();
});
test('model strings are not interpreted as HTML, suggestions stay distinct',()=>{
 const data=fixture();data.result.sections['待确认问题']=[{id:'suggest',kind:'ai_suggestion',text:'<img src=x onerror=alert(1)>',evidence:[]}];
 const {dom,d}=mount(data);
 assert.equal(d.querySelectorAll('.text-sales-board img').length,0);
 assert.match(d.querySelector('.text-sales-board').textContent,/AI 建议，无会议事实出处/);dom.window.close();
});
test('switching tabs moves the single original transcript back, not a detached copy',()=>{
 const {dom,d}=mount(fixture());
 assert.equal(d.querySelector('.meeting-body').classList.contains('text-board-active'),true);
 d.querySelector('[data-tab=raw]').click();assert.equal(d.querySelector('#tab-raw .transcript')?.id,'transcript');
 assert.equal(d.querySelector('.meeting-body').classList.contains('text-board-active'),false);
 d.querySelector('[data-tab=processed]').click();assert.equal(d.getElementById('processed-md').textContent,'整理稿原样测试');
 assert.equal(d.querySelector('.meeting-body').classList.contains('text-board-active'),false);
 d.querySelector('[data-tab=summary]').click();assert.equal(d.querySelectorAll('#transcript').length,1);
 assert.equal(d.querySelector('.meeting-body').classList.contains('text-board-active'),true);dom.window.close();
});
test('atomic summary, visible conditions, collapsed history and evidence roles remain separate',()=>{
 const data=fixture();data.result.processing_protocol='atomic-facts-v3';
 const item=data.result.sections['预算与时间'][0];item.speaker='客户甲';
 item.conditions=[{text:'权限检查通过后再安排 <img src=x>',evidence:[item.evidence[0]]}];
 item.history=[{text:'已撤回的历史口径',evidence:[item.evidence[1]]}];
 item.evidence[0].role='当前结论';item.evidence[1].role='历史说法';
 const {dom,d}=mount(data);
 assert.equal(d.querySelector('.text-field-value').textContent,item.text);
 assert.match(d.querySelector('.text-fact-conditions').textContent,/权限检查通过/);
 assert.equal(d.querySelector('.text-fact-history').open,false);
 assert.match(d.querySelector('.text-fact-history summary').textContent,/已更正/);
 assert.equal(d.querySelector('.text-sales-board img'),null);
 d.querySelectorAll('.text-evidence-links button')[1].click();
 assert.match(d.querySelector('.text-source-hint').textContent,/历史说法.*第2行/);
 assert.match(d.querySelector('.text-field-kind').textContent,/客户甲/);dom.window.close();
});
test('explicit refusal and legacy results are not presented as new confirmed agreements',()=>{
 const data=fixture();data.result.sections['已达成事项']=[{id:'no',text:'未同意采购',kind:'declined',evidence:[]}];
 const {dom,d}=mount(data);
 assert.match(d.querySelector('.text-sales-board').textContent,/明确未同意/);
 assert.match(d.querySelector('.text-sales-board').textContent,/旧版结果/);dom.window.close();
});
test('draft authorization gate prevents start and does not make requests',()=>{
 const data=fixture();data.job={state:'draft',total:0,completed_clean:0,completed_extract:0};data.result=null;
 const {dom,d,requests}=mount(data);
 assert.equal(d.getElementById('text-start-btn').disabled,true);d.getElementById('text-start-btn').click();
 assert.equal(requests.length,0);assert.match(d.getElementById('polish-warning-text').textContent,/未授权/);dom.window.close();
});
test('blank separator is not an unprocessed speech and completed clean remains pending verification',()=>{
 const data=fixture();data.job={state:'failed',total:1,completed_clean:1,completed_extract:0};data.result=null;
 data.partial_clean=[{text:'原文',cleaned:'整理稿'},{text:'',cleaned:null}];
 const {dom,d}=mount(data);assert.equal(d.querySelectorAll('.text-uncleaned').length,0);
 assert.equal(d.querySelector('[data-tab=processed]').textContent,'清洗稿 · 旧版待核对');
 assert.equal(d.querySelector('[data-tab=summary]').disabled,false);
 assert.equal(d.querySelectorAll('.text-board-section').length,0);dom.window.close();
});
test('partial failure labels uncleaned text and never enables a finished board',()=>{
 const data=fixture();data.job={state:'failed',total:2,completed_clean:1,completed_extract:0,error:{message:'超时，可重试'}};data.result=null;
 data.partial_clean=[{text:'原文一',cleaned:'整理一'},{text:'原文二',cleaned:null}];data.model_gate={enabled:true};
 const {dom,d}=mount(data);
 assert.equal(d.querySelector('[data-tab=processed]').disabled,false);
 assert.equal(d.querySelector('[data-tab=summary]').disabled,false);
 assert.equal(d.querySelectorAll('.text-board-section').length,0);
 assert.match(d.getElementById('processed-md').textContent,/未清洗 · 保留原文/);
 assert.match(d.getElementById('text-start-btn').textContent,/重试看板/);dom.window.close();
});
