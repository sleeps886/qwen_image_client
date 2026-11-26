'use strict';
(() => {
  const $=id=>document.getElementById(id);
  let dataset=null,state=null,busy=false;
  let sourceDataset=null,selectedTargetId='';
  let cacheReady=false;
  let chatPreview=null,preparedPointData=null,chatBlocked=false;
  let singlePredictionDone=false;
  let chatView='messages',reviewViewKey=null;
  function placeSharedScene(){
    const host=chatView==='review'&&state?.status==='awaiting_review'
      ?$('reviewTrajectoryPreview'):($('chatMessages').querySelector('.assistant:last-of-type')||$('chatMessages'));
    if(sharedScene.childNodes.length&&sharedScene.parentNode!==host)host.appendChild(sharedScene);
  }
  function syncChatView(){
    const pending=state?.status==='awaiting_review';
    const key=pending?`${state.session_id}:${state.version}`:null;
    if(key!==reviewViewKey){reviewViewKey=key;chatView=pending?'review':'messages';$('reviewReason').value='';$('reviewFeedback').hidden=true;}
    if(!pending)chatView='messages';
    const reviewOpen=pending&&chatView==='review';
    $('chatViewTabs').hidden=!pending;
    $('trajectoryReview').hidden=!reviewOpen;
    $('chatMessages').hidden=reviewOpen;
    $('chatComposer').hidden=reviewOpen;
    $('chatNotice').hidden=reviewOpen;
    for(const [id,selected] of [['showChatMessages',!reviewOpen],['showTrajectoryReview',reviewOpen]]){
      $(id).setAttribute('aria-selected',String(selected));$(id).tabIndex=selected?0:-1;
    }
    placeSharedScene();
  }
  let backendReady=false;
  const newConversationId=()=>globalThis.crypto?.randomUUID?.()||'local-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
  let conversationId=newConversationId(),conversationName='当前会话',conversationIndex=[];
  const sharedScene=document.createElement('section');sharedScene.id='sharedTrajectoryScene';sharedScene.setAttribute('aria-label','持续更新的综合轨迹图');
  let sceneRefreshToken=0;const sceneStateCache=new Map();
  let sceneView='bearing';
  function recoveryLink(){
    const link=$('openPredictionService');link.hidden=backendReady||window.location.port!=='8870';
    if(!link.hidden){const url=new URL(window.location.href);url.port='8872';const source=dataset?.source_result_id||$('batchResultId').value;if(source)url.searchParams.set('source_result',source);link.href=url.href;}
  }
  async function verifyBackend(){
    backendReady=false;$('backendReadiness').dataset.state='checking';
    try{
      const response=await fetch('/openapi.json',{cache:'no-store',signal:AbortSignal.timeout(6000)});
      if(!response.ok)throw new Error('无法读取当前服务接口定义');
      const schema=await response.json(),paths=schema.paths||{};
      backendReady=!!paths['/api/incremental/sessions/{session_id}/review']&&!!paths['/api/incremental/all-points-preview']&&!!paths['/api/incremental/sessions/{session_id}/preview']&&!!paths['/api/incremental/sessions/{session_id}/execution']&&
        !!schema.components?.schemas?.AdvancePrediction?.properties?.selected_times&&
        !!paths['/api/incremental/sessions/{session_id}/step']?.post?.parameters?.some(parameter=>parameter.name==='stream'&&parameter.in==='query');
      $('backendReadiness').dataset.state=backendReady?'ready':'error';
      $('backendReadinessTitle').textContent=backendReady?'✓ 当前预测接口已就绪（端口 '+window.location.port+'）':'✕ 当前服务是旧版，不能执行本次预测';
      $('backendReadinessDetail').textContent=backendReady?'点位准备、分组输入和节点执行接口均已加载；尚未调用模型。':
        '当前地址 '+window.location.origin+' 缺少新版预测接口，继续点击只会404。请切换下方新版入口，或重启当前后端后重新检测。原批次和任务文件未删除。';
    }catch(error){$('backendReadiness').dataset.state='error';$('backendReadinessTitle').textContent='✕ 当前服务接口检测失败';$('backendReadinessDetail').textContent=error.message+'；未发送模型请求，请确认服务在线。';}
    recoveryLink();controls();return backendReady;
  }
  let executionTimer=null,executionPolling=0,lastExecution=null;
  const executionDefinitions=[['input_validation','输入与版本校验'],['platform_assessment','平台状态与阵列几何分析'],['context_preparation','本轮点位与上下文准备'],['model_prediction','模型方位推理与预测'],['answer_validation','回复与候选引用校验'],['spatial_planning','平台与目标轨迹融合规划'],['state_commit','保存本轮轨迹与记录'],['trajectory_review','interrupt · 是否采用轨迹']];
  function drawExecution(snapshot={nodes:[],status:'not_started'}){
    if(snapshot.execution_id){
      if(lastExecution?.execution_id===snapshot.execution_id&&snapshot.sequence<lastExecution.sequence)return;
      if(lastExecution?.execution_id!==snapshot.execution_id&&lastExecution?.created_at>snapshot.created_at)return;
      lastExecution=snapshot;
    }else lastExecution=null;
    const statuses={pending:'待执行',running:'执行中',completed:'已完成',failed:'失败',skipped:'未执行',waiting_for_user:'等待用户采用或打回'};
    $('executionNodes').replaceChildren();let completed=0;
    const single=$('singlePrediction').checked;
    const core=['platform_assessment','model_prediction','spatial_planning','trajectory_review'];
    $('executionTitle').textContent=single?'单轮轨迹预测路径':'Agent 执行路径';
    $('executionSupportNodes').replaceChildren();
    for(const [name,label] of executionDefinitions){
      const event=snapshot.nodes?.find(n=>n.node===name)||{status:['completed','failed'].includes(snapshot.status)?'skipped':'pending'};
      const row=document.createElement('li');row.className='execution-node '+event.status;
      const title=document.createElement('strong');title.textContent=label;
      const detail=document.createElement('span');detail.textContent=(statuses[event.status]||event.status)+(typeof event.elapsed_ms==='number'?` · ${(event.elapsed_ms/1000).toFixed(3)}秒`:'');
      row.append(title,detail);
      if(event.error_type){const error=document.createElement('small');error.textContent='失败类型：'+event.error_type;row.appendChild(error);}
      if(event.outcome){const outcome=document.createElement('small');outcome.textContent=({ready:'平台配置已就绪',missing_metadata:'缺少平台数据，保留方位预测',bearing_only:'当前只能确定方位方向',spatial_history_only:'仅历史空间候选可用',spatial_prediction_candidates:'已生成空间预测候选（保留镜像）'})[event.outcome]||event.outcome;row.appendChild(outcome);}
      (single&&!core.includes(name)?$('executionSupportNodes'):$('executionNodes')).appendChild(row);
      if(event.status==='completed'&&(!single||core.includes(name)))completed++;
    }
    $('executionProgress').max=single?4:8;
    $('executionProgress').value=completed;
    const current=snapshot.nodes?.find(n=>n.status==='running'||n.status==='waiting_for_user')||snapshot.nodes?.find(n=>n.status==='failed');
    $('executionCurrent').textContent=snapshot.recovered_from_saved_reply?'已从保存回复本地恢复，未再次调用模型':current?`第${(snapshot.base_version??0)+1}轮 → ${current.label}`:snapshot.status==='completed'?`第${snapshot.base_version+1}轮已完成`:'等待发送本轮点位';
    $('executionBadge').textContent=({running:'执行中',completed:'已完成',failed:'执行失败',not_started:'未执行',waiting_for_user:'interrupt 待确认',rejected:'已打回'})[snapshot.status]||'等待事件';
  }
  async function readExecution(sid,version){
    const response=await fetch(`/api/incremental/sessions/${sid}/execution`,{signal:AbortSignal.timeout(5000)});
    if(!response.ok){if(response.status===404)$('executionHelp').textContent='执行记录接口尚未加载或任务不存在，请重启后端后加载任务；未伪造节点状态。';return;}
    const snapshot=await response.json();
    if(state?.session_id!==sid||version!==undefined&&snapshot.base_version!==version)return;
    $('executionHelp').textContent='后端真实执行快照；刷新不自动续跑，节点完成不代表预测准确。';
    drawExecution(snapshot);
  }
  function streamedReason(text){
    const start=/"reason"\s*:\s*"/.exec(text);if(!start)return '';
    let result='';const content=text.slice(start.index+start[0].length);
    for(let i=0;i<content.length;i++){
      const char=content[i];if(char==='"')break;
      if(char!=='\\'){result+=char;continue;}
      if(++i>=content.length)break;
      const escape=content[i];
      if(escape==='u'){const hex=content.slice(i+1,i+5);if(!/^[0-9a-f]{4}$/i.test(hex))break;result+=String.fromCharCode(parseInt(hex,16));i+=4;}
      else{const escapes={'n':'\n','r':'\r','t':'\t','b':'\b','f':'\f','"':'"','\\':'\\','/':'/'};if(!(escape in escapes))break;result+=escapes[escape];}
    }
    return result;
  }
  async function streamStep(sid,body,onEvent){
    const response=await fetch(`/api/incremental/sessions/${sid}/step?stream=true`,{method:'POST',headers:{'Content-Type':'application/json','Accept':'text/event-stream'},body:JSON.stringify(body)});
    if(!response.ok){const error=await response.json();throw new Error(typeof error.detail==='string'?error.detail:'流式预测请求失败');}
    if(!response.headers.get('content-type')?.includes('text/event-stream')||!response.body)throw new Error('服务未返回流式响应，请重启新版后端；未自动重试。');
    const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',result;
    const consume=block=>{const data=block.split(/\r?\n/).filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');if(!data)return;const event=JSON.parse(data);if(event.event==='error')throw new Error(event.message);if(event.event==='result')result=event.result;else onEvent?.(event);};
    try{
      while(true){const chunk=await reader.read();buffer+=decoder.decode(chunk.value||new Uint8Array(),{stream:!chunk.done});if(buffer.length>16*1024*1024)throw new Error('流式事件过大');
        let boundary;while((boundary=/\r?\n\r?\n/.exec(buffer))){consume(buffer.slice(0,boundary.index));buffer=buffer.slice(boundary.index+boundary[0].length);}
        if(chunk.done)break;
      }
      if(buffer.trim())consume(buffer);
      if(!result)throw new Error('流式连接提前结束，未收到已校验结果；未自动重试。');
      return result;
    }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
  }
  async function executeStep(body,onEvent){
    if(!backendReady)throw new Error('当前服务缺少新版预测接口，已阻止发送；请使用顶部新版入口或重启后重新检测。');
    const sid=state.session_id,token=++executionPolling;clearTimeout(executionTimer);drawExecution();
    $('executionCurrent').textContent='请求已发出，等待后端实际节点事件……';
    async function poll(){
      try{await readExecution(sid,body.version);}catch{$('executionHelp').textContent='节点状态连接暂时不可用；不推断模型已结束，也不会重试付费请求。';}
      if(executionPolling===token)executionTimer=setTimeout(poll,600);
    }
    poll();
    try{
      const result=await streamStep(sid,body,onEvent);
      if(result.execution)drawExecution(result.execution);return result;
    }finally{
      executionPolling++;clearTimeout(executionTimer);
      try{await readExecution(sid,body.version);}catch{/* Keep last confirmed snapshot. */}
    }
  }
  const cacheAvailable=()=>typeof WorkspaceCache!=='undefined';
  function savePrediction(){
    if(!cacheReady||!cacheAvailable())return;
    const snapshot={version:1,dataset,state,sourceDataset,selectedTargetId,conversationId,conversationName,sceneView,
      seeds:$('seeds').value,stride:$('stride').value,horizon:$('horizon').value,maxCalls:$('maxCalls').value,
      selectionKey,selectedPointIds:[...selectedPointIds],groupSelectionKey,selectedGroupKeys:[...selectedGroupKeys],singlePrediction:$('singlePrediction').checked,singlePredictionDone,questionMode:$('questionTemplateMode').value,sessionId:$('sessionId').value,chatDraft:$('chatInput').value,chatQuestion:$('chatQuestion').value};
    if(dataset){const entry={id:conversationId,name:conversationName,updatedAt:Date.now(),rounds:state?.rounds.length||0};conversationIndex=[entry,...conversationIndex.filter(item=>item.id!==conversationId)];drawConversations();}
    return Promise.all([WorkspaceCache.set('prediction-workspace',snapshot),
      WorkspaceCache.set('prediction-conversation-'+conversationId,snapshot),WorkspaceCache.set('prediction-conversations',conversationIndex)])
      .catch(()=>{$('predictionCacheStatus').textContent='浏览器本地保存失败；请导出重要结果，后端任务仍按任务编号保留。';});
  }
  function drawConversations(){
    $('conversationList').replaceChildren();
    for(const entry of conversationIndex){const button=document.createElement('button');button.type='button';button.className='conversation-entry';button.textContent=entry.name+' · '+entry.rounds+'轮';button.title=entry.name;button.dataset.conversationId=entry.id;button.setAttribute('aria-current',String(entry.id===conversationId));button.disabled=busy;button.addEventListener('click',()=>run(()=>switchConversation(entry.id)));$('conversationList').appendChild(button);}
  }
  async function switchConversation(id){
    if(id===conversationId)return;
    await savePrediction();const saved=await WorkspaceCache.get('prediction-conversation-'+id);
    if(!saved?.dataset)throw new Error('本地会话数据不存在，未调用模型。');
    const savedState=saved.state?await request('/api/incremental/sessions/'+encodeURIComponent(saved.state.session_id)):null;
    sourceDataset=saved.sourceDataset||(sourceDataset?.source_result_id===saved.dataset.source_result_id?sourceDataset:saved.dataset);dataset=saved.dataset;selectedTargetId=saved.selectedTargetId||saved.dataset.selected_target_id||'';state=null;preparedPointData=null;chatPreview=null;chatBlocked=false;lastExecution=null;
    conversationId=id;conversationName=saved.conversationName||'已保存会话';useManualDataset();
    for(const key of ['seeds','stride','horizon','maxCalls','sessionId'])if(saved[key]!==undefined)$(key).value=saved[key];
    $('singlePrediction').checked=saved.singlePrediction!==false;singlePredictionDone=!!saved.singlePredictionDone;
    $('chatConsent').checked=false;$('predictionConsent').checked=false;$('questionTemplateMode').value='auto';
    selectionKey='';groupSelectionKey='';inventory();
    if(savedState)render(savedState);
    else{$('chatMessages').replaceChildren();drawExecution();}
    await prepareChat(true);
    if(!singlePredictionDone&&saved.groupSelectionKey===groupSelectionKey){const allowed=new Set(candidateGroups().filter(groupAllowed).map(groupKey)),restored=saved.selectedGroupKeys?.filter(key=>allowed.has(key));if(restored?.length){selectedGroupKeys=new Set(restored);renderPointViews();}}
    if(saved.chatQuestion){$('chatQuestion').value=saved.chatQuestion;$('questionTemplateMode').value=saved.questionMode||'auto';syncPointInput();}
    $('conversationNotice').textContent='已切换会话；发送授权已取消，未调用模型。';drawConversations();savePrediction();
  }
  const active=new Set(['ready','tracking','coasting','ambiguous']);
  const pointMarker='\n\n点位数据：\n';
  const pointFields=['candidate_id','t','bearing_deg','array','platform_id'];
  function allCandidates(){
    const points=dataset?.candidates||dataset?.images?.flatMap(im=>im.candidates.map(p=>({...p,array:im.array,platform_id:im.platform_id})))||[];
    return points.map(p=>({...p,candidate_id:p.candidate_id||p.id}));
  }
  function targetCatalog(){return (sourceDataset?.target_catalog||dataset?.target_catalog||[]).filter(target=>typeof target.target_id==='string'&&Array.isArray(target.candidate_ids));}
  function targetRequired(){return targetCatalog().length>0&&!selectedTargetId;}
  function drawTargetMenu(){
    const menu=$('targetTrackSelect');menu.replaceChildren();const catalog=targetCatalog();
    const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent=catalog.length?'请选择候选目标':'当前输入点位（无目标关联目录）';menu.appendChild(placeholder);
    for(const [index,target] of catalog.entries()){const option=document.createElement('option');option.value=target.target_id;option.textContent=`候选目标${index+1} · ${target.target_id} · ${target.candidate_ids.length}点`;menu.appendChild(option);}
    menu.value=selectedTargetId;menu.disabled=busy||!catalog.length;
    $('targetTrackNotice').textContent=selectedTargetId?`当前仅显示 ${selectedTargetId} 对应测量。离线关联为候选假设，身份未确认；可按时间勾选。`:
      catalog.length?'请选择候选目标。选择后另开独立会话，只使用该目标对应的hull/tow候选点；旧记录保留，不调用模型。':'此输入没有可核对的目标关联目录，不自动编造目标分组。';
  }
  function editablePoints(){return (preparedPointData?.stage==='initialization'?preparedPointData.history:
    state?.initialized?preparedPointData?.new_candidates:preparedPointData?.history)||[];}
  function messagePoints(){return editablePoints().map(p=>Object.fromEntries(pointFields.map(k=>[k,p[k]])));}
  let selectedPointIds=new Set(),selectionKey='',selectedGroupKeys=new Set(),groupSelectionKey='';
  const groupKey=g=>JSON.stringify([g.platform_id,g.t]);
  function candidateGroups(){
    const groups=new Map();
    for(const p of allCandidates()){
      const key=groupKey(p);if(!groups.has(key))groups.set(key,{platform_id:p.platform_id,t:p.t,hull:[],tow:[]});
      groups.get(key)[p.array].push(p);
    }
    return [...groups.values()].sort((a,b)=>a.platform_id.localeCompare(b.platform_id)||a.t-b.t);
  }
  function selectedGroups(){return candidateGroups().filter(g=>selectedGroupKeys.has(groupKey(g)));}
  function validateGroupContext(context,groups){
    if(!Array.isArray(context.selected_time_groups))throw new Error('后端尚未支持同步组输入，请重启后端后重新准备');
    const expected=groups.flatMap(g=>[...g.hull,...g.tow]),actual=context.selected_time_groups.flatMap(g=>[...g.hull,...g.tow]);
    const lookup=new Map(actual.map(p=>[p.candidate_id,p]));
    if(expected.length!==actual.length||expected.some(p=>!lookup.has(p.candidate_id)||pointFields.some(key=>p[key]!==lookup.get(p.candidate_id)[key])))
      throw new Error('任务中的hull/tow测量与本批选择不一致，未调用模型。旧任务请重新载入批次后创建新任务，避免漏发另一阵列。');
  }
  function groupAllowed(g){
    const reference=preparedPointData?.history?.[0];
    return !!reference&&g.platform_id===reference.platform_id&&(!state?.initialized||g.t>state.visible_until);
  }
  function fixedStart(g){return !state?.initialized&&preparedPointData?.history?.some(p=>p.t===g.t&&p.platform_id===g.platform_id);}
  function parseMessage(){
    const groups=selectedGroups();
    if(!groups.length)throw new Error('请先勾选要发送的hull/tow时间组');
    if(groups.length>16)throw new Error('每轮最多选择16个时间组，请分轮发送');
    if(groups.some(g=>!groupAllowed(g)))throw new Error('当前只支持同平台、晚于已提交时刻的时间组');
    return {question:$('chatQuestion').value.trim(),selected_times:groups.map(g=>g.t),groups,
      observations:groups.flatMap(g=>[...g.hull,...g.tow]).map(p=>Object.fromEntries(pointFields.map(k=>[k,p[k]])))};
  }
  function groupedTable(groups,selectable=true,compact=false){
    const table=document.createElement('table');table.className='track-comparison group-table';
    const header=document.createElement('tr');
    for(const label of [...(selectable?['选择']:[]),'时间／平台','hull 船体阵','tow 拖曳阵']){const th=document.createElement('th');th.textContent=label;header.appendChild(th);}
    const head=document.createElement('thead');head.appendChild(header);table.appendChild(head);const body=document.createElement('tbody');
    for(const group of groups){
      const row=document.createElement('tr'),select=document.createElement('td'),check=document.createElement('input');
      check.type='checkbox';check.checked=selectedGroupKeys.has(groupKey(group));check.disabled=busy||!groupAllowed(group)||fixedStart(group);check.dataset.groupKey=groupKey(group);check.setAttribute('aria-label','选择时间组 '+group.t+'秒 '+group.platform_id);
      check.addEventListener('change',()=>{
        if(check.checked)selectedGroupKeys.add(groupKey(group));else selectedGroupKeys.delete(groupKey(group));
        $('questionTemplateMode').value='auto';preloadQuestion();controls();savePrediction();
      });if(selectable){select.appendChild(check);row.appendChild(select);}
      const time=document.createElement('td');time.textContent=group.t+'秒／'+group.platform_id+(fixedStart(group)?'（起始）':'');row.appendChild(time);
      for(const array of ['hull','tow']){const cell=document.createElement('td');const text=group[array].map(p=>p.candidate_id+'：'+Number(p.bearing_deg).toFixed(2)+'°').join('；')||'该阵列无测量';cell.title=text;
        const bearings=group[array].map(p=>Number(p.bearing_deg));
        cell.textContent=compact?(bearings.length?`${bearings.length}点\n${Math.min(...bearings).toFixed(1)}～${Math.max(...bearings).toFixed(1)}°`:'—'):text;row.appendChild(cell);}
      body.appendChild(row);
    }
    table.appendChild(body);return table;
  }
  const qualityText=p=>[p.quality==='fitted'?'已拟合':p.quality==='needs_review'?'待复核':'',p.mixed?'混合峰':''].filter(Boolean).join('／');
  function pointTable(points,selectable=false){
    const table=document.createElement('table');table.className='track-comparison';const hasQuality=points.some(p=>qualityText(p));
    const head=document.createElement('thead'),header=document.createElement('tr');
    for(const name of [...(selectable?['发送']:[]),'点编号','时间（秒）','方位（度）','阵列',...(hasQuality?['质量']:[])]){const th=document.createElement('th');th.textContent=name;header.appendChild(th);}
    head.appendChild(header);table.appendChild(head);const body=document.createElement('tbody');
    for(const p of points){
      const row=document.createElement('tr');
      if(selectable){const cell=document.createElement('td'),check=document.createElement('input');check.type='checkbox';check.checked=selectedPointIds.has(p.candidate_id);check.disabled=busy||!state?.initialized;check.dataset.pointId=p.candidate_id;check.setAttribute('aria-label','发送点位 '+p.candidate_id);
        check.addEventListener('change',()=>{if(check.checked)selectedPointIds.add(p.candidate_id);else selectedPointIds.delete(p.candidate_id);syncPointInput();savePrediction();});cell.appendChild(check);row.appendChild(cell);}
      for(const value of [p.candidate_id,Number(p.t).toFixed(2),Number(p.bearing_deg).toFixed(3),p.array==='hull'?'船体阵':'拖曳阵',...(hasQuality?[qualityText(p)]:[])]){const cell=document.createElement('td');cell.textContent=String(value);row.appendChild(cell);}
      body.appendChild(row);
    }
    table.appendChild(body);return table;
  }
  function showPointDetails(){
    const p=allCandidates().find(p=>p.candidate_id===$('pointDetailSelect').value);
    $('pointDetailFields').replaceChildren();if(!p)return;
    const fields=[['点位编号',p.candidate_id],['时间',Number(p.t).toFixed(2)+' 秒'],['方位角',Number(p.bearing_deg).toFixed(3)+'°'],['平台',p.platform_id],['阵列',p.array==='hull'?'船体阵':'拖曳阵']];
    const current=selectedGroupKeys.has(groupKey(p));
    fields.push(['使用范围',current?'本轮已选（同步组）':
      preparedPointData?.history?.some(point=>point.candidate_id===p.candidate_id)?'历史上下文':'仅点库浏览，不发送']);
    if(qualityText(p))fields.push(['质量',qualityText(p)]);
    for(const [key,label,unit] of [['strength','相对强度',''],['sigma_deg','高斯峰宽','°'],['bearing_error_assumption_deg','角误差代理','°']]){
      if(Number.isFinite(p[key]))fields.push([label,p[key].toFixed(3)+unit]);
    }
    for(const [name,value] of fields){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=value;$('pointDetailFields').append(dt,dd);}
  }
  function clearPointViews(){
    $('pointDetailSelect').replaceChildren();$('pointDetailFields').replaceChildren();$('candidateGroupList').replaceChildren();$('chatPointTable').textContent='尚无可发送点位';$('chatPayloadSummary').textContent='全量候选点 · 等待载入';
  }
  function useManualDataset(){
    const url=new URL(window.location.href);url.searchParams.delete('source_result');url.searchParams.set('empty','1');window.history.replaceState(null,'',url);
  }
  function syncPointInput(){
    if(!preparedPointData)return;
    const groups=selectedGroups(),observations=groups.flatMap(g=>[...g.hull,...g.tow]);const input={question:$('chatQuestion').value.trim(),observations};$('chatInput').value=JSON.stringify({question:input.question,point_data:preparedPointData,selected_time_groups:groups},null,2);
    $('chatPayloadSummary').textContent=`共${candidateGroups().length}个时间组／${allCandidates().length}点 · 已选${groups.length}组、${observations.length}点`;
    if(targetRequired())$('chatPayloadSummary').textContent='请选择候选目标后查看点位';
    $('chatAttachmentSummary').textContent=`点位附件 · ${groups.length}个时间组 / ${observations.length}个观测点`;
    showPointDetails();
  }
  function stageTemplate(){
    const count=editablePoints().length,horizon=state?.horizon||Number($('horizon').value);
    if(!state?.initialized&&count===1)return ['单点待观察','当前只有1个实测点，请说明证据不足，不假设静止、不编造变化率，也不输出数值预测；等待下一次新增点后再判断趋势。'];
    if(state?.history.length===1)return ['两点暂定趋势',`请结合本轮新增点判断能否接续；若接入后已有2点，可给出暂定线性方位预测（未来${horizon}秒），不必等3点。待复核质量不等于无效，不能声称真实身份已确认。`];
    if(!state?.initialized)return ['初始化',`请根据这${count}个候选种子点判断初始方位变化趋势，说明是否可能属于同一目标，并预测之后${horizon}秒的方位轨迹；证据不足时保留歧义，不编造未提供的参数。`];
    if(state.status==='ambiguous')return ['歧义复核',`请结合本轮新增候选点复核上一轮歧义，说明每个接续选择的点位依据；无法区分时保持未确定，并给出之后${horizon}秒的有限方位预测。`];
    if(state.status==='coasting')return ['缺测接续',`请判断本轮点位是否支持重新接回原轨迹；没有可靠观测时保持缺测，区分实测和外推，并预测之后${horizon}秒的可能方位。`];
    return ['连续预测',`请用本轮选中的新增点核对上一轮预测，判断延续或转向，说明接入依据及偏差，并预测之后${horizon}秒的方位轨迹。`];
  }
  function renderPointViews(){
    const points=editablePoints(),key=JSON.stringify([state?.session_id,state?.version,points.map(p=>[p.candidate_id,p.t,p.bearing_deg])]);
    if(key!==selectionKey){selectionKey=key;selectedPointIds=new Set(points.map(p=>p.candidate_id));}
    const groupKeyNow=JSON.stringify([state?.session_id,state?.version,points.map(p=>p.candidate_id)]);
    if(groupKeyNow!==groupSelectionKey){
      groupSelectionKey=groupKeyNow;
      const defaults=preparedPointData?.selected_time_groups||(!state?.initialized?preparedPointData?.history:points)||[];
      selectedGroupKeys=new Set(defaults.map(groupKey));
    }
    drawTargetMenu();$('candidateGroupList').replaceChildren(groupedTable(targetRequired()?[]:candidateGroups(),true,true));
    $('chatPointTable').replaceChildren(groupedTable(selectedGroups()));
    const old=$('pointDetailSelect').value;$('pointDetailSelect').replaceChildren();
    for(const p of (targetRequired()?[]:allCandidates()).sort((a,b)=>a.platform_id.localeCompare(b.platform_id)||a.array.localeCompare(b.array)||a.t-b.t||a.candidate_id.localeCompare(b.candidate_id))){const option=document.createElement('option');option.value=p.candidate_id;option.textContent=p.candidate_id+' · '+p.platform_id+'/'+p.array+' · '+p.t+'秒 · '+Number(p.bearing_deg).toFixed(2)+'°';$('pointDetailSelect').appendChild(option);}
    if([...$('pointDetailSelect').options].some(option=>option.value===old))$('pointDetailSelect').value=old;
    showPointDetails();syncPointInput();
  }
  const status=text=>{$('predictionStatus').textContent=text;};
  const validResult=id=>/^[0-9]{8}_[0-9]{6}_[0-9a-f]{8}$/.test(id||'');
  function receipt(kind,title,detail){
    $('batchReceipt').dataset.state=kind;$('batchReceiptTitle').textContent=title;$('batchReceiptDetail').textContent=detail;
    $('batchPointPreview').hidden=kind!=='success';
    if(kind!=='success')$('batchPointRows').replaceChildren();
  }
  function notifySource(resultId,kind,count=0,message=''){
    try{localStorage.setItem('gaussian-transfer-receipt',JSON.stringify({resultId,kind,count,message,at:Date.now()}));}catch{/* Receipt in this page remains authoritative. */}
  }
  function verifyBatch(batch,resultId){
    if(batch?.version!=='gaussian-prediction-batch-v1'||batch.source_result_id!==resultId||
       !Array.isArray(batch.candidates)||!batch.candidates.length||batch.point_count!==batch.candidates.length||
       typeof batch.batch_id!=='string'||!batch.batch_id)throw new Error('批次来源、版本或点数校验失败');
    const ids=new Set();
    for(const p of batch.candidates){
      if(typeof p.candidate_id!=='string'||!p.candidate_id||ids.has(p.candidate_id)||
         !Number.isFinite(p.t)||p.t<0||!Number.isFinite(p.bearing_deg)||p.bearing_deg<0||p.bearing_deg>180||
         !['hull','tow'].includes(p.array)||typeof p.platform_id!=='string'||!p.platform_id||
         !['gaussian_profile','gaussian_diagnostic'].includes(p.source_kind))throw new Error('批次存在无效点、重复编号或非高斯测量');
      ids.add(p.candidate_id);
    }
  }
  async function loadBatch(resultId){
    clearPointViews();
    drawExecution();
    singlePredictionDone=false;chatBlocked=false;chatPreview=null;preparedPointData=null;$('chatMessages').replaceChildren();$('chatConsent').checked=false;
    dataset=null;state=null;$('sessionId').value='';$('seeds').value='';$('predictionConsent').checked=false;
    $('inventory').textContent='正在接收……';$('sourceBatchDownload').hidden=true;$('predictionSummary').textContent='尚未创建本批预测任务';$('roundLog').textContent='尚未执行';$('predictionChart').replaceChildren();
    receipt('loading','正在接收高斯点位数据……',`来源：${resultId}；仅从本地服务读取，不调用模型。`);
    try{
      if(!validResult(resultId))throw new Error('请填写有效的来源结果编号');
      const batch=await request('/api/incremental/batches/'+encodeURIComponent(resultId));verifyBatch(batch,resultId);
      dataset=batch;sourceDataset=batch;selectedTargetId='';$('batchResultId').value=resultId;inventory();
      const arrays=[...new Set(batch.candidates.map(p=>p.platform_id+'/'+p.array))].join('、');
      const times=batch.candidates.map(p=>p.t);
      receipt('success',`✓ 已成功接收 ${batch.point_count} 个高斯点位`,
        `来源：${resultId} / ${batch.source_file}；阵列：${arrays}；时间：${Math.min(...times)}～${Math.max(...times)}秒；待复核：${batch.review_point_count}点。未调用模型，请选择3～5个种子后创建任务。`);
      for(const p of batch.candidates.slice(0,12)){
        const row=document.createElement('tr');
        for(const value of [p.candidate_id,p.platform_id+'/'+p.array,p.t,p.bearing_deg.toFixed(4),p.quality+(p.mixed?' / 混合峰':'')]){
          const cell=document.createElement('td');cell.textContent=String(value);row.appendChild(cell);
        }
        $('batchPointRows').appendChild(row);
      }
      $('sourceBatchDownload').href='/api/incremental/batches/'+encodeURIComponent(resultId);$('sourceBatchDownload').hidden=false;
      status(`传递成功：实际收到${batch.point_count}个高斯点位；尚未调用模型。`);
      notifySource(resultId,'success',batch.point_count);return batch;
    }catch(error){
      $('inventory').textContent='本次未接收到有效点位';$('batchSource').textContent='高斯批次接收失败，未使用旧缓存或示例替代。';
      receipt('error','✕ 高斯点位接收失败',error.message+'。可检查来源编号后点击“载入／重试”；不会自动调用模型。');
      notifySource(resultId,'error',0,error.message);throw error;
    }
  }
  function drawSidebarPlatform(){
    const root=$('sidebarPlatformFields');root.replaceChildren();
    const motion=state?.platform_motion;
    const config=state?.platform_config||(motion?{platforms:[{
      ...(dataset?.platform_config?.platforms||[]).find(p=>p.name===motion.platform_id),
      name:motion.platform_id,start_ll:[motion.initial_latitude,motion.initial_longitude],
      heading_deg:motion.heading_deg,speed_kn:motion.speed_kn
    }]}:dataset?.platform_config);
    const platforms=Array.isArray(config?.platforms)?config.platforms:[];
    if(!platforms.length){const note=document.createElement('p');note.className='note';note.textContent='当前数据未包含平台配置';root.appendChild(note);return;}
    for(const platform of platforms){
      const title=document.createElement('h3');title.textContent=`平台 ${platform.name||''}`;root.appendChild(title);
      const fields=[],ll=platform.start_ll;
      if(Array.isArray(ll)&&Number.isFinite(ll[0]))fields.push(['初始纬度',ll[0].toFixed(6)+'°']);
      if(Array.isArray(ll)&&Number.isFinite(ll[1]))fields.push(['初始经度',ll[1].toFixed(6)+'°']);
      if(Number.isFinite(platform.heading_deg))fields.push(['航向',platform.heading_deg.toFixed(1)+'°']);
      if(Number.isFinite(platform.speed_kn))fields.push(['航速',platform.speed_kn.toFixed(2)+' 节']);
      for(const [key,label] of [['hull_m','船体阵偏移'],['tow_m','拖曳阵偏移']]){
        const pair=platform[key];if(Array.isArray(pair)&&pair.length===2&&pair.every(Number.isFinite))fields.push([label,`[${pair.join(', ')}] 米`]);
      }
      const list=document.createElement('dl');list.className='point-fields';
      for(const [name,value] of fields){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=value;list.append(dt,dd);}
      root.appendChild(list);
    }
  }
  function controls(){
    drawSidebarPlatform();
    const jointSource=sourceDataset?.source_result_id||dataset?.source_result_id;
    $('openJointPrediction').href='/joint-prediction'+(jointSource?'?source_result='+encodeURIComponent(jointSource):'');
    const reviewing=state?.status==='awaiting_review';
    if(reviewing)$('chatState').textContent='本轮已暂停 · 等待确认';
    syncChatView();
    for(const id of ['adoptTrajectory','rejectTrajectory'])$(id).disabled=busy||!reviewing;
    $('allPointsPreview').disabled=busy||!dataset||targetRequired()||reviewing;
    recoveryLink();
    $('targetTrackSelect').disabled=busy||!targetCatalog().length;
    $('openNewConversation').disabled=busy||!dataset;$('createConversation').disabled=busy||!dataset;
    document.querySelectorAll('.conversation-entry').forEach(button=>button.disabled=busy);
    for(const id of ['create','reload','example','datasetFile','sessionId','loadGaussianBatch','batchResultId','suggestSeeds','loadDebugTrack']) $(id).disabled=busy;
    $('create').disabled=busy||!dataset||!backendReady;
    $('chatPrepare').disabled=busy||!dataset||!backendReady||state?.status==='awaiting_review';
    $('chatInput').disabled=busy;
    $('chatQuestion').disabled=busy;
    $('questionTemplateMode').disabled=busy;
    document.querySelectorAll('[data-group-key]').forEach(check=>{const g=candidateGroups().find(g=>groupKey(g)===check.dataset.groupKey);check.disabled=busy||!g||!groupAllowed(g)||fixedStart(g);});
    $('chatSend').disabled=!backendReady||busy||!dataset||targetRequired()||(!state&&!preparedPointData)||chatBlocked||!!state&&(!active.has(state.status)||state.calls>=state.max_calls);
    $('singlePrediction').disabled=busy;
    $('chatSend').textContent=singlePredictionDone?'准备下一轮（免费）':$('singlePrediction').checked?'运行一次预测 ↗':'发送并预测下一段 ↗';
    const hint=$('chatActionHint');
    hint.dataset.warning=String(!singlePredictionDone&&(!selectedGroups().length||!$('chatConsent').checked));
    hint.textContent=busy?'正在处理，请勿重复点击。':targetRequired()?'请先在左侧选择候选目标，系统将另开目标专属会话。':chatBlocked?'本轮未完成，请查看失败原因；未自动重试。':
      state&&!active.has(state.status)?`任务状态：${state.status}，不能继续调用；请核对任务或重新建立候选假设。`:
      state&&state.calls>=state.max_calls?'本任务已达到模型调用上限，不能继续预测。':
      singlePredictionDone?'本轮已完成。点击下方按钮免费准备下一轮。':
      !selectedGroups().length?'当前未选择点位：点击运行按钮会先免费恢复本轮默认点位，不调用模型。':
      !$('chatConsent').checked?'请先勾选上方发送授权，再运行本轮预测（可能产生API费用）。':
      `已选择${selectedGroups().length}个时间组，本轮可运行一次预测。`;
    $('step').disabled=!backendReady||busy||!state||!active.has(state.status)||state.calls>=state.max_calls;
    $('export').disabled=busy||!state;
    $('step').textContent=state?.initialized?'加入下一窗口并预测（一次模型调用）':'执行初始化预测（一次模型调用）';
  }
  async function request(url,body){
    const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result=await response.json();
    if(response.status===404&&new URL(url,window.location.origin).pathname.startsWith('/api/incremental/sessions/'))throw new Error(backendReady?
      '当前任务不存在或已被移动，请重新载入点位批次并创建任务；未调用模型。':
      '当前地址缺少新版预测接口，请切换顶部新版服务入口，或重启当前后端后重新检测；未调用模型。');
    if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail||result));
    return result;
  }
  function inventory(){
    const points=dataset?.candidates||dataset?.images?.flatMap(im=>im.candidates.map(p=>({...p,array:im.array,platform_id:im.platform_id})))||[];
    if(!Array.isArray(points)||!points.length)throw new Error('文件中没有候选点。platform.json仅含平台参数；请从原页面传入高斯批次或导入点位JSON。');
    $('debugTrackNotice').hidden=!dataset.debug_reference;
    if(dataset.debug_reference)$('debugTrackNotice').textContent=`单轨逐点调试：${dataset.debug_reference.ridge_id}，共${points.length}点。片段选取使用全时段离线关联，只用于预测调试，不是独立身份真值或空间路线。每次发送新增1点，不自动调用模型。`;
    $('inventory').textContent=points.map(p=>`${p.candidate_id||p.id} | ${p.platform_id}/${p.array} | t=${p.t} | ${p.bearing_deg}°${p.quality?' | '+p.quality:''}${p.mixed?' | 混合峰待复核':''}`).join('\n');
    $('batchSource').textContent=dataset?.source_result_id?
      `高斯批次来源：${dataset.source_result_id} / ${dataset.source_file}；点数：${points.length}；待复核点：${dataset.review_point_count??'未统计'}。第一期仍需手选同一平台/阵列的3～5个种子，未自动确认目标。`:
      '手工导入点位：不是平台参数。候选身份待判断，创建和导入不调用模型。';
    status(`已导入${points.length}个候选；可选择同一阵列、不同时间的1～5个种子。单点不能判断趋势。`);
    if(!state)preloadInitialChat(points);
  }
  function question(){
    if($('chatQuestion').value.trim())return $('chatQuestion').value.split(pointMarker)[0].trim();
    try{return JSON.parse($('chatInput').value).question||'请根据本轮点位判断接续关系，解释依据并预测下一段轨迹。';}
    catch{return '请根据本轮点位判断接续关系，解释依据并预测下一段轨迹。';}
  }
  function preloadQuestion(){
    renderPointViews();
    const [stage,base]=stageTemplate();const groups=selectedGroups();const template=base+' 本轮选择'+groups.length+'个同步时间组（'+groups.map(g=>g.t+'秒').join('、')+'），hull候选'+groups.reduce((n,g)=>n+g.hull.length,0)+'点、tow候选'+groups.reduce((n,g)=>n+g.tow.length,0)+'点。请联合参考两阵测量，不按顺序强行配对；输出仍为当前主阵列的方位预测。';
    if($('questionTemplateMode').value==='auto'||!$('chatQuestion').value.trim())$('chatQuestion').value=template;
    else $('chatQuestion').value=$('chatQuestion').value.split(pointMarker)[0].trim();
    $('questionTemplateStage').textContent=stage+'模板 · 当前阶段自动准备问题，内容可编辑';
    syncPointInput();return $('chatQuestion').value.trim();
  }
  function preloadInitialChat(raw){
    const points=raw.map(p=>({...p,candidate_id:p.candidate_id||p.id}));
    const lookup=new Map(points.map(p=>[p.candidate_id,p]));
    let ids=$('seeds').value.split(/[,，\s]+/).filter(Boolean);
    if(ids.length<1||ids.some(id=>!lookup.has(id))){
      const ordered=[...points].sort((a,b)=>a.t-b.t||a.candidate_id.localeCompare(b.candidate_id));
      const first=ordered.find(p=>p.array==='hull')||ordered[0];
      const same=ordered.filter(p=>p.array===first.array&&p.platform_id===first.platform_id);
      let paths=[{points:[],cost:0}];
      for(const t of [...new Set(same.map(p=>p.t))].slice(0,3)){
        const row=same.filter(p=>p.t===t);
        const next=[];
        for(const path of paths)for(const point of row){
          const last=path.points.at(-1),before=path.points.at(-2);
          const slope=last?(point.bearing_deg-last.bearing_deg)/(point.t-last.t):0;
          const previousSlope=before?(last.bearing_deg-before.bearing_deg)/(last.t-before.t):slope;
          const penalty=(point.quality==='needs_review'?1:0)+(point.mixed?1:0);
          next.push({points:[...path.points,point],cost:path.cost+Math.abs(slope)+2*Math.abs(slope-previousSlope)+penalty});
        }
        paths=next.sort((a,b)=>a.cost-b.cost||a.points.at(-1).candidate_id.localeCompare(b.points.at(-1).candidate_id)).slice(0,32);
      }
      ids=(paths[0]?.points||[]).map(p=>p.candidate_id);$('seeds').value=ids.join(',');
    }
    const history=ids.map(id=>lookup.get(id));
    preparedPointData={stage:'initialization',seed_candidate_ids:ids,history};chatPreview=null;
    $('chatInput').value=JSON.stringify({question:preloadQuestion(),point_data:preparedPointData},null,2);
    $('chatInputHint').textContent=`已加载本批全部${points.length}个候选点，可在菜单查看任意时刻和阵列。本轮只发送${history.length}个初始化种子；全量点库不会整批发送，种子仍为待确认假设。`;
    $('chatState').textContent='点位已预载 · 尚未发送';
    syncPointInput();
    if(!state)$('chatMessages').replaceChildren();
    showInitialTrack(history,points);refreshSharedScene();
  }
  function showInitialTrack(history,points){
    const sorted=[...history].sort((a,b)=>a.t-b.t);
    const valid=sorted.length>=1&&sorted.length<=5&&sorted.every(p=>Number.isFinite(p.t)&&p.t>=0&&Number.isFinite(p.bearing_deg)&&p.bearing_deg>=0&&p.bearing_deg<=180&&['hull','tow'].includes(p.array))&&
      new Set(sorted.map(p=>p.t)).size===sorted.length&&new Set(sorted.map(p=>p.platform_id+'/'+p.array)).size===1;
    if(!valid){$('predictionChart').replaceChildren();$('predictionSummary').textContent='初始轨迹待准备：需要同平台、同阵列、不同时间的3～5个点。';return;}
    const last=sorted.at(-1);
    chart({history:sorted,visible_candidates:points.filter(p=>p.array===last.array&&p.platform_id===last.platform_id&&p.t<=last.t),
      visible_until:last.t,horizon:0,rounds:[],prediction:[],initial_preview:true});
    const review=sorted.some((p,i)=>p.quality==='needs_review'||p.mixed||i>0&&Math.abs(p.bearing_deg-sorted[i-1].bearing_deg)>20);
    $('predictionSummary').textContent=`初始${sorted.length}点轨迹已显示 · 本地点位连线假设 · 模型调用0次\n${sorted.map(p=>`${p.candidate_id}：${p.t}秒 / ${p.bearing_deg.toFixed(3)}°`).join('\n')}\n${review?'包含待复核点、混合峰或较大角度跳变，请核对种子；':''}不是模型预测，也不是已确认的同目标轨迹或空间路线。`;
  }
  function chatMessage(role,text){
    const article=document.createElement('article');article.className='prediction-message '+role;
    const title=document.createElement('strong');title.textContent=role==='user'?'你':'小怪 · 轨迹 Agent';
    const content=document.createElement('p');content.textContent=text;article.append(title,content);$('chatMessages').appendChild(article);return article;
  }
  function platformFigure(round,previousRound,motionOverride){
    const motion=round.input.platform_state||motionOverride||state?.platform_motion;
    const points=motion?.points?.filter(point=>point.t<=round.input.visible_until);if(!points?.length)return null;
    const current=points.at(-1),previous=points.filter(point=>point.t<=(previousRound?.input.visible_until??0)).at(-1)||points[0];
    const figure=document.createElement('figure');figure.className='platform-motion-figure';
    const caption=document.createElement('figcaption');caption.textContent=`平台 ${motion.platform_id} · 位置与变化（截至 ${current.t}秒）`;figure.appendChild(caption);
    const fields=document.createElement('dl');fields.className='platform-motion-fields';
    const format=value=>(Math.abs(value)<.05?0:value).toFixed(1);
    for(const [name,value] of [['纬度',current.latitude.toFixed(6)+'°'],['经度',current.longitude.toFixed(6)+'°'],
      ['东／北位移',format(current.east_m)+' / '+format(current.north_m)+' 米（相对初始）'],
      ['平台航向',Number(motion.heading_deg).toFixed(1)+'°（配置）'],['平台航速',Number(motion.speed_kn).toFixed(2)+' 节（配置）'],
      ['本轮位移',format(Math.hypot(current.east_m-previous.east_m,current.north_m-previous.north_m))+' 米'],
      ['累计位移',format(current.displacement_m)+' 米']]){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=value;fields.append(dt,dd);}
    figure.appendChild(fields);
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 520 260');svg.setAttribute('role','img');svg.setAttribute('aria-label',`平台${motion.platform_id}本轮位置与配置运动路径（不是目标空间轨迹）`);
    const cloud=[...points,current.sensors.hull,current.sensors.tow],east=cloud.map(p=>p.east_m),north=cloud.map(p=>p.north_m);
    const midE=(Math.min(...east)+Math.max(...east))/2,midN=(Math.min(...north)+Math.max(...north))/2;
    const scale=.8*Math.min(420/Math.max(40,Math.max(...east)-Math.min(...east)),175/Math.max(40,Math.max(...north)-Math.min(...north)));
    const x=p=>260+(p.east_m-midE)*scale,y=p=>125-(p.north_m-midN)*scale;
    const node=(tag,attrs,text)=>svgNode(svg,tag,attrs,text);
    node('line',{x1:35,y1:225,x2:485,y2:225,stroke:'#555'});node('line',{x1:35,y1:225,x2:35,y2:22,stroke:'#555'});
    node('text',{x:45,y:25,fill:'#aaa','font-size':11},'北 ↑');node('text',{x:390,y:247,fill:'#aaa','font-size':11},'东 →（相对初始位置）');
    if(motion.speed_kn!==0)node('polyline',{points:points.map(p=>`${x(p)},${y(p)}`).join(' '),fill:'none',stroke:'#85acff','stroke-width':2,'stroke-dasharray':'5 4','data-platform-path':'true'});
    node('circle',{cx:x(points[0]),cy:y(points[0]),r:5,fill:'none',stroke:'#aaa'});
    const cx=x(current),cy=y(current);
    node('polygon',{points:`${cx},${cy-10} ${cx-6},${cy+7} ${cx+6},${cy+7}`,fill:'#85acff',transform:`rotate(${motion.heading_deg} ${cx} ${cy})`});
    node('text',{x:cx+12,y:cy-12,fill:'#ccc','font-size':11},`平台 ${motion.platform_id}${current.displacement_m<.05?'（位置未变）':''}`);
    node('line',{x1:x(current.sensors.hull),y1:y(current.sensors.hull),x2:x(current.sensors.tow),y2:y(current.sensors.tow),stroke:'#9e8a63','stroke-dasharray':'3 4'});
    for(const [array,color] of [['hull','#65c7ad'],['tow','#dfb36b']]){const sensor=current.sensors[array];node('circle',{cx:x(sensor),cy:y(sensor),r:3.5,fill:color});node('text',{x:x(sensor)+10,y:y(sensor)+15,fill:color,'font-size':10},array==='hull'?'船体阵':'拖曳阵');}
    figure.appendChild(svg);
    const note=document.createElement('p');note.className='note';note.textContent='来源：保存的平台配置，按恒航向／恒航速推算；虚线为平台路径，非实时GPS，也不是目标空间轨迹。'+(!round.input.platform_state&&state?.platform_config_backfilled?' 旧轮次为补充展示，不表示先前模型已使用这些数据。':'');figure.appendChild(note);
    const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='查看各时间点的平台坐标';details.appendChild(summary);
    const table=document.createElement('table');table.className='track-comparison';const head=document.createElement('tr');
    for(const name of ['时间（秒）','纬度','经度','东向（米）','北向（米）']){const th=document.createElement('th');th.textContent=name;head.appendChild(th);}table.appendChild(head);
    for(const point of points){const row=document.createElement('tr');for(const value of [point.t,point.latitude.toFixed(6),point.longitude.toFixed(6),format(point.east_m),format(point.north_m)]){const cell=document.createElement('td');cell.textContent=String(value);row.appendChild(cell);}table.appendChild(row);}
    const scroll=document.createElement('div');scroll.className='track-comparison-scroll';scroll.appendChild(table);details.appendChild(scroll);figure.appendChild(details);return figure;
  }
  function spatialFigure(round,platformDetails,sceneItems){
    const plan=round.spatial_plan;if(!plan)return null;
    const figure=document.createElement('figure');figure.className='spatial-plan-figure';
    const caption=document.createElement('figcaption');caption.textContent=sceneItems?'综合轨迹图 · 平台与目标（持续更新）':`第${round.input.step_id}轮 · 平台与目标轨迹预测`;figure.appendChild(caption);
    const stages=document.createElement('p');stages.className='fusion-stages';stages.textContent='① 平台状态分析 → ② 目标方位预测 → ③ 双阵融合与空间规划';figure.appendChild(stages);
    const spatialPane=document.createElement('div');spatialPane.dataset.scenePane='space';
    const status=document.createElement('p');status.className='spatial-plan-status';status.textContent=plan.reason;spatialPane.appendChild(status);
    if(plan.computed_on_read){const note=document.createElement('p');note.className='note';note.textContent='此空间规划为使用已保存点位进行的本次本地补算，未再次调用模型，不代表旧模型曾输出空间坐标。';figure.appendChild(note);}
    const items=sceneItems||[{id:'current',label:'当前目标',round,motion:round.input.platform_state||state?.platform_motion,color:'#85acff'}];
    const motion=round.input.platform_state||items[0]?.motion||state?.platform_motion;
    const navigation=new Map();for(const item of items)for(const point of item.motion?.points||[])if(point.t<=item.round.input.visible_until)navigation.set(point.t,point);
    const platformPoints=[...navigation.values()].sort((a,b)=>a.t-b.t);
    if(platformPoints.length){
      const current=platformPoints.at(-1),branches=items.flatMap(item=>(item.round.spatial_plan.branches||[]).map(branch=>({...branch,targetId:item.id,color:item.color,cutoff:item.round.input.visible_until,pending:item.round.review_status==='pending'}))),
        rays=items.flatMap(item=>item.round.spatial_plan.branches?.length?[]:(item.round.spatial_plan.direction_rays||[]).slice(-2).map(ray=>({...ray,targetId:item.id,color:item.color})));
      const cloud=[...platformPoints.map(p=>({x:p.east_m,y:p.north_m})),...Object.values(current.sensors).map(p=>({x:p.east_m,y:p.north_m}))];
      for(const branch of branches)for(const segment of [...branch.observed_segments,...branch.prediction_segments])cloud.push(...segment);
      const displayRadius=Math.max(300,Math.hypot(current.sensors.hull.east_m-current.sensors.tow.east_m,current.sensors.hull.north_m-current.sensors.tow.north_m)*3);
      const rayEnds=rays.map(ray=>{const angle=ray.absolute_bearing_deg*Math.PI/180;return {x:ray.east_m+displayRadius*Math.sin(angle),y:ray.north_m+displayRadius*Math.cos(angle)};});cloud.push(...rayEnds);
      const xs=cloud.map(p=>p.x),ys=cloud.map(p=>p.y),midX=(Math.min(...xs)+Math.max(...xs))/2,midY=(Math.min(...ys)+Math.max(...ys))/2;
      const scale=.85*Math.min(900/Math.max(100,Math.max(...xs)-Math.min(...xs)),400/Math.max(100,Math.max(...ys)-Math.min(...ys)));
      const x=p=>500+(p.x-midX)*scale,y=p=>250-(p.y-midY)*scale;
      const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 1000 500');svg.setAttribute('role','img');svg.setAttribute('aria-label','平台及目标空间候选与未来预测融合图');
      const node=(tag,attrs,text)=>svgNode(svg,tag,attrs,text);
      for(let i=0;i<=5;i++){const sx=50+i*180,sy=450-i*80;node('line',{x1:sx,y1:45,x2:sx,y2:450,stroke:'#363636'});node('line',{x1:50,y1:sy,x2:950,y2:sy,stroke:'#363636'});node('text',{x:sx-15,y:475,fill:'#aaa','font-size':11},((sx-500)/scale+midX).toFixed(0));node('text',{x:5,y:sy+4,fill:'#aaa','font-size':11},((250-sy)/scale+midY).toFixed(0));}
      node('text',{x:55,y:25,fill:'#bbb','font-size':12},'北向（米） · 相对平台初始位置');node('text',{x:880,y:497,fill:'#bbb','font-size':12},'东向（米）');
      const line=(points,color,dashed=false,kind='',targetId='')=>{if(points.length<2)return;node('polyline',{points:points.map(p=>`${x(p)},${y(p)}`).join(' '),fill:'none',stroke:color,'stroke-width':2,...(dashed?{'stroke-dasharray':'7 5'}:{}),...(kind?{'data-spatial-kind':kind}:{}),...(targetId?{'data-scene-target':targetId}:{})});};
      if(motion.speed_kn!==0)line(platformPoints.map(p=>({x:p.east_m,y:p.north_m})),'#aaa',true,'platform');
      for(const point of (motion.speed_kn===0?[platformPoints.at(-1)]:platformPoints)){const marker=node('circle',{cx:x({x:point.east_m,y:point.north_m}),cy:y({x:point.east_m,y:point.north_m}),r:2.5,fill:'#ccc','data-platform-path-time':point.t});const title=document.createElementNS('http://www.w3.org/2000/svg','title');title.textContent=`平台${motion.platform_id} · ${point.t}秒 · 纬度${point.latitude.toFixed(6)}° / 经度${point.longitude.toFixed(6)}°（配置推算）`;marker.appendChild(title);}
      const palette=['#85acff','#d9b36d','#c692e6','#6fcab4'];
      for(const [index,branch] of branches.entries()){
        const color=branch.color||palette[index%palette.length];
        for(const segment of branch.observed_segments){line(segment,color,branch.pending,'observed',branch.targetId);for(const point of segment){const marker=node('circle',{cx:x(point),cy:y(point),r:2.5,fill:color,'data-scene-target':branch.targetId,'data-observation-time':point.t});const title=document.createElementNS('http://www.w3.org/2000/svg','title');title.textContent=`${branch.targetId} · ${point.t}秒 · 东${point.x.toFixed(1)}米 / 北${point.y.toFixed(1)}米（空间候选）`;marker.appendChild(title);}}
        const last=branch.observed_segments.at(-1)?.at(-1);
        for(const segment of branch.prediction_segments){line(last?.t===branch.cutoff?[last,...segment]:segment,color,true,'prediction',branch.targetId);for(const point of segment)node('circle',{cx:x(point),cy:y(point),r:3,fill:'#202020',stroke:color});}
        if(last){node('circle',{cx:x(last),cy:y(last),r:4,fill:color});if(sceneItems)node('text',{x:x(last)+8,y:y(last)-8,fill:color,'font-size':11},branch.targetId);}
      }
      for(const [index,ray] of rays.entries())line([{x:ray.east_m,y:ray.north_m},rayEnds[index]],ray.color||'#a4a4c7',true,'direction_only',ray.targetId);
      const center={x:current.east_m,y:current.north_m},cx=x(center),cy=y(center);
      node('circle',{cx,cy,r:12,fill:'#212121',stroke:'#fff','stroke-width':2,'data-platform-marker':'true'});
      node('polygon',{points:`${cx},${cy-9} ${cx-6},${cy+7} ${cx+6},${cy+7}`,fill:'#fff',transform:`rotate(${motion.heading_deg} ${cx} ${cy})`});node('text',{x:cx+17,y:cy-15,fill:'#fff','font-size':14},`平台 ${motion.platform_id} · ${current.t}秒`);
      for(const [array,color] of [['hull','#65c7ad'],['tow','#dfb36b']]){const p=current.sensors[array];node('circle',{cx:x({x:p.east_m,y:p.north_m}),cy:y({x:p.east_m,y:p.north_m}),r:3,fill:color});}
      if(sceneItems){const bearingPane=document.createElement('div');bearingPane.dataset.scenePane='bearing';const bearingSvg=document.createElementNS('http://www.w3.org/2000/svg','svg');bearingSvg.setAttribute('role','img');bearingSvg.setAttribute('aria-label','所有目标方位轨迹及同步平台点位');
        chart({...items[0].trackState,scene_targets:items,platform_motion:motion,platform_navigation:platformPoints,
          visible_until:Math.max(...items.map(item=>item.round.input.visible_until))},bearingSvg);
        bearingPane.appendChild(bearingSvg);const basisNote=document.createElement('p');basisNote.className='note';basisNote.textContent='各颜色实线/点为不同目标的已接入方位，虚线为各自下一段预测；下方点位带为平台位置，不混入角度轴。';bearingPane.appendChild(basisNote);figure.appendChild(bearingPane);
      }
      spatialPane.appendChild(svg);
      const position=document.createElement('p');position.className='scene-platform-position';position.textContent=`◉ 平台 ${motion.platform_id}：纬度 ${current.latitude.toFixed(6)}°，经度 ${current.longitude.toFixed(6)}°；东/北位移 ${current.east_m.toFixed(1)} / ${current.north_m.toFixed(1)} 米`;figure.appendChild(position);
      const legend=document.createElement('p');legend.className='note';legend.textContent=branches.length?`实线/点：已采用历史空间候选；待确认轮为虚线；彩色虚线：混合空间预测；灰线：平台配置路径。${sceneItems?'各目标内分支为各自的镜像/几何解释，分支数不等于目标数。':`${branches.length}个分支表示同一目标假设的不同镜像/几何解释，不表示${branches.length}个已确认目标。`}缺测或交会失败处断线。`:'彩色虚线仅为未来方位方向，射线长度只是显示范围，不代表目标距离。尚无可靠空间预测线路。';spatialPane.appendChild(legend);
    }
    figure.appendChild(spatialPane);
    if(sceneItems){
      const tabs=document.createElement('div');tabs.className='scene-view-tabs';
      const applyView=()=>{for(const pane of figure.querySelectorAll('[data-scene-pane]'))pane.hidden=pane.dataset.scenePane!==sceneView;for(const button of tabs.children)button.setAttribute('aria-pressed',String(button.dataset.sceneView===sceneView));};
      for(const [value,label] of [['bearing','方位轨迹 · 含平台点位'],['space','空间融合']]){const button=document.createElement('button');button.type='button';button.dataset.sceneView=value;button.textContent=label;button.addEventListener('click',()=>{sceneView=value;applyView();savePrediction();});tabs.appendChild(button);}figure.insertBefore(tabs,stages.nextSibling);applyView();
      const legend=document.createElement('div');legend.className='scene-target-legend';
      for(const item of items){const chip=document.createElement('span');chip.dataset.sceneLegend=item.id;chip.style.color=item.color;chip.textContent=`● ${item.label} / ${item.round.input.array} · 截至${item.round.input.visible_until}秒${item.round.spatial_plan.branches?.length?'':'（仅方位）'}`;legend.appendChild(chip);}figure.appendChild(legend);
      const note=document.createElement('p');note.className='note';note.textContent='同来源、同平台坐标基准的已保存结果在本图叠加；平台标记为图中已知最新时刻，各目标进度见图例。跨会话叠加仅供显示，不会发送给当前模型。';figure.appendChild(note);
    }
    const method=document.createElement('p');method.className='note';method.textContent='空间预测依据：模型主阵方位＋另一阵近期数值趋势＋平台与阵列几何。不是模型直接猜测空间坐标，也不保证唯一解。';spatialPane.appendChild(method);
    if(platformDetails){platformDetails.querySelector('svg')?.remove();const details=document.createElement('details');details.className='platform-analysis-details';const title=document.createElement('summary');title.textContent='平台分析节点 · 坐标、航向航速及位移';details.append(title,platformDetails);figure.appendChild(details);}
    return figure;
  }
  async function refreshSharedScene(){
    const token=++sceneRefreshToken,current=state;
    const source=dataset?.source_result_id||sourceDataset?.source_result_id;
    const sourceKey=source||sourceDataset?.batch_id||current?.dataset_version;
    if(!sourceKey){sharedScene.replaceChildren();sharedScene.remove();return;}
    const candidates=[];
    if(current?.rounds.length)candidates.push({id:conversationId,name:conversationName,selectedTargetId,state:current,dataset});
    if(cacheAvailable())for(const entry of conversationIndex){
      if(entry.id===conversationId)continue;
      let saved;try{saved=await WorkspaceCache.get('prediction-conversation-'+entry.id);}catch{continue;}if(token!==sceneRefreshToken)return;
      if(!saved?.state?.rounds?.length)continue;
      const same=source?saved.dataset?.source_result_id===source:(saved.sourceDataset?.batch_id||saved.state.dataset_version)===sourceKey;
      if(same)candidates.push({id:entry.id,name:entry.name,selectedTargetId:saved.selectedTargetId,state:saved.state,dataset:saved.dataset});
    }
    const targets=new Map(),items=[];
    for(const candidate of candidates){
      const id=candidate.selectedTargetId||candidate.state.dataset_source?.selected_target_id||candidate.state.session_id;
      if(targets.has(id))continue;
      let trackState=candidate.state;
      if(!trackState.rounds.at(-1)?.spatial_plan||!trackState.platform_motion){
        const key=trackState.session_id+':'+trackState.version;
        try{if(!sceneStateCache.has(key))sceneStateCache.set(key,await request('/api/incremental/sessions/'+encodeURIComponent(trackState.session_id)));trackState=sceneStateCache.get(key);}catch{continue;}
      }
      if(token!==sceneRefreshToken)return;
      const round=trackState.rounds.at(-1),motion=round?.input.platform_state||trackState.platform_motion;if(!round?.spatial_plan||!motion)continue;
      const origin=motion.points[0]?.sensors;
      const frame=JSON.stringify([motion.platform_id,motion.initial_latitude,motion.initial_longitude,motion.heading_deg,motion.speed_kn,origin]);
      if(items.length&&items[0].frame!==frame)continue;
      let colorHash=2166136261;for(const char of id)colorHash=Math.imul(colorHash^char.charCodeAt(0),16777619)>>>0;
      targets.set(id,true);items.push({id,label:candidate.selectedTargetId||candidate.name||id,round,motion,frame,trackState,color:`hsl(${colorHash%360} 72% 72%)`});
    }
    if(token!==sceneRefreshToken)return;
    if(!items.length){
      sharedScene.replaceChildren();
      if(current?.rounds.length){const fallback=document.createElement('figure');fallback.className='shared-bearing-fallback';const title=document.createElement('figcaption');title.textContent='综合轨迹图 · 当前方位预测（缺少平台坐标，暂不能生成空间图）';
        const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('role','img');svg.setAttribute('aria-label','当前目标方位预测');chart(current,svg);fallback.append(title,svg);sharedScene.appendChild(fallback);placeSharedScene();
      }else sharedScene.remove();return;
    }
    const base=items[0],figure=spatialFigure(base.round,platformFigure(base.round,base.trackState.rounds.at(-2),base.motion),items);
    sharedScene.replaceChildren(figure);
    if(source&&/^[A-Za-z0-9_-]+$/.test(source)){
      const compare=document.createElement('details'),summary=document.createElement('summary');summary.textContent='对照原工作台保存图（同一来源，不代表真值）';
      const image=document.createElement('img');compare.addEventListener('toggle',()=>{if(compare.open&&!image.src)image.src=`/api/results/${encodeURIComponent(source)}/backend_candidate.png`;});image.alt='原工作台后端空间候选图';image.loading='lazy';image.style.width='100%';
      image.addEventListener('error',()=>{image.remove();const note=document.createElement('p');note.textContent='此来源没有保存可用的后端候选图，请在原工作台对照。';compare.appendChild(note);},{once:true});
      compare.append(summary,image);sharedScene.appendChild(compare);
    }
    placeSharedScene();
    requestAnimationFrame(()=>{if(token===sceneRefreshToken)$('chatMessages').scrollTop=$('chatMessages').scrollHeight;});
  }
  function drawChat(){
    $('chatMessages').replaceChildren();
    const rounds=state?.rounds||[];
    const acceptedIds=new Set(rounds.flatMap(round=>round.model_answer.accepted_candidate_ids));
    const history=(state?.history||[]).filter(point=>!acceptedIds.has(point.candidate_id));
    const completedRounds=[];
    for(const round of rounds){
      const message=chatMessage('user',(round.user_question||'根据本轮点位预测下一段轨迹。')+`\n[第${round.input.step_id}轮点位附件：历史${round.input.history.length}点，新增${round.input.new_candidates.length}点，开放至${round.input.visible_until}秒]`);
      const attachment=document.createElement('details');attachment.className='message-evidence';const attachmentTitle=document.createElement('summary');attachmentTitle.textContent='查看本轮点位附件';
      const points=document.createElement('div');points.className='track-comparison-scroll';points.appendChild(round.input.selected_time_groups?groupedTable(round.input.selected_time_groups,false):pointTable(round.input.step_id===1?round.input.history:round.input.new_candidates));attachment.append(attachmentTitle,points);message.appendChild(attachment);
      const result=round.model_answer;
      if(round.offline_all_points){
        const audit=document.createElement('p');audit.className='note';
        audit.textContent=`全量输入${round.input.selected_time_groups.length}个时间组；主阵多峰跳过${round.excluded_ambiguous_times.length}组，主阵缺测${(round.missing_primary_times||[]).length}组。所有原始点仍保存在本轮附件，多峰/缺测不强行连线。`;
        message.appendChild(audit);
      }
      const reply=chatMessage('assistant',`第${round.input.step_id}轮 · ${{extend:'接续',missed:'缺测',ambiguous:'歧义待定',end:'结束'}[result.decision]}\n${result.reason}\n接入点：${result.accepted_candidate_ids.join('、')||'无'}\n下一段预测：${result.prediction.map(p=>`${p.t}秒 / ${p.bearing_deg.toFixed(3)}°`).join('；')||'无'}\n以上是方位轨迹假设，不是已确认空间路线。`);
      const residuals=round.input.previous_prediction_evaluation.filter(item=>result.accepted_candidate_ids.includes(item.candidate_id));
      if(residuals.length){const detail=document.createElement('p');detail.className='note';detail.textContent='上一轮预测与本轮接入点偏差：'+residuals.map(item=>`${item.candidate_id}：${item.absolute_error_deg.toFixed(3)}°`).join('；')+'（接续仍为候选假设）';reply.appendChild(detail);}
      const ids=new Set(result.accepted_candidate_ids);
      history.push(...state.history.filter(point=>ids.has(point.candidate_id)));
      completedRounds.push(round);
      const figure=document.createElement('figure');figure.className='chat-prediction-figure';
      const caption=document.createElement('figcaption');caption.textContent=`第${round.input.step_id}轮 · 方位轨迹与下一段预测`;
      const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
      svg.setAttribute('viewBox','0 0 1000 420');svg.setAttribute('role','img');svg.setAttribute('aria-label',caption.textContent);
      chart({history,rounds:completedRounds,visible_candidates:state.visible_candidates.filter(point=>point.t<=round.input.visible_until),
        visible_until:round.input.visible_until,horizon:state.horizon,prediction:result.prediction,platform_motion:round.input.platform_state||state.platform_motion},svg);
      const legend=document.createElement('p');legend.className='note';legend.textContent='灰点：已开放候选；绿色线：接入点轨迹估计（待采用为虚线）；蓝色虚线：本轮模型预测；橙色虚线：后端参考。缺测处断线。';
      figure.append(caption,svg,legend);
      const evidence=document.createElement('details');evidence.className='bearing-evidence';const summary=document.createElement('summary');summary.textContent='查看本轮方位—时间依据';evidence.append(summary,figure);reply.appendChild(evidence);
      if(round.spatial_plan){const snapshot=document.createElement('details'),title=document.createElement('summary'),previous=completedRounds.at(-2);snapshot.className='round-spatial-snapshot';title.textContent='回看本轮空间规划快照';snapshot.appendChild(title);
        snapshot.addEventListener('toggle',()=>{if(snapshot.open&&!snapshot.querySelector('figure')){const frame=spatialFigure(round,platformFigure(round,previous));if(frame)snapshot.appendChild(frame);}});reply.appendChild(snapshot);}
    }
    refreshSharedScene();
  }
  async function prepareChat(resetSelection=false){
    if(!dataset)return;
    if(resetSelection){groupSelectionKey='';selectedGroupKeys.clear();}
    if(singlePredictionDone){
      const last=state?.rounds?.at(-1);
      if(last){preparedPointData=last.input;$('chatQuestion').value=last.user_question||'请根据本轮点位判断接续关系，解释依据并预测下一段轨迹。';renderPointViews();}
      $('chatState').textContent='单次预测已完成';$('chatNotice').textContent='本次预测已停止。点击“准备下一轮（免费）”开放新点，检查后再运行一次预测。';return;
    }
    if(!state){
      const points=dataset.candidates||dataset.images.flatMap(im=>im.candidates.map(p=>({...p,array:im.array,platform_id:im.platform_id})));
      preloadInitialChat(points);return;
    }
    if(!active.has(state.status)||state.calls>=state.max_calls){chatPreview=null;preparedPointData=null;$('chatState').textContent='本任务已停止／完成';$('chatNotice').textContent=`任务状态：${state.status}；不自动续跑或重试。请查看技术调试记录。`;return;}
    chatPreview=await request(`/api/incremental/sessions/${state.session_id}/preview`);
    preparedPointData=chatPreview.context;
    $('chatInput').value=JSON.stringify({question:preloadQuestion(),point_data:preparedPointData},null,2);
    $('chatInputHint').textContent=`本批共${allCandidates().length}个候选点均可浏览。第${preparedPointData.step_id}轮证据只开放至${preparedPointData.visible_until}秒；右侧勾选决定本轮实际发送点位。`;
    syncPointInput();
    $('chatState').textContent='本轮点位已预载 · 等待发送';savePrediction();
  }
  $('datasetFile').addEventListener('change',event=>run(async()=>{
    try{
      const file=event.target.files[0];if(!file)return;
      if(state?.rounds.length){await savePrediction();conversationId=newConversationId();conversationName=`导入会话${conversationIndex.length+1}`;}
      if(file.size>5*1024*1024)throw new Error('候选文件最多5MiB');
      dataset=JSON.parse(await file.text());sourceDataset=dataset;selectedTargetId=dataset.selected_target_id||'';useManualDataset();state=null;singlePredictionDone=false;drawExecution();chatBlocked=false;$('chatConsent').checked=false;$('sessionId').value='';$('sourceBatchDownload').hidden=true;inventory();receipt('idle','已手工导入点位文件','这不是从原页面自动接收的批次；请核对来源。');controls();
      savePrediction();
    }catch(error){dataset=null;clearPointViews();$('inventory').textContent='未载入有效点位';$('batchSource').textContent=error.message;$('sourceBatchDownload').hidden=true;status(error.message);controls();savePrediction();}
  }));
  $('example').addEventListener('click',()=>run(async()=>{
    if(state?.rounds.length){await savePrediction();conversationId=newConversationId();conversationName=`示例会话${conversationIndex.length+1}`;}
    useManualDataset();
    state=null;singlePredictionDone=false;drawExecution();chatBlocked=false;$('chatConsent').checked=false;$('sessionId').value='';
    dataset={candidates:Array.from({length:16},(_,i)=>({candidate_id:`P${i}`,t:i*10,bearing_deg:30+i*1.5,array:'hull',platform_id:'A'}))};
    sourceDataset=dataset;selectedTargetId='';
    $('seeds').value='P0,P2,P4';$('sourceBatchDownload').hidden=true;inventory();receipt('idle','当前是本地点位示例，不是已传递的高斯数据','P0、P2等编号为示例。要接收原页面的数据，请填写来源结果编号并载入批次。');controls();savePrediction();
  }));
  function svgNode(target,tag,attrs={},text){
    const element=document.createElementNS('http://www.w3.org/2000/svg',tag);
    for(const [key,value] of Object.entries(attrs))element.setAttribute(key,String(value));
    if(text!==undefined)element.textContent=text;
    target.appendChild(element);return element;
  }
  function chart(trackState=state,target=$('predictionChart')){
    target.replaceChildren();
    const node=(tag,attrs,text)=>svgNode(target,tag,attrs,text);
    const candidates=trackState.visible_candidates;
    const series=trackState.scene_targets;
    const start=Math.min(...(series?series.flatMap(item=>item.trackState.history.map(p=>p.t)):candidates.map(p=>p.t)));
    const end=Math.max(start+1,...(series?series.map(item=>item.round.input.prediction_end):[trackState.visible_until+trackState.horizon]));
    const x=t=>65+(t-start)/(end-start)*900,y=b=>365-b/180*320;
    const motion=trackState.platform_motion,platformPoints=(trackState.platform_navigation||motion?.points)?.filter(p=>p.t>=start&&p.t<=trackState.visible_until)||[];
    target.setAttribute('viewBox',platformPoints.length?'0 0 1000 550':'0 0 1000 420');
    node('rect',{x:0,y:0,width:1000,height:platformPoints.length?550:420,fill:'#fff'});
    for(let b=0;b<=180;b+=30){node('line',{x1:65,y1:y(b),x2:965,y2:y(b),stroke:'#ddd'});node('text',{x:12,y:y(b)+5,fill:'#333'},`${b}°`);}
    for(let i=0;i<=5;i++){const t=start+(end-start)*i/5;node('text',{x:x(t)-12,y:395,fill:'#333'},`${t.toFixed(1)}s`);}
    node('text',{x:65,y:22,fill:'#333'},series?'各目标相对船首折叠方位 · 同一张图持续延续':`相对船首折叠方位 · ${trackState.history[0].platform_id}/${trackState.history[0].array}`);
    const line=(points,color,dashed=false,targetId='')=>{if(points.length<2)return;
      const ordered=[...new Set(candidates.map(p=>p.t))].sort((a,b)=>a-b),gaps=ordered.slice(1).map((t,i)=>t-ordered[i]).sort((a,b)=>a-b);
      const cadence=gaps.length?gaps[Math.floor(gaps.length/2)]:Infinity;
      if(points.some((p,i)=>i&&p.t-points[i-1].t>cadence*1.8)){
        let chunk=[];for(const p of points){if(chunk.length&&p.t-chunk.at(-1).t>cadence*1.8){line(chunk,color,dashed,targetId);chunk=[];}chunk.push(p);}line(chunk,color,dashed,targetId);return;
      }
      node('polyline',{points:points.map(p=>`${x(p.t)},${y(p.bearing_deg)}`).join(' '),fill:'none',stroke:color,'stroke-width':2,...(dashed?{'stroke-dasharray':'7 5'}:{}),...(targetId?{'data-bearing-target':targetId}:{})});};
    if(series){
      for(const item of series){const history=item.trackState.history,rounds=item.trackState.rounds,acceptedIds=new Set(rounds.flatMap(r=>r.model_answer.accepted_candidate_ids)),seeds=history.filter(p=>!acceptedIds.has(p.candidate_id));line(seeds,item.color,rounds[0]?.review_status==='pending',item.id);let tail=seeds.at(-1);
        for(const round of rounds){const ids=new Set(round.model_answer.accepted_candidate_ids),accepted=history.filter(p=>ids.has(p.candidate_id));if(accepted.length){line(tail?[tail,...accepted]:accepted,item.color,round.review_status==='pending',item.id);tail=accepted.at(-1);}else if(round.input.new_candidates.length)tail=null;}
        for(const point of history)node('circle',{cx:x(point.t),cy:y(point.bearing_deg),r:4,fill:item.color,'data-bearing-target':item.id,'data-observation-time':point.t});
        const last=history.at(-1),prediction=item.round.model_answer.prediction;line(last?.t===item.round.input.visible_until?[last,...prediction]:prediction,item.color,true,item.id);
        for(const point of prediction)node('circle',{cx:x(point.t),cy:y(point.bearing_deg),r:3,fill:'none',stroke:item.color});
      }
    }else{
    for(const p of candidates)node('circle',{cx:x(p.t),cy:y(p.bearing_deg),r:3,fill:'#aaa'});
    const initial=trackState.history.filter(p=>!trackState.rounds.some(r=>r.model_answer.accepted_candidate_ids.includes(p.candidate_id)));
    line(initial,'#087f75',trackState.rounds[0]?.review_status==='pending'||trackState.initial_preview);let tail=initial.at(-1);
    for(const round of trackState.rounds){
      const ids=new Set(round.model_answer.accepted_candidate_ids);
      const accepted=trackState.history.filter(p=>ids.has(p.candidate_id));
      if(accepted.length){line(tail?[tail,...accepted]:accepted,'#087f75',round.review_status==='pending');tail=accepted.at(-1);}
      else if(round.input.new_candidates.length)tail=null;
    }
    for(const p of trackState.history)node('circle',{cx:x(p.t),cy:y(p.bearing_deg),r:4,fill:'#087f75'});
    const lastRound=trackState.rounds.at(-1),last=trackState.history.at(-1);
    // Do not draw an observed-to-future bridge across a missing observation window.
    const anchor=last.t===trackState.visible_until?[last]:[];
    if(lastRound)line([...anchor,...lastRound.input.baseline_prediction],'#c47700',true);
    line([...anchor,...trackState.prediction],'#2166d1',true);
    }
    node('line',{x1:x(trackState.visible_until),y1:35,x2:x(trackState.visible_until),y2:365,stroke:'#777','stroke-dasharray':'3 4'});
    if(platformPoints.length){
      const latest=platformPoints.at(-1);node('line',{x1:65,y1:415,x2:965,y2:415,stroke:'#777'});
      node('text',{x:65,y:438,fill:'#ddd','font-size':13},motion.speed_kn===0?`平台 ${motion.platform_id} 静止 · 固定位置（下方不表示时间轨迹）`:`平台 ${motion.platform_id} 时间点位带（不是空间路径）`);
      if(motion.speed_kn!==0)node('line',{x1:x(platformPoints[0].t),y1:462,x2:x(latest.t),y2:462,stroke:'#aaa','stroke-width':2});
      for(const point of (motion.speed_kn===0?[platformPoints.at(-1)]:platformPoints)){const marker=node('circle',{cx:motion.speed_kn===0?65:x(point.t),cy:462,r:point.t===latest.t?7:4,fill:point.t===latest.t?'#fff':'#9bc1ff',stroke:'#50647f','data-platform-time':point.t,...(series&&point.t===latest.t?{'data-platform-marker':'true'}:{})});const title=document.createElementNS('http://www.w3.org/2000/svg','title');title.textContent=`平台${motion.platform_id} · ${point.t}秒 · 纬度${point.latitude.toFixed(6)}° / 经度${point.longitude.toFixed(6)}°`;marker.appendChild(title);}
      node('text',{x:65,y:500,fill:'#ddd','font-size':13},`当前：${latest.t}秒 · 纬度 ${latest.latitude.toFixed(6)}° · 经度 ${latest.longitude.toFixed(6)}°`);
      node('text',{x:65,y:527,fill:'#aaa','font-size':12},`平台东/北位移：${latest.east_m.toFixed(1)} / ${latest.north_m.toFixed(1)} 米（配置推算）`);
    }
  }
  function render(result){
    state=result;$('sessionId').value=state.session_id;
    try{localStorage.setItem('incremental-session',state.session_id);}catch{/* IndexedDB snapshot remains the primary cache. */}
    $('predictionSummary').textContent=`状态：${state.status} | 已提交版本：${state.version} | 调用：${state.calls}/${state.max_calls}\n已开放至：${state.visible_until}s | 已接入点：${state.history.length}\n预测仅为方位—时间假设，不是空间轨迹。`;
    $('roundLog').textContent=JSON.stringify(state.rounds,null,2);chart();drawChat();controls();savePrediction();
    if(result.execution)drawExecution(result.execution);else if(backendReady)readExecution(state.session_id).catch(()=>{});
  }
  async function run(action){
    if(busy)return;busy=true;controls();
    $('reviewFeedback').hidden=true;
    try{await action();}catch(error){status(error.message);$('chatNotice').textContent=error.message;
      if(state?.status==='awaiting_review'){$('reviewFeedback').textContent=error.message;$('reviewFeedback').hidden=false;}
    }finally{busy=false;controls();}
  }
  $('create').addEventListener('click',()=>run(async()=>{
    if(!dataset)throw new Error('请先导入候选数据');
    render(await request('/api/incremental/sessions',{dataset,seed_ids:$('seeds').value.split(/[,，\s]+/).filter(Boolean),stride:Number($('stride').value),horizon:Number($('horizon').value),max_calls:Number($('maxCalls').value)}));
    singlePredictionDone=false;chatBlocked=false;await prepareChat();
    status('本地任务已创建，尚未调用模型。请确认费用后执行初始化预测。');
  }));
  $('reload').addEventListener('click',()=>run(async()=>{
    render(await request(`/api/incremental/sessions/${encodeURIComponent($('sessionId').value.trim())}`));
    singlePredictionDone=!!state.rounds.at(-1)?.recovered_from_saved_reply&&$('singlePrediction').checked;chatBlocked=false;await prepareChat();
    status('已加载已提交状态，未调用模型；pending/未知结果不会自动重试。');
  }));
  $('step').addEventListener('click',()=>run(async()=>{
    if(!$('predictionConsent').checked)throw new Error('请先勾选点位发送及费用确认');
    status('本轮处理中，请勿重复提交；页面断开不保证模型停止或不计费。');
    try{
      render(await executeStep({version:state.version,consent:true}));
      await prepareChat();
      status('本轮已校验并保存。可检查预测后继续下一轮，或停止点击以暂停。');
    }catch(error){
      // A lost HTTP response must never enable a blind replay of a paid request.
      try{render(await request(`/api/incremental/sessions/${state.session_id}`));}catch{state=null;}
      throw error;
    }
  }));
  $('export').addEventListener('click',()=>{
    const url=URL.createObjectURL(new Blob([JSON.stringify(state,null,2)],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;link.download=`${state.session_id}-visible.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  $('chatPrepare').addEventListener('click',()=>run(async()=>{singlePredictionDone=false;await prepareChat(true);}));
  $('singlePrediction').addEventListener('change',()=>{drawExecution(lastExecution||undefined);controls();savePrediction();});
  $('chatConsent').addEventListener('change',controls);
  $('chatOptions').open=false;
  $('toggleComposer').addEventListener('click',()=>{
    const compact=document.querySelector('.chat-composer').classList.toggle('composer-collapsed');
    if(compact){$('chatOptions').open=false;$('chatAttachments').open=false;}
    $('toggleComposer').textContent=compact?'展开输入区 ⌃':'收起输入区 ⌄';
    $('toggleComposer').setAttribute('aria-expanded',String(!compact));
    $('toggleComposer').setAttribute('aria-label',compact?'展开输入区':'收起输入区');
  });
  $('seeds').addEventListener('input',()=>{
    if(!dataset||state)return;
    const points=dataset.candidates||dataset.images.flatMap(im=>im.candidates.map(p=>({...p,array:im.array,platform_id:im.platform_id})));
    const ids=$('seeds').value.split(/[,，\s]+/).filter(Boolean),lookup=new Map(points.map(p=>[p.candidate_id||p.id,p]));
    if(ids.length>=1&&ids.length<=5&&ids.every(id=>lookup.has(id)))preloadInitialChat(points);
    else{
      preparedPointData=null;chatPreview=null;$('chatInput').value='';$('predictionChart').replaceChildren();
      clearPointViews();
      $('predictionSummary').textContent='种子编号不完整或不存在，初始轨迹暂不显示。';
    }
    controls();savePrediction();
  });
  $('suggestSeeds').addEventListener('click',()=>run(async()=>{
    if(state)throw new Error('当前任务已建立，不修改已提交种子；请重新载入批次建立新假设。');
    if(!dataset)throw new Error('请先载入点位批次');
    $('seeds').value='';inventory();savePrediction();
  }));
  $('pointDetailSelect').addEventListener('change',showPointDetails);
  $('targetTrackSelect').addEventListener('change',()=>run(async()=>{
    const id=$('targetTrackSelect').value;if(!id||id===selectedTargetId){drawTargetMenu();return;}
    const full=sourceDataset||dataset,target=targetCatalog().find(item=>item.target_id===id);
    if(!target)throw new Error('候选目标不存在，未更改会话。');
    const ids=new Set(target.candidate_ids),points=full.candidates.filter(point=>ids.has(point.candidate_id));
    if(!points.length)throw new Error('目标没有可核对的测量点，未更改会话。');
    await savePrediction();
    sourceDataset=full;selectedTargetId=id;
    dataset={...full,candidates:points,target_catalog:[],selected_target_id:id,batch_id:target.dataset_id||full.batch_id,
      point_count:points.length,review_point_count:points.filter(point=>point.quality==='needs_review'||point.mixed).length};
    conversationId=newConversationId();conversationName=`目标 ${id} · 会话${conversationIndex.length+1}`;
    state=null;chatBlocked=false;singlePredictionDone=false;chatPreview=null;preparedPointData=null;selectionKey='';groupSelectionKey='';
    $('sessionId').value='';$('seeds').value='';$('stride').value='1';$('maxCalls').value='20';$('chatConsent').checked=false;$('predictionConsent').checked=false;$('questionTemplateMode').value='auto';
    useManualDataset();drawExecution();inventory();
    $('conversationNotice').textContent=`已为候选目标 ${id} 新建会话，旧记录保留；未调用模型。`;
    $('chatNotice').textContent='仅加载该目标对应的候选测量。请按时间勾选点位，再确认发送授权；离线目标关联并非真实身份确认。';
    drawConversations();savePrediction();
  }));
  $('openNewConversation').addEventListener('click',()=>{$('newConversationForm').open=true;$('conversationStart').focus();});
  $('createConversation').addEventListener('click',()=>run(async()=>{
    if(!dataset)throw new Error('请先载入候选数据');
    const all=allCandidates(),reference=state?.history[0]||preparedPointData?.history[0]||all.find(p=>p.array==='hull')||all[0];
    const pool=all.filter(p=>p.platform_id===reference.platform_id&&p.array===reference.array);
    const times=[...new Set(pool.map(p=>p.t))].sort((a,b)=>a-b),start=Number($('conversationStart').value);
    if(!Number.isInteger(start)||start<1||start>times.length)throw new Error(`起点必须为1～${times.length}的时间点序号`);
    const historyTimes=start===1?[times[0]]:times.slice(Math.max(0,start-4),start-1);
    const accepted=state?.history||preparedPointData?.history||[];const seeds=[];
    for(const t of historyTimes){const options=pool.filter(p=>p.t===t);const known=accepted.find(p=>p.t===t&&options.some(q=>q.candidate_id===p.candidate_id));
      seeds.push(known||options.sort((a,b)=>Math.abs(a.bearing_deg-(seeds.at(-1)||reference).bearing_deg)-Math.abs(b.bearing_deg-(seeds.at(-1)||reference).bearing_deg))[0]);}
    await savePrediction();
    const result=await request('/api/incremental/sessions',{dataset,seed_ids:seeds.map(p=>p.candidate_id),stride:1,horizon:Number($('horizon').value),max_calls:20});
    conversationId=newConversationId();conversationName=`会话${conversationIndex.length+1} · 从第${start}个时间点`;
    state=null;chatBlocked=false;singlePredictionDone=false;chatPreview=null;preparedPointData=null;selectionKey='';groupSelectionKey='';
    $('seeds').value=seeds.map(p=>p.candidate_id).join(',');$('stride').value='1';$('maxCalls').value='20';$('chatConsent').checked=false;$('predictionConsent').checked=false;$('questionTemplateMode').value='auto';
    useManualDataset();render(result);await prepareChat(true);
    selectedGroupKeys=new Set([...historyTimes,times[start-1]].map(t=>groupKey({platform_id:reference.platform_id,t})));preloadQuestion();
    $('newConversationForm').open=false;$('chatAttachments').open=true;
    $('conversationNotice').textContent=`已免费新建：第${start}个时间点为${times[start-1]}秒，带入${seeds.length}个历史候选点；旧会话已保留，未调用模型。`;
    $('chatNotice').textContent='新会话已就绪。检查历史候选与本轮点位，勾选发送授权后再运行预测。';drawConversations();savePrediction();
  }));
  $('loadDebugTrack').addEventListener('click',()=>run(async()=>{
    const source=dataset?.source_result_id||$('batchResultId').value.trim();
    if(!source)throw new Error('请先从原页面载入高斯批次，再载入单轨调试片段。');
    const batch=await request(`/api/incremental/batches/${encodeURIComponent(source)}/debug-track`);
    dataset=batch;sourceDataset=batch;selectedTargetId='';useManualDataset();state=null;singlePredictionDone=false;chatBlocked=false;preparedPointData=null;
    $('sessionId').value='';$('seeds').value=batch.candidates[0].candidate_id;$('stride').value='1';$('maxCalls').value='20';
    $('singlePrediction').checked=false;$('chatConsent').checked=false;$('questionTemplateMode').value='auto';
    drawExecution();inventory();controls();savePrediction();
    $('debugTrackNotice').hidden=false;$('debugTrackNotice').textContent=`单轨调试：${batch.debug_reference.ridge_id}，共${batch.point_count}点。从1点开始，每次发送新增1个观测；只做方位预测。片段由全时段离线关联选取，不是身份真值。`;
    $('chatNotice').textContent='已准备第1个点，未调用模型。确认后手动逐次发送：1点证据不足，2点暂定趋势，后续点验证并修正。旧任务文件仍保留。';
  }));
  for(const [id,view] of [['showChatMessages','messages'],['showTrajectoryReview','review']]){
    $(id).addEventListener('click',()=>{chatView=view;syncChatView();});
    $(id).addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      event.preventDefault();chatView=event.key==='Home'?'messages':event.key==='End'?'review':chatView==='review'?'messages':'review';
      syncChatView();$(chatView==='review'?'showTrajectoryReview':'showChatMessages').focus();
    });
  }
  for(const [id,adopt] of [['adoptTrajectory',true],['rejectTrajectory',false]]){
    $(id).addEventListener('click',()=>run(async()=>{
      const reason=$('reviewReason').value.trim();
      if(!adopt&&!reason)throw new Error('请填写打回原因');
      render(await request(`/api/incremental/sessions/${state.session_id}/review`,{version:state.version,adopt,reason}));
      singlePredictionDone=false;chatBlocked=false;chatPreview=null;$('reviewReason').value='';
      if(active.has(state.status))await prepareChat(true);
      $('chatState').textContent=adopt?'已采用 · 轨迹已定稿':'已打回 · 等待重新选点';
      $('chatNotice').textContent=adopt?'已采用本轮候选。未来预测仍为虚线，不代表真实路径。':'已恢复本轮前的点位、预测和时间边界；调用次数不退回。请重选点位或修改问题，明确发送后才重新运行。';
    }));
  }
  $('allPointsPreview').addEventListener('click',()=>run(async()=>{
    const ids=$('seeds').value.split(/[,，\s]+/).filter(Boolean);
    await savePrediction();
    const preview=await request('/api/incremental/all-points-preview',{dataset,seed_ids:ids,horizon:Number($('horizon').value)});
    conversationId=newConversationId();conversationName=`${selectedTargetId||'当前目标'} · 全部点位试绘`;
    render(preview);
    chatBlocked=false;singlePredictionDone=false;chatPreview=null;
    $('chatState').textContent='全部点位试绘 · 等待采用';
    $('chatNotice').textContent='使用当前目标全部时间组，模型调用0次。多峰不强行接续，空间交会失败或缺测处断线；不是完整工作台反演算法。请对照原工作台图后采用或打回。';
  }));
  $('questionTemplateMode').addEventListener('change',()=>{preloadQuestion();savePrediction();});
  $('chatInput').addEventListener('input',savePrediction);
  $('chatQuestion').addEventListener('input',()=>{
    $('questionTemplateMode').value='custom';syncPointInput();savePrediction();
  });
  $('chatSend').addEventListener('click',()=>run(async()=>{
    if(singlePredictionDone){
      singlePredictionDone=false;chatBlocked=false;$('questionTemplateMode').value='auto';await prepareChat(true);
      $('chatNotice').textContent='下一轮点位已准备（免费），尚未调用模型。请检查新点和问题，再点击“运行一次预测”。';return;
    }
    if(!selectedGroups().length){
      await prepareChat(true);$('chatNotice').textContent='已免费重新准备本轮点位，未调用模型。请检查附件、勾选发送授权，再运行预测。';return;
    }
    if(!$('chatConsent').checked){$('chatConsent').focus();throw new Error('请先勾选输入框下方的发送授权及API费用确认，再点击运行预测。');}
    const input=JSON.parse($('chatInput').value);
    const message=parseMessage();input.question=message.question;
    if(typeof input.question!=='string'||!input.question.trim()||input.question.length>2000)throw new Error('question需为1～2000字的问题');
    if(!preparedPointData||JSON.stringify(input.point_data)!==JSON.stringify(preparedPointData))throw new Error('预载点位不可直接改写；请点击“重新准备本轮点位”，只编辑question');
    if(!state){
      render(await request('/api/incremental/sessions',{dataset,seed_ids:preparedPointData.seed_candidate_ids,stride:Number($('stride').value),horizon:Number($('horizon').value),max_calls:Number($('maxCalls').value)}));
      await prepareChat();
    }
    chatPreview=await request(`/api/incremental/sessions/${state.session_id}/preview?times=${encodeURIComponent(JSON.stringify(message.selected_times))}`);
    validateGroupContext(chatPreview.context,message.groups);
    preparedPointData=chatPreview.context;$('chatQuestion').value=input.question;renderPointViews();
    $('chatNotice').textContent='Agent正在判断本轮点位并预测下一段……本轮至多一次模型调用。';$('chatState').textContent='Agent处理中';
    chatMessage('user',input.question);
    const live=chatMessage('assistant','正在连接模型流式输出……');live.classList.add('streaming');live.querySelector('strong').textContent='小怪 · 正在回复（校验前草稿）';const liveText=live.querySelector('p');let streamed='';
    try{
      render(await executeStep({version:state.version,consent:true,question:input.question,selected_times:message.selected_times,context_digest:chatPreview.context_digest},event=>{
        if(event.event==='delta'){streamed+=event.text||'';liveText.textContent=streamedReason(streamed)||'正在接收模型结构化结果，等待可展示的回复正文……';}
        else if(event.event==='phase'){if(!streamedReason(streamed))liveText.textContent=event.message;if(event.phase==='spatial_planning')$('chatState').textContent='平台与目标轨迹融合规划中';}
        $('chatMessages').scrollTop=$('chatMessages').scrollHeight;
      }));
    }catch(error){
      chatBlocked=true;
      try{render(await request(`/api/incremental/sessions/${state.session_id}`));}catch{state=null;}
      chatMessage('user',input.question);const draft=streamedReason(streamed);chatMessage('assistant',(draft?'流式草稿（未通过最终校验，不作为轨迹结果）：\n'+draft+'\n\n':'')+'本轮未完成或回复未通过校验，未自动重试。'+error.message);throw error;
    }
    if(state?.status==='awaiting_review'){
      chatPreview=null;$('chatState').textContent='interrupt · 等待采用或打回';
      $('chatNotice').textContent='虚线候选已保存，请在确认终端选择采用或打回；刷新不会自动采用或重跑。';return;
    }
    if($('singlePrediction').checked){
      singlePredictionDone=true;chatPreview=null;
      $('chatState').textContent='单次预测已完成';$('chatNotice').textContent='本次预测及校验已完成。点击“准备下一轮（免费）”开放新点，检查后再运行一次预测。';savePrediction();return;
    }
    $('questionTemplateMode').value='auto';await prepareChat();
    $('chatNotice').textContent=state&&active.has(state.status)&&state.calls<state.max_calls?
      '本轮Agent回复和预测已保存；下一轮点位已准备。可继续提问并发送，或停止发送以暂停。':'本轮回复已保存；任务已完成、结束或达到调用上限，不再自动推进。';
  }));
  try{$('sessionId').value=localStorage.getItem('incremental-session')||'';}catch{/* Storage may be disabled. */}
  controls();
  drawExecution();
  const sourceResult=new URLSearchParams(window.location.search).get('source_result');
  const emptyWorkspace=new URLSearchParams(window.location.search).get('empty')==='1';
  let initializationSucceeded=false;
  run(async()=>{
    await verifyBackend();
    let saved,mainSaved;
    if(cacheAvailable()){
      try{[saved,mainSaved,conversationIndex]=await Promise.all([WorkspaceCache.get('prediction-workspace'),WorkspaceCache.get('main-state'),WorkspaceCache.get('prediction-conversations')]);conversationIndex=Array.isArray(conversationIndex)?conversationIndex:[];}
      catch{$('predictionCacheStatus').textContent='浏览器本地存储不可用，刷新可能丢失未创建任务的数据。';}
    }
    const mainData=mainSaved?.transfer?.data||mainSaved?.result;
    const mainHasGaussian=mainData?.downloads?.['inverse_fields.json']||mainData?.downloads?.['measurement_report.json']||mainSaved?.transfer?.allowSavedAttempt;
    const selectedSource=sourceResult || (!emptyWorkspace && validResult(saved?.dataset?.source_result_id)?saved.dataset.source_result_id:
      !emptyWorkspace&&mainHasGaussian&&validResult(mainData?.result_id)?mainData.result_id:null);
    let loadedBatch;
    if(selectedSource){
      $('batchResultId').value=selectedSource;
      loadedBatch=await loadBatch(selectedSource);
      $('predictionCacheStatus').textContent='已从本地服务核对实际高斯批次，未使用示例数据替代，未调用模型。';
    }
    const matching=saved?.version===1 && (selectedSource?
      saved.dataset?.source_result_id===selectedSource && saved.dataset?.batch_id===loadedBatch?.batch_id:!sourceResult);
    if(matching){
      sceneView=saved.sceneView==='space'?'space':'bearing';
      sourceDataset=saved.sourceDataset||loadedBatch||saved.dataset;selectedTargetId=saved.selectedTargetId||saved.dataset?.selected_target_id||'';
      conversationId=saved.conversationId||conversationId;conversationName=saved.conversationName||'历史会话';
      if(typeof saved.singlePrediction==='boolean')$('singlePrediction').checked=saved.singlePrediction;
      singlePredictionDone=!!saved.singlePredictionDone;
      dataset=loadedBatch||saved.dataset;
      if(dataset)inventory();
      if(!selectedSource && dataset)receipt('idle','已恢复手工／示例点位，不是原页面传递成功','如需原页面高斯数据，请填写来源结果编号后载入。');
      for(const id of ['seeds','stride','horizon','maxCalls','sessionId'])if(saved[id]!==undefined)$(id).value=saved[id];
      if(saved.state){
        render(saved.state);
        try{render(await request(`/api/incremental/sessions/${encodeURIComponent(saved.state.session_id)}`));
          if(saved.state.status==='invalid_response'&&state.rounds.at(-1)?.recovered_from_saved_reply&&$('singlePrediction').checked)singlePredictionDone=true;
        }
        catch{status('已恢复本地显示快照，但无法核对后端任务。请加载已保存任务核对后再推进。');state=null;}
      }
      $('predictionCacheStatus').textContent='已恢复批次、种子、参数及最近任务；费用确认已取消，未自动调用模型。';
      if(state||!saved.state)status(selectedSource?`传递成功：已核对${dataset.point_count}个高斯点位，并恢复本批参数；未调用模型。`:'已恢复本地工作区，未调用模型。');
    }
    if(dataset?.source_result_id){$('sourceBatchDownload').href='/api/incremental/batches/'+encodeURIComponent(dataset.source_result_id);$('sourceBatchDownload').hidden=false;}
    $('predictionConsent').checked=false;
    $('chatConsent').checked=false;
    if(matching&&saved.questionMode)$('questionTemplateMode').value=saved.questionMode;
    if(dataset){if(backendReady)await prepareChat();if(matching&&saved.chatDraft&&!singlePredictionDone&&backendReady){
      if(saved.questionMode==='custom'&&typeof saved.chatQuestion==='string'&&saved.chatQuestion.trim()){
        $('chatQuestion').value=saved.chatQuestion.split(pointMarker)[0].trim();
        try{parseMessage();}catch{preloadQuestion();}
      }else preloadQuestion();
      if(saved.groupSelectionKey===groupSelectionKey&&Array.isArray(saved.selectedGroupKeys)){const valid=new Set(candidateGroups().filter(groupAllowed).map(groupKey));const restoredGroups=new Set(saved.selectedGroupKeys.filter(key=>valid.has(key)));if(restoredGroups.size||!saved.selectedGroupKeys.length)selectedGroupKeys=restoredGroups;for(const g of candidateGroups().filter(fixedStart))selectedGroupKeys.add(groupKey(g));renderPointViews();}
      if(saved.selectionKey===selectionKey&&Array.isArray(saved.selectedPointIds)){const allowed=new Set(messagePoints().map(p=>p.candidate_id));selectedPointIds=new Set(saved.selectedPointIds.filter(id=>allowed.has(id)));if(!state?.initialized)selectedPointIds=allowed;renderPointViews();}
      try{const restored=parseMessage();const prepared=JSON.parse($('chatInput').value);$('chatInput').value=JSON.stringify({...prepared,question:restored.question,submitted_observations:restored.observations},null,2);}catch{}
    }}
    drawConversations();initializationSucceeded=true;
  }).finally(()=>{cacheReady=true;if(initializationSucceeded)savePrediction();});
  $('loadGaussianBatch').addEventListener('click',()=>run(async()=>{
    await loadBatch($('batchResultId').value.trim());
    const url=new URL(window.location.href);url.searchParams.set('source_result',dataset.source_result_id);window.history.replaceState(null,'',url);
    savePrediction();
  }));
  $('recheckPredictionBackend').addEventListener('click',()=>run(async()=>{if(await verifyBackend())await prepareChat();}));
  $('togglePredictionAdvanced').addEventListener('click',()=>{
    $('predictionAdvanced').hidden=!$('predictionAdvanced').hidden;
    $('togglePredictionAdvanced').setAttribute('aria-expanded',String(!$('predictionAdvanced').hidden));
  });
  const narrowPanels=window.matchMedia('(max-width: 1100px)'),mobilePanels=window.matchMedia('(max-width: 760px)');
  function syncPanelButtons(){
    $('togglePointSidebar').setAttribute('aria-expanded',String(mobilePanels.matches?document.body.classList.contains('points-open'):!document.body.classList.contains('points-collapsed')));
    $('toggleExecutionSidebar').setAttribute('aria-expanded',String(narrowPanels.matches?document.body.classList.contains('execution-open'):!document.body.classList.contains('execution-collapsed')));
  }
  $('togglePointSidebar').addEventListener('click',()=>{document.body.classList.toggle(mobilePanels.matches?'points-open':'points-collapsed');document.body.classList.remove('execution-open');syncPanelButtons();});
  $('toggleExecutionSidebar').addEventListener('click',()=>{document.body.classList.toggle(narrowPanels.matches?'execution-open':'execution-collapsed');document.body.classList.remove('points-open');syncPanelButtons();});
  narrowPanels.addEventListener('change',syncPanelButtons);mobilePanels.addEventListener('change',syncPanelButtons);syncPanelButtons();
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){document.body.classList.remove('points-open','execution-open');$('predictionAdvanced').hidden=true;$('togglePredictionAdvanced').setAttribute('aria-expanded','false');syncPanelButtons();}});
  for(const id of ['seeds','stride','horizon','maxCalls','sessionId'])$(id).addEventListener('input',savePrediction);
  $('clearPredictionCache').addEventListener('click',async()=>{
    if(busy||!cacheAvailable())return;
    cacheReady=false;
    try{await WorkspaceCache.remove('prediction-workspace');try{localStorage.removeItem('incremental-session');}catch{}
      window.location.assign('/incremental?empty=1');
    }catch{$('predictionCacheStatus').textContent='清除保存失败，请检查浏览器存储权限。';cacheReady=true;}
  });
  window.addEventListener('pagehide',savePrediction);
})();
