// Text-only adaptation of Tingji's existing upload/history/detail pages.
window.TINGJI_TEXT_ONLY = true;
document.documentElement.classList.add('text-only');
const TEXT_PREFIX = '/api/meeting-assistant';
function textReady(id = null) {
  if (parent !== window) parent.postMessage({ type: 'tingji.ready', meetingId: id }, location.origin);
}
function initTextIndex() {
  document.title = '会议纪录助手';
  document.querySelector('.brand').textContent = '会议纪录助手';
  document.querySelector('.brand-sub').textContent = '把会议稿变成可核对的工作资料';
  const steps = document.querySelectorAll('.step');
  [['导入会议稿', '选择未经清洗的 Markdown，不用提前整理'], ['保存原文', '保留原件，刷新或重开后仍能找回'], ['清洗与看板', '导入即确认：先清洗完整逐字稿，再从清洗稿生成看板']].forEach((s, i) => {
    steps[i].querySelector('.step-title').textContent = s[0];
    steps[i].querySelector('.step-desc').textContent = s[1];
  });
  document.querySelector('#dropzone p').textContent = '拖入会议稿，或点击选择文件';
  document.querySelector('#dropzone .hint').textContent = 'UTF-8 编码的 .md · 每份不超过 1 MB / 20,000 行 · 一小时以内会议';
  document.getElementById('dropzone').setAttribute('aria-label', '选择或拖入 Markdown 会议稿');
  document.getElementById('file-input').accept = '.md,text/markdown';
  document.getElementById('upload-hint').textContent = '可修改标题。';
  const upload=document.querySelector('.upload-card');upload.hidden=true;
  const open=document.createElement('button');open.type='button';open.className='text-import-button';open.textContent='导入会议纪录';open.setAttribute('aria-expanded','false');
  open.onclick=()=>{upload.hidden=!upload.hidden;open.setAttribute('aria-expanded',String(!upload.hidden));if(!upload.hidden)document.getElementById('dropzone').focus();};
  document.querySelector('header').append(open);
  const section=document.getElementById('history').parentElement;section.classList.add('text-history-section');
  section.querySelector('h2').hidden=true;
  document.querySelector('.brand-sub').textContent='一眼看清要跟进的客户和待办';
  document.getElementById('upload-hint').textContent='导入后自动清洗逐字稿并生成销售看板，无需再次点击。';
  const todo=document.createElement('section');todo.id='text-todo-strip';todo.className='text-todo-strip';section.prepend(todo);
  const bar=document.createElement('div');bar.className='text-home-bar';
  const tabs=document.createElement('div');tabs.className='text-home-tabs';tabs.setAttribute('role','tablist');
  for(const [key,label] of HOME_TABS){const t=document.createElement('button');t.type='button';t.dataset.tab=key;t.textContent=label;t.className='text-home-tab'+(key==='all'?' active':'');t.setAttribute('role','tab');
    t.onclick=()=>{window.textHomeTab=key;tabs.querySelectorAll('button').forEach(x=>x.classList.toggle('active',x===t));renderHistory();};tabs.append(t);}
  const search=document.createElement('input');search.type='search';search.id='text-meeting-search';search.placeholder='查找客户或会议';search.setAttribute('aria-label','查找客户或会议');search.oninput=()=>renderHistory();
  bar.append(tabs,search);todo.after(bar);
  textReady();
}
async function copyMeetingReference(id,button) {
  try{await navigator.clipboard.writeText(`[[meeting:${id}]]`);button.textContent='已复制会议引用';}
  catch(_){button.textContent='复制失败，请重试';}
}
function confirmTextAction(message) {
  return new Promise(resolve=>{const d=document.createElement('dialog');d.className='text-editor-dialog';const p=document.createElement('p');p.textContent=message;d.append(p);
    for(const [label,yes] of [['取消',false],['继续',true]]){const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=()=>{d.close();d.remove();resolve(yes);};d.append(b);}
    d.addEventListener('cancel',()=>{d.remove();resolve(false);},{once:true});document.body.append(d);d.showModal();});
}
const HOME_TABS=[['all','全部'],['follow','待跟进'],['processing','处理中'],['attention','需处理'],['archived','已归档']];
function homeState(m){
  if(m.error)return 'attention';
  const st=m.completion?.state;
  if(st==='generated'||st==='saved')return 'follow';
  if(st==='processing'||Object.values(m.product_states||{}).some(x=>['queued','running','waiting'].includes(x)))return 'processing';
  if(st==='sample')return 'sample';
  return 'attention';
}
function dueRank(due){
  if(!due)return 9;if(/今天|今日|马上|尽快/.test(due))return 0;if(/明天/.test(due))return 1;if(/后天/.test(due))return 2;
  if(/周[一二三四五六日天]|星期|礼拜/.test(due))return /下周/.test(due)?4:3;return 5;
}
function renderTextHome(all){
  const list=document.getElementById('history');list.replaceChildren();
  const node=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  const live=all.filter(m=>!m.archived),tab=window.textHomeTab||'all';
  const counts={all:live.length,archived:all.length-live.length};for(const m of live){const k=homeState(m);counts[k]=(counts[k]||0)+1;}
  document.querySelectorAll('.text-home-tab').forEach(t=>{const label=HOME_TABS.find(x=>x[0]===t.dataset.tab)[1];t.textContent=label+(counts[t.dataset.tab]?` ${counts[t.dataset.tab]}`:'');});
  // Priority to-dos across meetings: my own first, then earliest due.
  const strip=document.getElementById('text-todo-strip');strip.replaceChildren();
  const todos=live.flatMap(m=>(m.card?.todos||[]).map(t=>({...t,m,mine:/销售|我方|我们/.test(t.owner||'')})));
  todos.sort((x,y)=>(y.mine-x.mine)||(dueRank(x.due)-dueRank(y.due))||(y.confirmed-x.confirmed));
  if(todos.length){
    const head=node('div','text-strip-head');head.append(node('h2','','我的优先待办'),node('span','text-strip-sub',`${todos.filter(t=>t.mine).length} 项我方待办 · ${todos.filter(t=>!t.mine).length} 项等客户`));strip.append(head);
    const row=node('div','text-todo-row');
    for(const t of todos.slice(0,4)){
      const r=dueRank(t.due),tile=node('a','text-todo-tile '+(r===0?'due-now':r===1?'due-soon':t.mine?'due-later':'due-wait'));tile.href=meetingUrl(t.m.id)+'#next';
      const top=node('div','text-todo-top');top.append(node('span','text-todo-due',t.due||'期限未明确'));if(!t.mine)top.append(node('span','text-todo-wait','等客户'));else if(!t.confirmed)top.append(node('span','text-todo-wait','待确认'));
      tile.append(top,node('strong','',t.label),node('span','text-todo-meta',(t.m.card?.customer_name||t.m.title)+(t.owner?' · '+t.owner:'')));row.append(tile);
    }
    strip.append(row);if(todos.length>4)strip.append(node('p','text-strip-more',`另有 ${todos.length-4} 项，打开对应会议查看`));
  }
  const term=(document.getElementById('text-meeting-search')?.value||'').trim().toLocaleLowerCase();
  let shown=tab==='archived'?all.filter(m=>m.archived):tab==='all'?live:live.filter(m=>homeState(m)===tab);
  if(term)shown=shown.filter(m=>(m.title+' '+(m.card?.customer_name||'')).toLocaleLowerCase().includes(term));
  const rank=m=>({attention:1,processing:2,follow:0,sample:3}[homeState(m)]??4);
  shown=[...shown].sort((x,y)=>rank(x)-rank(y)||Math.min(...(x.card?.todos||[]).map(t=>dueRank(t.due)),9)-Math.min(...(y.card?.todos||[]).map(t=>dueRank(t.due)),9));
  for(const m of shown)list.append(buildSalesCard(m));
  if(!shown.length)list.append(node('li','text-home-empty',all.length?'这里暂时没有会议。':'暂无会议，点右上角“导入会议纪录”开始。'));
}
function buildSalesCard(m) {
  const node=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  const state=homeState(m),card=m.card||{},li=node('li','text-meeting-card text-tile-'+state);li.dataset.id=m.id;
  const open=()=>{location.href=meetingUrl(m.id);};li.tabIndex=0;li.setAttribute('role','link');li.setAttribute('aria-label','打开会议 '+m.title);li.onclick=open;
  li.onkeydown=e=>{if(e.target===li&&e.key==='Enter'){e.preventDefault();open();}};
  const name=card.customer_name||m.title;
  const top=node('div','text-tile-top');top.append(node('span','text-tile-icon',[...name.replace(/^[A-Za-z]稿\s*·\s*/,'')][0]||'会'));
  const who=node('div','text-tile-who');who.append(node('h3','text-card-title',name),node('p','text-card-topic',card.customer_name?m.title:(m.status_label||'')));top.append(who);
  const go=node('span','text-tile-open',{follow:'跟进',processing:'处理中',attention:'处理',sample:'查看'}[state]||'打开');top.append(go);li.append(top);
  const lines=node('dl','text-tile-lines');
  const next=(card.todos||[])[0],block=(card.obstacle_points||[])[0];
  for(const [k,v] of [['下一步',next?next.label+(next.due&&!next.label.includes(next.due)?' · '+next.due:''):''],['阻碍',block||'']]){if(!v)continue;const row=node('div','');row.append(node('dt','',k),node('dd','',v));lines.append(row);}
  if(lines.children.length)li.append(lines);
  else li.append(node('p','text-card-empty',state==='processing'?'正在清洗和生成看板…':m.error?.message||m.status_label||'尚未生成看板'));
  const chips=node('div','text-tile-chips');
  if(card.pending_count)chips.append(node('span','warn',`待确认 ${card.pending_count}`));
  if((card.todos||[]).length>1)chips.append(node('span','',`待办 ${card.todos.length}`));
  if(card.need_count)chips.append(node('span','',`需求 ${card.need_count}`));
  if(m.archived)chips.append(node('span','',`已归档`));
  const tools=node('span','text-tile-tools');
  const cite=node('button','mini text-card-cite','复制引用');cite.disabled=!!m.sample||!!m.error;cite.title=m.sample?'静态样例不发送模型':'粘贴到本平台办公聊天，发送时读取最新已保存资料';cite.onclick=e=>{e.stopPropagation();copyMeetingReference(m.id,cite);};
  const keep=node('button','mini text-card-archive',m.archived?'取消归档':'归档');keep.title=m.archived?'放回会议列表':'从列表隐藏，不删除任何内容';
  keep.onclick=async e=>{e.stopPropagation();keep.disabled=true;try{const r=await apiFetch(`/api/meetings/${m.id}/archive`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({archived:!m.archived})});if(!r.ok)throw new Error();await loadHistory();}catch(_){keep.disabled=false;keep.textContent='操作失败';}};
  tools.append(cite,keep);chips.append(tools);li.append(chips);
  return li;
}
async function initTextMeeting() {
  const id = location.pathname.split('/').pop();
  document.title = '会议原文 · 会议纪录助手';
  document.querySelector('.brand').textContent = '会议纪录助手';
  document.querySelector('[data-tab="processed"]').disabled = true;
  document.querySelector('[data-tab="summary"]').disabled = true;
  document.querySelector('[data-tab="summary"]').textContent = '销售看板';
  document.querySelector('[data-tab="processed"]').textContent = '清洗稿';
  document.querySelector('[data-tab="compare"]').hidden = true;
  document.getElementById('search-input').placeholder = '搜索原文';
  const transcript = document.getElementById('transcript');
  transcript.textContent = '正在读取已保存的原文…';
  textReady(id);
  try {
    const response = await apiFetch(`/api/meetings/${id}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '读取失败，请稍后重试。');
    document.getElementById('m-title').textContent = data.meta.title;
    document.title = `${data.meta.title} · 会议纪录助手`;
    document.getElementById('m-meta').textContent = `导入于 ${fmtDate(data.meta.created_at)}`;
    const notice = document.getElementById('polish-warning');
    notice.classList.remove('hidden');
    document.getElementById('polish-warning-text').textContent = '原文已保存。';
    transcript.replaceChildren();
    const nodes = data.source_index.segments.map(segment => {
      const line = document.createElement('div');
      line.className = 'transcript-line text-source-line';
      line.id = segment.segment_id;
      line.dataset.sourceId = segment.segment_id;
      const number = document.createElement('span');
      number.className = 'text-line-number';
      number.textContent = String(segment.line);
      number.setAttribute('aria-label', `第 ${segment.line} 行`);
      const content = document.createElement('span');
      content.className = 'text-line-content';
      // Do not render user Markdown as HTML, load remote images or invent timestamps.
      content.textContent = data.source_text.slice(segment.start_utf16, segment.end_utf16).replace(/\r?\n$/, '');
      line.append(number, content);
      transcript.appendChild(line);
      return line;
    });
    let matches = [], selected = -1;
    const search = document.getElementById('search-input');
    const count = document.getElementById('search-count');
    function move(delta) {
      nodes.forEach(n => n.classList.remove('active'));
      if (!matches.length) { count.textContent = search.value ? '无匹配' : ''; return; }
      selected = (selected + delta + matches.length) % matches.length;
      matches[selected].classList.add('active');
      matches[selected].scrollIntoView({ block: 'center', behavior: 'smooth' });
      count.textContent = `${selected + 1} / ${matches.length}`;
    }
    search.addEventListener('input', () => {
      const term = search.value.toLocaleLowerCase();
      matches = term ? nodes.filter(n => n.lastElementChild.textContent.toLocaleLowerCase().includes(term)) : [];
      selected = -1; move(1);
    });
    search.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); move(e.shiftKey ? -1 : 1); } });
    document.getElementById('search-prev').onclick = () => move(-1);
    document.getElementById('search-next').onclick = () => move(1);
    const format = document.getElementById('export-format');
    format.replaceChildren(new Option('原始稿 .md', 'original'));
    document.getElementById('export-btn').textContent = '下载原件';
    document.getElementById('export-btn').onclick = () => { location.href = tingjiUrl(`/api/meetings/${id}/source`); };
    setupTextProcessing(id, data, nodes);
  } catch (error) {
    transcript.textContent = error.message;
    transcript.setAttribute('role', 'alert');
    document.getElementById('export-btn').disabled = true;
  }
}
