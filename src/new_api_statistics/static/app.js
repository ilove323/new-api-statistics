/* Fetch one database aggregate and filter the snapshot locally by user. */
const $ = id => document.getElementById(id);
let snapshot = null;
let ranking = 'model_amount';
const tokenFields = ['total_tokens','input_tokens','output_tokens','cache_read_tokens','cache_write_tokens'];
const number = (n, digits=6) => n === null || n === undefined ? '—' : Number(n).toLocaleString('zh-CN',{maximumFractionDigits:digits});
const cell = (tr, value, cls='') => {const td=document.createElement('td');td.textContent=value;td.className=cls;tr.append(td);return td;};
const userCell = (tr,row) => {
  const td=cell(tr,row.username,'username');
  if(row.display_name){const name=document.createElement('span');name.className='display-name';name.textContent=row.display_name;td.append(name);}
  return td;
};
// Browser-only diagnostics: never add fields to the API snapshot or Excel export.
const developerMode = new URLSearchParams(window.location.search).get('dev') === '1';
if(developerMode){
  for(const label of ['缓存命中率','每百万 Token 金额']){
    const th=document.createElement('th');th.textContent=label;
    document.querySelector('table thead tr').append(th);
  }
}
function developerCells(tr,row){
  if(!developerMode)return;
  const read=Number(row.cache_read_tokens),denominator=Number(row.input_tokens)+read;
  cell(tr,denominator>0?number(read/denominator*100,2)+'%':'—');
  const tokens=Number(row.total_tokens);
  cell(tr,tokens>0?number(Number(row.amount)/tokens*1000000,6):'—');
}
function render() {
  hideMoneyTooltip();
  const data=snapshot.rows.filter(r=>!$('user').value || r.username===$('user').value);
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
    cell(tr,number(r.request_count,0));cell(tr,r.model_name,'model');
    for(const key of [...tokenFields,'group_ratio','input_price','output_price','cache_price','write_price','amount']){
      const td=cell(tr,number(r[key],tokenFields.includes(key)?0:6));
      if(key==='amount')bindMoneyTooltip(td,()=>rowMoneyFormula(r));
    }
    developerCells(tr,r);
    $('rows').append(tr);
  }
  if(!data.length){const tr=document.createElement('tr');cell(tr,'该时间范围内暂无消费记录','empty').colSpan=developerMode?16:14;$('rows').append(tr);}
  const tr=document.createElement('tr');cell(tr,'总计');cell(tr,number(total.request_count,0));cell(tr,'');tokenFields.forEach(k=>cell(tr,number(total[k],0)));
  for(let i=0;i<5;i++)cell(tr,'');
  bindMoneyTooltip(cell(tr,number(total.amount)),()=>totalMoneyFormula(data,total.amount));
  developerCells(tr,total);
  $('totals').append(tr);
  renderRanking(data);
  $('status').textContent=`${snapshot.start.replace('T',' ')} 至 ${snapshot.end.replace('T',' ')} · ${data.length} 条用户模型汇总`;
  if(data.some(r=>['input_price','output_price','cache_price','write_price'].some(k=>r[k]===null)))$('status').textContent+=' · 部分当前价格未配置，显示为 —';
}
function renderRanking(data) {
  const models=new Map();data.forEach(r=>models.set(r.model_name,(models.get(r.model_name)||0)+Number(r.amount)));
  const tokens=ranking==='user_tokens';
  const entries=ranking==='model_amount'?[...models].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])):
    snapshot.rankings[ranking].filter(r=>!$('user').value||r.username===$('user').value).map(r=>[r.username,Number(r[tokens?'total_tokens':'amount'])]);
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
    snapshot=await response.json();$('user').replaceChildren(new Option('全部用户',''));
    [...new Set(snapshot.rows.map(r=>r.username))].sort().forEach(name=>$('user').add(new Option(name,name)));
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
$('query').addEventListener('submit',query);$('user').addEventListener('change',()=>snapshot&&render());
$('export').addEventListener('click',()=>{if(snapshot)window.location.assign('/statistics/api/export?'+new URLSearchParams({start:snapshot.start,end:snapshot.end,user:$('user').value}));});
query();
