// Dependency-free, local image/PDF export. Only saved text, no remote assets or HTML.
window.MeetingExport=(()=>{
  const bytes=s=>new TextEncoder().encode(s);
  const join=parts=>{const out=new Uint8Array(parts.reduce((n,p)=>n+p.length,0));let at=0;for(const p of parts){out.set(p,at);at+=p.length;}return out;};
  function pdf(images){
    const objects=[null,bytes('<< /Type /Catalog /Pages 2 0 R >>'),bytes(`<< /Type /Pages /Count ${images.length} /Kids [${images.map((_,i)=>`${3+i*3} 0 R`).join(' ')}] >>`)];
    for(let i=0;i<images.length;i++){
      const id=3+i*3,ops=bytes('q 595.28 0 0 841.89 0 0 cm /Im0 Do Q\n'),image=images[i];
      objects.push(bytes(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.28 841.89] /Resources << /XObject << /Im0 ${id+2} 0 R >> >> /Contents ${id+1} 0 R >>`));
      objects.push(join([bytes(`<< /Length ${ops.length} >>\nstream\n`),ops,bytes('endstream')]));
      objects.push(join([bytes(`<< /Type /XObject /Subtype /Image /Width 1120 /Height 1584 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${image.length} >>\nstream\n`),image,bytes('\nendstream')]));
    }
    const chunks=[bytes('%PDF-1.4\n')],offsets=[0];let length=chunks[0].length;
    for(let i=1;i<objects.length;i++){offsets.push(length);const chunk=join([bytes(`${i} 0 obj\n`),objects[i],bytes('\nendobj\n')]);chunks.push(chunk);length+=chunk.length;}
    chunks.push(bytes(`xref\n0 ${objects.length}\n0000000000 65535 f \n${offsets.slice(1).map(x=>String(x).padStart(10,'0')+' 00000 n \n').join('')}trailer\n<< /Size ${objects.length} /Root 1 0 R >>\nstartxref\n${length}\n%%EOF\n`));return new Blob(chunks,{type:'application/pdf'});
  }
  function paragraphs(record,product){
    const value=record[product]||(product==='board'?(record.legacy_result||record.result):null);
    if(!value)throw new Error('该产物没有已保存版本。');
    const rows=[{text:record.meta.title,size:32},{text:record.meta.sample?'界面样例 · 非AI生成':'待核对草稿 · 不代表已确认事实',size:22},{text:'保存版本：'+(value.version||'旧版'),size:18}];
    if(value.human_edited)rows.push({text:'人工修改版本 · 原有来源仅供重新核对',size:22});
    if(value.processing_complete===false)rows.push({text:`部分草稿：${value.processed_batches}/${value.total_batches}，未全部完成。`,size:22});
    if(product==='clean'){
      if(value.rows)for(const r of value.rows)rows.push({text:(r.cleaned===null&&r.text.trim()?'[未清洗 · 保留原文] ':'')+(r.cleaned??r.text),size:24});
      else rows.push({text:value.processed,size:24});
    }else for(const [section,items] of Object.entries(value.sections)){
      rows.push({text:section,size:28});if(!items.length)rows.push({text:'尚无已提取事项，不代表原文未提及。',size:22});
      for(const item of items){rows.push({text:(item.label?item.label+'：':'')+item.text,size:24});if(item.human_edited)rows.push({text:'[人工修改 · 尚未重新核对]',size:20});for(const c of item.conditions||[])rows.push({text:'前提 / 限定：'+c.text,size:22});for(const h of item.history||[])rows.push({text:'历史（已更正）：'+h.text,size:22});rows.push({text:'依据：'+((item.evidence||[]).map(r=>(r.role||'原文')+'第'+r.line+'行').join('；')||'无可靠原文依据'),size:18});}
    }
    return rows;
  }
  function layout(ctx,rows){
    const result=[];for(const {text,size} of rows){ctx.font=`${size}px sans-serif`;for(const paragraph of String(text??'').split(/\r?\n/)){let line='';for(const char of paragraph){if(line&&ctx.measureText(line+char).width>1008){result.push({text:line,size,height:Math.ceil(size*1.6)});line='';}line+=char;}result.push({text:line,size,height:Math.ceil(size*1.6)});}result.push({text:'',size:12,height:12});}return result;
  }
  const blob=(canvas,type)=>new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('图片生成失败，没有裁切或截断正文。')),type,.94));
  async function render(record,product,format){
    await document.fonts?.ready;const canvas=document.createElement('canvas');canvas.width=1120;canvas.height=1584;const ctx=canvas.getContext('2d');if(!ctx)throw new Error('浏览器不支持图片导出。');
    const lines=layout(ctx,paragraphs(record,product)),height=lines.reduce((n,l)=>n+l.height,112);
    // A single JPG where safe; longer boards are explicitly paged, never truncated.
    const pageHeight=format==='jpg'&&height<=28000?height:1584;canvas.height=pageHeight;
    const pages=[];let y=56,index=1;
    function clear(){ctx.fillStyle='#fff';ctx.fillRect(0,0,1120,pageHeight);ctx.fillStyle='#273247';ctx.textBaseline='top';y=56;}
    async function finish(){ctx.fillStyle='#788397';ctx.font='18px sans-serif';ctx.fillText(`第 ${index++} 页 · 已保存版本 · 待核对`,56,pageHeight-36);pages.push(await blob(canvas,'image/jpeg'));}
    clear();for(const line of lines){if(y+line.height>pageHeight-56){await finish();clear();}ctx.fillStyle='#273247';ctx.font=`${line.size}px sans-serif`;ctx.fillText(line.text,56,y);y+=line.height;}await finish();
    if(format==='pdf')return [{blob:pdf(await Promise.all(pages.map(async b=>new Uint8Array(await b.arrayBuffer())))),name:(product==='board'?'销售看板':'清洗稿')+'-待核对.pdf'}];
    return pages.map((b,i)=>({blob:b,name:'销售看板-待核对'+(pages.length>1?`-第${i+1}页`:'')+'.jpg'}));
  }
  function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);}
  return {render,download,pdf,paragraphs};
})();
