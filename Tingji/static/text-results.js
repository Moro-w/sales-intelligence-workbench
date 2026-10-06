// Board-first adapter on Tingji's existing page. Model/user text is never rendered as HTML.
function setupTextProcessing(id, initial, sourceNodes) {
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  const button=(text,fn,cls='mini')=>{const n=el('button',cls,text);n.type='button';n.onclick=fn;return n;};
  const rawPanel=document.getElementById('tab-raw'), transcript=document.getElementById('transcript');
  const cleanPanel=document.getElementById('processed-md'), summaryPanel=document.getElementById('tab-summary');
  const notice=document.getElementById('polish-warning-text');
  const controls=el('div','text-job-controls'), progress=el('span','text-job-progress');progress.setAttribute('role','status');
  const actions=el('div','text-product-actions');
  const boardAction=button('生成销售看板',()=>start('board'));boardAction.id='text-start-btn';
  const cleanAction=button('生成清洗稿',()=>start('clean'));cleanAction.id='text-clean-btn';
  const allAction=button('开始处理：先清洗，再生成看板',()=>start('both'));allAction.id='text-start-all-btn';
  actions.append(allAction,boardAction,cleanAction);controls.append(progress,actions);document.querySelector('.meeting-meta').append(controls);
  const details=el('details','text-task-details'), detailSummary=el('summary','','处理详情'), detailBody=el('div');details.append(detailSummary,detailBody);controls.insertBefore(details,actions);
  const split=el('div','text-result-split'), board=el('div','text-sales-board'), source=el('aside','text-board-source');
  board.setAttribute('aria-label','销售看板');source.setAttribute('aria-label','原文依据');
  const sourceHeading=el('h3','','原始逐字稿'), sourceHint=el('div','text-source-hint','点击看板中的数据核对原话。');sourceHint.setAttribute('role','status');source.append(sourceHeading,sourceHint);
  const toolbar=el('div','text-board-toolbar'), tools=el('div','text-source-tools');
  const toggle=button('收起原文',()=>{sourceHidden=!sourceHidden;syncSource();});toggle.setAttribute('aria-expanded','true');
  const width=el('input');width.type='range';width.min='28';width.max='55';width.value='38';width.setAttribute('aria-label','原文栏宽度');width.oninput=()=>split.style.setProperty('--source-width',width.value+'%');
  tools.append(toggle,width);toolbar.append(el('span','',''),tools);split.append(board,source);summaryPanel.replaceChildren(toolbar,split);
  const kinds={human_added:'人工修改 / 补充 · 未重新核对',reported:'原话信息',sales_claim:'供应商自述 · 未验证',tentative:'意向 / 尚未确定',conflict:'存在分歧',agreed:'原文明确约定',declined:'明确未同意 / 已排除',ai_suggestion:'AI建议（非会议事实）'};
  const active=new Set(['queued','running','cleaning','extracting','saving']);const polling=new Set([...active,'waiting']);
  let record=initial, timer=null, closed=false, selected='summary', sending=false, sourceHidden=true, focusedId=null, requestError='';
  const abort=new AbortController();
  function currentBoard(){return record.board||record.legacy_result||record.result;}
  function jobFor(p){return record.products?.[p]||(p==='board'?record.job:{state:'idle',done:0,total:0,gate:{enabled:false}});}
  function gateFor(p){return jobFor(p)?.gate||record.model_gate||{enabled:false};}
  function syncSource(){split.classList.toggle('text-source-hidden',sourceHidden);toggle.textContent=sourceHidden?'显示原文':'收起原文';toggle.setAttribute('aria-expanded',String(!sourceHidden));width.hidden=sourceHidden;}
  function showTab(tab){
    selected=tab;document.querySelector('.meeting-body').classList.toggle('text-board-active',tab==='summary');document.body.classList.toggle('text-on-board',tab==='summary');
    document.querySelectorAll('[data-edit-product]').forEach(b=>{b.hidden=b.dataset.editProduct!==(tab==='summary'?'board':tab==='processed'?'clean':'');});
    document.querySelectorAll('.tab-btn').forEach(b=>{const on=b.dataset.tab===tab;b.classList.toggle('active',on);b.setAttribute('aria-selected',String(on));});
    document.querySelectorAll('.meeting-body > .panel').forEach(p=>p.classList.toggle('active',p.id==='tab-'+tab));
    (tab==='summary'?source:rawPanel).append(transcript);
  }
  for(const b of document.querySelectorAll('.tab-btn'))if(b.dataset.tab!=='compare')b.onclick=()=>showTab(b.dataset.tab);
  // The primary product is first, even before it exists.
  const boardTab=document.querySelector('[data-tab="summary"]');boardTab.parentElement?.prepend(boardTab);
  function clearHighlight(){sourceNodes.forEach((n,i)=>{n.classList.remove('active');const s=record.source_index.segments[i];n.lastElementChild.textContent=record.source_text.slice(s.start_utf16,s.end_utf16).replace(/\r?\n$/,'');});}
  function locate(item,index,card){
    const ref=item.evidence[index];clearHighlight();board.querySelectorAll('.text-field-selected').forEach(n=>n.classList.remove('text-field-selected'));
    focusedId=item.id;card.classList.add('text-field-selected');card.open=true;sourceHidden=false;syncSource();showTab('summary');
    if(!ref||!Number.isInteger(ref.start_utf16)||!Number.isInteger(ref.end_utf16)||ref.start_utf16<0||ref.end_utf16<=ref.start_utf16||record.source_text.slice(ref.start_utf16,ref.end_utf16)!==ref.quote){sourceHint.textContent='出处不匹配，未进行近似跳转。';return;}
    let first=null;record.source_index.segments.forEach((s,i)=>{
      if(s.end_utf16<=ref.start_utf16||s.start_utf16>=ref.end_utf16)return;
      const node=sourceNodes[i],raw=record.source_text.slice(s.start_utf16,s.end_utf16).replace(/\r?\n$/,'');
      const a=Math.max(0,ref.start_utf16-s.start_utf16),b=Math.min(raw.length,ref.end_utf16-s.start_utf16);if(b<=a)return;
      const mark=el('mark','text-exact-evidence',raw.slice(a,b));node.lastElementChild.replaceChildren(document.createTextNode(raw.slice(0,a)),mark,document.createTextNode(raw.slice(b)));node.classList.add('active');first??=node;
    });
    sourceHint.textContent=`${item.human_edited?'修改前依据 · 新结论未验证':ref.role||'原文'} · ${index+1}/${item.evidence.length}处 · 第${ref.line}行 · 待核对`;
    first?.scrollIntoView({block:'center',behavior:'smooth'});
  }
  // Speaker-stated uncertainty in the clean transcript: highlighted, never resolved by the app.
  const UNSURE=/[^。！？；，\n]*(?:说不清|没听准|没听清|怕说错|记不清|不确定|不知道|还没定|没定|说乱了|待确认|不先填)[^。！？；，\n]*/g;
  function markUncertain(target,text){let last=0,count=0;for(const m of text.matchAll(UNSURE)){if(!m[0].trim())continue;target.append(text.slice(last,m.index),el('mark','text-unsure',m[0]));last=m.index+m[0].length;count++;}target.append(text.slice(last));return count;}
  const SHOWN=5;
  const SHORT={'后续行动':'下一步','待确认问题':'待确认','客户需求与产品适配':'客户需求','已达成事项':'已达成','决策与采购流程':'决策与采购'};
  function headline(item){
    let text=item.label||item.text;
    if(item.due&&item.due!=='未明确'&&!text.includes(item.due))text+=' · '+item.due;
    if(item.topic==='budget'&&/不是已批|未批|没批|未审批|尚未审批|未获批/.test(item.text)&&!text.includes('未审批'))text+=' · 未审批';
    return text;
  }
  function openItem(section,item){
    const node=[...board.querySelectorAll('.text-board-section')].find(n=>n.dataset.section===section);if(!node||!item)return;node.open=true;
    const card=[...node.querySelectorAll('.text-board-field')].find(c=>c.dataset.fieldId===item.id);if(card){card.hidden=false;card.open=true;card.scrollIntoView({block:'nearest',behavior:'smooth'});}
  }
  const tags={tentative:'待确认',sales_claim:'我方自述',agreed:'已约定',declined:'未同意',conflict:'有分歧',ai_suggestion:'AI建议'};
  function renderField(item){
    const card=el('details','text-board-field'),blocked=item.review_state==='blocked';card.dataset.fieldId=item.id;
    if(item.id===focusedId){card.classList.add('text-field-selected');card.open=true;}if(blocked)card.classList.add('text-field-blocked');
    const summary=el('summary','text-field-headline');summary.append(el('span','text-field-label',item.label||item.text));
    const chips=[];if(item.human_edited)chips.push('人工修改');else if(tags[item.kind])chips.push(tags[item.kind]);
    if(item.topic==='budget'&&/不是已批|未批|没批|未审批|尚未审批|未获批/.test(item.text))chips.push('未审批');
    if(item.due&&item.due!=='未明确'&&!(item.label||'').includes(item.due))chips.unshift(item.due);
    if(item.conditions?.length)chips.push(`${item.conditions.length}项前提`);if(blocked)chips.push('待修复');
    for(const c of chips)summary.append(el('span','text-field-tag'+(['未审批','待确认','有分歧','待修复'].includes(c)?' text-field-tag-warn':''),c));card.append(summary);
    const body=el('div','text-field-body');
    const value=button(item.text,()=>locate(item,0,card),'text-field-value');value.disabled=!item.evidence?.length;value.title='点击查看原文';value.setAttribute('aria-label','核对原文：'+item.text);body.append(value);
    if(item.conditions?.length){body.append(el('p','text-condition-label','前提与范围'));const list=el('ul','text-fact-conditions');for(const c of item.conditions)list.append(el('li','',c.text));body.append(list);}
    if(item.history?.length){const d=el('details','text-fact-history');d.append(el('summary','','已更正的旧说法'));for(const h of item.history)d.append(el('p','',h.text));body.append(d);}
    const plan=[item.owner&&item.owner!=='未明确'?'负责人：'+item.owner:'',item.due&&item.due!=='未明确'?'期限：'+item.due:''].filter(Boolean);
    if(plan.length)body.append(el('p','text-field-plan',plan.join(' · ')));
    const who=item.speaker==='未标明'?'':item.speaker;body.append(el('span','text-field-kind',(who?who+' · ':'')+(blocked?'此项待修复':kinds[item.kind]||'待核对')));
    const links=el('div','text-evidence-links');(item.evidence||[]).forEach((ref,i)=>links.append(button(`${ref.role||'原文'} ${i+1} · 第${ref.line}行`,()=>locate(item,i,card))));
    if(item.evidence?.length){body.append(button(item.evidence.length>1?`查看原文（${item.evidence.length}处）`:'查看原文',()=>locate(item,0,card),'mini text-open-source'));
      if(item.evidence.length>1){const more=el('details','text-evidence-more');more.append(el('summary','','逐处查看'),links);body.append(more);}else body.append(links);}
    else body.append(el('p','text-product-error',item.kind==='ai_suggestion'?'AI 建议，无会议事实出处':'依据不足，不展示为可靠结论。'));
    for(const issue of item.issues||[])body.append(el('p','text-product-error',issue.message));
    card.append(body);return card;
  }
  function render(){
    const result=currentBoard(), sample=record.meta.sample||result?.origin==='sample';board.replaceChildren();cleanPanel.replaceChildren();
    boardTab.disabled=false;boardTab.textContent=sample?'销售看板 · 样例':'销售看板';
    const clean=record.clean?.processed??record.legacy_result?.processed??record.result?.processed;const partial=record.clean?.rows||record.partial_clean;
    const cleanTab=document.querySelector('[data-tab="processed"]');cleanTab.disabled=clean===undefined&&!partial?.length;
    cleanTab.textContent=record.clean?'清洗稿':(clean!==undefined||partial?.length)?'清洗稿 · 旧版待核对':'清洗稿 · 未生成';
    if(record.clean?.human_edited)cleanPanel.append(el('p','text-product-error','人工编辑版本 · 原有逐段映射未重新验证，原稿仍只读。'));
    let unsure=0;
    if(partial?.length)for(const row of partial){const missing=row.cleaned===null&&!!row.text.trim();const line=el('div',missing?'text-uncleaned':'');if(missing)line.append('[未清洗 · 保留原文] ');unsure+=markUncertain(line,row.cleaned??row.text);if(!row.text.trim())line.style.minHeight='1em';cleanPanel.append(line);}
    else if(clean!==undefined){const box=el('div','');unsure+=markUncertain(box,clean);cleanPanel.append(box);}
    if(unsure)cleanPanel.prepend(el('p','text-unsure-note',`黄色标出 ${unsure} 处说话人自己表示不确定的话（如“说不清”“没听准”），这些内容不能当成定论。`));
    if(!result){
      const empty=el('div','text-board-empty');empty.append(el('h2','',jobFor('board')?.state==='waiting'?'清洗完成后自动生成销售看板':'销售看板尚未生成'),el('p','','一次确认后先清洗完整逐字稿，再从清洗稿提炼需求、预算、采购流程和下一步；点击依据仍跳回原始稿。两项分别保存，失败会显示实际状态。'));
      empty.append(el('p','text-empty-reason',jobFor('board')?.error?.message||(jobFor('board')?.state==='waiting'?(['failed','interrupted'].includes(jobFor('clean').state)?'清洗未完成，看板仍在等待；请先重试清洗。':'正在清洗，完成后自动开始提取看板。'):'')||gateFor('board').reason||'原稿已保存。点击上方“开始处理”。'));board.append(empty);
    }else{
      const legacy=!record.board&&!sample;
      if(sample||legacy)board.append(el('p',sample?'text-sample-banner':'text-board-caveat',sample?'界面样例 · 静态数据，非AI生成，不代表测试通过':'旧版结果 · 保留原貌，尚未按新流程重做'));
      if(result.processing_complete===false)board.append(el('p','text-product-error',`仅完成 ${result.processed_batches}/${result.total_batches} 部分，全文尚未处理完成。`));
      // One card: who, three key facts, then every section folded to a single line; open on demand.
      const items=n=>(result.sections[n]||[]),usableOf=n=>items(n).filter(i=>i.review_state!=='blocked');
      const pick=(n,pred=()=>true)=>usableOf(n).find(pred);
      const panel=el('div','text-board-card'),meeting=pick('会议概况',i=>i.topic==='meeting');
      const named=meeting?.customer||result.customer,customer=named&&named!=='未明确'?named:'';
      const head=el('div','text-board-head');head.append(el('h2','text-board-customer',customer||record.meta.title));
      if(customer)head.append(el('span','text-board-topic',record.meta.title));panel.append(head);
      // Stage: the agreed boundary (同意A、没同意B) wherever the model filed it, then any agreement that is not a to-do.
      function progressItem(){
        const pool=['已达成事项','决策与采购流程','预算与时间','待确认问题'].flatMap(n=>usableOf(n).filter(i=>i.kind==='agreed').map(i=>[n,i]));
        return pool.find(([,i])=>/没同意|未同意|不同意/.test(i.text))||pool.find(([n])=>n==='已达成事项')||pool[0]||['已达成事项',null];
      }
      const facts=el('div','text-board-facts');
      for(const [key,section,item] of [['预算','预算与时间',pick('预算与时间',i=>i.topic==='budget')],['进展',...progressItem()],['下一步','后续行动',pick('后续行动')]]){
        const row=button('',()=>openItem(section,item),'text-fact-row');row.disabled=!item;
        row.append(el('span','text-fact-key',key),el('span','text-fact-value',item?headline(item):'未提取'));facts.append(row);
      }
      panel.append(facts);
      const order=['后续行动','待确认问题','客户需求与产品适配','预算与时间','已达成事项','决策与采购流程','会议概况'];
      const names=[...order.filter(n=>n in result.sections),...Object.keys(result.sections).filter(n=>!order.includes(n))];
      const accordion=el('div','text-board-sections');
      for(const title of names){
        const all=items(title),usable=usableOf(title),blocked=all.filter(i=>i.review_state==='blocked');
        const section=el('details','text-board-section');section.dataset.section=title;
        const summary=el('summary','text-section-summary'),h=el('h3','',SHORT[title]||title);summary.append(h,el('span','text-section-count',String(usable.length)));
        summary.append(el('span','text-section-preview',usable.length?headline(usable[0])+(usable.length>1?` 等${usable.length}项`:''):'暂无'));section.append(summary);
        const body=el('div','text-section-body');
        if(!all.length)body.append(el('p','text-empty-field','暂无已提取事项，不能据此认定原文未提及。'));
        const list=el('div','text-field-list');usable.forEach((item,i)=>{const card=renderField(item);if(i>=SHOWN)card.hidden=true;list.append(card);});body.append(list);
        if(usable.length>SHOWN){const label=`展开其余 ${usable.length-SHOWN} 项`,more=button(label,()=>{const open=more.dataset.open!=='1';more.dataset.open=open?'1':'';[...list.children].forEach((n,i)=>{if(i>=SHOWN)n.hidden=!open;});more.textContent=open?'收起':label;},'mini text-section-more');body.append(more);}
        if(blocked.length){const d=el('details','text-blocked-fields');d.append(el('summary','',`另有 ${blocked.length} 条待人工补充`));blocked.forEach(item=>d.append(renderField(item)));body.append(d);}
        section.append(body);accordion.append(section);
      }
      panel.append(accordion);
      if(!sample&&!legacy)panel.append(el('p','text-board-caveat','AI 从清洗稿整理 · 点开任一条可核对原文'));
      board.append(panel);
      const notes=(result.issues||[]).filter(i=>!i.field_id);
      if(notes.length){const d=el('details','text-board-issues');d.append(el('summary','','核对提示'));for(const i of notes)d.append(el('p','',i.message));board.append(d);}
    }
    configureExports();showTab(selected);
    for(const [p,b] of [['board',editBoard],['clean',editClean]])b.disabled=!record[p];
    for(const [p,b] of [['board',compareBoard],['clean',compareClean]])b.hidden=!record.candidates?.[p];
  }
  function update(){
    const boardJob=jobFor('board'),cleanJob=jobFor('clean');const result=currentBoard();
    const first=boardJob.state==='idle'&&cleanJob.state==='idle';allAction.hidden=!first;boardAction.hidden=first;cleanAction.hidden=first;
    allAction.disabled=sending||!gateFor('board').enabled||!gateFor('clean').enabled||!!record.meta.sample;
    allAction.title=gateFor('board').reason||gateFor('clean').reason||'一次确认自动启动两项处理';
    progress.textContent=(record.meta.sample?'界面样例 · 非AI结果':active.has(cleanJob.state)?'第1步 清洗进行中…':cleanJob.state==='failed'?'清洗失败 · 可重试':cleanJob.state==='interrupted'?'清洗已中断 · 可重试':record.clean?'清洗稿待核对':'清洗尚未开始')+' ｜ '+(active.has(boardJob.state)?'第2步 从清洗稿提取看板…':boardJob.state==='waiting'?'看板等待清洗完成':result?'看板草稿 · 待核对':boardJob.state==='failed'?'看板生成失败':boardJob.state==='interrupted'?'看板已中断':'看板尚未开始');
    if(record.completion&&!record.meta.sample)progress.textContent=record.completion.label+' ｜ '+progress.textContent;
    // Paid regenerate actions stay out of the way once both results exist; first run, failures and progress stay visible.
    const settled=!first&&!!result&&![boardJob.state,cleanJob.state].some(st=>active.has(st)||['failed','interrupted','waiting'].includes(st));
    if(settled&&!record.meta.sample)progress.textContent=record.clean?'看板和清洗稿已生成':'看板已生成';
    (settled?details:controls).append(actions);detailSummary.textContent=settled?'处理详情 · 重新生成':'处理详情';
    for(const [p,b] of [['board',boardAction],['clean',cleanAction]]){const j=jobFor(p),artifact=record[p];b.textContent=active.has(j.state)?(p==='board'?'看板生成中…':'清洗中…'):['failed','interrupted'].includes(j.state)?(p==='board'?'重试看板':'重试清洗'):artifact?(p==='board'?'重新生成看板':'重新生成清洗稿'):(p==='board'?'生成销售看板':'生成清洗稿');const needsClean=p==='board'&&!(record.clean?.processing_complete||record.products?.clean?.model_version);if(p==='board'&&j.state==='waiting')b.textContent='等待清洗完成';
      b.disabled=sending||active.has(j.state)||!gateFor(p).enabled||!!record.meta.sample||needsClean;b.title=needsClean?'看板从清洗稿提取，需先完成清洗稿':gateFor(p).reason||'仅处理本会议';}
    const cause=boardJob.error?.code==='BUDGET_EXHAUSTED'?boardJob.last_error:boardJob.error;
    notice.textContent=requestError||(record.meta.sample?'这是静态界面样例，未调用模型。':(cause?'生成问题：'+cause.message+' ':'')+(!gateFor('board').enabled?(gateFor('board').reason||'AI 处理尚未开通，原稿和已保存内容可继续查看。'):'先清洗完整逐字稿，再从清洗稿提取销售看板；看板依据仍跳回原始稿。'));
    const cleanChanged=record.clean?.human_edited&&record.board?.based_on_clean_version!==record.clean.version;
    if(cleanChanged)notice.textContent+=' 清洗稿已人工修改，请检查看板是否需要同步编辑。重新生成看板使用最近一次带逐段映射的AI清洗稿，不会自动采纳人工改动。';
    document.getElementById('polish-warning').classList.toggle('hidden',!!result&&!cause&&!requestError&&!cleanChanged);
    detailBody.replaceChildren();for(const p of ['board','clean']){const j=jobFor(p);detailBody.append(el('p','',`${p==='board'?'看板':'清洗'}：${j.state} · ${j.done??0}/${j.total??0}`));if(j.last_error)detailBody.append(el('p','','上次失败：'+j.last_error.message));if(j.error)detailBody.append(el('p','','本次失败：'+j.error.message));if(!gateFor(p).enabled)detailBody.append(el('p','','当前不能重试：'+gateFor(p).reason));}
    if(record.legacy_job?.error)detailBody.append(el('p','','保留的旧流程记录：'+record.legacy_job.error.message));
  }
  function markdown(product){
    const artifact=product==='board'?currentBoard():record.clean;const sample=record.meta.sample||artifact?.origin==='sample';
    const safe=text=>String(text).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/[\\`*_{}\[\]()#!|]/g,'\\$&');
    const lines=[`# ${sample?'[界面样例] ':''}${safe(record.meta.title)}`,sample?'静态样例，非AI结果。':'待核对草稿，尚未确认完整性与准确性。',`保存版本：${artifact?.version||'旧版'}`,''];
    if(artifact?.human_edited)lines.push('人工修改版本；原有来源仅供复核，尚未重新核对。','');
    if(artifact?.processing_complete===false)lines.push(`部分草稿：仅完成 ${artifact.processed_batches}/${artifact.total_batches}，尚未处理完整。`,'');
    if(product==='clean'){
      if(artifact?.rows)for(const row of artifact.rows)lines.push(safe((row.cleaned===null&&row.text.trim()?'[未清洗 · 保留原文] ':'')+(row.cleaned??row.text)));
      else lines.push(safe(artifact?.processed||''));
    } else for(const [title,items] of Object.entries(artifact.sections)){lines.push('## '+safe(title));for(const item of items){lines.push('- '+(item.human_edited?'[人工修改 · 待核对] ':'')+safe(item.text));if(item.review_state==='blocked')lines.push('  - 本项存在问题，结论未确认。');for(const c of item.conditions||[])lines.push('  - 条件：'+safe(c.text));for(const h of item.history||[])lines.push('  - 历史（已更正）：'+safe(h.text));for(const r of item.evidence||[])lines.push(`  - ${safe(r.role||'原文')}第${r.line}行：${safe(r.quote)}`);}}
    return lines.join('\n');
  }
  function configureExports(){
    const format=document.getElementById('export-format');format.replaceChildren();
    const opts=[['board-md','看板 Markdown',!!currentBoard()],['board-jpg','看板 JPG',!!currentBoard()],['board-pdf','看板 PDF',!!currentBoard()],['clean-md','清洗稿 Markdown',!!record.clean],['clean-pdf','清洗稿 PDF',!!record.clean],['original','原始稿 .md',true]];
    for(const [value,label,enabled] of opts){const opt=new Option(label,value);opt.disabled=!enabled;format.append(opt);}format.value=currentBoard()?'board-md':'original';
    const download=document.getElementById('export-btn');download.textContent='下载';download.onclick=async()=>{
      if(format.value==='original'){location.href=tingjiUrl(`/api/meetings/${id}/source`);return;}
      const [p,kind]=format.value.split('-');if(!['board','clean'].includes(p)||!['md','jpg','pdf'].includes(kind))return;
      if(record.edit_drafts?.[p]&&!await confirmTextAction('存在编辑草稿。本次导出上次已保存版本，不包含草稿修改。继续？'))return;
      download.disabled=true;download.textContent='正在生成文件…';try{
        if(kind==='md'){MeetingExport.download(new Blob([markdown(p)],{type:'text/markdown;charset=utf-8'}),`${record.meta.sample?'界面样例-':''}${p==='board'?'销售看板':'清洗稿'}-待核对.md`);return;}
        const files=await MeetingExport.render(record,p,kind);
        if(files.length===1)MeetingExport.download(files[0].blob,(record.meta.sample?'界面样例-':'')+files[0].name);
        else{const d=el('dialog','text-editor-dialog');d.append(el('h2','',`看板较长，完整导出为${files.length}页 JPG`),el('p','','没有裁切正文。请逐页下载所有图片。'));for(const f of files)d.append(button('下载 '+f.name,()=>MeetingExport.download(f.blob,f.name)));d.append(button('关闭',()=>{d.close();d.remove();}));document.body.append(d);d.showModal();}
      }catch(e){requestError='导出失败：'+e.message;update();}finally{download.disabled=false;download.textContent='下载';}
    };
  }
  async function read(path){const r=await apiFetch(path,{signal:abort.signal});const data=await r.json().catch(()=>({detail:'服务暂不可用。'}));if(!r.ok)throw new Error(data.detail||'读取失败。');return data;}
  async function reload(){const next=await read(`/api/meetings/${id}`);if(next.meta.source_sha256!==record.meta.source_sha256)throw new Error('原文版本变化，已停止更新。');const changed=JSON.stringify([next.board?.version,next.clean?.version,next.job?.state,next.products?.clean?.state,next.candidates])!==JSON.stringify([record.board?.version,record.clean?.version,record.job?.state,record.products?.clean?.state,record.candidates]);record=next;if(changed)render();update();}
  function schedule(){if(!closed){clearTimeout(timer);timer=setTimeout(poll,Object.values(record.products||{board:record.job}).some(j=>polling.has(j.state))?2000:10000);}}
  async function poll(){try{await reload();}catch(e){if(!closed){notice.textContent='连接中断，显示的是此前保存内容；无法确认最新状态。';boardAction.disabled=true;cleanAction.disabled=true;}}schedule();}
  async function start(product){
    if(sending||record.meta.sample||(product==='both'?(!gateFor('board').enabled||!gateFor('clean').enabled):!gateFor(product).enabled))return;
    const artifact=record[product],regenerate=product!=='both'&&jobFor(product).state==='draft'&&!!artifact;
    if(regenerate&&!await confirmTextAction('重新生成会再次调用 AI。人工修改将保留，新结果需比较后采纳。继续？'))return;
    sending=true;requestError='';update();try{const r=await apiFetch(`/api/meetings/${id}/process`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product,regenerate,base_version:artifact?.version??null})});const data=await r.json();if(!r.ok)throw new Error(data.detail||'无法开始。');await reload();}catch(e){requestError=e.message;}finally{sending=false;update();schedule();}
  }
  const editor=createMeetingEditor(id,()=>record,reload,text=>{requestError=text;update();});
  const editBoard=button('编辑看板',()=>editor.open('board')),editClean=button('编辑清洗稿',()=>editor.open('clean'));editBoard.dataset.editProduct='board';editClean.dataset.editProduct='clean';
  const compareBoard=button('比较看板新建议',()=>editor.compare('board')),compareClean=button('比较清洗新建议',()=>editor.compare('clean'));
  const cite=button('复制会议引用',()=>copyMeetingReference(id,cite));cite.disabled=!!record.meta.sample;cite.title=record.meta.sample?'静态样例不发送模型':'粘贴到办公聊天，发送时读取已保存版本';document.querySelector('.export-row').append(editBoard,editClean,compareBoard,compareClean,cite);
  window.addEventListener('pagehide',()=>{closed=true;clearTimeout(timer);abort.abort();},{once:true});syncSource();render();update();showTab('summary');schedule();
  if(location.hash==='#next'){const n=[...board.querySelectorAll('.text-board-section')].find(x=>x.dataset.section==='后续行动');if(n){n.open=true;n.scrollIntoView?.({block:'nearest'});}}
}
