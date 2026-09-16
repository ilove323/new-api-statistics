/* Fetch one database aggregate and filter the snapshot locally by user and model. */
const $ = id => document.getElementById(id);
let snapshot = null;
let ranking = 'model_amount';
const tokenFields = ['total_tokens','input_tokens','output_tokens','cache_read_tokens','cache_write_tokens'];
const detailColumns = ['username','request_count','model_name',...tokenFields,'group_ratio','input_price','output_price','cache_price','write_price','amount'];
const columnStorageKey = 'new-api-statistics.visible-columns';
const number = (n, digits=6) => n === null || n === undefined ? '—' : Number(n).toLocaleString('zh-CN',{maximumFractionDigits:digits});
const cell = (tr, value, cls='', column='') => {const td=document.createElement('td');td.textContent=value;td.className=cls;if(column)td.dataset.column=column;tr.append(td);return td;};
const userCell = (tr,row) => {
  const td=cell(tr,row.username,'username','username');
  if(row.display_name){const name=document.createElement('span');name.className='display-name';name.textContent=row.display_name;td.append(name);}
  return td;
};
function selectedRows(){
  if(!snapshot)return [];
  return snapshot.rows.filter(r=>(!$('user').value||r.username===$('user').value)&&(!$('model').value||r.model_name===$('model').value));
}
function visibleColumns(){
  return new Set([...document.querySelectorAll('[data-column-toggle]:checked')].map(input=>input.dataset.columnToggle));
}
function saveVisibleColumns(){
  try{localStorage.setItem(columnStorageKey,JSON.stringify([...visibleColumns()]));}catch{}
}
function applyColumnVisibility(){
  const visible=visibleColumns();
  document.querySelectorAll('#usage-table [data-column]').forEach(element=>{
    if(detailColumns.includes(element.dataset.column))element.hidden=!visible.has(element.dataset.column);
  });
  $('usage-table').style.setProperty('--usage-table-min-width',Math.max(480,(visible.size+(developerMode?2:0))*115)+'px');
}
function loadVisibleColumns(){
  let saved;
  try{saved=JSON.parse(localStorage.getItem(columnStorageKey));}catch{}
  const inputs=document.querySelectorAll('[data-column-toggle]');
  if(!Array.isArray(saved)||!saved.length){inputs.forEach(input=>input.checked=true);return;}
  inputs.forEach(input=>input.checked=saved.includes(input.dataset.columnToggle));
}
function populateFilter(id,placeholder,values,label){
  const select=$(id),selected=select.value;
  select.replaceChildren(new Option(placeholder,''));
  values.forEach(value=>select.add(new Option(label(value),value)));
  select.value=values.includes(selected)?selected:'';
}
// Browser-only diagnostics: never add fields to the API snapshot or Excel export.
const developerMode = new URLSearchParams(window.location.search).get('dev') === '1';
if(developerMode){
  for(const [column,label] of [['cache_hit_rate','缓存命中率'],['amount_per_million','每百万 Token 金额']]){
    const th=document.createElement('th');th.textContent=label;th.dataset.column=column;
    document.querySelector('#usage-table thead tr').append(th);
  }
}
function developerCells(tr,row){
  if(!developerMode)return;
  const read=Number(row.cache_read_tokens),denominator=Number(row.input_tokens)+read;
  cell(tr,denominator>0?number(read/denominator*100,2)+'%':'—','','cache_hit_rate');
  const tokens=Number(row.total_tokens);
  cell(tr,tokens>0?number(Number(row.amount)/tokens*1000000,6):'—','','amount_per_million');
}
function render() {
  hideMoneyTooltip();
  const data=selectedRows();
  const total={amount:0,request_count:0,...Object.fromEntries(tokenFields.map(k=>[k,0]))};
  data.forEach(r=>{total.amount+=Number(r.amount);total.request_count+=Number(r.request_count);tokenFields.forEach(k=>total[k]+=Number(r[k]));});
  $('amount').textContent='¥ '+number(total.amount,2);
  tokenFields.forEach(k=>$(k).textContent=number(total[k],0));
  const seconds=Number(snapshot.totals.duration_seconds);
  $('tpm').textContent=number(total.total_tokens*60/seconds,2);
  $('rpm').textContent=number(total.request_count*60/seconds,4);
  $('counts').textContent=`${new Set(data.map(r=>r.user_id)).size} / ${new Set(data.map(r=>r.model_name)).size}`;
  $('rows').replaceChildren();$('totals').replaceChildren();
  for(let i=0;i<data.length;i++) {
    const r=data[i],tr=document.createElement('tr');
    if(i===0 || data[i-1].user_id!==r.user_id || data[i-1].username!==r.username) {
      let end=i+1;while(end<data.length && data[end].user_id===r.user_id && data[end].username===r.username)end++;
      userCell(tr,r).rowSpan=end-i;
    }
    cell(tr,number(r.request_count,0),'','request_count');cell(tr,r.model_name,'model','model_name');
    for(const key of [...tokenFields,'group_ratio','input_price','output_price','cache_price','write_price','amount']){
      const td=cell(tr,number(r[key],tokenFields.includes(key)?0:6),'',key);
      if(key==='amount')bindMoneyTooltip(td,()=>rowMoneyFormula(r));
    }
    developerCells(tr,r);
    $('rows').append(tr);
  }
  if(!data.length){const tr=document.createElement('tr');cell(tr,'该时间范围内暂无消费记录','empty').colSpan=visibleColumns().size+(developerMode?2:0);$('rows').append(tr);}
  const tr=document.createElement('tr');cell(tr,'总计','','username');cell(tr,number(total.request_count,0),'','request_count');cell(tr,'','','model_name');tokenFields.forEach(k=>cell(tr,number(total[k],0),'',k));
  for(const key of ['group_ratio','input_price','output_price','cache_price','write_price'])cell(tr,'','',key);
  bindMoneyTooltip(cell(tr,number(total.amount),'','amount'),()=>totalMoneyFormula(data,total.amount));
  developerCells(tr,total);
  $('totals').append(tr);
  applyColumnVisibility();
  renderRanking(data);
  $('status').textContent=`${snapshot.start.replace('T',' ')} 至 ${snapshot.end.replace('T',' ')} · ${data.length} 条用户模型汇总`;
  if(data.some(r=>['input_price','output_price','cache_price','write_price'].some(k=>r[k]===null)))$('status').textContent+=' · 部分当前价格未配置，显示为 —';
}
function renderRanking(data) {
  const models=new Map();data.forEach(r=>models.set(r.model_name,(models.get(r.model_name)||0)+Number(r.amount)));
  const tokens=ranking==='user_tokens';
  const entries=ranking==='model_amount'?[...models].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])):
    rankingsForSelection(data,ranking).map(r=>[r.username,Number(r[tokens?'total_tokens':'amount'])]);
  const max=Math.max(...entries.map(e=>e[1]),1);
  $('chart').replaceChildren();
  if(!entries.length){$('chart').textContent='该时间范围内暂无消费记录';return;}
  let rank=0;
  for(const [model,amount] of entries){
    const row=document.createElement('div');row.className='bar-row';
    const label=document.createElement('span');label.className='bar-label';label.textContent=`${++rank}. ${model}`;label.title=model;
    const track=document.createElement('div');track.className='bar-track';const bar=document.createElement('div');bar.className='bar';bar.style.width=`${amount/max*100}%`;track.append(bar);
    const val=document.createElement('span');val.className='bar-value';val.textContent=tokens?number(amount,0)+' Token':'¥ '+number(amount,2);row.append(label,track,val);$('chart').append(row);
  }
}
function rankingsForSelection(data,type){
  const field=type==='user_tokens'?'total_tokens':'amount',users=new Map();
  data.forEach(row=>users.set(row.username,(users.get(row.username)||0)+Number(row[field])));
  return [...users].map(([username,value])=>({username,[field]:value})).sort((a,b)=>b[field]-a[field]||a.username.localeCompare(b.username));
}
const tabs=[...document.querySelectorAll('[data-ranking]')];
tabs.forEach((tab,i)=>{
  tab.addEventListener('click',()=>{
    ranking=tab.dataset.ranking;
    tabs.forEach(t=>{t.setAttribute('aria-selected',String(t===tab));t.tabIndex=t===tab?0:-1;});
    $('chart').setAttribute('aria-labelledby',tab.id);
    if(snapshot)render();
  });
  tab.addEventListener('keydown',event=>{
    let target;
    if(event.key==='ArrowRight')target=tabs[(i+1)%tabs.length];
    if(event.key==='ArrowLeft')target=tabs[(i+tabs.length-1)%tabs.length];
    if(event.key==='Home')target=tabs[0];
    if(event.key==='End')target=tabs[tabs.length-1];
    if(target){event.preventDefault();target.focus();target.click();}
  });
});
async function query(event){
  event?.preventDefault();if($('submit').disabled)return;
  $('submit').disabled=true;document.querySelectorAll('[data-preset]').forEach(b=>b.disabled=true);
  $('export').disabled=true;$('status').className='';$('status').textContent='正在查询…';
  const params=new URLSearchParams({start:$('start').value,end:$('end').value});
  try{
    const response=await fetch('/statistics/api/usage?'+params);
    if(!response.ok){let msg='查询失败，请重试';try{msg=(await response.json()).error||msg;}catch{}throw new Error(msg);}
    snapshot=await response.json();
    const userNames=[...new Set(snapshot.rows.map(r=>r.username))].sort();
    const displayNames=new Map(snapshot.rows.map(r=>[r.username,r.display_name]));
    populateFilter('user','全部用户',userNames,name=>displayNames.get(name)?`${name}（${displayNames.get(name)}）`:name);
    populateFilter('model','全部模型',[...new Set(snapshot.rows.map(r=>r.model_name))].sort(),name=>name);
    $('updated').textContent='更新于 '+snapshot.updated_at.replace('T',' ').slice(0,19)+' 北京时间';render();$('export').disabled=false;
  }catch(error){$('status').className='error';$('status').textContent=error.message;}
  finally{$('submit').disabled=false;document.querySelectorAll('[data-preset]').forEach(b=>b.disabled=false);}
}
document.querySelectorAll('[data-preset]').forEach(button=>button.addEventListener('click',()=>{
  const range=presetRange(button.dataset.preset);$('start').value=range.start;$('end').value=range.end;
  document.querySelectorAll('[data-preset]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
  query();
}));
for(const id of ['start','end'])$(id).addEventListener('input',()=>document.querySelectorAll('[data-preset]').forEach(b=>b.setAttribute('aria-pressed','false')));
$('query').addEventListener('submit',query);
for(const id of ['user','model'])$(id).addEventListener('change',()=>snapshot&&render());
document.querySelectorAll('[data-column-toggle]').forEach(input=>input.addEventListener('change',()=>{
  if(!document.querySelector('[data-column-toggle]:checked'))input.checked=true;
  saveVisibleColumns();applyColumnVisibility();
}));
$('show-all-columns').addEventListener('click',()=>{document.querySelectorAll('[data-column-toggle]').forEach(input=>input.checked=true);saveVisibleColumns();applyColumnVisibility();});
$('export').addEventListener('click',()=>{if(snapshot)window.location.assign('/statistics/api/export?'+new URLSearchParams({start:snapshot.start,end:snapshot.end,user:$('user').value,model:$('model').value}));});
loadVisibleColumns();applyColumnVisibility();
query();
