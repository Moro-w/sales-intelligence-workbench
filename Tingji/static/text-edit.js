// Human editing on the existing meeting page. Never edits original.md or calls a model.
function createMeetingEditor(id,getRecord,changed,message) {
  const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  let opened=false;
  const post=async(path,body)=>{const r=await apiFetch(`/api/meetings/${id}/${path}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw new Error(d.detail||'保存失败，输入保留。');return d;};
  async function open(product) {
    if(opened)return;const record=getRecord(),current=record[product];if(!current){message('请先生成该产物。旧版结果暂为只读。');return;}
    opened=true;const draft=record.edit_drafts?.[product];let draftVersion=draft?.draft_version??null,dirty=false,chain=Promise.resolve(),timer=null,closed=false;
    const dialog=node('dialog');dialog.className='text-editor-dialog';dialog.setAttribute('aria-label',product==='board'?'编辑销售看板':'编辑清洗稿');
    dialog.append(node('h2',product==='board'?'编辑销售看板':'编辑完整清洗稿'),node('p','人工修改会保留标记，原有依据只供复核；原始稿不会改变。自动草稿不等于更新已保存版本。'));
    const status=node('p');status.className='text-editor-status';status.setAttribute('role','status');dialog.append(status);
    const form=node('div');form.className='text-editor-fields';dialog.append(form);const inputs=[];let cleanInput;
    const field=(label,value,parent=form,rows=3)=>{const wrap=node('label',label),input=node('textarea');input.rows=rows;input.value=value||'';wrap.append(input);parent.append(wrap);return input;};
    function add(item,section) {
      const group=node('fieldset');group.append(node('legend',section));form.append(group);
      let choice=null;if(!item.id){choice=node('select');for(const title of Object.keys(current.sections))choice.append(new Option(title,title));choice.value=section;choice.setAttribute('aria-label','新事项所属区域');group.append(choice);}
      const entry={id:item.id??null,section,choice,label:field('事项名称',item.label||'人工补充',group,1),text:field('结论',item.text,group),conditions:field('前提 / 限定（每行一项）',(item.conditions||[]).map(x=>typeof x==='string'?x:x.text).join('\n'),group),history:field('已更正的旧说法（每行一项）',(item.history||[]).map(x=>typeof x==='string'?x:x.text).join('\n'),group)};
      if(item.id){const label=node('label','删除此事项 ');entry.remove=node('input');entry.remove.type='checkbox';label.append(entry.remove);group.append(label);}inputs.push(entry);
    }
    const restored=draft&&draft.base_version===current.version?draft.patch:null;
    if(product==='clean')cleanInput=field('清洗稿正文',restored?.processed??current.processed,form,20);
    else {
      const saved=new Map((restored?.fields||[]).filter(x=>x.id).map(x=>[x.id,x]));
      for(const [section,items] of Object.entries(current.sections))for(const item of items){const value=saved.get(item.id);add(value?{...item,...value}:item,section);if(value?.delete)inputs[inputs.length-1].remove.checked=true;}
      for(const item of restored?.fields||[])if(!item.id)add(item,item.section);
      const more=node('button','新增人工补充');more.type='button';more.onclick=()=>{add({text:''},'待确认问题');markDirty();};dialog.append(more);
    }
    if(draft&&!restored){status.textContent='检测到旧版本草稿，保存已受保护。以下保留旧输入供比较，不会静默覆盖。';form.prepend(node('pre',JSON.stringify(draft.patch,null,2)));}
    else if(restored)status.textContent='已恢复上次自动保存的草稿，尚未更新保存版本。';
    function patch(){return product==='clean'?{processed:cleanInput.value}:{fields:inputs.map(x=>({id:x.id,section:x.choice?.value||x.section,label:x.label.value,text:x.text.value,conditions:x.conditions.value.split('\n').filter(s=>s.trim()),history:x.history.value.split('\n').filter(s=>s.trim()),...(x.remove?.checked?{delete:true}:{})}))};}
    function persist(){
      clearTimeout(timer);const edits=patch();chain=chain.catch(()=>{}).then(async()=>{if(!dirty||closed)return;const serial=JSON.stringify(edits);status.textContent='正在保存编辑草稿…';const saved=await post('draft',{product,base_version:current.version,patch:edits,draft_version:draftVersion});draftVersion=saved.draft_version;if(JSON.stringify(patch())===serial)dirty=false;status.textContent='编辑草稿已保存；点击“保存版本”更新正式内容。';});return chain;
    }
    function markDirty(){dirty=true;status.textContent='有未保存修改';clearTimeout(timer);timer=setTimeout(()=>persist().catch(e=>status.textContent=e.message),700);}
    form.addEventListener('input',markDirty);form.addEventListener('change',markDirty);
    const actions=node('div');actions.className='text-editor-actions';dialog.append(actions);
    const save=node('button','保存版本'),close=node('button','关闭编辑（保留草稿）');save.type=close.type='button';actions.append(save,close);
    function dismiss(){closed=true;opened=false;clearTimeout(timer);window.removeEventListener('beforeunload',leaving);dialog.close();dialog.remove();}
    const leaving=e=>{if(dirty){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',leaving);
    save.onclick=async()=>{save.disabled=close.disabled=true;try{await persist();await post('save',{product,base_version:current.version,patch:patch(),draft_version:draftVersion});dirty=false;await changed();dismiss();message('已保存新版本；人工修改仍需核对，原稿未改变。');}catch(e){status.textContent=e.message;}finally{save.disabled=close.disabled=false;}};
    close.onclick=async()=>{try{await persist();await changed();dismiss();}catch(e){status.textContent=e.message;}};
    dialog.addEventListener('cancel',e=>{e.preventDefault();close.click();});document.body.append(dialog);dialog.showModal();
  }
  function compare(product){
    if(opened)return;const record=getRecord(),prior=record[product],next=record.candidates?.[product];if(!prior||!next)return;
    opened=true;const d=node('dialog');d.className='text-editor-dialog';d.append(node('h2','比较更新建议'),node('p','已保存的人工修改不会自动覆盖。采纳只更新当前版本，旧版本保留；不代表事实已核对。'));
    const plain=v=>product==='clean'?v.processed:Object.entries(v.sections).map(([s,items])=>s+'\n'+items.map(i=>i.text+'\n'+(i.conditions||[]).map(c=>'前提：'+c.text).join('\n')+'\n'+(i.history||[]).map(h=>'旧说法：'+h.text).join('\n')).join('\n')).join('\n\n');
    const columns=node('div');columns.className='text-compare-columns';for(const [title,v] of [['当前保存版本',prior],['模型新建议',next]]){const block=node('section');block.append(node('h3',title),node('pre',plain(v)));columns.append(block);}d.append(columns);const status=node('p');status.setAttribute('role','status');d.append(status);
    for(const [label,adopt] of [['保留当前版本',false],['采纳新建议',true]]){const b=node('button',label);b.type='button';b.onclick=async()=>{b.disabled=true;try{await post('suggestion',{product,base_version:prior.version,candidate_version:next.version,adopt});await changed();opened=false;d.close();d.remove();}catch(e){status.textContent=e.message;b.disabled=false;}};d.append(b);}
    d.addEventListener('close',()=>{opened=false;d.remove();});document.body.append(d);d.showModal();
  }
  return {open,compare};
}
