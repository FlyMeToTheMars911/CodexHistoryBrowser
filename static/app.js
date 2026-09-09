'use strict';
const $ = selector => document.querySelector(selector);
const form = $('#filters');
const dialog = $('#detail-dialog');
const state = {page: 1, pageSize: 20, query: null, listRequest: 0, detailVersion: 0, detail: null, items: [], cursor: -1, more: false, detailBusy: false, indexWasRunning: false};
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const formatTime = seconds => seconds ? new Date(seconds * 1000).toLocaleString('zh-CN', {year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '—';
const toolNames = {commandExecution:'命令执行',fileChange:'文件修改',reasoning:'思考记录',webSearch:'网页检索',contextCompaction:'上下文整理',subAgentActivity:'子代理活动',mcpToolCall:'工具调用',dynamicToolCall:'动态工具',imageView:'查看图片',imageGeneration:'生成图片',plan:'执行计划',collabAgentToolCall:'协作调用'};

async function api(path, params={}) {
  const response = await fetch(path + '?' + new URLSearchParams(params));
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '请求失败');
  return result;
}
function notify(message) { $('#toast').textContent=message; $('#toast').hidden=false; clearTimeout(notify.timer); notify.timer=setTimeout(()=>$('#toast').hidden=true,3000); }
function highlight(text, query) {
  if (!query) return escapeHTML(text);
  const expression = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
  let cursor=0, html='';
  for (const match of String(text).matchAll(expression)) { html+=escapeHTML(text.slice(cursor,match.index))+'<mark>'+escapeHTML(match[0])+'</mark>'; cursor=match.index+match[0].length; }
  return html+escapeHTML(String(text).slice(cursor));
}
function inline(text) {
  // Escape all input; never execute HTML from stored conversations.
  const tokens=[];
  const token = html => { tokens.push(html); return '\u0000'+(tokens.length-1)+'\u0000'; };
  let safe=escapeHTML(text).replace(/`([^`]+)`/g,(_,code)=>token('<code>'+code+'</code>'));
  safe=safe.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,(_,label,url)=>token('<a href="'+url+'" target="_blank" rel="noopener noreferrer">'+label+'</a>'));
  safe=safe.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/~~([^~]+)~~/g,'<del>$1</del>');
  return safe.replace(/\u0000(\d+)\u0000/g,(_,index)=>tokens[Number(index)] || '');
}
function markdown(text) {
  const lines=String(text).replace(/\r/g,'').split('\n');
  let html='', index=0;
  while(index<lines.length) {
    const line=lines[index];
    const fence=line.match(/^\s*(`{3,}|~{3,})(.*)$/);
    if(fence) {
      const code=[]; index++;
      const closing=new RegExp('^\\s*'+fence[1][0]+'{'+fence[1].length+',}\\s*$');
      while(index<lines.length&&!closing.test(lines[index])) code.push(lines[index++]);
      if(index<lines.length) index++;
      html+='<div class="code-wrap"><span class="code-language">'+escapeHTML(fence[2].trim())+'</span><pre><code>'+escapeHTML(code.join('\n'))+'</code></pre></div>'; continue;
    }
    if(!line.trim()){index++;continue;}
    const heading=line.match(/^(#{1,6})\s+(.+)$/);
    if(heading){const level=Math.min(heading[1].length+1,4);html+='<h'+level+'>'+inline(heading[2])+'</h'+level+'>';index++;continue;}
    if(/^\s*[-*_]{3,}\s*$/.test(line)){html+='<hr>';index++;continue;}
    if(line.includes('|')&&index+1<lines.length&&/^\s*\|?\s*:?-{3,}.*\|/.test(lines[index+1])) {
      const cells = row => row.trim().replace(/^\||\|$/g,'').split('|').map(cell=>cell.trim());
      html+='<table><thead><tr>'+cells(line).map(c=>'<th>'+inline(c)+'</th>').join('')+'</tr></thead><tbody>';index+=2;
      while(index<lines.length&&lines[index].includes('|')&&lines[index].trim())html+='<tr>'+cells(lines[index++]).map(c=>'<td>'+inline(c)+'</td>').join('')+'</tr>';
      html+='</tbody></table>';continue;
    }
    const list=line.match(/^\s*([-+*]|\d+[.)])\s+(.+)$/);
    if(list){const tag=/\d/.test(list[1])?'ol':'ul';html+='<'+tag+'>';while(index<lines.length){const item=lines[index].match(/^\s*([-+*]|\d+[.)])\s+(.+)$/);if(!item||(/\d/.test(item[1])?'ol':'ul')!==tag)break;html+='<li>'+inline(item[2])+'</li>';index++;}html+='</'+tag+'>';continue;}
    if(/^>\s?/.test(line)){html+='<blockquote>'+inline(line.replace(/^>\s?/,''))+'</blockquote>';index++;continue;}
    const paragraph=[line];index++;
    while(index<lines.length&&lines[index].trim()&&!/^\s*(#{1,6}\s|`{3,}|~{3,}|[-+*]\s|\d+[.)]\s|>)/.test(lines[index])) paragraph.push(lines[index++]);
    html+='<p>'+paragraph.map(inline).join('<br>')+'</p>';
  }
  return html;
}
function currentParams() { const params=Object.fromEntries(new FormData(form));params.include_agents=form.elements.include_agents.checked?'1':'0';return params; }
function updateStatus(status) {
  const element=$('#index-status');
  element.classList.toggle('ready',!status.running&&!status.error);
  element.textContent=status.error || (status.running ? `正在更新内容索引 ${status.indexed} / ${status.total} · 更新期间内容查询可能尚未包含全部记录` : status.last_sync ? '内容索引已就绪 · 自动增量更新 · 内容按连续关键词匹配，日期范围含起止当天' : '正在准备内容索引…');
  $('#source-path').textContent=status.codex_home;
}
async function search(reset=false) {
  if(reset){state.page=1;state.query=currentParams();}
  const serial=++state.listRequest;
  $('#list-state').hidden=false;$('#list-state').className='state-box';$('#list-state').textContent='正在查询…';$('#results-table').hidden=true;$('#page-buttons').replaceChildren();
  const params={...state.query,page:state.page,page_size:state.pageSize};
  history.replaceState(null,'','?'+new URLSearchParams(params)+location.hash);
  try {
    const data=await api('/api/search',params); if(serial!==state.listRequest)return;
    state.page=data.page;$('#total').textContent=data.total.toLocaleString();updateStatus(data.index);
    $('#result-hint').textContent=(state.query.time_field==='created_at'?'按创建时间倒序':'按最近更新时间倒序')+(state.query.content?' · 内容匹配「'+state.query.content+'」':'');
    $('#results').innerHTML=data.items.map(row=>`<tr><td><button class="title-button" data-thread="${escapeHTML(row.id)}">${highlight(row.title,state.query.title)}</button>${row.snippet?'<div class="snippet">'+highlight(row.snippet,state.query.content)+'</div>':''}</td><td><span class="project-tag">${escapeHTML(row.project_name)}</span><div class="path" title="${escapeHTML(row.project_path)}">${escapeHTML(row.project_path)}</div></td><td class="time"><div title="创建时间">${formatTime(row.created_at)}</div><div class="updated" title="更新时间">${formatTime(row.updated_at)}</div></td><td><span class="status-pill ${row.archived?'archived':''}">${row.archived?'已归档':'未归档'}</span></td><td><button class="arrow" data-thread="${escapeHTML(row.id)}" aria-label="查看会话详情">↗</button></td></tr>`).join('');
    $('#list-state').hidden=data.total>0;$('#list-state').textContent='没有找到匹配的会话。试试减少查询条件，或勾选“包含子代理会话”。';$('#results-table').hidden=data.total===0;
    $('#page-info').textContent=data.total?`第 ${(data.page-1)*data.page_size+1}–${Math.min(data.page*data.page_size,data.total)} 条，共 ${data.total} 条`:'共 0 条';
    const pages=[...new Set([1,data.pages,data.page-1,data.page,data.page+1].filter(n=>n>0&&n<=data.pages))].sort((a,b)=>a-b);
    let pagination=`<button class="page-button" data-page="${data.page-1}" ${data.page===1?'disabled':''} aria-label="上一页">‹</button>`,last=0;
    for(const page of pages){if(last&&page-last>1)pagination+='<span class="page-gap">…</span>';pagination+=`<button class="page-button ${page===data.page?'active':''}" data-page="${page}" ${page===data.page?'aria-current="page"':''}>${page}</button>`;last=page;}
    pagination+=`<button class="page-button" data-page="${data.page+1}" ${data.page===data.pages?'disabled':''} aria-label="下一页">›</button>`;$('#page-buttons').innerHTML=pagination;
  }catch(error){if(serial!==state.listRequest)return;$('#list-state').className='state-box error';$('#list-state').textContent=error.message;$('#total').textContent='—';$('#page-info').textContent='查询失败';}
}
function contentText(content) {
  if(typeof content==='string')return content;
  if(Array.isArray(content))return content.map(contentText).filter(Boolean).join('\n');
  if(content&&typeof content==='object')return content.text || contentText(content.content) || '';
  return '';
}
function textOf(item) { const d=item.data; if(item.item_type==='userMessage')return contentText(d.content);if(item.item_type==='agentMessage')return d.text||contentText(d.content);return ['command','aggregatedOutput','aggregated_output','stdout','stderr','text','summary','content','result','output','arguments','changes'].filter(key=>d[key]).map(key=>typeof d[key]==='string'?d[key]:JSON.stringify(d[key],null,2)).join('\n\n') || JSON.stringify(d,null,2); }
function renderItems(items) {
  let previous=state.items.length?state.items[state.items.length-1].turn_id:null;
  const fragment=document.createDocumentFragment();
  for(const item of items) {
    if(previous!==item.turn_id){const divider=document.createElement('div');divider.className='turn-divider';divider.textContent='会话轮次 · '+formatTime(item.created_at_ms/1000);fragment.append(divider);previous=item.turn_id;}
    const text=textOf(item), d=item.data, kind=item.item_type;
    const node=document.createElement(kind==='userMessage'||kind==='agentMessage'?'article':'details');
    if(kind==='userMessage'||kind==='agentMessage'){
      const user=kind==='userMessage';node.className='message '+(user?'user':'agent');
      node.innerHTML=`<div class="message-head"><span class="message-author"><span class="avatar">${user?'你':'⌘'}</span>${user?'用户':'Codex'}${!user?'<span class="phase">'+(d.phase==='final'?'最终回复':d.phase==='commentary'?'过程说明':'')+'</span>':''}</span><span>${formatTime(item.created_at_ms/1000)} <button class="copy-button">复制</button></span></div><div class="prose">${markdown(text)}</div>`;
      node.querySelector('.copy-button').addEventListener('click',()=>navigator.clipboard.writeText(text).then(()=>notify('已复制')).catch(()=>notify('复制失败，请手动选择文本')));
      if(Array.isArray(d.content)&&d.content.some(part=>!['text','input_text','output_text','Text'].includes(part.type))){const note=document.createElement('div');note.className='attachment-note';note.textContent='此消息含非文本内容，其引用信息可在原始数据中查看。';node.append(note);}
    }else{
      node.className='tool';const command=Array.isArray(d.command)?d.command.join(' '):d.command;const summary=command||d.tool||d.name||'';
      node.innerHTML='<summary>'+escapeHTML(toolNames[kind]||kind)+(summary?' · '+escapeHTML(String(summary).slice(0,140)):'')+'</summary><div class="tool-body"></div>';
      // Defer large output DOM allocation until the tool record is expanded.
      node.addEventListener('toggle',()=>{if(node.open&&!node.dataset.rendered){const pre=document.createElement('pre');pre.className='tool-output';pre.textContent=text;node.querySelector('.tool-body').append(pre);node.dataset.rendered='1';}}, {once:false});
    }
    const raw=document.createElement('details');raw.className='raw-json';raw.innerHTML='<summary>原始数据</summary>';raw.addEventListener('toggle',()=>{if(raw.open&&!raw.dataset.rendered){const pre=document.createElement('pre');pre.textContent=JSON.stringify(d,null,2);raw.append(pre);raw.dataset.rendered='1';}});
    (node.querySelector('.tool-body')||node).append(raw);fragment.append(node);
  }
  $('#messages').append(fragment);state.items.push(...items);
}
async function loadDetail(all=false) {
  if(state.detailBusy)return;
  state.detailBusy=true;const version=state.detailVersion;$('#load-more').disabled=true;$('#load-all').disabled=true;$('#export').disabled=true;
  try {
    do {
      const data=await api('/api/thread',{id:state.detail.id,after:state.cursor,limit:60});if(version!==state.detailVersion)return;
      state.detail=data.thread;state.cursor=data.next_cursor;state.more=data.has_more;
      $('#detail-title').textContent=data.thread.title;$('#detail-meta').textContent=[data.thread.project_name, data.thread.model||'未记录模型',data.thread.archived?'已归档':'未归档','更新于 '+formatTime(data.thread.updated_at)].join(' · ');
      renderItems(data.items);$('#detail-state').hidden=true;
      $('#loaded-count').textContent=`已加载 ${state.items.length} 条记录${state.more?'':' · 已全部加载'}`;
      $('#load-more').hidden=!state.more;$('#load-all').hidden=!state.more;
      if(all&&state.more){$('#loaded-count').textContent+= ' · 正在加载全部…';await new Promise(resolve=>setTimeout(resolve,0));}
    }while(all&&state.more&&version===state.detailVersion);
    if(!state.items.length){$('#detail-state').hidden=false;$('#detail-state').textContent='此会话暂无消息记录。';}
  }catch(error){if(version===state.detailVersion){$('#detail-state').hidden=false;$('#detail-state').className='state-box error';$('#detail-state').textContent=error.message;throw error;}}
  finally{if(version===state.detailVersion){state.detailBusy=false;$('#load-more').disabled=false;$('#load-all').disabled=false;$('#export').disabled=false;}}
}
async function openDetail(id) {
  state.detailVersion++;state.detail={id};state.items=[];state.cursor=-1;state.more=true;state.detailBusy=false;
  $('#messages').replaceChildren();$('#detail-title').textContent='正在加载会话…';$('#detail-meta').textContent='';$('#loaded-count').textContent='';$('#detail-state').className='state-box';$('#detail-state').textContent='正在读取完整内容…';$('#detail-state').hidden=false;$('#load-more').hidden=false;$('#load-all').hidden=false;
  if(!dialog.open)dialog.showModal();$('#conversation').scrollTop=0;
  history.replaceState(null,'',location.pathname+location.search+'#thread='+encodeURIComponent(id));
  await loadDetail().catch(()=>{});
}
function closeDetail(){dialog.close();}
dialog.addEventListener('close',()=>{state.detailVersion++;state.detailBusy=false;history.replaceState(null,'',location.pathname+location.search);});
$('#close-detail').addEventListener('click',closeDetail);
dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)closeDetail();}});
$('#show-tools').addEventListener('change',event=>$('#messages').classList.toggle('hide-tools',!event.target.checked));
$('#load-more').addEventListener('click',()=>loadDetail().catch(()=>{}));$('#load-all').addEventListener('click',()=>loadDetail(true).catch(()=>{}));
$('#export').addEventListener('click',async()=>{const version=state.detailVersion;try{if(state.more)await loadDetail(true);if(version!==state.detailVersion||state.more)return;const text='# '+state.detail.title+'\n\n项目：'+state.detail.project_name+'\n\n会话 ID：'+state.detail.id+'\n\n'+state.items.map(item=>'## '+(item.item_type==='userMessage'?'用户':item.item_type==='agentMessage'?'Codex':toolNames[item.item_type]||item.item_type)+'\n\n'+textOf(item)).join('\n\n---\n\n');const link=document.createElement('a');link.href=URL.createObjectURL(new Blob(['\ufeff'+text],{type:'text/markdown;charset=utf-8'}));link.download=(state.detail.title.replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').slice(0,80)||'会话')+'.md';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);}catch(error){notify('导出失败：'+error.message);}});
form.addEventListener('submit',event=>{event.preventDefault();search(true);});form.addEventListener('reset',()=>setTimeout(()=>search(true),0));
$('#page-size').addEventListener('change',event=>{state.pageSize=Number(event.target.value);search(true);});
$('#page-buttons').addEventListener('click',event=>{const button=event.target.closest('[data-page]');if(!button||button.disabled)return;state.page=Number(button.dataset.page);search();$('.results-panel').scrollIntoView({behavior:'smooth',block:'start'});});
$('#results').addEventListener('click',event=>{const button=event.target.closest('[data-thread]');if(button)openDetail(button.dataset.thread);});
async function loadProjects(){const selected=form.elements.project.value;const projects=await api('/api/projects');$('#project').innerHTML='<option value="">全部项目</option>'+projects.map(project=>'<option value="'+escapeHTML(project.key)+'" title="'+escapeHTML(project.path)+'">'+escapeHTML(project.name)+' · '+project.count+'</option>').join('');form.elements.project.value=selected;}
$('#refresh').addEventListener('click',async()=>{try{await loadProjects();await search(true);}catch(error){notify(error.message);}});
async function init(){
  const params=new URLSearchParams(location.search);
  try{await loadProjects();for(const [key,value] of params){const element=form.elements.namedItem(key);if(element){if(element.type==='checkbox')element.checked=value==='1';else element.value=value;}}
    state.page=Math.max(1,Number(params.get('page'))||1);state.pageSize=[10,20,50,100].includes(Number(params.get('page_size')))?Number(params.get('page_size')):20;$('#page-size').value=state.pageSize;state.query=currentParams();await search();
    const id=new URLSearchParams(location.hash.slice(1)).get('thread');if(id)openDetail(id);
  }catch(error){$('#list-state').className='state-box error';$('#list-state').textContent=error.message;}
}
setInterval(async()=>{try{const status=await api('/api/status');updateStatus(status);if(state.indexWasRunning&&!status.running&&state.query?.content)search();state.indexWasRunning=status.running;}catch(error){$('#index-status').textContent='无法连接本地服务，请确认启动窗口仍在运行。';}},4000);
init();
