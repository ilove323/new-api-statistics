/* Monitoring is site-wide and independent of report filters and Excel exports. */
let balanceData=null,balanceVersion=null,balanceLoading=null,balanceLoadingLive=false;
let channelVersion=null,channelDirty=false,channelBusy=false;
const balanceMoney=value=>value===null||value===undefined?'—':'¥ '+number(value,2);
const balanceTime=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';
async function balanceRequest(path,options={}){
  const response=await fetch('/statistics/api/balance'+path,options);
  let data;try{data=await response.json();}catch{throw new Error('监控服务暂不可用，请稍后重试。');}
  if(!response.ok)throw new Error(data.error||'监控请求失败。');
  return data;
}
function balanceWrite(method,body){return {method,headers:{'Content-Type':'application/json','X-Statistics-Request':'1'},body:JSON.stringify(body)};}
function renderBalance(){
  const data=balanceData;
  $('balance-summary').replaceChildren();$('balance-months').replaceChildren();$('balance-alert-list').replaceChildren();
  if(!data.configured){$('balance-status').textContent='余额监控数据库尚未配置。';return;}
  const s=data.settings,state=data.state;
  $('balance-bell').classList.toggle('alert-active',s.enabled&&data.alerts.some(a=>!a.resolved_at));
  $('balance-bell').setAttribute('aria-label',s.enabled&&data.alerts.some(a=>!a.resolved_at)?'余额警报：余额不足':'余额警报');
  $('balance-status').className=data.stale&&s.enabled?'error':'';
  $('balance-status').textContent=!s.enabled?'监控未启用':!data.valid?'等待定时任务更新':
    `截至 ${balanceTime(state.checked_at)}（北京时间）${data.stale?' · 数据更新延迟':''}`;
  if(s.enabled&&state?.last_error)$('balance-status').textContent+=' · '+state.last_error;
  for(const [label,value] of [['总额度',s.budget],['已归档消费',data.valid?state.archived_amount:null],['本月消费',data.valid?state.current_amount:null],['剩余额度',data.valid?state.remaining:null]]){
    const div=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=balanceMoney(value);div.append(dt,dd);$('balance-summary').append(div);
  }
  for(const a of data.alerts){
    const div=document.createElement('div');div.className='balance-alert';
    const title=document.createElement('strong');title.textContent=a.resolved_at?'余额不足 · 已解除':'余额不足 · 活动中';
    const detail=document.createElement('p');detail.textContent=`剩余 ${balanceMoney(a.remaining)}，${a.resolved_at?'报警阈值':'低于阈值'} ${balanceMoney(a.threshold)}`;
    const time=document.createElement('p');time.textContent=`检查时间：${balanceTime(a.updated_at)}`;
    div.append(title,detail,time);
    $('balance-alert-list').append(div);
  }
  for(const m of data.months){const tr=document.createElement('tr');cell(tr,m.month.slice(0,7));cell(tr,balanceMoney(m.amount));$('balance-months').append(tr);}
  if(!data.months.length){const tr=document.createElement('tr');cell(tr,'暂无月度归档').colSpan=2;$('balance-months').append(tr);}
}
function refreshBalance(live=$('balance-alerts').open){
  if(balanceLoading)return live&&!balanceLoadingLive?balanceLoading.then(()=>refreshBalance(true)):balanceLoading;
  balanceLoadingLive=live;
  balanceLoading=(async()=>{
    try{balanceData=await balanceRequest(live?'?live=1':'');renderBalance();$('balance-bell').title='余额警报';return balanceData;}
    catch(e){$('balance-status').textContent=e.message;$('balance-status').className='error';$('balance-bell').title='余额监控查询失败';throw e;}
    finally{balanceLoading=null;}
  })();
  return balanceLoading;
}
$('balance-gear').addEventListener('click',async()=>{
  $('balance-settings').showModal();$('balance-save').disabled=true;$('balance-settings-status').textContent='正在读取设置…';
  channelVersion=null;channelDirty=false;$('channel-secret').value='';$('channel-webhook-url').value='';$('channel-signing-secret').value='';channelButtons();
  $('channel-status').textContent='正在读取渠道配置…';
  try{
    const data=await refreshBalance();
    if(!data?.configured){$('balance-settings-status').textContent='余额监控数据库尚未配置，请稍后重试。';return;}
    const s=data.settings;balanceVersion=s.version;
    $('balance-enabled').checked=s.enabled;$('balance-budget').value=s.budget;$('balance-threshold').value=s.threshold;$('balance-start').value=s.start_month.slice(0,7);
    $('balance-settings-status').textContent='';$('balance-save').disabled=false;
    await loadChannel();
  }catch(e){$('balance-settings-status').textContent=e.message;}
});
$('balance-bell').addEventListener('click',async()=>{
  $('balance-alerts').showModal();$('balance-bell').disabled=true;
  $('balance-status').className='';$('balance-status').textContent='正在检查余额与警报…';
  try{
    if(balanceLoading)await balanceLoading.catch(()=>{});
    balanceData=await balanceRequest('/check',balanceWrite('POST',{}));renderBalance();
  }catch(e){$('balance-status').textContent=e.message;$('balance-status').className='error';}
  finally{$('balance-bell').disabled=false;}
});
document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>$(button.dataset.close).close()));
$('balance-form').addEventListener('submit',async event=>{
  event.preventDefault();$('balance-save').disabled=true;
  $('balance-settings-status').textContent='正在保存设置并归档历史月份…';
  try{
    await balanceRequest('/settings',balanceWrite('PUT',{enabled:$('balance-enabled').checked,budget:$('balance-budget').value,threshold:$('balance-threshold').value,start_month:$('balance-start').value,version:balanceVersion}));
    $('balance-settings').close();$('balance-alerts').showModal();await refreshBalance();
  }catch(e){$('balance-settings-status').textContent=e.message;}
  finally{$('balance-save').disabled=false;}
});
function channelButtons(){
  for(const id of ['channel-enabled','channel-type','channel-app-id','channel-secret','channel-receive-type','channel-receive-id','channel-webhook-url','channel-signing-enabled','channel-signing-secret']){
    $(id).disabled=channelBusy||channelVersion===null;
  }
  $('channel-save').disabled=channelBusy||channelVersion===null;
  $('channel-test').disabled=channelBusy||channelVersion===null||channelDirty;
}
function channelFields(channel,receiveType){
  const dingtalk=channel==='dingtalk_webhook';
  $('channel-feishu-fields').hidden=dingtalk;$('channel-dingtalk-fields').hidden=!dingtalk;
  const options=[['chat_id','群聊（chat_id）'],['user_id','个人（user_id）']];
  $('channel-receive-type').replaceChildren(...options.map(([value,label])=>new Option(label,value)));
  if(options.some(([value])=>value===receiveType))$('channel-receive-type').value=receiveType;
}
function renderChannel(data){
  channelVersion=data.version;channelDirty=data.channel!==data.active_channel;
  $('channel-enabled').checked=data.enabled;$('channel-type').value=data.channel;
  channelFields(data.channel,data.receive_id_type);
  $('channel-app-id').value=data.app_id||'';$('channel-secret').value='';
  $('channel-secret').placeholder=data.secret_configured?'已保存；留空保持不变':'未配置';
  $('channel-receive-type').value=data.receive_id_type||'chat_id';$('channel-receive-id').value=data.receive_id||'';
  $('channel-webhook-url').value='';$('channel-webhook-url').placeholder=data.webhook_configured?'已保存；留空保持不变':'未配置';
  $('channel-signing-enabled').checked=Boolean(data.signing_enabled);$('channel-signing-secret').value='';
  $('channel-signing-secret').placeholder=data.signing_secret_configured?'已保存；留空保持不变':'未配置';
  $('channel-signing-secret-field').hidden=!$('channel-signing-enabled').checked;
  $('channel-status').textContent=data.last_error?`最近发送失败：${data.last_error}`:
    data.last_success_at?`最近发送成功：${balanceTime(data.last_success_at)}`:'';
  channelButtons();
}
async function loadChannel(channel=''){
  try{renderChannel(await balanceRequest('/channel'+(channel?'?channel='+encodeURIComponent(channel):'')));}
  catch(e){$('channel-status').textContent=e.message;}
}
$('channel-type').addEventListener('change',async()=>{
  channelBusy=true;channelButtons();$('channel-status').textContent='正在读取渠道配置…';
  await loadChannel($('channel-type').value);
  channelBusy=false;channelButtons();
});
$('channel-form').addEventListener('input',()=>{channelDirty=true;channelButtons();});
$('channel-signing-enabled').addEventListener('change',()=>{$('channel-signing-secret-field').hidden=!$('channel-signing-enabled').checked;});
$('channel-form').addEventListener('submit',async event=>{
  event.preventDefault();if(channelBusy)return;
  channelBusy=true;channelButtons();$('channel-status').textContent='正在保存渠道…';
  const fields=['channel-enabled','channel-type','channel-app-id','channel-secret','channel-receive-type','channel-receive-id','channel-webhook-url','channel-signing-enabled','channel-signing-secret'];
  fields.forEach(id=>$(id).disabled=true);
  try{
    const body={version:channelVersion,enabled:$('channel-enabled').checked,channel:$('channel-type').value};
    if(body.channel==='dingtalk_webhook')Object.assign(body,{webhook_url:$('channel-webhook-url').value,
      signing_enabled:$('channel-signing-enabled').checked,signing_secret:$('channel-signing-secret').value});
    else Object.assign(body,{app_id:$('channel-app-id').value,app_secret:$('channel-secret').value,
      receive_id_type:$('channel-receive-type').value,receive_id:$('channel-receive-id').value});
    const data=await balanceRequest('/channel',balanceWrite('PUT',body));
    renderChannel(data);$('channel-status').textContent='渠道已保存。';
  }catch(e){$('channel-status').textContent=e.message;}
  finally{channelBusy=false;fields.forEach(id=>$(id).disabled=false);channelButtons();}
});
$('channel-test').addEventListener('click',async()=>{
  if(channelBusy||channelDirty)return;
  channelBusy=true;channelButtons();$('channel-status').textContent='正在发送测试消息…';
  try{
    await balanceRequest('/channel/test',balanceWrite('POST',{version:channelVersion}));
    $('channel-status').textContent='测试消息已发送。';
  }catch(e){$('channel-status').textContent=e.message;}
  finally{channelBusy=false;channelButtons();}
});
refreshBalance().catch(()=>{});
