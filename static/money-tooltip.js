/* One viewport-positioned tooltip supports hover, keyboard focus and touch. */
const moneyTip=document.createElement('div');
moneyTip.id='money-tooltip';moneyTip.className='money-tooltip';moneyTip.role='tooltip';moneyTip.hidden=true;
document.body.append(moneyTip);
let moneyAnchor=null,moneyHideTimer;
const moneyNumber=n=>n===null||n===undefined?'未配置':Number(n).toLocaleString('zh-CN',{maximumFractionDigits:9});
function rowMoneyFormula(row){
  const f=row.cost_formula;
  if(!f)return [row.model_name,'暂无计算信息'];
  const lines=[`${row.username} · ${row.model_name}`,'单价单位：元 / 百万 Token'];
  for(const t of f.terms)lines.push(`${t.label}：${moneyNumber(t.tokens)} × ${moneyNumber(t.price)}${t.tokens===0&&t.price===null?'（用量为 0，不计费）':''}`);
  const terms=f.terms.map(t=>t.tokens===0&&t.price===null?'0':`${moneyNumber(t.tokens)} × ${moneyNumber(t.price)}`);
  lines.push(`(${terms.join(' + ')}) × ${moneyNumber(f.ratio)} ÷ 1,000,000`);
  lines.push(`计算金额：${f.calculated===null?'无法完整计算（缺少单价或倍率）':'¥ '+moneyNumber(f.calculated)}`);
  lines.push(`实际消费金额：¥ ${moneyNumber(row.amount)}`);
  if(f.difference!==null)lines.push(`差额（实际 − 计算）：¥ ${moneyNumber(f.difference)}`);
  lines.push('计算使用当前单价和最后倍率；实际金额来自消费日志汇总。');
  if(f.converted)lines.push('缓存读使用数学折算后的整数，四舍五入可能产生尾差。');
  else if(f.ratio_count>1)lines.push('区间存在多种历史倍率，按最后倍率重算可能与实际金额不同。');
  return lines;
}
function totalMoneyFormula(rows,total){
  return ['当前明细消费金额合计',...rows.map((r,i)=>`${i+1}. ${r.username} · ${r.model_name}：¥ ${moneyNumber(r.amount)}`),
    `${rows.length?rows.map(r=>moneyNumber(r.amount)).join(' + '):'0'} = ${moneyNumber(total)}`];
}
function hideMoneyTooltip(){
  clearTimeout(moneyHideTimer);
  moneyAnchor?.removeAttribute('aria-describedby');moneyAnchor=null;moneyTip.hidden=true;
}
function placeMoneyTooltip(){
  if(!moneyAnchor)return;
  const rect=moneyAnchor.getBoundingClientRect(),tip=moneyTip.getBoundingClientRect(),gap=8;
  const left=Math.max(gap,Math.min(rect.right-tip.width,innerWidth-tip.width-gap));
  const top=rect.top-tip.height-gap>=gap?rect.top-tip.height-gap:Math.max(gap,Math.min(rect.bottom+gap,innerHeight-tip.height-gap));
  moneyTip.style.left=`${left}px`;moneyTip.style.top=`${top}px`;
}
function showMoneyTooltip(anchor,content){
  hideMoneyTooltip();moneyAnchor=anchor;
  moneyTip.replaceChildren(...content().map((line,i)=>{const p=document.createElement('p');p.textContent=line;if(i===0)p.className='money-title';return p;}));
  moneyTip.hidden=false;moneyTip.scrollTop=0;anchor.setAttribute('aria-describedby',moneyTip.id);placeMoneyTooltip();
}
function bindMoneyTooltip(anchor,content){
  anchor.classList.add('money-cell');anchor.tabIndex=0;
  anchor.addEventListener('pointerenter',()=>showMoneyTooltip(anchor,content));
  anchor.addEventListener('focus',()=>showMoneyTooltip(anchor,content));
  anchor.addEventListener('click',()=>showMoneyTooltip(anchor,content));
  anchor.addEventListener('pointerleave',()=>{moneyHideTimer=setTimeout(hideMoneyTooltip,200);});
  anchor.addEventListener('blur',hideMoneyTooltip);
}
moneyTip.addEventListener('pointerenter',()=>clearTimeout(moneyHideTimer));
moneyTip.addEventListener('pointerleave',()=>{moneyHideTimer=setTimeout(hideMoneyTooltip,200);});
document.addEventListener('keydown',e=>{if(e.key==='Escape')hideMoneyTooltip();});
document.addEventListener('pointerdown',e=>{if(moneyAnchor&&!moneyTip.contains(e.target)&&!moneyAnchor.contains(e.target))hideMoneyTooltip();});
document.addEventListener('scroll',e=>{
  if(e.target===moneyTip||!moneyAnchor)return;
  const rect=moneyAnchor.getBoundingClientRect();
  if(rect.bottom<0||rect.top>innerHeight||rect.right<0||rect.left>innerWidth)hideMoneyTooltip();
  else placeMoneyTooltip();
},true);
window.addEventListener('resize',hideMoneyTooltip);
