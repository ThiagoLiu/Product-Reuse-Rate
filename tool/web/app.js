const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state={categories:[],trash:[]},view='home',selected=null,busy=false,updateTarget=null;
const collapsed = new Set();
let rankMode = 'major';
const childrenOf = id => state.categories.filter(c => c.parent_id === id);
function categoryLabel(category) {
 const parent = state.categories.find(c => c.id === category.parent_id);
 return parent ? parent.name + ' / ' + category.name : category.name;
}
const token=$('meta[name=app-token]').content;
async function request(path,data){const r=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:{'X-App-Token':token,'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});if(!r.ok){const e=await r.json();throw Error(e.error||'操作失败')}return r}
async function api(path,data){return (await request(path,data)).json()}
function notice(text,error=false){$('#notice').innerHTML=text?`<div class="${error?'error':'success'}">${esc(text)}</div>`:''}
async function run(fn){if(busy)return;busy=true;document.body.style.cursor='progress';try{await fn()}catch(e){notice(e.message,true);if($('#modal').open){let message=$('#modalError');if(!message){message=document.createElement('div');message.id='modalError';message.className='error';$('#modalBody').append(message)}message.textContent=e.message}}finally{busy=false;document.body.style.cursor=''}}
async function refresh(){state=await api('state');render()}
function pct(v){return v==null?'暂无结果':(v*100).toFixed(2)+'%'}
function stat(label,value,note='',accent=false){return `<div class="stat ${accent?'accent':''}"><span class="label">${label}</span><strong>${value}</strong><small>${note}</small></div>`}
function table(headers,rows){return `<div class="table-wrap"><table><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')||`<tr><td colspan="${headers.length}" class="empty">暂无数据</td></tr>`}</tbody></table></div>`}
function tr(values){return '<tr>'+values.map(v=>`<td>${v}</td>`).join('')+'</tr>'}
function panel(title,body,actions=''){return `<div class="panel"><div class="panel-top"><h2>${title}</h2><div class="actions">${actions}</div></div>${body}</div>`}
function setTitle(title,sub){$('#title').textContent=title;$('#subtitle').textContent=sub}
function go(next,id=null){view=next;selected=id;notice('');render(false)}
function renderCategories() {
 const list = $('#categories');
 const scrollTop = list.scrollTop;
 const query = $('#categorySearch').value.trim().toLocaleLowerCase();
 const matches = c => c.name.toLocaleLowerCase().includes(query);
 const roots = state.categories.filter(c => c.parent_id === null);
 const row = (c, indent, toggle = '') => {
   const active = view === 'category' && selected === c.id;
   return `<div class="category-item ${active ? 'active' : ''} ${indent ? 'category-child' : ''}">
     ${toggle}
     <button class="category-link" data-category="${c.id}" ${active ? 'aria-current="page"' : ''} title="${esc(c.name)}">
       <span>${esc(c.name)}</span><span class="category-projects">${c.project_count}</span>
     </button>
   </div>`;
 };
 $('#categoryCount').textContent = state.categories.length;
 list.innerHTML = roots.map(root => {
   const children = childrenOf(root.id);
   const visible = matches(root) ? children : children.filter(matches);
   if (query && !matches(root) && !visible.length) return '';
   const open = Boolean(query) || !collapsed.has(root.id);
   const toggle = children.length ? `<button class="tree-toggle" data-toggle="${root.id}"
     aria-label="${open ? '折叠' : '展开'} ${esc(root.name)}" aria-expanded="${open}">${open ? '▾' : '▸'}</button>` : '';
   return row(root, false, toggle) + (open ? visible.map(c => row(c, true)).join('') : '');
 }).join('') || `<p class="sidebar-empty">${query ? '无匹配分类' : '暂无分类'}</p>`;
 list.scrollTop = scrollTop;
}
function render(rebuildCategories = true) {
 if (rebuildCategories) renderCategories();
 for (const button of document.querySelectorAll('#categories .category-link')) {
   const active = view === 'category' && Number(button.dataset.category) === selected;
   button.closest('.category-item').classList.toggle('active', active);
   if (active) button.setAttribute('aria-current', 'page');
   else button.removeAttribute('aria-current');
 }
 for (const name of ['home', 'trash', 'manage']) {
   $('#'+name).classList.toggle('active', view === name);
 }
 if (view === 'home') home();
 else if (view === 'category') category();
 else if (view === 'trash') trash();
 else manage();
}
function home() {
 const major = rankMode === 'major';
 setTitle('总览', major ? '大分类按有结果的小分类复用率取算术平均。' : '按最小分类的复用率从高到低排列。');
 const roots = state.categories.filter(c => c.parent_id === null);
 const cats = major ? roots : state.categories.filter(c => !c.is_group);
 const label = c => major ? c.name : categoryLabel(c);
 const valid = cats.filter(c => c.result.rate !== null).sort((a, b) => b.result.rate - a.result.rate);
 const waiting = cats.filter(c => c.result.rate === null);
 const max = Math.max(1, Math.ceil(Math.max(0, ...valid.map(c => c.result.rate)) * 4) / 4);
 const rows = valid.map(c => `<button class="chart-row" data-category="${c.id}" title="${esc(label(c))}">
   <span class="chart-label">${esc(label(c))}</span>
   <span class="track"><span class="bar" style="width:${c.result.rate / max * 100}%"></span></span>
   <span class="rate">${pct(c.result.rate)}</span>
   <span class="scale">${c.project_count} 个项目 · ${c.result.total} 条</span>
 </button>`).join('');
 const ticks = Array.from({length: 5}, (_, i) => `<span>${Number((max * i * 25).toFixed(2))}%</span>`).join('');
 const chart = valid.length
   ? `<div class="chart-axis"><div class="ticks">${ticks}</div></div><div class="chart-list">${rows}</div>`
   : `<div class="empty"><p>${cats.length ? '导入项目后即可比较分类复用率。' : '先新建分类，再添加项目表格。'}</p>${cats.length ? '' : '<button class="primary" data-new>新建分类</button>'}</div>`;
 const pending = waiting.length
   ? `<div class="pending"><span>暂无结果</span>${waiting.map(c => `<button data-category="${c.id}">${esc(label(c))}</button>`).join('')}</div>`
   : '';
 $('#content').innerHTML = `<div class="stats overview-stats">
   ${stat('最高复用率', pct(valid[0]?.result.rate), valid[0] ? esc(label(valid[0])) : '', true)}
   ${stat('物料总记录', cats.reduce((sum, c) => sum + c.result.total, 0))}</div>`
   + panel(major ? '大分类复用率对比' : '小分类复用率排名', chart + pending,
     `<button data-rank-mode="major" aria-pressed="${major}">按大分类</button>
      <button data-rank-mode="minor" aria-pressed="${!major}">按小分类</button>`);
}
function category(){const c=state.categories.find(c=>c.id===selected);if(!c){go('home');return}const r=c.result;
 if (c.is_group) {
   setTitle(c.name, '有结果的小分类等权计入平均复用率。');
   const rows = childrenOf(c.id).map(child => tr([
     `<button data-category="${child.id}">${esc(child.name)}</button>`,
     child.project_count, child.result.total, pct(child.result.rate),
   ]));
   $('#content').innerHTML = `<div class="stats overview-stats">
     ${stat('平均复用率', pct(r.rate), `${r.rated_children} 个小分类有结果`, true)}
     ${stat('物料总记录', r.total)}</div>` +
     panel('小分类', table(['名称','项目数','物料行数','复用率'], rows),
       `<button class="primary" data-add-child="${c.id}">新建小分类</button>`);
   return;
 }
 setTitle(categoryLabel(c),'按本分类的全部有效项目计算。');
 const canMove = c.parent_id !== null && childrenOf(c.parent_id).length > 1;
 $('#content').innerHTML=`<div class="stats">${stat('分类复用率',pct(r.rate),r.rate>1?'结果超过100%，请核对原始数据':'按已确认公式计算',true)}${stat('物料总记录',r.total,`${c.projects.length}个项目 · ${r.unique}种编码`)}${stat('跨项目重复编码',r.duplicate_count,'每个编码最多计1次')}${stat('排除重复后的借用件',r.borrowed,`原始借用件记录 ${r.raw_borrowed} 条`)}</div>`+
 panel('项目资料库',table(['选择','项目','物料行数','借用件 / 新开件','当前版本','操作'],c.projects.map(p=>tr([`<input type="checkbox" aria-label="选择项目 ${esc(p.name)}" data-select-project="${p.id}">`,`<b>${esc(p.name)}</b>`,p.rows.length,`${p.rows.filter(r=>r.kind==='借用件').length} / ${p.rows.filter(r=>r.kind==='新开件').length}`,`${esc(p.created)}<br><span class="muted">${p.version_count}个版本</span>`,`<button data-detail="${p.id}">查看明细</button><button data-original="${p.version_id}">原表</button><button data-versions="${p.id}">历史版本</button><button data-update="${p.id}">更新此项目</button>${canMove ? `<button data-move-project="${p.id}">移动项目</button>` : ''}<button data-remove="${p.id}" data-kind="project">删除</button>`]))),'<button data-batch-delete>删除选中项目</button><button data-import class="primary">添加 / 更新项目</button>')+
 panel('计算过程',`<div class="formula">（${r.duplicate_count} + ${r.borrowed}）÷（${r.total} − ${r.duplicate_count}）<strong>${r.numerator} ÷ ${r.denominator} = ${pct(r.rate)}</strong></div><p>每次从全部有效项目重新统计，不累加历史计算结果，不对项目或季度复用率取平均。</p>${r.rate>1?'<div class="warning">结果超过100%，已保留真实计算值，请核对数据。</div>':''}`)+
 panel('跨项目重复编码',table(['物料编码','项目数','出现位置'],r.duplicates.map(d=>tr([esc(d.code),new Set(d.occurrences.map(o=>o.project_id)).size,d.occurrences.map(o=>`${esc(o.project)} · ${esc(o.kind)} · ${esc(o.sheet)} 第${o.row}行`).join('<br>')]))))+
 panel('借用件历史对照',`<p>以下 ${r.unverified.length} 种编码标记为借用件，但尚未在其他已保存项目中出现。仅供核对，不改变计算。</p><details><summary>展开核对清单</summary>${table(['编码','所属项目'],r.unverified.map(d=>tr([esc(d.code),esc([...new Set(d.occurrences.map(o=>o.project))].join('、'))])))}</details>`);
}
function trash(){setTitle('回收站','这里的数据不参与计算。恢复后重新加入；永久删除前自动备份。');$('#content').innerHTML=panel('已删除的数据',table(['类型','名称','所属分类','操作'],state.trash.map(r=>tr([r.kind==='category'?(r.category?'小分类':'大分类'):'项目',esc(r.name),esc(r.category||'—'),`<button data-restore="${r.id}" data-kind="${r.kind}">恢复</button><button class="danger" data-purge="${r.id}" data-kind="${r.kind}">永久删除</button>`]))))+'<p>永久删除会清除当前数据中的相关版本、原表。已有备份或迁移包仍可能包含旧数据。</p>'}
function manage(){setTitle('备份与迁移','将完整数据带到另一台电脑，继续累计使用。');$('#content').innerHTML=`<div class="split">${panel('导出全部数据','<p>包含全部分类、项目、原始Excel副本、历史版本和回收站。将迁移包发送到B电脑，在同一工具内恢复即可。</p><button class="primary" data-backup>导出迁移包</button>')}${panel('恢复迁移包','<p>完整替换本机当前资料库，不合并两台电脑的数据。恢复前自动备份当前数据库。</p><button data-migration>选择迁移包并恢复</button>')}</div>${panel('本地数据说明','<p>数据位于程序旁的 data 文件夹，原始Excel以完整文件形式保存在数据库内。外部原表删除不影响使用。</p><p>自动备份位于 data/backups。可在退出程序后完整复制工具文件夹迁移。自动备份不会自动删除，建议定期保管和清理。</p><p>更新项目会保留旧版本。如需排除项目，请在项目列表中删除。</p>')}`}
function modal(html,onConfirm,label='确认'){ $('#modalBody').innerHTML=html;$('#modalBody').querySelector('h2').id='modalTitle';$('#confirmModal').textContent=label;$('#confirmModal').hidden=!onConfirm;$('#confirmModal').onclick=()=>run(async()=>{await onConfirm();$('#modal').close()});$('#modal').showModal() }
function createCategory(parent = null) {
 const title = parent ? '新建小分类' : '新建大分类';
 const hint = parent
   ? `<p>所属大分类：${esc(parent.name)}。已有直属项目会移入「未分组」。</p>`
   : '';
 modal(`<h2>${title}</h2>${hint}<label for="categoryName">分类名称</label>
   <input type="text" id="categoryName" maxlength="80" placeholder="${parent ? '例如：座椅前排' : '例如：座椅'}">`,
   async () => {
     const name = $('#categoryName').value.trim();
     await api('category', {name, parent_id: parent?.id ?? null});
     if (parent) collapsed.delete(parent.id);
     await refresh();
     if (parent) go('category', parent.id);
     notice('分类已创建');
   }, '创建分类');
 $('#categoryName').focus();
}
function moveProject(id) {
 const source = state.categories.find(c => c.id === selected);
 const project = source.projects.find(p => p.id === id);
 const options = childrenOf(source.parent_id).filter(c => c.id !== source.id)
   .map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
 modal(`<h2>移动项目</h2><p>${esc(project.name)}</p><label for="moveTarget">目标小分类</label>
   <select id="moveTarget">${options}</select>`, async () => {
     await api('move_project', {id, category: Number($('#moveTarget').value)});
     await refresh();
     notice('项目已移动，复用率已更新');
   }, '移动项目');
}
async function download(path,name){const r=await request(path),url=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function encode(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('文件读取失败'));reader.readAsDataURL(file)})}
async function importFiles(list, category, projectId) {
 const chosen = [...list].filter(file => file.name.toLowerCase().endsWith('.xlsx') && !file.name.startsWith('~$'));
 if (!chosen.length) throw Error('请选择 .xlsx 表格');
 if (chosen.reduce((sum, file) => sum + file.size, 0) > 140 * 1024 * 1024) {
   throw Error('本次文件过大，请分批导入');
 }
 notice('正在读取表格…');
 const files = [];
 for (const file of chosen) files.push({name: file.name, content: await encode(file)});
 const payload = {category, files, project_id: projectId};
 const preview = await api('preview', payload);
 const rows = preview.files.map(file => tr([
   esc(file.name), file.status, file.count, file.borrowed,
   Object.entries(file.sheets).map(([name, count]) => `${esc(name)}（${count} 条）`).join('<br>'),
   file.warnings.map(esc).join('<br>') || '—',
 ]));
 modal(`<h2>导入核对</h2><p>同名项目将更新。请核对参与统计的工作表和行数。</p>
   <div class="modal-scroll">${table(['项目','状态','物料行数','借用件','工作表','核对提醒'], rows)}</div>`,
   async () => {
     await api('import', payload);
     await refresh();
     notice('已保存，复用率和分类对比已更新');
   }, '确认保存');
 notice('');
}
$('#home').onclick = () => go('home');
$('#addCategory').onclick = () => createCategory();
$('#categorySearch').oninput = renderCategories;
$('#trash').onclick = () => go('trash');
$('#manage').onclick = () => go('manage');
$('#closeModal').onclick = () => $('#modal').close();
$('#files').onchange = event => {
 const files = [...event.target.files];
 const projectId = updateTarget;
 const category = selected;
 event.target.value = '';
 updateTarget = null;
 if (files.length) run(() => importFiles(files, category, projectId));
};
$('#migrationFile').onchange=e=>run(async()=>{const f=e.target.files[0];if(!f)return;const content=await encode(f);e.target.value='';modal('<h2>恢复全部数据？</h2><p>将覆盖本机当前资料库。恢复前自动备份，迁移包会经过完整性校验。</p>',async()=>{await api('migration',{content});go('home');await refresh();notice('迁移数据已恢复')},'确认恢复')});
const categoryList = $('#categories');
const categoryMenu = $('#categoryMenu');
function renameCategory(id) {
 categoryMenu.hidden = true;
 const item = categoryList.querySelector(`[data-category="${id}"]`)?.closest('.category-item');
 const category = state.categories.find(c => c.id === id);
 if (!item || !category || item.querySelector('form')) return;
 item.innerHTML = `<form class="category-rename">
   <input type="text" aria-label="分类名称" maxlength="80" required value="${esc(category.name)}">
   <button type="submit" title="保存" aria-label="保存名称">✓</button>
   <button type="button" title="取消" aria-label="取消重命名">×</button>
 </form>`;
 const form = item.querySelector('form');
 const input = form.querySelector('input');
 const cancel = () => {
   renderCategories();
   categoryList.querySelector(`[data-category="${id}"]`)?.focus();
 };
 form.querySelector('[type=button]').onclick = cancel;
 form.addEventListener('keydown', event => {
   if (event.key === 'Escape') { event.preventDefault(); cancel(); }
 });
 form.onsubmit = event => {
   event.preventDefault();
   const name = input.value.trim();
   if (!name) { input.focus(); return; }
   if (name === category.name) { cancel(); return; }
   run(async () => {
     await api('category', {id, name});
     await refresh();
     notice('分类名称已更新');
   });
 };
 input.focus();
 input.select();
}
categoryList.addEventListener('dblclick', event => {
 const target = event.target.closest('.category-link');
 if (target) renameCategory(Number(target.dataset.category));
});
categoryList.addEventListener('contextmenu', event => {
 const target = event.target.closest('.category-link');
 if (!target) return;
 event.preventDefault();
 categoryMenu.dataset.categoryId = target.dataset.category;
 const category = state.categories.find(c => c.id === Number(target.dataset.category));
 categoryMenu.querySelector('[data-category-action="add-child"]').hidden = category.parent_id !== null;
 categoryMenu.hidden = false;
 categoryMenu.style.left = Math.min(event.clientX, innerWidth - categoryMenu.offsetWidth - 8) + 'px';
 categoryMenu.style.top = Math.min(event.clientY, innerHeight - categoryMenu.offsetHeight - 8) + 'px';
 categoryMenu.querySelector('button:not([hidden])').focus();
});
categoryList.addEventListener('keydown', event => {
 const target = event.target.closest('.category-link');
 if (event.key === 'F2' && target) {
   event.preventDefault();
   renameCategory(Number(target.dataset.category));
 }
});
document.addEventListener('pointerdown', event => {
 if (!categoryMenu.contains(event.target)) categoryMenu.hidden = true;
});
categoryMenu.addEventListener('click', event => {
 const action = event.target.closest('[data-category-action]')?.dataset.categoryAction;
 if (!action) return;
 const id = Number(categoryMenu.dataset.categoryId);
 categoryMenu.hidden = true;
 if (action === 'add-child') createCategory(state.categories.find(c => c.id === id));
 if (action === 'rename') renameCategory(id);
 if (action === 'delete') confirmCategoryDeletion(id);
});
function confirmCategoryDeletion(id) {
 const category = state.categories.find(c => c.id === id);
 if (!category) return;
 modal(`<h2>删除分类“${esc(category.name)}”？</h2>
   <p>该分类及其 ${category.project_count} 个有效项目将移入回收站，可在回收站恢复。</p>`,
   async () => {
     await api('trash', {kind: 'category', id});
     if (view === 'category' && selected === id) go('home');
     await refresh();
     notice('分类已移入回收站');
   }, '移入回收站');
}
categoryMenu.addEventListener('keydown', event => {
 if (event.key === 'Escape') {
   categoryMenu.hidden = true;
   categoryList.querySelector(`[data-category="${categoryMenu.dataset.categoryId}"]`)?.focus();
 }
});
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;const d=b.dataset;
 if (d.toggle) {
   const id = Number(d.toggle);
   if (collapsed.has(id)) collapsed.delete(id);
   else collapsed.add(id);
   renderCategories();
 }
 if (d.rankMode) { rankMode = d.rankMode; home(); }
 if (d.addChild) createCategory(state.categories.find(c => c.id === Number(d.addChild)));
 if (d.moveProject) moveProject(Number(d.moveProject));
 if(d.category)go('category',Number(d.category));if('new'in d)createCategory();if('import' in d){updateTarget=null;$('#files').click()}if(d.update){updateTarget=Number(d.update);$('#files').click()}
 if (d.rename) renameCategory(Number(d.rename));

 if(d.remove||d.restore||d.purge){const action=d.remove?'trash':d.restore?'restore':'purge',id=Number(d.remove||d.restore||d.purge);modal(`<h2>${action==='trash'?'移入回收站':action==='restore'?'恢复数据':'永久删除'}？</h2><p>${action==='purge'?'此操作清除原表及全部版本，不能从回收站恢复。已有备份不受影响。':action==='restore'?'恢复后将重新参与计算。':'该项目将停止参与计算，可以在回收站恢复。'}</p>`,async()=>{await api(action,{kind:d.kind,id});await refresh();notice('操作完成，统计已更新')})}
 if('batchDelete'in d){const ids=[...document.querySelectorAll('[data-select-project]:checked')].map(el=>Number(el.dataset.selectProject));if(!ids.length){notice('请先勾选要删除的项目',true);return}const c=state.categories.find(c=>c.id===selected);const names=c.projects.filter(p=>ids.includes(p.id)).map(p=>esc(p.name));modal(`<h2>删除 ${ids.length} 个项目？</h2><p>${names.join('、')} 将移入回收站并停止参与计算，可以恢复。</p>`,async()=>{await api('trash_many',{category:c.id,ids});await refresh();notice('已移入回收站，统计已更新')},'移入回收站')}
 if(d.versions)run(async()=>{const versions=await api('versions?id='+d.versions);modal('<h2>历史版本</h2><p>统计只使用最新版本。可下载任一版本的原表。</p>'+table(['导入时间','文件','操作'],versions.map((v,i)=>tr([esc(v.created)+(i===0?' · 当前':'') ,esc(v.filename),`<button data-original="${v.id}" data-filename="${esc(v.filename)}">下载原表</button>`]))),null)});
 if(d.original)run(()=>download('original?id='+d.original,d.filename||'项目原表.xlsx'));
 if(d.detail){const c=state.categories.find(c=>c.id===selected),p=c.projects.find(p=>p.id===Number(d.detail));modal(`<h2>${esc(p.name)} · 物料明细</h2><p>当前 ${p.rows.length} 条，保留 ${p.version_count} 个版本。更新同名项目只使用最新版本计算。</p>${p.warnings.length?`<div class="warning">${p.warnings.map(esc).join('<br>')}</div>`:''}<input id="searchRows" type="search" placeholder="搜索编码或借用件 / 新开件"><div class="modal-scroll" id="rowTable"></div>`,null);const draw=()=>{const q=$('#searchRows').value.trim();$('#rowTable').innerHTML=table(['物料编码','分类','工作表','原始行'],p.rows.filter(r=>r.code.includes(q)||r.kind.includes(q)).map(r=>tr([esc(r.code),esc(r.kind),esc(r.sheet),r.row])))};$('#searchRows').oninput=draw;draw()}
 if('backup'in d)run(()=>download('export','物料复用工具迁移包.zip'));if('migration'in d)$('#migrationFile').click();
});
const tabId = crypto.randomUUID();
let heartbeat;
function checkIn(action) {
 fetch('/api/' + action, {
   method: 'POST',
   headers: {'X-App-Token': token, 'Content-Type': 'application/json'},
   body: JSON.stringify({tab: tabId}),
   keepalive: true,
 }).catch(() => {});
}
function startHeartbeat() {
 clearInterval(heartbeat);
 checkIn('tab-open');
 heartbeat = setInterval(() => checkIn('tab-open'), 5000);
}
window.addEventListener('pagehide', () => {
 clearInterval(heartbeat);
 checkIn('tab-close');
});
window.addEventListener('pageshow', startHeartbeat);
document.addEventListener('visibilitychange', () => {
 if (!document.hidden) startHeartbeat();
});
startHeartbeat();
run(refresh);
