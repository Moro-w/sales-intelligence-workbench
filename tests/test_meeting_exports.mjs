// Export engine exercised with real JPEG encoding and a PDF reader, offline only.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,readdirSync} from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
import vm from 'node:vm';
const root=path.resolve(import.meta.dirname,'..'),vendor=path.join(root,'AionUi/node_modules/.bun'),names=readdirSync(vendor);
const canvasLib=createRequire(import.meta.url)(path.join(vendor,names.find(n=>n.startsWith('@napi-rs+canvas@')),'node_modules/@napi-rs/canvas/index.js'));
const pdfjs=await import(pathToFileURL(path.join(vendor,names.find(n=>n.startsWith('pdfjs-dist@')),'node_modules/pdfjs-dist/legacy/build/pdf.mjs')));
function engine(){
 const drawn=[];const context={TextEncoder,Blob,Uint8Array,setTimeout,URL,window:{},document:{fonts:{ready:Promise.resolve()},createElement(tag){assert.equal(tag,'canvas');const c=canvasLib.createCanvas(1120,1584),ctx=c.getContext('2d'),draw=ctx.fillText.bind(ctx);ctx.fillText=(text,...args)=>{drawn.push(text);draw(text,...args);};c.getContext=()=>ctx;c.toBlob=callback=>c.encode('jpeg',94).then(b=>callback(new Blob([b],{type:'image/jpeg'})));return c;}}};
 vm.createContext(context);vm.runInContext(readFileSync(path.join(root,'Tingji/static/text-export.js'),'utf8'),context);return {api:context.window.MeetingExport,drawn};
}
function record(){return {meta:{title:'离线导出工程检查，非模型结果'},board:{version:'board-save',sections:{'预算与时间':[{label:'预算',text:'5—7万元，未审批。',conditions:[{text:'仅虚构资料，不能接生产系统。'}],history:[{text:'已更正的旧说法'}],evidence:[{role:'当前',line:9}]}]}},clean:{version:'clean-save',processed:'中文清洗稿，未审批😀。'}};}

test('JPG is a valid image and retains conditions/history and saved version',async()=>{
 const {api,drawn}=engine(),files=await api.render(record(),'board','jpg');
 const image=await canvasLib.loadImage(Buffer.from(await files[0].blob.arrayBuffer()));assert.equal(image.width,1120);
 assert(drawn.some(s=>s.includes('未审批')));assert(drawn.some(s=>s.includes('不能接生产系统')));assert(drawn.some(s=>s.includes('已更正')));assert(drawn.some(s=>s.includes('board-save')));
});

test('long clean PDF spans pages without dropping the tail and loads in an independent reader',async()=>{
 const {api,drawn}=engine(),r=record();r.clean.processed='首部工程标记\n'+'中文内容，负责人尚未确定。\n'.repeat(120)+'尾部独立工程标记';
 const [file]=await api.render(r,'clean','pdf');const doc=await pdfjs.getDocument({data:new Uint8Array(await file.blob.arrayBuffer())}).promise;
 assert(doc.numPages>2);assert(drawn.some(s=>s.includes('首部工程标记')));assert(drawn.some(s=>s.includes('尾部独立工程标记')));assert(drawn.every(s=>typeof s==='string'));await doc.destroy();
});

test('partial and human-edited artifacts remain labelled in exported paragraphs',()=>{
 const {api}=engine(),r=record();r.clean={...r.clean,human_edited:true,processing_complete:false,processed_batches:1,total_batches:2,rows:[{text:'未处理原话',cleaned:null}]};
 const rows=api.paragraphs(r,'clean').map(x=>x.text).join('\n');assert.match(rows,/人工修改/);assert.match(rows,/未全部完成/);assert.match(rows,/未清洗 · 保留原文/);
});
