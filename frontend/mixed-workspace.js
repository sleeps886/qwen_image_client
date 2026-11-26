'use strict';
(() => {
  const $=id=>document.getElementById(id),API='/api/incremental/mixed-sessions';
  let dataset=null,state=null,selected=new Set(),groups=new Set(['']),preview=null,busy=false,ready=false,pollTimer=null;
  let spatialCardsAvailable=false,retryAvailable=false,batchAvailable=false,sequenceAvailable=false,sequencePreview=null,sequenceBody=null,sequenceRun=null,sequenceTimer=null,sequenceErrors=0;
  let spatialLineSnapshot=null,spatialLineChanges=[],spatialLineColors={},spatialLineReason='auto';
  let sequenceSpatialView={zoom:1,dx:0,dy:0,scale:1};
  let liveContext=null,liveDraft='',livePhase='',liveActive=false,liveExecutionId=null,liveBaseVersion=null,liveSaveAt=0,lastExecution=null,plotPreview=null;
  let recheckMode=false,inspectedPointId=null,pendingPlatform=null,selectionLimit=null,appendBusy=false;
  let spatialView={zoom:1,dx:0,dy:0},spatialRenderInfo=null,spatialRevision=null;
  let executionHistory=new Map(),executionBlocks=new Map();
  let conversations=[],conversationId=crypto.randomUUID(),conversationDraft=false,messages=[],customQuestion=false;
  const colors=['#50c6ad','#e6b566','#7aa9ef','#c194ed','#f28484','#8dcf79'];
  const color=tid=>(colors[Number(tid.slice(1))-1]||`hsl(${(Number(tid.slice(1))*137.508)%360} 65% 60%)`);
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const points=()=>dataset?.candidates||[];
  const sorted=items=>[...items].sort((a,b)=>a.t-b.t||a.platform_id.localeCompare(b.platform_id)||a.array.localeCompare(b.array)||a.candidate_id.localeCompare(b.candidate_id));
  const selectedPoints=()=>sorted(points().filter(p=>selected.has(p.candidate_id)));
  const pendingPoints=()=>selectedPoints().filter(p=>!state?.submitted_ids.includes(p.candidate_id));
  const inputPoints=()=>recheckMode?selectedPoints():pendingPoints();
  const catalog=()=>dataset?.target_catalog||[];
  async function fetchJson(path,options={},timeout=15000){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeout);
    try{const response=await fetch(path,{...options,signal:controller.signal});const data=await response.json();return {response,data};}
    catch(error){if(controller.signal.aborted)throw new Error('请求超时：'+path+'。未自动重试，请检查后端服务后重试；导入不会调用模型。');throw error;}
    finally{clearTimeout(timer);}
  }
  async function request(path,body){const {response:r,data}=await fetchJson(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined},body?30000:15000);if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data));return data;}
  function note(text){$('chatNotice').textContent=text;}
  async function save(){if(!dataset&&!conversationDraft)return;const snapshot={dataset,conversationDraft,state,selected:[...selected],groups:[...groups],messages,executionHistory:[...executionHistory.values()],question:$('chatQuestion').value,customQuestion,recheckMode,conversationId,sampleCount:$('samplePointCount').value,sampleMode:$('samplePointMode').value,sampleModeVersion:2,spatialMirrorSide:$('sequenceMirrorSide').value,spatialAutoConnect:$('sequenceAutoConnect').checked,spatialLineSnapshot,spatialLineChanges,spatialLineColors,reviewLimit:$('sequenceReviewLimit').value,gapLimit:$('sequenceGapLimit').value,useSequential:$('useSequential').checked,executionMode:$('sequenceMode').value,batchSize:$('sequenceBatchSize').value,batchConcurrency:$('sequenceConcurrency').value,spatialCards:$('sequenceSpatialCards').checked,retryEnabled:$('sequenceRetryEnabled').checked,retryLimit:$('sequenceRetryLimit').value,retryBudget:$('sequenceRetryBudget').value,workspaceSource:$('workspaceSource').value,pointImportMode:$('pointImportMode').value};
    conversations=[{id:conversationId,name:dataset?`混合观测 · ${state?.rounds.length||0}轮 · ${points().length}点 · ${conversationId.slice(0,4)}`:`新会话 · 待导入 · ${conversationId.slice(0,4)}`},...conversations.filter(c=>c.id!==conversationId)];
    try{await WorkspaceCache.set('mixed-conversation-'+conversationId,snapshot);await WorkspaceCache.set('mixed-workspace',snapshot);await WorkspaceCache.set('mixed-conversations',conversations);}catch{note('浏览器保存失败；后端任务仍按编号保存，请记录任务编号。');}drawConversations();}
  function controls(){$('chatPrepare').textContent=busy?'正在处理…':'准备输入（免费）';$('chatSend').textContent=busy?'正在处理，请稍候…':'分析归属并列出集合 ↗';$('platformFileImport').disabled=busy;$('pointFileImport').disabled=appendBusy||(busy&&!canAppend());$('pointFileDropZone').setAttribute('aria-busy',String(appendBusy||busy));workspaceInputControls();for(const b of $('conversationList').querySelectorAll('button'))b.disabled=busy;for(const b of $('pointFileList').querySelectorAll('button'))b.disabled=busy;const waiting=state?.status==='awaiting_review',blocked=state&&state.status!=='ready',retry=state?.manual_rerun;
    $('rerunPanel').hidden=!retry?.phase;$('rerunTitle').textContent=state?.plot_status==='awaiting_association_recheck'?'本轮需要关联复核':'本轮未完成，可手动重新运行';$('rerunHint').textContent=retry?.reason||'';
    $('rerunUnknownWarning').hidden=!retry?.unknown_result;const retryDisabled=busy||!ready||!retry?.available||(retry?.unknown_result&&!$('rerunUnknownAck').checked);
    $('prepareRerun').disabled=retryDisabled;$('prepareRerun').textContent=retry?.phase==='plot'?'重新运行绘图（先免费准备）':'重新分析本轮（先免费准备）';$('rerunAssociation').hidden=!retry?.can_reassociate;$('rerunAssociation').disabled=retryDisabled;
    $('chatSend').disabled=busy||appendBusy||!ready||!dataset||!!blocked||!inputPoints().length||!$('chatConsent').checked;
    $('chatPrepare').disabled=busy||appendBusy||!ready||!dataset||!!blocked||!inputPoints().length;
    $('chatComposer').hidden=!!waiting||!!blocked||state?.rerun_prepared?.phase==='plot'||(!!retry?.phase&&!inputPoints().length);$('trajectoryReview').hidden=!waiting;
    $('chatState').textContent=busy?'处理中':waiting?'等待数据集合确认':state?.status==='ready'?(state.plot_status==='awaiting_association_recheck'?'需要关联复核':'等待新增观测'):state?.status||'待分析观测';
    for(const id of ['adoptTrajectory','rejectTrajectory','openNewConversation','targetTrackSelect','clearSelection','selectInitialPoints'])$(id).disabled=busy;
    for(const n of $('candidateGroupList').querySelectorAll('input'))n.disabled=busy||waiting;
    for(const n of $('selectedPoints').querySelectorAll('button'))n.disabled=busy||waiting;$('selectInitialPoints').disabled=busy||waiting||!dataset;$('selectAllPoints').disabled=busy||waiting||!dataset||!visible().length;$('samplePoints').disabled=$('samplePointCount').disabled=$('samplePointMode').disabled=busy||waiting||!dataset||!visible().length;
    $('refreshRunningTask').hidden=!['pending','plot_pending'].includes(state?.status);$('refreshRunningTask').disabled=busy;$('recoverAssociation').hidden=state?.status!=='invalid_response';$('recoverAssociation').disabled=busy||!ready;
    const canPlot=state?.status==='ready'&&state?.rounds.at(-1)?.review_status==='adopted'&&!['generated','bearing_generated','awaiting_association_recheck','awaiting_association'].includes(state?.plot_status)&&!retry?.phase;
    $('modelPlotPanel').hidden=!canPlot&&!state?.status?.startsWith('plot_');
    const spatialBlocked=$('modelPlotPurpose').value==='spatial'&&(plotPreview?plotPreview.can_generate_spatial===false:!(state?.association_audit?.spatially_supported_targets>0));$('runModelPlot').disabled=busy||!ready||!canPlot||spatialBlocked||!$('modelPlotConsent').checked||state.calls>=state.max_calls;$('runModelPlot').textContent=$('modelPlotPurpose').value==='bearing'?'授权模型生成方位诊断 ↗':'授权模型生成空间轨迹 ↗';$('modelPlotPurpose').disabled=busy;$('modelPlotCapability').textContent=spatialBlocked?'当前没有通过校验的跨阵目标／空间候选；空间调用已禁用，不浪费一次绘图费用。可继续补证据后复核，或明确选择仅方位诊断。':$('modelPlotPurpose').value==='bearing'?'本次仅请求方位诊断，不生成东／北空间路线；仍需单独授权费用。':'';
    $('prepareModelPlot').disabled=busy||!ready||!canPlot;
    $('modelPlotHint').textContent=$('modelPlotPurpose').value==='bearing'?'独立授权调用，仅生成模型方位诊断；不能当东／北空间轨迹。缺曲线时保留实际观测，不由本地补线。':state?.status?.startsWith('plot_')?'绘图调用未完成或回复无效；保留原结果，不自动重试，不使用本地线性替代。':'独立授权调用：输出东／北坐标空间轨迹。方位图只是过渡预览；如原回复没有空间坐标，重新调用可能增加费用，不会自动重试。';
    $('toggleExecutionSidebar').disabled=!executionHistory.size;$('supplementEvidence').disabled=busy||appendBusy||!dataset||waiting;
    $('prepareAssociationRecheck').hidden=state?.status!=='ready'||!state?.submitted_ids.length||state?.rerun_prepared?.phase==='plot';$('prepareAssociationRecheck').disabled=busy;
    $('recoverPlot').hidden=state?.status!=='plot_invalid_response';$('recoverPlot').disabled=busy||!ready;
    sequenceControls();

  }
  async function run(fn){if(busy||appendBusy)return;busy=true;controls();try{await fn();}catch(e){note(e.message);}finally{busy=false;controls();await save();}}
  async function drawPointFiles(){const host=$('pointFileList');if(!host.childElementCount)host.append(el('p','正在读取独立文件库；旧数据升级时保留原记录。','note'));let files;try{files=await PointFiles.list();}catch(error){host.replaceChildren(el('p','文件库暂不可读：'+error.message+' 文件未删除。','association-warning'));throw error;}host.replaceChildren();if(!files.length)host.append(el('p','暂无数据点文件，请拖入 ZIP 轨迹包或 observations.json。','note'));for(const file of files){const row=el('div',undefined,'point-file'),name=el('strong',file.name),meta=el('small',`${file.data.candidates?.length||0}个原始观测 · ${new Set((file.data.candidates||[]).map(p=>p.t)).size}个时刻`),actions=el('div',undefined,'actions'),open=el('button','打开'),download=el('button','导出全部点位'),remove=el('button','删除');open.onclick=()=>run(()=>load({...file.data,point_file_id:file.id}));download.onclick=()=>PointFiles.download(file);remove.onclick=()=>{if(busy||!confirm('删除此观测点文件？若当前正在浏览它，将同步清空点位、选择和平台显示；历史会话和后端结果保留。'))return;void run(async()=>{await deletePointFile(file);await drawPointFiles();});};open.disabled=remove.disabled=busy;actions.append(open,download,remove);row.append(name,meta,actions);host.append(row);}}
  function drawConversations(){$('conversationList').replaceChildren();for(const item of conversations){const row=el('div',undefined,'conversation-item'),b=el('button',item.name,'conversation-entry'),remove=el('button','×','conversation-delete');row.dataset.conversationId=item.id;b.setAttribute('aria-current',String(item.id===conversationId));b.disabled=busy;b.onclick=()=>run(async()=>{await save();const saved=await WorkspaceCache.get('mixed-conversation-'+item.id);await restore(saved);});remove.type='button';remove.title='删除会话，不删除导入文件';remove.setAttribute('aria-label','删除会话 '+item.name);remove.disabled=busy;remove.onclick=()=>{if(busy||!confirm('删除“'+item.name+'”？仅清除此浏览器中的会话记录，不删除独立文件库的导入文件，也不删除后端结果。'))return;void run(()=>deleteConversation(item.id));};row.append(b,remove);$('conversationList').append(row);}}
  function resetWorkspace(){
    clearSequenceView();conversationDraft=false;
    clearInterval(pollTimer);pollTimer=null;lastAppend=null;$('pointImportMode').value='replace';$('workspaceSource').value='workspace';$('workspaceRawPoints').value='';$('workspaceReceipt').textContent='';$('workspaceReceipt').hidden=true;
    $('pointSamplingStatus').textContent='';dataset=null;state=null;selected=new Set();groups=new Set(['']);preview=null;recheckMode=false;lastExecution=null;liveContext=null;liveDraft='';liveActive=false;livePhase='';executionHistory.clear();executionBlocks.clear();plotPreview=null;messages=[];customQuestion=false;inspectedPointId=null;conversationId=crypto.randomUUID();$('pointCard').hidden=true;$('pointHover').hidden=true;$('chatConsent').checked=false;$('modelPlotConsent').checked=false;$('rerunUnknownAck').checked=false;$('batchSource').textContent='尚未导入数据';$('chatInput').textContent='尚未准备输入';question();drawExecution({status:'not_started',nodes:[]});render();drawConversations();
  }
  function isCurrentPointFile(file){
    if(!dataset)return false;
    const id=dataset.point_file_id||dataset.source_result_id||dataset.batch_id;
    if(id)return id===file.id;
    // Older local snapshots may not yet contain a library ID.
    const raw=file.data.candidates||[];
    if((dataset.import_filter?.original_count??points().length)!==raw.length)return false;
    const key=p=>JSON.stringify([p.candidate_id||p.id,p.t,p.platform_id,p.array,p.bearing_deg]);
    const keys=new Set(raw.map(key));return points().length>0&&points().every(p=>keys.has(key(p)));
  }
  async function deletePointFile(file){
    const active=isCurrentPointFile(file);
    await PointFiles.remove(file.id);
    if(active){
      await WorkspaceCache.remove('mixed-workspace');
      await WorkspaceCache.remove('mixed-pending-platform');pendingPlatform=null;
      resetWorkspace();
      $('pointFileImportStatus').textContent='当前数据文件已删除，点位列表与选择已清空。';
      $('platformImportStatus').textContent='尚未导入平台数据。';
      const url=new URL(location.href);url.searchParams.delete('source_result');history.replaceState(null,'',url);
    }
    note(active?'文件已删除，当前点位、选择与平台显示已清空；历史会话和后端结果保留。':'文件已删除，当前浏览的其他文件不受影响。');
  }
  async function deleteConversation(id){
    // Complete local removals before changing the active form; never call backend deletion.
    const remaining=conversations.filter(c=>c.id!==id),active=id===conversationId;
    await WorkspaceCache.remove('mixed-conversation-'+id);
    await WorkspaceCache.set('mixed-conversations',remaining);
    const latest=await WorkspaceCache.get('mixed-workspace');if(active||latest?.conversationId===id)await WorkspaceCache.remove('mixed-workspace');
    conversations=remaining;
    if(active){resetWorkspace();const url=new URL(location.href);url.searchParams.delete('source_result');history.replaceState(null,'',url);}
    await drawPointFiles();drawConversations();note('会话已删除；独立文件库中的导入文件仍保留，可继续打开或导出。后端结果未删除。');
  }

  let lastAppend=null;
  function canAppend(){return !!dataset&&!!state?.workspace_input&&$('pointImportMode').value==='append'&&!appendBusy;}
  function workspaceInputControls(){
    const enabled=!!state?.workspace_input;
    $('enableWorkspaceInput').disabled=busy||appendBusy||!ready||!dataset||enabled;
    $('appendWorkspacePoints').disabled=appendBusy||!ready||!dataset||(busy&&!enabled);
    $('retryWorkspaceAppend').disabled=appendBusy||!lastAppend||(busy&&!enabled);
    $('workspaceTimeline').readOnly=enabled;$('workspaceTimeline').disabled=appendBusy;
    $('workspaceSource').disabled=appendBusy;$('pointImportMode').disabled=appendBusy||(busy&&!enabled);
  }
  function drawWorkspaceInput(){
    const input=state?.workspace_input,summary=state?.input_summary;
    if(input)$('workspaceTimeline').value=input.timeline_id;
    else if($('workspaceTimeline').dataset.conversation!==conversationId){$('workspaceTimeline').value='relative-'+conversationId;$('workspaceTimeline').dataset.conversation=conversationId;}
    $('workspaceInputStatus').textContent=summary?`当前会话持久化保留${summary.accepted_count}点，尚未提交${summary.unsubmitted_count}点；观测版本${summary.observation_revision}，接收序号${summary.receipt_seq}。分析或待确认时仍可追加，原快照不变。`:state?'这是已有会话。可在此启用持久化追加，保留旧轮次、正文和空间结果；不会自动重新调用模型。':'导入后仍在此工作台处理。新会话免费准备时自动启用持久化追加。';
    $('enableWorkspaceInput').textContent=input?'已启用当前会话追加':'启用当前会话追加（免费）';
    $('retryWorkspaceAppend').hidden=!lastAppend||lastAppend.sid!==state?.session_id;$('exportWorkspace').hidden=!input;if(input)$('exportWorkspace').href=`${API}/${state.session_id}/export`;
    workspaceInputControls();
  }
  async function ensureWorkspaceState(maxCalls=20){
    if(!state){state=await request(API,{dataset,horizon:20,max_calls:maxCalls,persistent_input:true,timeline_id:$('workspaceTimeline').value});$('sessionId').value=state.session_id;drawWorkspaceInput();}
  }
  async function syncWorkspaceCatalog(){
    if(!state?.workspace_input)return;
    const sid=state.session_id,catalogue=await request(`${API}/${sid}/catalog`);
    if(state?.session_id!==sid)return;
    dataset={...dataset,candidates:catalogue.candidates,platform_config:dataset.platform_config||catalogue.platform_config};
  }
  function platformSignature(config){return JSON.stringify((config?.platforms||[]).map(p=>[p.name,...p.start_ll,p.heading_deg,p.speed_kn,...p.hull_m,...p.tow_m]).sort((a,b)=>a[0].localeCompare(b[0])));}
  async function appendData(data,body=null){
    if(!dataset)throw new Error('请先导入当前会话的初始观测');
    if(data?.platform_config&&platformSignature(data.platform_config)!==platformSignature(dataset.platform_config))throw new Error('追加文件的平台配置不同或当前未配置平台；请先核对平台，不会静默改变已有分析依据。');
    await ensureWorkspaceState();
    if(!state.workspace_input)throw new Error('已有会话请先点击“启用当前会话追加”，旧分析会保留。');
    const sid=state.session_id;
    const payload=body||{idempotency_key:crypto.randomUUID(),source_id:$('workspaceSource').value,timeline_id:$('workspaceTimeline').value,observations:data.candidates};
    lastAppend={sid,body:payload,data};$('retryWorkspaceAppend').hidden=false;
    const result=await request(`${API}/${sid}/observations`,payload);
    if(state?.session_id!==sid)throw new Error('观测已保存在原会话，请切回原会话查看');
    const receipt=result.receipt;$('workspaceReceipt').hidden=false;$('workspaceReceipt').textContent=JSON.stringify(receipt,null,2);
    const mapping=new Map(payload.observations.map((p,i)=>[p?.candidate_id||p?.id||p?.source_observation_id,receipt.results[i]]).filter(([,row])=>['accepted','duplicate'].includes(row.status)).map(([id,row])=>[id,row.candidate_id]));
    for(const group of data?.target_catalog||[]){if(!Array.isArray(group?.candidate_ids))continue;const ids=group.candidate_ids.map(id=>mapping.get(id)).filter(Boolean);if(ids.length)dataset.target_catalog=[...(dataset.target_catalog||[]),{target_id:`追加${(dataset.target_catalog||[]).length+1} · ${group.target_id}`,candidate_ids:ids}];}
    delete dataset.point_file_id;delete dataset.source_result_id;delete dataset.batch_id;
    await syncWorkspaceCatalog();
    if(!busy)state=await request(API+'/'+sid);
    else if(state.input_summary)state.input_summary={...state.input_summary,accepted_count:points().length,receipt_seq:receipt.receipt_seq,observation_revision:receipt.observation_revision,unsubmitted_count:points().filter(p=>!state.submitted_ids.includes(p.candidate_id)).length};
    $('batchSource').textContent=`已保留${points().length}点（原始观测＋持续追加），会话与旧结果未替换；新到点待选择分析。`;groups=new Set(['']);if(!busy){preview=null;plotPreview=null;$('chatConsent').checked=false;$('modelPlotConsent').checked=false;question();}
    drawWorkspaceInput();drawImportFilter();drawMenu();drawRows();drawSelection();drawChart();await save();
    note(`已追加${receipt.accepted}点，全部有效观测共${points().length}点。新点未自动勾选，也未混入正在执行的快照；未调用模型。`);
    return result;
  }
  async function appendAction(action){if(appendBusy)return;appendBusy=true;controls();$('workspaceInputStatus').textContent='正在追加观测，当前模型快照和旧结果保持不变…';try{await action();}catch(error){note(error.message+'；接入回执可用原请求键重读，不会重新调用模型。');}finally{appendBusy=false;drawWorkspaceInput();controls();await save();}}
  async function appendFiles(files){await appendAction(async()=>{
    if(files.length>16||files.some(f=>f.size>16*1024*1024)||files.reduce((n,f)=>n+f.size,0)>20*1024*1024)throw new Error('文件数量或大小超过导入资源限制');
    const form=new FormData();for(const file of files)form.append('files',file);
    const response=await fetch('/api/incremental/import-observations',{method:'POST',body:form}),data=await response.json();if(!response.ok)throw new Error(data.detail||'文件解析失败');
    await PointFiles.put(data,{manual:true});await drawPointFiles();await appendData(data);$('pointFileImport').value='';$('pointFileImportStatus').textContent='文件已追加到当前会话，原会话、选择、消息和结果保留；未调用模型。';
  });}
  $('enableWorkspaceInput').onclick=()=>run(async()=>{await ensureWorkspaceState();if(!state.workspace_input)state=await request(`${API}/${state.session_id}/enable-input`,{version:state.version,timeline_id:$('workspaceTimeline').value});await syncWorkspaceCatalog();render();note('已在当前工作台启用持久化追加，原分析与空间绘图保留；未调用模型。');});
  $('appendWorkspacePoints').onclick=()=>void appendAction(async()=>{const raw=JSON.parse($('workspaceRawPoints').value);await appendData({candidates:Array.isArray(raw)?raw:[raw]});});
  $('retryWorkspaceAppend').onclick=()=>void appendAction(async()=>{if(!lastAppend||lastAppend.sid!==state?.session_id)throw new Error('原请求不属于当前会话');await appendData(lastAppend.data,lastAppend.body);});
  $('pointImportMode').onchange=()=>{controls();void save();};$('workspaceSource').onchange=()=>void save();
  function question(){if(customQuestion)return;if($('useSequential').checked){if($('sequenceMode').value==='batch-snapshot-v1'){$('chatQuestion').value='每批最多50组，批内共享同一历史快照，由模型判断归属并统一归并。允许中间补点、末尾续接、新目标及修订历史。不要把转折直接当作新目标；证据不足保留待定，不按来源、固定强度或峰宽猜身份。';return;}$('chatQuestion').value='逐组判断当前新增观测与已有轨迹的接续。其他已处理观测仅为历史或待决复核，不能使用未处理队列。证据不足保留暂定、竞争或未归属；确认更新后再单独授权预测。';return;}const chosen=inputPoints();$('chatQuestion').value=`本轮加入${chosen.length}个观测点，分布在${new Set(chosen.map(p=>p.t)).size}个时刻。请判断它们与已有轨迹的接续关系；无法接续的点可建立新目标假设或暂不归属，请先判断各目标归属并在执行节点展示资料，待我确认后另行调用模型绘图与预测。`;}
  function invalidate(){sequencePreview=null;sequenceBody=null;preview=null;$('chatInput').textContent='输入已变化，请免费重新准备。';question();drawSelection();drawChart();controls();void save();}
  function drawMenu(){const menu=$('targetTrackSelect');menu.replaceChildren();const all=el('option','全部候选');all.value='';all.selected=groups.has('');menu.append(all);for(const item of catalog()){const option=el('option','候选分组 '+item.target_id);option.value=item.target_id;option.selected=groups.has(item.target_id);menu.append(option);}}
  function visible(){if(groups.has('')||!groups.size)return points();const ids=new Set(catalog().filter(g=>groups.has(g.target_id)).flatMap(g=>g.candidate_ids));return points().filter(p=>ids.has(p.candidate_id));}
  function drawRows(){const host=$('candidateGroupList');host.replaceChildren();const rows=new Map();for(const p of sorted(visible())){const k=JSON.stringify([p.platform_id,p.t]);if(!rows.has(k))rows.set(k,[]);rows.get(k).push(p);}
    $('chatPayloadSummary').textContent=`当前浏览 ${visible().length}点／${rows.size}行；全批 ${points().length}点。勾选不预设目标。`;
    for(const row of rows.values()){const box=el('div',undefined,'mixed-row'),label=el('label'),check=el('input');check.type='checkbox';check.checked=row.every(p=>selected.has(p.candidate_id));check.indeterminate=!check.checked&&row.some(p=>selected.has(p.candidate_id));check.setAttribute('aria-label',`整行 ${row[0].platform_id} ${row[0].t}秒`);
      label.append(check,el('span',`${row[0].t}秒 · ${row[0].platform_id} · hull ${row.filter(p=>p.array==='hull').length} / tow ${row.filter(p=>p.array==='tow').length}`));box.append(label);
      const detail=el('details');detail.append(el('summary',`展开${row.length}个观测点（非目标配对）`));
      for(const p of row){const l=el('label'),c=el('input');c.type='checkbox';c.checked=selected.has(p.candidate_id);c.dataset.candidateId=p.candidate_id;c.setAttribute('aria-label','选择观测 '+p.candidate_id);c.onchange=()=>{if(c.checked)selected.add(p.candidate_id);else selected.delete(p.candidate_id);check.checked=row.every(p=>selected.has(p.candidate_id));check.indeterminate=!check.checked&&row.some(p=>selected.has(p.candidate_id));invalidate();};l.dataset.pointRow=p.candidate_id;l.classList.toggle('point-highlight',p.candidate_id===inspectedPointId);l.append(c,el('span',`${p.candidate_id} · ${p.array} · ${p.bearing_deg.toFixed(3)}°`),el('small',p.quality||'未提供质量'));detail.append(l);}
      check.onchange=()=>{for(const p of row){if(check.checked)selected.add(p.candidate_id);else selected.delete(p.candidate_id);}for(const c of detail.querySelectorAll('input'))c.checked=check.checked;check.indeterminate=false;invalidate();};box.append(detail);host.append(box);
    }controls();
  }
  function drawSelection(){const chosen=selectedPoints();$('selectedSummary').textContent=recheckMode?`已选复核观测 · ${chosen.length}点（重新分析，不计新增）`:`已选观测 · ${chosen.length}点（本轮新增${pendingPoints().length}点）`;$('selectedPoints').replaceChildren();for(const p of chosen){const row=el('div'),remove=el('button','移除');remove.disabled=busy;remove.onclick=()=>{selected.delete(p.candidate_id);drawRows();invalidate();};row.append(el('span',`${p.t}秒 · ${p.candidate_id} · ${p.array}`),remove);$('selectedPoints').append(row);}
    $('chatAttachmentSummary').textContent=recheckMode?`历史关联复核 ${inputPoints().length}个已选观测点`:`本轮明确选择 ${pendingPoints().length}个新增观测点`;$('chatPointTable').replaceChildren();for(const p of inputPoints())$('chatPointTable').append(el('div',`${p.candidate_id} | ${p.platform_id}/${p.array} | ${p.t}秒 | ${p.bearing_deg}°`));drawSelectionDiagnostics();}
  function associationTables(round,host){
    if(round.reply_format?.note)host.append(el('p',round.reply_format.note,'note'));
    const status={pending:'待确认',adopted:'已确认',rejected:'已打回'}[round.review_status]||round.review_status;
    host.append(el('h3',`第${round.input.step_id}轮 · 各目标数据集合（${status}）`));
    const lookup=new Map(points().map(p=>[p.candidate_id,p]));
    const prior=state.rounds.slice(0,state.rounds.indexOf(round)).filter(r=>r.review_status==='adopted').at(-1);
    const previous=new Set(Object.values(prior?.tracks||{}).flatMap(t=>t.history.map(p=>p.candidate_id)));
    const table=(title,items,reason)=>{
      const section=el('section',undefined,'association-collection');section.append(el('h4',title));if(reason)section.append(el('p',reason,'note'));
      const scroll=el('div',undefined,'association-table-scroll'),t=el('table',undefined,'track-comparison');
      const head=el('thead'),hr=el('tr');for(const text of ['点编号','时间（秒）','平台／阵列','方位（°）','质量','本轮状态'])hr.append(el('th',text));head.append(hr);t.append(head);const body=el('tbody');
      for(const p of sorted(items)){const row=el('tr');row.dataset.candidateId=p.candidate_id;const mode=round.input.revision_mode==='historical_replay'&&p.t>=round.input.affected_from?'历史复核':previous.has(p.candidate_id)?'已有观测':'本轮观测';for(const v of [p.candidate_id,p.t,p.platform_id+'/'+p.array,p.bearing_deg.toFixed(3),[p.quality,p.mixed===true?'混合峰':''].filter(Boolean).join('／')||'未提供',mode])row.append(el('td',String(v)));body.append(row);}
      t.append(body);scroll.append(t);section.append(scroll);host.append(section);
    };
    for(const [targetId,target]of Object.entries(round.targets||{})){
      const items=(target.legacy_track_ids||[]).flatMap(id=>round.tracks[id]?.history||[]);
      table(`${targetId} · 物理目标假设 · ${(target.fragment_ids||[]).join(' + ')} · ${target.evidence?.eligible_for_spatial?'跨阵时序检查通过，仍非真值':'证据待复核，不补距离'}`,items,target.reason+'；'+(target.evidence?.reason||''));
    }
    for(const [tid,track]of Object.entries(round.tracks||{}))table(`${tid.replace(/^T/,'F')} · ${track.history.length}个观测点 · ${track.evidence_tier==='tentative_fragment'||new Set(track.history.map(p=>p.t)).size<3?'暂定片段，不能据此另计物理目标':'多时刻支持片段，物理身份仍待验证'}`,track.history,track.reason);
    if(!Object.keys(round.tracks||{}).length)host.append(el('p','本轮未形成可接入轨迹的集合；不强行指定目标数量。'));
    // Pools are snapshotted with each round, so later reviews never rewrite the displayed old result.
    const unassigned=round.unassigned_ids||(state.rounds.at(-1)===round?state.unassigned_ids:[]);
    if(unassigned.length)table(`未归属池 · ${unassigned.length}点`,unassigned.map(id=>lookup.get(id)).filter(Boolean));
    const ambiguities=round.ambiguities||(state.rounds.at(-1)===round?state.ambiguities:[]);
    if(ambiguities.length){table(`待决关联 · ${ambiguities.length}点`,ambiguities.map(a=>lookup.get(a.candidate_id)).filter(Boolean));for(const a of ambiguities)host.append(el('p',`${a.candidate_id} → ${a.possible_tracks.join(' / ')}：${a.reason}`,'note'));}
    for(const warning of round.warnings||[])host.append(el('p',`${warning.candidate_id||warning.target_ref||'关联校验'}：${warning.reason}`,'association-warning'));
    if(round.input.revision_mode==='historical_replay')host.append(el('p',`历史补点修订：从 ${round.input.affected_from} 秒起复核。晚时刻观测不倒灌为早时刻已知预测，旧预测记录保留。`,'note'));
    if(round.review_status==='pending')host.append(el('p','请核对节点中的集合。确认本身免费，之后还需单独授权第二次模型绘图调用。','note'));
  }
  function drawMessages(){
    for(const round of state?.rounds||[]){if(!messages.some(m=>m.round_revision===round.revision))messages.push({role:'assistant',text:'已完成本轮归属分析。',round_revision:round.revision});if(round.model_plot&&!messages.some(m=>m.plot_revision===round.revision))messages.push({role:'assistant',text:round.model_plot.summary||'本轮模型绘图判断已完成。',plot_revision:round.revision});}
    $('chatMessages').replaceChildren();
    for(const msg of messages){
      if(msg.role==='execution'){
        const snapshot=executionHistory.get(msg.execution_id);if(snapshot){const block=executionBlock(snapshot);paintExecution(snapshot,block);$('chatMessages').append(block);}continue;
      }
      const box=el('article',undefined,'prediction-message '+msg.role);box.append(el('strong',msg.role==='user'?'你':'小怪'),el('p',msg.text));
      const round=msg.round_revision!==undefined?state?.rounds.find(r=>r.revision===msg.round_revision):null;
      if(round)box.append(el('p',`${Object.keys(round.targets??(round===state.rounds.at(-1)?state.targets||{}:{})).length}个目标假设 · ${Object.keys(round.tracks||{}).length}个片段 · 未归属 ${round.unassigned_ids?.length||0} · 待决 ${round.ambiguities?.length||0}`,'note'));
      $('chatMessages').append(box);
    }
    identifyLatestExecution();
  }
  function svgNode(tag,attrs,parent=$('predictionChart')){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,String(v));parent.append(n);return n;}
  function pointMembership(p){
    const pending=state?.status==='awaiting_review',tracks=pending?(state?.tracks||{}):(state?.confirmed_tracks||state?.tracks||{});
    const fragments=Object.entries(tracks).filter(([,v])=>v.history?.some(q=>q.candidate_id===p.candidate_id)).map(([id])=>id);
    const targets=Object.entries((pending?state?.targets:state?.confirmed_targets)||state?.targets||{}).filter(([,v])=>v.legacy_track_ids?.some(id=>fragments.includes(id))).map(([id])=>id);
    const ambiguity=(pending?state?.ambiguities:state?.confirmed_ambiguities||state?.ambiguities||[])?.find(a=>a.candidate_id===p.candidate_id);
    const unassigned=(pending?state?.unassigned_ids:state?.confirmed_unassigned_ids||state?.unassigned_ids||[])?.includes(p.candidate_id);
    return {fragments:fragments.map(id=>id.replace(/^T/,'F')),targets,status:ambiguity?'待决关联':unassigned?'未归属':fragments.length?(pending?'集合待确认':'已确认集合'):'待分析观测',ambiguity};
  }
  function pointSummary(p){const m=pointMembership(p);return `${p.candidate_id} · ${p.t}秒 · ${p.platform_id}/${p.array} · ${p.bearing_deg.toFixed(3)}° · ${p.quality||'质量未提供'} · ${m.status}`;}
  function showPointHover(p,records){const pair=PointSampling.sourcePairGroups(selectedPoints(),catalog()).find(g=>g.candidates.some(q=>q.candidate_id===p.candidate_id));$('pointHover').textContent=pointSummary(p)+'\n'+JSON.stringify(PointSampling.groupDetail(records||pair?.candidates||[p]),null,2);$('pointHover').hidden=false;}
  function renderPointCard(p){const host=$('pointCard');host.replaceChildren();host.hidden=false;const head=el('div',undefined,'point-card-head'),close=el('button','关闭');close.onclick=()=>{inspectedPointId=null;host.hidden=true;drawRows();drawChart();};head.append(el('strong','观测详情 · '+p.candidate_id),close);host.append(head);
    const m=pointMembership(p),fields=[['时间',p.t+'秒'],['平台 / 阵列',p.platform_id+' / '+p.array],['方位',p.bearing_deg+'°'],['状态',m.status],['片段',m.fragments.join('、')||'无'],['物理目标假设',m.targets.join('、')||'无（片段不等于目标）']];
    if(m.ambiguity)fields.push(['可能归属',(m.ambiguity.possible_tracks||[]).join('、')],['待决原因',m.ambiguity.reason||'未提供']);
    for(const [key,label] of [['quality','质量状态'],['strength','信号强度'],['sigma_deg','峰宽 sigma（°）'],['bearing_error_assumption_deg','方位误差假设（°）'],['mixed','混合峰']])fields.push([label,p[key]===undefined?'未提供':String(p[key])]);
    const grid=el('dl');for(const [k,v]of fields)grid.append(el('dt',k),el('dd',v));host.append(grid);const raw=el('details');raw.append(el('summary','完整观测字段'),el('pre',JSON.stringify(p,null,2)));host.append(raw);
    const nearby=[...new Map([...(state?.observations||[]),...selectedPoints()].map(q=>[q.candidate_id,q])).values()].filter(q=>q.candidate_id!==p.candidate_id&&q.t===p.t);if(nearby.length){const choices=el('div','同一时刻其他观测（含重叠点）：');for(const q of nearby){const b=el('button',q.candidate_id+' / '+q.array);b.onclick=()=>inspectPoint(q);choices.append(b);}host.append(choices);}
  }
  function inspectPoint(p){inspectedPointId=p.candidate_id;drawChart();let row=[...$('candidateGroupList').querySelectorAll('[data-point-row]')].find(n=>n.dataset.pointRow===p.candidate_id);
    if(!row){groups=new Set(['']);drawMenu();drawRows();row=[...$('candidateGroupList').querySelectorAll('[data-point-row]')].find(n=>n.dataset.pointRow===p.candidate_id);}
    $('candidateGroupList').querySelectorAll('[data-point-row]').forEach(n=>n.classList.toggle('point-highlight',n.dataset.pointRow===p.candidate_id));if(row){row.closest('details').open=true;row.scrollIntoView({block:'nearest',behavior:'smooth'});}void save();
  }
  function drawImportFilter(){
    const host=$('importFilterReport');host.replaceChildren();host.hidden=!dataset;if(!dataset)return;
    const stats=ObservationDiagnostics.summarize(points()),legacy=dataset.legacy_import_filter||dataset.import_filter;
    host.append(el('summary',`数据诊断：当前${stats.point_count}点 · 双阵时刻组${stats.strict_dual_group_count}组 · 单阵时刻组${stats.single_array_group_count}组${legacy?.excluded?.length?'（旧快照曾排除'+legacy.excluded.length+'点，展开查看）':''}`),el('p','单点、单阵和不同时间到达的有效观测都保留。严格同平台同时间仅用于统计，不代表已配对同一目标，也不是接收或关联的门槛。'));
    if(legacy?.excluded?.length)host.append(el('p',`此旧快照曾按旧规则排除${legacy.excluded.length}点，本次没有擅自补入已有分析。请重新打开原始数据文件以完整保留；旧结果不会被改写。`,'association-warning'));
  }
  function updateSamplingMode(){
    const paired=$('samplePointMode').value==='source_pairs',grouped=$('samplePointMode').value==='synchronous_groups';
    if(paired){$('samplePointCountLabel').textContent='要选多少组 hull＋tow（1组算1点）';$('samplePoints').textContent='随机小间隔选双阵组';$('pointSamplingHint').textContent='默认按同数据集、同平台、同时间的1条 hull＋1条 tow 成组。填20即选20组、40条原始观测，按时间分散并小幅随机浮动；缺阵或多个候选不强行凑对，可手动勾选。替换当前浏览范围的选择，保留其他分组选择。来源仅用于测试抽样，不发送模型身份标签。';return;}
    $('samplePointCountLabel').textContent=grouped?'要选多少组同步时刻':'要选多少个单独观测点';
    $('samplePoints').textContent=grouped?'按同步时刻组抽样':'随机小间隔选点';
    $('pointSamplingHint').textContent=grouped?'仅作可选对照：抽取同平台同时间的完整双阵候选组，保留组内所有点，不认定目标配对；实际点数可能多于组数。单阵点仍在原数据中。替换当前分组选择，保留其他分组已选点。':'填写的是单独观测点数，不是双阵组数，也不是每条轨迹的点数。按时间分散抽取，位置约±20%浮动，多点时保留首尾；可产生单阵/不同步结果，这属于有效稀疏测试。替换当前分组选择，保留其他分组已选点。';
  }
  function drawSelectionDiagnostics(){
    const stats=ObservationDiagnostics.summarize(selectedPoints()),host=$('selectionDiagnostics');
    if(!dataset){host.textContent='';return;}
    if(!stats.point_count){host.textContent='尚未选择观测点；单点、批次和缺阵输入均可保留。';return;}
    host.textContent=`当前已选${stats.point_count}个单点（hull ${stats.hull_point_count} / tow ${stats.tow_point_count}），严格同步双阵${stats.strict_dual_group_count}组。`+(stats.strict_dual_group_count?'同步组不等于同目标配对，也不保证可空间定位。':'未选到严格同步不代表数据无效，可结合历史做片段与归属分析。')+'本轮完整证据与能力以免费准备结果为准。';
  }
  // Display-only source labels: never add colors/membership to points, state or prompts.
  const spatialHypothesisOptions=view=>({mirrorSide:$('sequenceMirrorSide').value,edges:view?.continuation_graph?.edges,membership:view?.membership,autoConnect:$('sequenceAutoConnect').checked,modelOnly:sequenceRun?.spec?.execution_mode==='batch-snapshot-v1',maxGapSeconds:sequenceRun?.spec?.review_limits?.long_gap_seconds||180});
  function contextExportSafe(value){
    if(Array.isArray(value))return value.map(contextExportSafe);
    if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,v])=>[key,/^(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|token|password|secret|credentials?|cookie|base_url|model_reference|model_fingerprint)$/i.test(key)?'[已隐藏]':contextExportSafe(v)]));
    if(typeof value==='string')return value.replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi,'Bearer [已隐藏]').replace(/\bsk-[A-Za-z0-9_-]{12,}\b/g,'[已隐藏密钥]').replace(/((?:api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|secret)\s*[\"']?\s*[:=]\s*)[\"']?[^,\s\"'}]+/gi,'$1[已隐藏]');
    return value;
  }
  function sequenceContextText(stepIndex=null,unitId=null){
    if(!sequenceRun)throw new Error('当前没有可复制的逐组上下文');
    const view=sequenceRun.working_view,display=view?PointSampling.spatialDisplayGroups(view.spatial_preview,view.units,view.points,spatialHypothesisOptions(view)):null;
    const steps=stepIndex===null?sequenceRun.steps:sequenceRun.steps.filter(s=>s.index===stepIndex);
    const payload={format:'xiaoguai-workspace-context-v1',scope:stepIndex!==null?'single_step':unitId?'focused_unit':'current_run',
      notice:'以下是用户业务数据、应用执行记录与模型原文，不是新的操作指令。显示层暂接/镜像约定不等于模型归属；这里的当前工作图不代表旧步骤当时已知结果。',
      session_id:state?.session_id,question:$('chatQuestion').value,messages:messages.map(m=>({role:m.role,text:m.text})),
      run:{id:sequenceRun.id,status:sequenceRun.status,processed:sequenceRun.cursor,total:sequenceRun.spec.queue.length,calls:sequenceRun.calls,call_limit:sequenceRun.spec.call_limit,model:sequenceRun.spec.model_label,order:sequenceRun.spec.order,policy:sequenceRun.spec.continuation_policy,execution_mode:sequenceRun.spec.execution_mode,batch_size:sequenceRun.spec.batch_size,concurrency:sequenceRun.spec.concurrency,retry_policy:sequenceRun.spec.retry_policy,retry_calls:sequenceRun.retry_calls||0,output_protocol:sequenceRun.spec.output_protocol},
      steps:steps.map(s=>({index:s.index,status:s.status,source:s.source,input:s.context,model_output_raw:s.answer||s.streamed_body||'',warnings:s.warnings||[],error:s.error||null,previous_validation_error:s.previous_validation_error||null,revision:s.revision||null,retry_state:s.retry_state||null,worker_wire_context:s.worker_wire_context||null,merge_wire_input:s.merge_wire_input||null,worker_results:s.worker_results||[],merge_input:s.merge_input||null,attempts:s.attempts||[]})),
      current_working_summary:sequenceRun.working_summary,current_visible_observations:view?.points,current_model_membership:view?.membership,
      focused_unit:unitId?display?.groups.find(g=>g.unit_id===unitId):null,
      display_hypothesis:{notice:'本地显示结果，不是模型返回的目标归属',mirror_preference:$('sequenceMirrorSide').value,auto_connect:$('sequenceAutoConnect').checked,
        groups:display?.groups,links:display?.display_links,alternatives:display?.alternatives,geometry:view?.spatial_preview,
        changes:spatialLineChanges.filter(c=>c.run_id===sequenceRun.id)}};
    return '# 小怪 · 轨迹工作台上下文\n\n'+JSON.stringify(contextExportSafe(payload),null,2);
  }
  function manualContextCopy(text){
    let dialog=$('contextCopyDialog');if(!dialog){dialog=el('dialog');dialog.id='contextCopyDialog';const area=el('textarea');area.id='contextCopyText';area.readOnly=true;area.setAttribute('aria-label','待复制业务上下文');const actions=el('div',undefined,'actions'),select=el('button','全选'),close=el('button','关闭');select.onclick=()=>{area.focus();area.select();};close.onclick=()=>dialog.close();actions.append(select,close);dialog.append(el('h3','复制业务上下文'),el('p','浏览器限制了剪贴板写入。请全选后按 Ctrl+C；原数据不会修改。'),area,actions);document.body.append(dialog);}
    $('contextCopyText').value=text;dialog.showModal();$('contextCopyText').focus();$('contextCopyText').select();
  }
  async function copySequenceContext(button,stepIndex=null,unitId=null){
    try{const text=sequenceContextText(stepIndex,unitId);let copied=false;
      try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(text);copied=true;}}catch{}
      if(!copied){const area=el('textarea');area.value=text;area.style.cssText='position:fixed;left:0;top:0;opacity:0';document.body.append(area);area.focus();area.select();try{copied=!!document.execCommand('copy');}catch{}area.remove();}
      if(!copied){manualContextCopy(text);return;}
      const label=button.querySelector('.context-copy-label');if(label)label.textContent='已复制';else button.textContent='✓ 已复制';note('已复制业务上下文；不含连接配置或认证字段，未调用模型。');
    }catch(error){note('复制失败：'+error.message);}
  }
  function contextCopyButton(label,stepIndex=null,unitId=null){
    const button=el('button',undefined,'context-copy-button');button.type='button';button.title=label;button.setAttribute('aria-label',label);button.dataset.copyScope=stepIndex!==null?'step':unitId?'unit':'all';
    const icon=svgNode('svg',{viewBox:'0 0 24 24',width:17,height:17,fill:'none',stroke:'currentColor','stroke-width':1.5},button);svgNode('rect',{x:8,y:3,width:13,height:13,rx:3},icon);svgNode('rect',{x:3,y:8,width:13,height:13,rx:3},icon);button.append(el('span','复制','context-copy-label'));button.onclick=()=>copySequenceContext(button,stepIndex,unitId);return button;
  }
  function recordSpatialLineChanges(display){
    if(!sequenceRun)return;
    const next={run_id:sequenceRun.id,edges:Object.fromEntries(display.display_links.map(e=>[JSON.stringify([e.from,e.to]),e])),points:Object.fromEntries(display.groups.filter(g=>g.selected).map(g=>[g.unit_id,{x:g.selected.point.x,y:g.selected.point.y,handedness:g.selected.handedness}]))};
    const old=spatialLineSnapshot?.run_id===next.run_id?spatialLineSnapshot:null;
    const added=Object.keys(next.edges).filter(k=>!old?.edges[k]).map(k=>next.edges[k]),removed=Object.keys(old?.edges||{}).filter(k=>!next.edges[k]).map(k=>old.edges[k]);
    const updated=Object.keys(next.edges).filter(k=>old?.edges[k]&&old.edges[k].basis!==next.edges[k].basis).map(k=>next.edges[k]);
    const switched=Object.keys(next.points).filter(k=>old?.points[k]&&Math.hypot(next.points[k].x-old.points[k].x,next.points[k].y-old.points[k].y)>1e-6).map(k=>({unit_id:k,from:old.points[k],to:next.points[k]}));
    if(added.length||removed.length||switched.length||updated.length){spatialLineChanges.push({run_id:sequenceRun.id,step:sequenceRun.cursor,reason:spatialLineReason==='manual'?'用户切换显示假设':'已处理新组/工作版本更新后的本地重算',added,removed,updated,switched});}
    spatialLineSnapshot=next;spatialLineReason='auto';
    const host=$('sequenceSpatialLinkChanges');host.replaceChildren();for(const change of spatialLineChanges.filter(c=>c.run_id===sequenceRun.id)){const row=el('details');row.append(el('summary',`第${change.step}步 · 新接${change.added.length}／撤线${change.removed.length}／更换位置${change.switched.length}／校验升级${change.updated.length}`),el('pre',JSON.stringify(change,null,2)));host.append(row);}
    const local=display.display_links.filter(e=>e.basis==='local_provisional').length;
    $('sequenceSpatialLinkSummary').textContent=`已校验接续${display.display_links.length-local}条；本地暂接${local}条（不是模型归属或可靠目标路线）。后续新组自动重算，改线及镜像更换留存。${display.auto_notice||''}`;
    if(added.length||removed.length||switched.length||updated.length)void save();
  }
  function drawSequenceSpatial(view){
    const svg=$('sequenceSpatialChart'),legend=$('sequenceSpatialLegend'),hover=$('sequenceSpatialHover');svg.replaceChildren();legend.replaceChildren();hover.hidden=true;
    const doc=view?.spatial_preview,valid=doc?.protocol==='sequential-spatial-v1',modelBranches=doc?.branch_selection_source==='model';$('sequenceMirrorSide').disabled=!!modelBranches;$('sequenceMirrorSide').closest('label').hidden=!!modelBranches;$('sequenceAutoConnect').disabled=!!modelBranches;if(modelBranches)$('sequenceAutoConnect').checked=true;$('sequenceAutoConnectLabel').textContent=modelBranches?'按模型快照接线和更新，不执行本地暂接':'自动暂接并随新组重算（本地显示假设，非模型归属）';
    $('sequenceSpatialStatus').textContent=valid?(doc.status==='conditional_candidates'?(modelBranches?'每组位置由模型明确选择的候选方案引用；分支与目标归属同版本保存，前端不再按最近点或左右约定换边。仍是可修订的工作假设，不是唯一定位。':'每组双阵采用一个位置假设。有已处理接续历史则比较连续性；无法区分镜像时按明确的显示约定暂选，可切换，不代表目标或位置已确认。'):doc.reason)+(doc.origin_lat_lon?` 坐标原点：${doc.origin_lat_lon[0]}°N，${doc.origin_lat_lon[1]}°E。`:''):'当前后端未提供逐组空间计算数据，请待调用结束后重启后端并刷新；不会用方位角补距离。';
    const display=PointSampling.spatialDisplayGroups(doc,view?.units||[],view?.points||[],spatialHypothesisOptions(view)),tracks=display.tracks,platforms=doc?.platforms||[];$('sequenceSpatialLegendTitle').textContent=`主图位置图例（${display.plotted}组，不重复镜像）`;const all=[...tracks.flatMap(t=>t.points),...platforms.flatMap(p=>p.points)];
    recordSpatialLineChanges(display);
    const groupHost=$('sequenceSpatialGroupList'),openUnits=new Set([...groupHost.children].filter(n=>n.open).map(n=>n.dataset.unitId));groupHost.replaceChildren();$('sequenceSpatialGroupTitle').textContent=`双阵组位置：${display.plotted}组可显示，${display.pending}组无法放置（展开组查看全部候选）`;
    const rawLookup=new Map((view?.points||[]).map(p=>[p.candidate_id,p]));const labels={provisional:'暂定采用一个位置（镜像未解除）',unstable_provisional:'暂定采用一个位置（交会不稳定）',unvalidated_provisional:'本步未校验的位置展示假设',position_ready:'唯一几何位置（仍依赖输入配对假设）',mirror_ambiguous:'位置待定：存在镜像歧义',unstable:'位置待定：交点不稳定',unavailable:'位置待定：无法定位',pending_validation:'位置待定：本步未通过归属校验'};
    for(const group of display.groups){const row=el('details');row.dataset.unitId=group.unit_id;row.dataset.positionStatus=group.status;row.open=openUnits.has(group.unit_id);row.append(el('summary',`${group.t??'未知'}秒 · ${labels[group.status]}${group.isolated?' · 孤立未接线':''}${group.distinct_positions>1?'（'+group.distinct_positions+'个可能位置）':''}`),el('pre',JSON.stringify({status:labels[group.status],model_assignment:group.model_assignment,connection_note:group.connection_note,incident_links:group.incident_links,selection_basis:group.selection_basis,adopted_candidate:group.selected,reason:group.reason,observations:PointSampling.groupDetail(group.candidate_ids.map(id=>rawLookup.get(id)).filter(Boolean)),candidates:group.candidates},null,2)));row.append(contextCopyButton('复制此组上下文',null,group.unit_id));groupHost.append(row);}
    $('sequenceSpatialStatus').textContent+=modelBranches?` 主图${display.plotted}个模型选定工作位置；${display.pending}组无可用模型位置。分支与归属同版本保存，前端不代选，不代表镜像已唯一消除。`:` 主图${display.plotted}个位置点；${display.pending}组缺少交点。镜像约定只是显示假设，不是模型解歧结果。`;
    $('sequenceSpatialDiagnostics').textContent=JSON.stringify({status:doc?.status,unavailable:doc?.unavailable||[],unstable:(doc?.tracks||[]).flatMap(t=>t.points.filter(p=>!p.stable).map(p=>({branch:t.handedness,t:p.t,unit_id:p.unit_id,reason:p.warning,sensitivity_m:p.sensitivity_m})))},null,2);
    $('sequenceSpatialUnavailable').hidden=!doc?.unavailable?.length&&!(doc?.tracks||[]).some(t=>t.points.some(p=>!p.stable));
    for(const id of ['sequenceSpatialZoomIn','sequenceSpatialZoomOut','sequenceSpatialReset'])$(id).disabled=!all.length;
    const W=Math.max(260,svg.clientWidth||450),H=320;svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
    const node=(tag,attrs)=>svgNode(tag,attrs,svg);
    if(!valid||!all.length){node('text',{x:18,y:70,fill:'#b8c8d4','font-size':12}).textContent=valid?'尚无可计算空间交点，请查看平台配置与双阵几何说明。':'等待逐组空间数据。';return;}
    const minX=Math.min(...all.map(p=>p.x)),maxX=Math.max(...all.map(p=>p.x)),minY=Math.min(...all.map(p=>p.y)),maxY=Math.max(...all.map(p=>p.y));
    const midX=(W+35)/2,midY=(H-15)/2,cx=(minX+maxX)/2+sequenceSpatialView.dx,cy=(minY+maxY)/2+sequenceSpatialView.dy;
    const scale=.86*Math.min((W-75)/Math.max(10,maxX-minX),(H-65)/Math.max(10,maxY-minY))*sequenceSpatialView.zoom;sequenceSpatialView.scale=scale;
    const x=p=>midX+(p.x-cx)*scale,y=p=>midY-(p.y-cy)*scale;
    for(let i=0;i<=4;i++){const xx=55+(W-75)*i/4,yy=H-40-(H-65)*i/4;node('line',{x1:xx,y1:25,x2:xx,y2:H-40,stroke:'#2e394a','stroke-dasharray':'2 4'});node('line',{x1:55,y1:yy,x2:W-20,y2:yy,stroke:'#2e394a','stroke-dasharray':'2 4'});node('text',{x:xx,y:H-19,fill:'#9daebe','font-size':10,'text-anchor':'middle'}).textContent=((xx-midX)/scale+cx).toFixed(0);node('text',{x:4,y:yy+4,fill:'#9daebe','font-size':10}).textContent=((midY-yy)/scale+cy).toFixed(0);}
    node('text',{x:8,y:16,fill:'#b7cad9','font-size':11}).textContent='北向（米）';node('text',{x:W-90,y:H-3,fill:'#b7cad9','font-size':11}).textContent='东向（米）';
    for(const platform of platforms){node('polyline',{points:platform.points.map(p=>`${x(p)},${y(p)}`).join(' '),fill:'none',stroke:'#ffe15c','stroke-dasharray':'3 3'});for(const p of platform.points){const dot=node('rect',{x:x(p)-3,y:y(p)-3,width:6,height:6,fill:'#ffe15c','data-platform-id':platform.id});svgNode('title',{},dot).textContent=`平台${platform.id} · ${p.t}秒 · 东${p.x.toFixed(1)}米 / 北${p.y.toFixed(1)}米`;}}
    if(!display.plotted&&display.pending)node('text',{x:60,y:45,fill:'#e6b566','font-size':11}).textContent=`${display.pending}组缺少交点，原因见详情`;
    const lookup=new Map((view.points||[]).map(p=>[p.candidate_id,p])),groupDiagnostics=new Map(display.groups.map(g=>[g.unit_id,g]));
    let newTrackColor=false;
    for(const track of tracks){
      const modelTargets=new Set(track.points.flatMap(p=>(p.candidate_ids||[]).map(id=>view?.membership?.[id]?.target_id)).filter(Boolean));
      const modelTarget=sequenceRun?.spec?.execution_mode==='batch-snapshot-v1'&&modelTargets.size===1?[...modelTargets][0]:null;
      const colorKey=JSON.stringify([sequenceRun?.id,modelTarget?'target:'+modelTarget:track.anchor_unit_id||track.hypothesis_key||track.id]);
      if(!spatialLineColors[colorKey]){const used=new Set(Object.values(spatialLineColors));let candidate=colors.find(c=>!used.has(c));let index=used.size;while(!candidate||used.has(candidate)){candidate=`hsl(${(index++*137.508)%360} 70% 65%)`;}spatialLineColors[colorKey]=candidate;newTrackColor=true;}
      const color=spatialLineColors[colorKey],chain=track.anchor_unit_id||track.id;
      for(let i=0;i<track.points.length;i++){const p=track.points[i],previous=track.points[i-1];
        if(previous&&p.connect_previous)node('polyline',{points:`${x(previous)},${y(previous)} ${x(p)},${y(p)}`,fill:'none',stroke:color,'stroke-width':2,'stroke-dasharray':'none','data-spatial-chain':chain,'data-suspect':String(!!p.suspect),'pointer-events':'none','data-line-basis':p.line_basis,'data-spatial-line':track.id});
        const dot=node('circle',{cx:x(p),cy:y(p),r:3.5,fill:p.stable?color:'#0d1322',stroke:color,'stroke-width':1.5,tabindex:0,role:'button','aria-label':`${p.t}秒 · 东向${p.x.toFixed(1)}米 · 北向${p.y.toFixed(1)}米 · 空间候选`,'data-spatial-chain':chain,'data-spatial-candidate':track.id,'data-unit-id':p.unit_id,'data-stable':String(p.stable)});
        const show=event=>{const diagnostic=groupDiagnostics.get(p.unit_id);hover.textContent=`${p.t}秒 · 条件空间候选${track.id} · 当前分支${p.selected_handedness?.join('/')||'未提供'}\n东向${p.x.toFixed(2)}米 · 北向${p.y.toFixed(2)}米\n${p.selection_basis?.type==='model_selected_branch'?'采用依据：模型明确选择的冻结候选分支（仍是工作假设，镜像未唯一消除）':['continuity_from_provisional_history','local_sequence_hypothesis'].includes(p.selection_basis?.type)?'采用依据：已处理工作链的候选路径重算（本地假设，仍未确认）':p.selection_basis?.type==='unique_geometry'?'采用依据：唯一几何交点（输入配对仍为假设）':'采用依据：明确的镜像显示约定，非模型解歧结论'}\n${p.warning||'可撤回的位置假设，非唯一定位或物理身份确认。'}\n${diagnostic?.model_assignment.map(m=>`${m.array}：${m.target_id?'目标假设'+m.target_id:m.fragment_id?'片段'+m.fragment_id:m.status==='unassigned'?'Agent未归属':'尚无目标归属'}`).join('；')}\n${diagnostic?.connection_note||''}\n敏感性（米）：${p.sensitivity_m===null?'无界/存在无交点扰动':p.sensitivity_m.toFixed(2)}；角误差假设（度）：${p.angle_error_deg.join('/')}\n`+JSON.stringify(PointSampling.groupDetail(p.candidate_ids.map(id=>lookup.get(id)).filter(Boolean)),null,2);hover.hidden=false;const b=dot.getBoundingClientRect();hover.style.left=Math.max(8,Math.min((event.clientX??b.right)+12,innerWidth-hover.offsetWidth-8))+'px';hover.style.top=Math.max(8,Math.min((event.clientY??b.top)+12,innerHeight-hover.offsetHeight-8))+'px';};dot.onmouseenter=dot.onmousemove=dot.onfocus=show;dot.onmouseleave=dot.onblur=()=>{hover.hidden=true;};
      }
      const item=el('span',`${modelTarget||track.id} · ${track.points.length}个位置点（暂定轨迹）`,'legend-item');item.style.setProperty('--legend-color',color);legend.append(item);
    }
    if(newTrackColor)void save();
  }
  function drawSequenceWorkingPreview(){
    const panel=$('sequenceWorkingPreview'),svg=$('sequenceWorkingChart'),legend=$('sequenceWorkingLegend'),hover=$('sequenceWorkingHover');
    panel.hidden=!sequenceRun;svg.replaceChildren();legend.replaceChildren();$('sequenceRevisionList').replaceChildren();$('sequenceWorkingCount').textContent='0组';hover.hidden=true;if(!sequenceRun)return;
    const view=sequenceRun.working_view;
    const notice=$('sequenceCompletionNotice'),total=sequenceRun.spec.queue.length,done=sequenceRun.cursor;
    const failed=['result_unknown','failed'].includes(sequenceRun.status),oldBatch=sequenceRun.spec.execution_mode==='batch-snapshot-v1'&&!sequenceRun.spec.spatial_association_contract;
    const rows=sequenceRun.steps?.at(-1)?.worker_results||[],returned=rows.filter(r=>r.status==='returned').length,unknown=rows.filter(r=>r.status==='result_unknown').length;
    notice.hidden=!failed&&!oldBatch;
    notice.textContent=(failed?`队列未完成，不是图漏画：已成功归并${done}/${total}组。本批有效返回${returned}组，结果未知${unknown}组；其余不能当作已完成轨迹。未知调用可能已计费，不自动重发。 `:'')+(oldBatch?'本队列未启用联合空间协议，仍为旧归属＋前端镜像；勾选框不能升级已执行的旧队列。新建会话可使用文件库原数据，默认启用新版。':'');
    drawSequenceSpatial(view);
    if(!view||view.protocol!=='working-view-v1'){$('sequenceWorkingStatus').textContent='后端尚未提供逐组显示数据，请在当前调用结束后重启后端并刷新。';svg.hidden=true;return;}
    svg.hidden=false;
    const lookup=new Map(view.points.map(p=>[p.candidate_id,p])),membership=view.membership,markers=[];
    for(const unit of view.units){const records=unit.candidate_ids.map(id=>lookup.get(id)).filter(Boolean);if(!records.length)continue;
      const point=records.find(p=>p.array==='hull')||records[0],m=membership[point.candidate_id]||{},targets=new Set(records.map(p=>membership[p.candidate_id]?.target_id).filter(Boolean));
      const disputed=records.some(p=>membership[p.candidate_id]?.status==='ambiguous')||targets.size>1;
      const associated=unit.validated!==false&&m.status==='associated'&&!disputed,key=associated?JSON.stringify([m.target_id?'target':'fragment',m.target_id||m.fragment_id,point.platform_id,point.array]):null;
      const id=m.target_id||m.fragment_id,label=key?(m.target_id?'目标假设 ':'片段 ')+id+' / '+point.array:unit.validated===false?'本步待复核（未接轨）':disputed?'待决／跨阵归属不一致':'未归属／起始待定';
      let hash=0;for(const char of key||'')hash=(Math.imul(hash,31)+char.charCodeAt(0))>>>0;
      markers.push({point,records,unit,key,label,color:key?`hsl(${hash%360} 65% 65%)`:'#8b999f'});
    }
    markers.sort((a,b)=>a.point.t-b.point.t||a.unit.unit_id.localeCompare(b.unit.unit_id));
    const archived=sequenceRun.status==='rejected',adopted=sequenceRun.status==='adopted';
    const remaining=Math.max(0,sequenceRun.spec.queue.length-sequenceRun.cursor-(view.pending_unit_count||0));
    const positionDisplay=PointSampling.spatialDisplayGroups(view.spatial_preview,view.units,view.points,spatialHypothesisOptions(view));
    $('sequenceWorkingCount').textContent=`已处理${sequenceRun.cursor}/${sequenceRun.spec.queue.length}组 · 位置假设${positionDisplay.plotted}个（暂定${positionDisplay.provisional}） · 缺定位${positionDisplay.pending}组`;
    const queuedRaw=new Set(sequenceRun.spec.queue.flatMap(u=>u.candidate_ids)).size;
    $('sequenceWorkingStatus').textContent=(archived?'已放弃的历史工作版本，不是当前确认轨迹。':adopted?'本次集合已由你确认；仍须保留几何与身份限制。':'工作预览，尚未人工确认。')+`队列${sequenceRun.spec.queue.length}组＝${queuedRaw}条原始观测；已校验${sequenceRun.cursor}组${view.pending_unit_count?'，另'+view.pending_unit_count+'组处理中/失败待复核':''}，尚未执行${remaining}组。当前可见${view.units.length}组/${view.points.length}条观测（含历史）；主图${positionDisplay.plotted}个位置假设（${positionDisplay.provisional}个暂定），${positionDisplay.pending}组缺少定位。未执行组不会假装已处理；未通过校验的展示点不接轨；镜像显示约定不改变模型归属。`;
    const W=Math.max(260,svg.clientWidth||400),H=230,node=(tag,attrs,parent=svg)=>svgNode(tag,attrs,parent);svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
    if(!markers.length){node('text',{x:18,y:65,fill:'#a0b2aa','font-size':12}).textContent='等待第一组完成；有观测即显示，不要求先形成轨迹。';return;}
    const t0=markers[0].point.t,t1=Math.max(t0+1,markers.at(-1).point.t),x=t=>45+(t-t0)/(t1-t0)*(W-65),y=b=>H-35-b/180*(H-55);
    for(let a=0;a<=180;a+=30){node('line',{x1:45,y1:y(a),x2:W-20,y2:y(a),stroke:'#33423e','stroke-dasharray':'3 5'});node('text',{x:6,y:y(a)+4,fill:'#9caca7','font-size':11}).textContent=a+'°';}
    const ticks=Math.max(2,Math.min(5,Math.floor(W/140)));for(let i=0;i<=ticks;i++){const t=t0+(t1-t0)*i/ticks;node('text',{x:x(t),y:H-12,fill:'#9caca7','font-size':10,'text-anchor':i===0?'start':i===ticks?'end':'middle'}).textContent=t.toFixed(1)+'秒';}
    const edgeMap=new Map((view.continuation_graph?.edges||[]).map(e=>[JSON.stringify([e.from_id,e.to_id]),e]));
    const hasEdges=Array.isArray(view.continuation_graph?.edges);const streams=new Map(),shown=new Map();
    for(const marker of markers){if(marker.key){if(!streams.has(marker.key))streams.set(marker.key,[]);streams.get(marker.key).push(marker);}shown.set(marker.key||marker.label,marker);}
    for(const [key,stream]of streams){const counts=new Map();for(const m of stream)counts.set(m.point.t,(counts.get(m.point.t)||0)+1);let run=[];
      const flush=()=>{if(run.length>1)node('polyline',{points:run.map(m=>`${x(m.point.t)},${y(m.point.bearing_deg)}`).join(' '),fill:'none',stroke:run[0].color,'stroke-width':1.5,'stroke-dasharray':adopted?'none':'4 3','pointer-events':'none','data-working-line':key,'data-member-ids':JSON.stringify(run.map(m=>m.point.candidate_id))});run=[];};
      if(hasEdges){
        let style=null;const flushEdge=()=>{if(run.length>1){const suspect=style==='suspect';node('polyline',{points:run.map(m=>`${x(m.point.t)},${y(m.point.bearing_deg)}`).join(' '),fill:'none',stroke:suspect?'#f1b65d':run[0].color,'stroke-width':suspect?2.5:1.5,'stroke-opacity':style==='low_confidence'?.5:1,'stroke-dasharray':suspect?'2 5':style==='low_confidence'?'3 6':'5 3','data-edge-status':style,'data-working-line':key,'data-member-ids':JSON.stringify(run.map(m=>m.point.candidate_id))});}run=[];};
        for(let i=1;i<stream.length;i++){const a=stream[i-1],b=stream[i],edge=edgeMap.get(JSON.stringify([a.point.candidate_id,b.point.candidate_id]));if(!edge||counts.get(a.point.t)>1||counts.get(b.point.t)>1){flushEdge();style=null;continue;}if(style!==edge.status||run.at(-1)!==a){flushEdge();style=edge.status;run=[a];}run.push(b);}flushEdge();
      }else{for(const m of stream){if(counts.get(m.point.t)>1){flush();continue;}run.push(m);}flush();}
    }
    for(const m of markers){const p=m.point,dot=node('circle',{cx:x(p.t),cy:y(p.bearing_deg),r:m.unit.current_run?4:3,fill:m.color,stroke:m.color,'stroke-width':1.5,tabindex:0,role:'button','data-candidate-id':p.candidate_id,'data-unit-id':m.unit.unit_id,'data-members':JSON.stringify(m.records.map(q=>q.candidate_id)),'data-association-key':m.key||'','data-current-run':String(m.unit.current_run),'data-validated':String(m.unit.validated!==false),'aria-label':`${p.t}秒 · ${m.label} · 查看本组完整信息`});
      const show=event=>{const associations=m.records.map(q=>q.array+': '+(membership[q.candidate_id]?.target_id||membership[q.candidate_id]?.fragment_id||'待定')).join('；');hover.textContent=`${p.t}秒 · ${m.unit.current_run?'本次已处理':'历史支持'} · ${m.label}\n${hasEdges?'暂接策略：低置信虚线可撤回，橙色为疑点；不是身份确认。':'旧版工作预览。'}\n${associations}\n`+JSON.stringify(PointSampling.groupDetail(m.records),null,2);hover.hidden=false;const b=dot.getBoundingClientRect();hover.style.left=Math.max(8,Math.min((event.clientX??b.right)+12,innerWidth-hover.offsetWidth-8))+'px';hover.style.top=Math.max(8,Math.min((event.clientY??b.top)+12,innerHeight-hover.offsetHeight-8))+'px';};
      dot.onmouseenter=dot.onmousemove=dot.onfocus=show;dot.onmouseleave=dot.onblur=()=>{hover.hidden=true;};
    }
    for(const revision of view.revisions||[]){const row=el('details');row.append(el('summary',`版本${revision.version} · ${revision.mode==='batch-snapshot-v1'?'模型批次快照':revision.mode==='local_review'?'疑点局部修订':'暂接'} · 归属变化${revision.changes?.length||0}条 · 撤回旧边${(revision.withdrawn_edges||revision.removed_edges||[]).length}条`),el('pre',JSON.stringify(revision,null,2)));$('sequenceRevisionList').append(row);}
    if(view.pending_review_requests?.length)$('sequenceRevisionList').prepend(el('p','存在Agent请求复核的历史片段，尚不能确认；队列结束后可免费准备复核，发送需重新授权。','association-warning'));
    for(const m of shown.values()){const item=el('span',m.label,'legend-item');item.style.setProperty('--legend-color',m.color);legend.append(item);}
  }
  function drawSelectedSourcePreview(){
    const chosen=selectedPoints(),svg=$('selectedSourceChart'),legend=$('selectedSourceLegend'),hover=$('selectedSourceHover');
    svg.replaceChildren();legend.replaceChildren();hover.hidden=true;
    const pairs=PointSampling.sourcePairGroups(chosen,catalog());
    $('selectedSourceCount').textContent=`${pairs.length}组双阵 · ${chosen.length}条观测${chosen.length-pairs.length*2?'（含'+(chosen.length-pairs.length*2)+'条未成组）':''}`;
    const membership=new Map();
    catalog().forEach((group,index)=>{
      const entry={key:'source-'+index,label:group.target_id||`数据集 ${index+1}`,color:colors[index]||`hsl(${(index*137.508)%360} 65% 65%)`};
      for(const id of new Set(group.candidate_ids||[])){if(!membership.has(id))membership.set(id,[]);membership.get(id).push(entry);}
    });
    const unknown={key:'unknown',label:'未标注来源',color:'#9aa9b4'},overlap={key:'overlap',label:'多个来源分组（不指定归属）',color:'#b8b8b8'};
    const source=p=>{const matches=membership.get(p.candidate_id)||[];return matches.length===1?matches[0]:matches.length?overlap:unknown;};
    const shown=new Map();
    const W=Math.max(300,svg.clientWidth||$('chatMessages').clientWidth||800),H=230;
    svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
    const node=(tag,attrs,parent=svg)=>svgNode(tag,attrs,parent);
    if(!chosen.length){node('text',{x:25,y:55,fill:'#9caca7','font-size':13}).textContent='请在左侧勾选观测点；这里只显示当前勾选，不显示历史未勾选点。';return;}
    const t0=chosen[0].t,t1=Math.max(t0+1,chosen.at(-1).t),x=t=>48+(t-t0)/(t1-t0)*(W-70),y=b=>H-38-b/180*(H-60);
    for(let a=0;a<=180;a+=30){node('line',{x1:48,y1:y(a),x2:W-22,y2:y(a),stroke:'#33423e','stroke-dasharray':'3 5'});node('text',{x:8,y:y(a)+4,fill:'#9caca7','font-size':12}).textContent=a+'°';}
    const ticks=Math.max(2,Math.min(5,Math.floor(W/150)));
    for(let i=0;i<=ticks;i++){const t=t0+(t1-t0)*i/ticks;node('text',{x:x(t),y:H-12,fill:'#9caca7','font-size':11,'text-anchor':i===0?'start':i===ticks?'end':'middle'}).textContent=t.toFixed(1)+'秒';}
    const markers=PointSampling.groupMarkers(chosen,catalog());
    for(const {point:p,observations:records} of markers){
      const entry=source(p);shown.set(entry.key,{...entry,count:(shown.get(entry.key)?.count||0)+1});
      const dot=node('circle',{cx:x(p.t),cy:y(p.bearing_deg),r:4,fill:entry.color,stroke:entry.color,'stroke-width':2,'data-candidate-id':p.candidate_id,'data-source-key':entry.key,'data-group-size':records.length,'data-member-ids':JSON.stringify(records.map(p=>p.candidate_id)),tabindex:0,'aria-label':`${entry.label} · ${p.t}秒 · ${records.length===2?'hull＋tow 双阵组':p.array}，聚焦查看完整观测`});
      const show=event=>{
        hover.textContent=`${entry.label} · ${p.t}秒 · ${records.length===2?'hull＋tow 双阵组':'未成组观测'}\n`+JSON.stringify(PointSampling.groupDetail(records),null,2);hover.hidden=false;
        const bounds=dot.getBoundingClientRect(),px=event.clientX??bounds.right,py=event.clientY??bounds.top;
        hover.style.left=Math.max(8,Math.min(px+14,innerWidth-hover.offsetWidth-8))+'px';hover.style.top=Math.max(8,Math.min(py+14,innerHeight-hover.offsetHeight-8))+'px';
      };
      dot.onmouseenter=dot.onmousemove=dot.onfocus=show;dot.onmouseleave=dot.onblur=()=>{hover.hidden=true;};
    }
    for(const entry of shown.values()){const item=el('span',`${entry.label} · ${entry.count}个标记`,'legend-item');item.style.setProperty('--legend-color',entry.color);item.dataset.sourceKey=entry.key;legend.append(item);}
  }
  function drawChart(){drawSelectedSourcePreview();$('platformScene').replaceChildren();$('bearingLegend').replaceChildren();const svg=$('predictionChart');svg.replaceChildren();const observations=new Map([...(state?.observations||[]),...selectedPoints()].map(p=>[p.candidate_id,p]));const plotTracks=state?.confirmed_tracks||(state?.status==='awaiting_review'?{}:state?.tracks)||{};const hasConfirmed=Object.keys(plotTracks).length>0;$('sharedTrajectoryScene').hidden=!observations.size;$('trajectorySceneTitle').textContent='方位—时间'+(hasConfirmed?(state?.status==='awaiting_review'?' · 上一确认版本':' · 已确认观测'):' · 待分析观测');const all=[...observations.values()];if(!all.length){svgNode('text',{x:40,y:60,fill:'#8d9ba5'}).textContent='自由选择观测点；首次提交前不建立轨迹或连线。';return;}
    const W=Math.max(240,$('predictionChart').clientWidth||$('sharedTrajectoryScene').clientWidth||$('chatMessages').clientWidth||600),H=210;svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
    const t0=Math.min(...all.map(p=>p.t)),t1=Math.max(t0+1,...all.map(p=>p.t)),x=t=>45+(t-t0)/(t1-t0)*(W-65),y=b=>H-35-b/180*(H-55);
    for(let a=0;a<=180;a+=30){svgNode('line',{x1:45,y1:y(a),x2:W-20,y2:y(a),stroke:'#2c373e','stroke-dasharray':'3 5'});svgNode('text',{x:10,y:y(a)+4,fill:'#8d9ba5','font-size':12}).textContent=a+'°';}
    const ticks=Math.max(2,Math.min(5,Math.floor(W/150)));for(let i=0;i<=ticks;i++){let t=t0+(t1-t0)*i/ticks;svgNode('text',{x:x(t),y:H-12,fill:'#8d9ba5','font-size':11,'text-anchor':i===0?'start':i===ticks?'end':'middle'}).textContent=t.toFixed(1)+'秒';}
    const circle=(p,c,r=4,hollow=false,records=[p])=>{const n=svgNode('circle',{cx:x(p.t),cy:y(p.bearing_deg),r,fill:hollow?'#161e24':c,stroke:c,'stroke-width':1.5,'data-kind':'observation','data-group-size':records.length,'data-member-ids':JSON.stringify(records.map(p=>p.candidate_id)),'data-candidate-id':p.candidate_id,'data-time':p.t,tabindex:0,role:'button','aria-label':pointSummary(p)});n.classList.toggle('point-active',p.candidate_id===inspectedPointId);const title=svgNode('title',{},n);title.textContent=pointSummary(p);n.onmouseenter=()=>showPointHover(p,records);n.onmouseleave=()=>{$('pointHover').hidden=true;};n.onfocus=()=>showPointHover(p,records);n.onblur=()=>{$('pointHover').hidden=true;};n.onclick=()=>inspectPoint(p);n.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();inspectPoint(p);}};};
    const pending=state?.status==='awaiting_review';
    const excluded=new Set([...(state?.confirmed_unassigned_ids||(pending?[]:state?.unassigned_ids)||[]),...(state?.confirmed_ambiguities||[]).map(a=>a.candidate_id)]);
    const pointColors=new Map();
    for(const [tid,track]of Object.entries(plotTracks)){
      const physical=Object.entries(state?.confirmed_targets||{}).find(([,t])=>t.legacy_track_ids?.includes(tid))?.[0];
      const c=color(physical||tid),accepted=track.history.filter(p=>observations.has(p.candidate_id)&&!excluded.has(p.candidate_id));
      if(!accepted.length)continue;
      for(const p of accepted)pointColors.set(p.candidate_id,c);
      const legend=el('span',(physical?physical+' · ':'')+tid.replace(/^T/,'F')+' / '+accepted[0].array,'legend-item');legend.style.setProperty('--legend-color',c);$('bearingLegend').append(legend);
    }
    // This top panel is an observation preview, never a fitted or forecast path.
    // One marker per complete display group; never average bearings or change facts.
    for(const {point:p,observations:records} of PointSampling.groupMarkers(all,catalog())){
      const cs=records.map(q=>pointColors.get(q.candidate_id)),c=cs.every(v=>v===cs[0])?cs[0]:null;
      circle(p,c||'#667783',c?3.5:3.,false,records);
    }
    drawPlatform();
    if(inspectedPointId){const p=observations.get(inspectedPointId);if(p)renderPointCard(p);else{inspectedPointId=null;$('pointCard').hidden=true;}}
  }
  function drawBearingResult(){
    const record=(state?.rounds||[]).filter(r=>r.review_status==='adopted'&&r.model_plot?.source==='model').at(-1),svg=$('bearingResultChart');svg.replaceChildren();$('bearingResultScene').hidden=!record;
    if(!record)return;
    const tracks=Object.entries(record.tracks||{}).filter(([,track])=>track.plot_source==='model');
    const actual=tracks.flatMap(([,track])=>track.history),curves=tracks.flatMap(([,track])=>(track.model_segments||[]).filter(s=>s.source==='model_fit').map(s=>s.curve).filter(c=>c.length>=2)),predictions=tracks.flatMap(([,track])=>track.prediction||[]);
    $('bearingResultStatus').textContent=`${record!==state.rounds.at(-1)||liveActive?'历史诊断版本；非本轮新结果。 ':''}实际支持${actual.length}点，模型拟合${curves.length}段、预测${predictions.length}点。`+(curves.length?'以下只展示模型返回并已校验的方位线，不代表东／北坐标。':'模型未返回可用拟合线，仅保留实际支持点；不会在本地补线冒充轨迹。');
    const cloud=[...actual,...curves.flat(),...predictions];if(!cloud.length)return;
    const W=Math.max(260,svg.clientWidth||700),H=220,t0=Math.min(...cloud.map(p=>p.t)),t1=Math.max(t0+1,...cloud.map(p=>p.t)),x=t=>45+(t-t0)/(t1-t0)*(W-65),y=b=>H-35-b/180*(H-55);svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
    const node=(name,attrs)=>svgNode(name,attrs,svg);
    for(const b of [0,60,120,180]){node('line',{x1:45,x2:W-20,y1:y(b),y2:y(b),stroke:'#2c373e'});node('text',{x:5,y:y(b)+4,fill:'#8d9ba5','font-size':11}).textContent=b+'°';}
    for(let i=0;i<5;i++){const t=t0+(t1-t0)*i/4;node('text',{x:x(t),y:H-10,fill:'#8d9ba5','font-size':11,'text-anchor':'middle'}).textContent=t.toFixed(1)+'秒';}
    for(const [tid,track]of tracks){const c=color(tid);for(const seg of track.model_segments||[])if(seg.source==='model_fit'&&seg.curve.length>=2)node('polyline',{points:seg.curve.map(p=>`${x(p.t)},${y(p.bearing_deg)}`).join(' '),fill:'none',stroke:c,'stroke-width':2,'data-kind':'validated-model-bearing-fit'});
      const predicted=track.prediction||[];if(predicted.length){const anchor=track.history.at(-1),sequence=[anchor,...predicted];node('polyline',{points:sequence.map(p=>`${x(p.t)},${y(p.bearing_deg)}`).join(' '),fill:'none',stroke:c,'stroke-dasharray':'5 4','data-kind':'validated-model-bearing-prediction'});}
      for(const p of track.history)node('circle',{cx:x(p.t),cy:y(p.bearing_deg),r:3,fill:p.array==='tow'?'#161e24':c,stroke:c,'data-kind':'actual-bearing-support'});
    }
  }
  function drawSpatial(){
    const svg=$('spatialChart');svg.replaceChildren();
    const latest=state?.rounds.at(-1)?.model_plot?.spatial_result;
    const completed=(state?.rounds||[]).filter(r=>r.review_status==='adopted'&&r.model_plot?.spatial_result?.tracks?.length).at(-1);
    const doc=completed?.model_plot.spatial_result||null;
    $('spatialTrajectoryScene').hidden=!doc&&!latest&&!plotPreview&&state?.rounds.at(-1)?.review_status!=='adopted';$('spatialStatusBadge').textContent=doc?(completed!==state?.rounds.at(-1)||liveActive?'历史空间版本 · 非本轮新结果':'候选 · 非唯一解'):'本轮尚无空间解';
    spatialRenderInfo=null;for(const id of ['spatialZoomIn','spatialZoomOut','spatialReset'])$(id).disabled=!doc;$('spatialLegendNote').hidden=!doc;
    $('downloadSpatial').hidden=!doc?.image_url;if(doc?.image_url)$('downloadSpatial').href=doc.image_url;
    if(!doc){svg.setAttribute('hidden','');$('spatialStatus').textContent=`当前关联版本：物理目标假设${state?.association_audit?.target_hypotheses??0}个，严格同步时刻${state?.association_audit?.strict_synchronous_groups??0}组。`+((latest?'尚无通过校验的空间轨迹。'+latest.reason:null)||(state?.status==='plot_invalid_response'?'保存的模型绘图回复未通过校验。可先免费本地复核；方位点图不是所要求的空间轨迹图。':'尚无可验证的模型空间坐标。确认集合后需单独授权空间绘图；缺少同步双阵证据时会明确说明，不能补距离。'));return;}
    svg.removeAttribute('hidden');const revision=state.session_id+':'+completed.revision+':'+doc.tracks.length;
    if(spatialRevision!==revision){spatialRevision=revision;spatialView={zoom:1,dx:0,dy:0};}
    $('spatialStatus').textContent=(completed!==state.rounds.at(-1)?'显示此前已返回的空间版本；本轮尚未形成新空间图。 ':'')+(state?.plot_status==='awaiting_association_recheck'?'模型要求先复核关联，不能重复付费绘图。 ':'')+doc.reason+(doc.unrepresented_source_tracks?.length?' 未能定位的片段：'+doc.unrepresented_source_tracks.join('、'):'');
    const W=Math.max(240,svg.clientWidth||800),H=Math.min(320,Math.max(220,W*.44));svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
    const platform=(state.platform_motion||[]).flatMap(m=>m.points.map(p=>({x:p.east_m,y:p.north_m})));const cloud=[...doc.tracks.flatMap(t=>t.points),...platform];
    const minX=Math.min(...cloud.map(p=>p.x)),maxX=Math.max(...cloud.map(p=>p.x)),minY=Math.min(...cloud.map(p=>p.y)),maxY=Math.max(...cloud.map(p=>p.y));
    const cx=(minX+maxX)/2+spatialView.dx,cy=(minY+maxY)/2+spatialView.dy;
    const scale=.82*Math.min((W-85)/Math.max(100,maxX-minX),(H-70)/Math.max(100,maxY-minY))*spatialView.zoom;
    spatialRenderInfo={scale};const x=p=>W/2+(p.x-cx)*scale,y=p=>H/2-(p.y-cy)*scale;
    const node=(tag,attrs)=>svgNode(tag,attrs,svg);
    for(let i=0;i<=5;i++){const sx=50+i*(W-75)/5,sy=H-35-i*(H-65)/5;node('line',{x1:sx,y1:30,x2:sx,y2:H-35,stroke:'#2c373e','stroke-dasharray':'2 4'});node('line',{x1:50,y1:sy,x2:W-25,y2:sy,stroke:'#2c373e','stroke-dasharray':'2 4'});node('text',{x:sx-12,y:H-15,fill:'#8d9ba5','font-size':11}).textContent=((sx-W/2)/scale+cx).toFixed(0);node('text',{x:4,y:sy+4,fill:'#8d9ba5','font-size':11}).textContent=((H/2-sy)/scale+cy).toFixed(0);}
    node('text',{x:50,y:17,fill:'#a6b5bf','font-size':12}).textContent='北向（米）';node('text',{x:W-100,y:H-1,fill:'#a6b5bf','font-size':12}).textContent='东向（米）';
    const palette=colors;const ids=[...new Set(doc.tracks.map(t=>t.id))],spatialColor=id=>/^T\d+$/.test(id)?color(id):palette[ids.indexOf(id)%palette.length];
    for(const tr of doc.tracks){const c=spatialColor(tr.id);const line=node('polyline',{points:tr.points.map(p=>`${x(p)},${y(p)}`).join(' '),fill:'none',stroke:c,'stroke-width':2.25,'stroke-dasharray':tr.branch.includes('h-')?'5 5':'none','data-spatial-track':tr.id});svgNode('title',{},line).textContent=tr.id+' / '+tr.branch+' · 空间关联候选，不是唯一目标真值';for(const p of [tr.points[0],tr.points.at(-1)])node('circle',{cx:x(p),cy:y(p),r:3,fill:c});}
    const center=platform.at(-1)||{x:0,y:0};node('rect',{x:x(center)-4,y:y(center)-4,width:8,height:8,fill:'#e6b566','data-platform-marker':'true'});
    ids.forEach((id,i)=>{node('line',{x1:W-175,y1:20+i*16,x2:W-150,y2:20+i*16,stroke:spatialColor(id),'stroke-width':2});node('text',{x:W-145,y:24+i*16,fill:'#a6b5bf','font-size':11}).textContent=id+' · 候选';});
  }
  function drawPlatform(){const host=$('platformScene');host.replaceChildren();for(const motion of state?.platform_motion||[]){host.append(el('p',`平台 ${motion.platform_id} · 配置导航路径（不是目标位置）· ${motion.speed_kn}节 / ${motion.heading_deg}°`,'note'));const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 600 150');svg.setAttribute('aria-label','平台配置导航轨迹');host.append(svg);const ps=motion.points,minX=Math.min(...ps.map(p=>p.east_m)),maxX=Math.max(minX+1,...ps.map(p=>p.east_m)),minY=Math.min(...ps.map(p=>p.north_m)),maxY=Math.max(minY+1,...ps.map(p=>p.north_m));svgNode('polyline',{points:ps.map(p=>`${25+(p.east_m-minX)/(maxX-minX)*550},${125-(p.north_m-minY)/(maxY-minY)*100}`).join(' '),fill:'none',stroke:'#334155','stroke-width':2},svg);for(const p of ps)svgNode('circle',{cx:25+(p.east_m-minX)/(maxX-minX)*550,cy:125-(p.north_m-minY)/(maxY-minY)*100,r:3,fill:'#334155'},svg);}}
  function drawResults(){
    const summary=$('predictionSummary');summary.replaceChildren();
    for(const text of [`物理目标假设${state?.association_audit?.target_hypotheses??0}个`,`片段 ${Object.keys(state?.tracks||{}).length}`,`未归属 ${state?.unassigned_ids.length||0}`,`待决 ${state?.ambiguities.length||0}`])summary.append(el('span',text,'chart-metric'));
    const audit=state?.association_audit,details=state?.unassigned_details||[];const local=details.filter(d=>d.reason_code==='local_gate').length,explicit=details.filter(d=>d.reason_code==='model_unassigned').length;$('associationQualityNotice').textContent=audit?.submitted_points?`当前关联版本已提交${audit.submitted_points}点：确定接入${audit.assigned_points}点，未归属${audit.unassigned_points}点，待决${audit.ambiguous_points}点；严格同步时刻${audit.strict_synchronous_groups}组。hull接入${audit.by_array?.hull.assigned??0}/${audit.by_array?.hull.submitted??0}，tow接入${audit.by_array?.tow.assigned??0}/${audit.by_array?.tow.submitted??0}。点库中另外${audit.not_submitted_points}点尚未提交。未归属中模型明确保留${explicit}点，本地门限退回${local}点。${audit.coverage<.5?'覆盖偏低，建议检查证据；不能为提高比例而强配。':''}`:'';summary.title='目标是假设，尚非身份真值；方位—时间图不表示距离，不补造缺测观测。';
    $('roundLog').textContent=JSON.stringify(state?.prediction_ledger||[],null,2);const hist=state?.rounds.at(-1)?.input.revision_mode==='historical_replay';$('revisionNotice').textContent=hist?'历史补点复核：从受影响时刻重新关联，保留更早片段。本结果是新修订，旧预测及其采用状态保留；确认修订集合后再单独授权模型生成新图。':'先展开聊天中的执行过程核对集合、未归属与歧义；确认后单独授权第二次模型绘图调用。';
  }
  function drawStreamOutput(snapshot,block){
    const host=block.querySelector('.execution-stream-output'),body=host.querySelector('.execution-stream-body'),text=snapshot.streamed_body||'';
    host.hidden=!text;if(!text)return;
    const follow=body.scrollHeight-body.clientHeight-body.scrollTop<=32;
    if(body._streamText!==text){
      const old=body._streamText||'';
      if(text.startsWith(old))body.append(document.createTextNode(text.slice(old.length)));else body.textContent=text;
      body._streamText=text;if(follow)body.scrollTop=body.scrollHeight;
    }
    const validationFailed=(snapshot.nodes||[]).some(n=>['validate','plot_validation'].includes(n.node)&&n.status==='failed');
    host.querySelector('.execution-stream-status').textContent=validationFailed||snapshot.streamed_status==='rejected'?'模型正文已返回，但校验未通过；以下保留原文。这不是页面断流，未自动重试。':snapshot.streamed_status==='failed'?'模型调用或上游流未完成，以下保留已收到正文；请核对执行记录，未自动重试。':snapshot.streamed_status==='received'?'正文已接收完毕；以本轮集合与校验结果为准。':snapshot.streamed_status==='interrupted'?'页面流已中断，以下保留已接收正文；请刷新任务状态核对后台结果，不要重复发送。':snapshot.streamed_status==='streaming'&&liveActive?'正在追加正文；草稿尚未通过最终校验。向上滚动可阅读前文。':'已保存的接收正文；可能不完整，以后台校验结果为准。';
  }
  function recordStreamBody(status='streaming'){
    if(!liveExecutionId)return;
    const snapshot=executionHistory.get(liveExecutionId);if(!snapshot)return;
    snapshot.streamed_body=liveDraft;snapshot.streamed_status=status;
    const block=executionBlocks.get(liveExecutionId);if(block)drawStreamOutput(snapshot,block);
    if(status==='streaming'&&Date.now()-liveSaveAt>1500){liveSaveAt=Date.now();void save();}
  }
  function nodeEvidence(host,context){
    const sections=Object.entries(context.tracks||{}).map(([tid,tr])=>[tid,tr.history||[]]);
    if(context.observations)sections.push(['本轮待关联观测',context.observations]);
    if(context.unassigned?.length)sections.push(['未归属观测（仅作判断资料）',context.unassigned]);
    for(const [title,items]of sections){host.append(el('strong',`${title} · ${items.length}点`));const table=el('div',undefined,'node-point-data');for(const p of items)table.append(el('div',`${p.observation_id} | ${p.t}秒 | ${p.platform_id}/${p.array} | ${p.bearing_deg}° | ${p.quality||'质量未提供'}`));host.append(table);}
    for(const w of context.numerical_checks||[])host.append(el('p',`${w.observation_id}：${w.reason}`,'association-warning'));
  }
  function executionBlock(snapshot){
    let block=executionBlocks.get(snapshot.execution_id);
    if(!block){block=$('executionTemplate').content.firstElementChild.cloneNode(true);block.dataset.executionId=snapshot.execution_id;executionBlocks.set(snapshot.execution_id,block);
      block.addEventListener('toggle',()=>{if(lastExecution?.execution_id===snapshot.execution_id)$('toggleExecutionSidebar').setAttribute('aria-expanded',String(block.open));});
    }
    return block;
  }
  function identifyLatestExecution(){
    const roles={title:'executionTitle',badge:'executionBadge',current:'executionCurrent',progress:'executionProgress',nodes:'executionNodes'};
    for(const [id,block]of executionBlocks){const latest=id===lastExecution?.execution_id;if(latest)block.id='executionPanel';else block.removeAttribute('id');for(const [role,name]of Object.entries(roles)){const element=block.querySelector(`[data-execution-role="${role}"]`);if(latest)element.id=name;else element.removeAttribute('id');}}
  }
  function paintExecution(snapshot,block){
    const get=role=>block.querySelector(`[data-execution-role="${role}"]`),nodes=get('nodes');
    const isLive=liveActive&&snapshot.execution_id===liveExecutionId;
    const round=state?.rounds.find(r=>r.input.step_id===snapshot.round_step);
    const statuses={pending:'待执行',running:'执行中',completed:'完成',failed:'失败',skipped:'未执行',waiting_for_user:'待人工确认'};
    const finished=(snapshot.nodes||[]).filter(n=>n.status==='completed').length;
    const previousStatus=block.dataset.status;
    let elapsed=(snapshot.nodes||[]).reduce((sum,n)=>sum+(Number(n.elapsed_ms)||0),0);
    const active=(snapshot.nodes||[]).find(n=>n.status==='running');
    if(active?.started_at)elapsed+=Math.max(0,Date.now()-Date.parse(active.started_at));
    const seconds=Math.max(0,Math.round(elapsed/1000)),duration=seconds<1?'不到1秒':seconds<60?seconds+'秒':Math.floor(seconds/60)+'分钟'+(seconds%60?seconds%60+'秒':'');

    const phase=snapshot.recovered_from_saved_reply?(snapshot.phase==='plot'?'绘图本地复核':'本地恢复'):snapshot.phase==='plot'?'模型绘图':'点位关联';
    const phaseStatus={running:'执行中',completed:'已完成',failed:'失败',waiting_for_user:'等待确认',rejected:'已打回'}[snapshot.status]||snapshot.status;
    get('title').textContent=snapshot.status==='running'?`执行中 · ${duration}`:`用时 ${duration}`;
    get('badge').textContent=phase+' · '+phaseStatus;block.dataset.status=snapshot.status;
    if(previousStatus==='running'&&snapshot.status!=='running'&&!snapshot.streamed_body)block.open=false;
    const openDetails=new Set([...nodes.querySelectorAll('details[open]')].map(d=>d.dataset.detailKey));nodes.replaceChildren();
    for(const n of snapshot.nodes||[]){const row=el('li',undefined,'execution-node '+n.status);row.dataset.node=n.node;
      const icon=el('span',n.kind==='model_gateway'?'✦':n.kind==='human_review'?'○':'›_','execution-tool-icon');icon.setAttribute('aria-hidden','true');
      const completedLabels={read_points:'已读取本轮观测',normalize:'已检查重复、时间和阵列',references:'已计算接续与跨阵参考',associate:'模型已返回归属判断',validate:'已校验候选引用与占用',update:'已整理待确认目标集合',commit:'已保存本轮记录',plot_input:'已准备绘图资料',plot_model:'模型已返回轨迹判断',plot_validation:'已校验模型轨迹与预测',plot_commit:'已保存绘图结果'};
      const prefix=n.status==='completed'?'已执行':n.status==='running'?'正在执行':statuses[n.status]||n.status;
      const title=el('strong',n.status==='completed'&&completedLabels[n.node]?completedLabels[n.node]:prefix+' '+n.label,'execution-tool-title');title.title=n.label;
      row.title=n.label+(n.elapsed_ms!==undefined?' · '+n.elapsed_ms+'ms':'');
      const time=n.elapsed_ms===undefined?'':n.elapsed_ms<1000?'<1秒':(n.elapsed_ms/1000).toFixed(1)+'秒';
      row.append(icon,title,el('span',time,'execution-tool-time'));
      if(n.error_type)row.append(el('small','错误类型：'+n.error_type));if(n.error_type==='CancelledError')row.append(el('p','本地任务被取消，可能涉及连接中断或服务停止；并非点数校验错误。上次调用可能已计费，请先核对服务记录。','association-warning'));if(n.error_detail)row.append(el('p',(n.kind==='model_gateway'?'调用说明：':'校验说明：')+n.error_detail,'association-warning'));
      const detail=(key,title,open=false)=>{const d=el('details',undefined,'node-evidence');d.dataset.detailKey=key;d.open=open||openDetails.has(key);d.append(el('summary',title));row.append(d);return d;};
      if(isLive&&liveContext&&['associate','plot_model'].includes(n.node)&&n.status==='running'){
        nodeEvidence(detail('live','查看本次判断资料'),liveContext);
      }
      if(!isLive&&n.node==='update'&&round)associationTables(round,detail('collection','查看本轮集合、未归属与校验明细'));
      if(!isLive&&n.node==='plot_input'&&round?.model_plot)nodeEvidence(detail('plot-input','查看第二次调用的资料快照'),round.model_plot.input);
      if(!isLive&&n.node==='plot_validation'&&round?.model_plot){const plot=round.model_plot,d=detail('plot-result','查看模型绘图判断与保留意见');for(const t of plot.model_answer.tracks)d.append(el('p',`${t.track_id}：${t.reason}`));for(const w of plot.warnings)d.append(el('p',`${w.track_id}：${w.reason}`));for(const w of plot.model_answer.unassigned_assessment||[])d.append(el('p',`${w.observation_id}：${w.reason}`));for(const request of plot.review_requests||[])d.append(el('p',(request.target_id||request.fragment_id)+'：需要关联复核 · '+request.reason,'association-warning'));if(plot.spatial_result){d.append(el('p','空间图：'+plot.spatial_result.reason));for(const error of plot.spatial_result.validation_errors||[])d.append(el('p',error,'association-warning'));}}
      nodes.append(row);
    }
    drawStreamOutput(snapshot,block);
    get('progress').max=(snapshot.nodes||[]).length||8;get('progress').value=finished;
    get('current').textContent=snapshot.status==='waiting_for_user'?(round?`关联判断已返回：${Object.keys(round.targets||{}).length}个目标假设，${Object.keys(round.tracks||{}).length}个片段。请先确认集合，尚未生成新预测。`:'关联判断已返回，等待确认集合；尚未生成新预测。'):snapshot.status==='failed'?'本次执行未完成，请展开对应步骤核对；未自动重试。':snapshot.status==='completed'?'本次执行已完成。':active?.label||'正在准备本轮资料。';
  }
  function drawExecution(snapshot){
    if(!snapshot.execution_id)return;
    if(lastExecution?.created_at&&snapshot.created_at&&snapshot.created_at<lastExecution.created_at)return;
    if(lastExecution&&lastExecution.execution_id===snapshot.execution_id&&snapshot.sequence<lastExecution.sequence)return;
    if(liveActive&&lastExecution?.execution_id===snapshot.execution_id&&lastExecution?.sequence===snapshot.sequence&&liveExecutionId===snapshot.execution_id)return;
    const phase=(snapshot.nodes||[]).some(n=>n.node.startsWith('plot_'))?'plot':'association';
    const matching=state?.rounds.find(r=>phase==='plot'?r.model_plot?.input.base_state_version===snapshot.base_version:r.input.base_state_version===snapshot.base_version);
    const previous=executionHistory.get(snapshot.execution_id);
    const ownsLive=liveActive&&snapshot.base_version===liveBaseVersion&&phase===livePhase;if(ownsLive)liveExecutionId=snapshot.execution_id;
    snapshot={...snapshot,streamed_body:ownsLive?liveDraft:previous?.streamed_body||snapshot.streamed_body||'',streamed_status:previous?.streamed_status||snapshot.streamed_status||(ownsLive?'streaming':undefined),phase,round_step:previous?.round_step||(liveActive?liveContext?.step_id:null)||matching?.input.step_id||state?.rounds.at(-1)?.input.step_id||1};
    lastExecution=snapshot;executionHistory.set(snapshot.execution_id,snapshot);if(ownsLive&&!previous)executionBlock(snapshot).open=true;
    if(!messages.some(m=>m.role==='execution'&&m.execution_id===snapshot.execution_id)){
      const message={role:'execution',execution_id:snapshot.execution_id};
      const index=messages.findIndex(m=>phase==='plot'?m.plot_revision!==undefined&&m.plot_revision===matching?.revision:m.round_revision===matching?.revision&&m.round_revision!==undefined);
      if(index>=0)messages.splice(index,0,message);else messages.push(message);
      drawMessages();
    }else{const block=executionBlock(snapshot);paintExecution(snapshot,block);identifyLatestExecution();}
    $('toggleExecutionSidebar').disabled=false;void save();
  }
  async function streamRequest(path,body){
    const response=await fetch(path+'?stream=true',{method:'POST',headers:{'Content-Type':'application/json','Accept':'text/event-stream'},body:JSON.stringify(body)});
    if(!response.ok){const data=await response.json();throw new Error(typeof data.detail==='string'?data.detail:'模型流请求失败');}
    if(!response.headers.get('content-type')?.includes('text/event-stream')||!response.body)throw new Error('服务没有返回流式接口；未自动重试，请重启后端。');
    const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',result;
    const consume=block=>{const data=block.split(/\r?\n/).filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n');if(!data)return;const event=JSON.parse(data);
      if(event.event==='error'){const error=new Error(event.message);error.streamStatus=event.code==='reply_validation_failed'||event.message?.startsWith('模型回复校验失败')?'rejected':'failed';throw error;}
      if(event.event==='context'){liveContext=event.context;livePhase=event.phase;void execution();}
      if(event.event==='delta'){liveDraft+=event.text||'';recordStreamBody();}
      if(event.event==='phase')if($('executionCurrent'))$('executionCurrent').textContent=event.message;
      if(event.event==='result')result=event.result;
    };
    try{while(true){const chunk=await reader.read();buffer+=decoder.decode(chunk.value||new Uint8Array(),{stream:!chunk.done});if(buffer.length>16*1024*1024)throw new Error('模型流事件超过限制');let match;while((match=/\r?\n\r?\n/.exec(buffer))){consume(buffer.slice(0,match.index));buffer=buffer.slice(match.index+match[0].length);}if(chunk.done)break;}if(buffer.trim())consume(buffer);if(!result)throw new Error('模型流未完整结束；未自动重试。');return result;}
    finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
  }
  async function paidStream(path,body){
    await save();liveActive=true;drawSpatial();liveContext=null;liveDraft='';liveExecutionId=null;liveSaveAt=0;liveBaseVersion=state.version;livePhase=path.endsWith('/plot-step')?'plot':'association';clearInterval(pollTimer);pollTimer=setInterval(()=>{void execution();},500);
    let outcome='interrupted';try{const result=await streamRequest(path,body);outcome='received';return result;}catch(error){outcome=error.streamStatus||'interrupted';throw error;}finally{await execution();recordStreamBody(outcome);liveActive=false;liveContext=null;liveDraft='';liveExecutionId=null;liveBaseVersion=null;clearInterval(pollTimer);await save();}
  }
  async function execution(){if(!state)return;const sid=state.session_id;try{const snapshot=await request(`${API}/${sid}/execution`);if(state?.session_id===sid)drawExecution(snapshot);}catch{if(state?.session_id===sid)note('执行记录连接不可用，不推断模型已完成。');}}
  function render(){drawSequenceWorkingPreview();drawBearingResult();drawWorkspaceInput();updateSamplingMode();drawImportFilter();drawMenu();drawRows();drawSelection();drawMessages();drawChart();drawSpatial();drawResults();controls();$('sessionId').value=state?.session_id||'';drawPlatformFields();}
  function drawPlatformFields(){
    const host=$('sidebarPlatformFields');host.replaceChildren();
    const config=dataset?.platform_config||pendingPlatform?.config;
    if(!config?.platforms?.length){host.append(el('p','尚未导入平台数据。','note'));return;}
    for(const [index,platform]of config.platforms.entries()){
      const card=el('section',undefined,'platform-data-card');card.append(el('h3','平台 '+platform.name));
      const fields=[['名称',platform.name],['经度 °E',platform.start_ll?.[1]],['纬度 °N',platform.start_ll?.[0]],['航向 °（北0顺时针）',platform.heading_deg],['航速 kn',platform.speed_kn],['舷侧阵偏移x（m）',platform.hull_m?.[0]],['舷侧阵偏移y（m）',platform.hull_m?.[1]],['拖曳阵偏移x（m）',platform.tow_m?.[0]],['拖曳阵偏移y（m）',platform.tow_m?.[1]]];
      for(const [label,value]of fields){const row=el('label',undefined,'platform-data-field'),input=el('input');input.type='text';input.readOnly=true;input.value=value??'未提供';input.setAttribute('aria-label',`平台${index+1} ${label}`);row.append(el('span',label),input);card.append(row);}
      const row=el('div',undefined,'platform-data-field');row.append(el('span','颜色'));
      if(/^#[0-9a-f]{6}$/i.test(platform.color||'')){const swatch=el('input');swatch.type='color';swatch.value=platform.color;swatch.disabled=true;swatch.setAttribute('aria-label',`平台${index+1} 颜色`);row.append(swatch,el('small',platform.color));}else row.append(el('span','未提供','note'));
      card.append(row);host.append(card);
    }
  }
  function checkPlatformNames(config,candidates){
    const names=new Set(config.platforms.map(p=>p.name));const missing=[...new Set(candidates.map(p=>p.platform_id))].filter(id=>!names.has(id));
    if(missing.length)throw new Error('平台配置缺少观测使用的平台：'+missing.join('、')+'；请导入同一样本的平台文件。');
  }
  $('platformFileImport').onchange=()=>run(async()=>{
    const file=$('platformFileImport').files[0];if(!file)return;
    const status=$('platformImportStatus');
    try{
      status.textContent='正在读取平台数据……';if(file.size>100000)throw new Error('平台JSON不能超过100KB');
      const body=new FormData();body.append('file',file);
      const response=await fetch('/api/incremental/import-platform',{method:'POST',body}),data=await response.json();
      if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'平台导入失败');
      const config=data.platform_config;let forked=false;
      if(dataset){
        checkPlatformNames(config,points());
        if(state){const oldSelected=new Set(selected),oldGroups=new Set(groups),filter=dataset.import_filter;await load({...dataset,platform_config:config,platform_file_name:file.name});dataset.import_filter=filter;selected=oldSelected;groups=oldGroups;forked=true;}
        else dataset={...dataset,platform_config:config,platform_file_name:file.name};
        preview=null;plotPreview=null;$('chatConsent').checked=false;$('modelPlotConsent').checked=false;$('chatInput').textContent='平台配置已更新，请免费重新准备输入。';question();render();
      }else{pendingPlatform={config,name:file.name};await WorkspaceCache.set('mixed-pending-platform',pendingPlatform);drawPlatformFields();}
      status.textContent=`已导入 ${file.name} · ${config.platforms.length}个平台${dataset?'，已应用于当前观测':'，等待导入观测'}${forked?'；已另建会话，旧结果保留':''}。未调用模型。`;note(status.textContent);
    }catch(error){status.textContent='导入失败：'+error.message;throw error;}
    finally{$('platformFileImport').value='';}
  });
  function initialObservationIds(data){
    const lookup=new Map(data.candidates.map(p=>[p.candidate_id,p]));
    const browseGroups=(Array.isArray(data.target_catalog)?data.target_catalog:[]).filter(g=>Array.isArray(g.candidate_ids)&&g.candidate_ids.some(id=>lookup.has(id)));
    const selections=browseGroups.length?browseGroups.map(g=>[...new Set(g.candidate_ids)].map(id=>lookup.get(id)).filter(Boolean)):[data.candidates];
    return new Set(selections.flatMap(group=>sorted(group).slice(0,3).map(p=>p.candidate_id)));
  }
  async function load(data){if(!data?.platform_config&&pendingPlatform)data={...data,platform_config:pendingPlatform.config,platform_file_name:pendingPlatform.name};if(!data||typeof data!=='object')throw new Error('候选数据须为JSON对象');const raw=data.candidates||data.images?.flatMap(im=>im.candidates.map(p=>({...p,array:im.array,platform_id:im.platform_id})));if(!Array.isArray(raw)||!raw.length)throw new Error('未找到真实候选点；platform.json不能作为观测');const normalized=raw.map(p=>({...p,candidate_id:p.candidate_id||p.id}));if(normalized.some(p=>typeof p.candidate_id!=='string'||!Number.isFinite(p.t)||!Number.isFinite(p.bearing_deg)||!['hull','tow'].includes(p.array)||typeof p.platform_id!=='string'))throw new Error('点位缺少有效编号、时间、方位、平台或阵列');if(new Set(normalized.map(p=>p.candidate_id)).size!==normalized.length)throw new Error('数据中候选编号重复');
    if(pendingPlatform&&data.platform_config===pendingPlatform.config)checkPlatformNames(pendingPlatform.config,normalized);
    const diagnostics=ObservationDiagnostics.summarize(normalized),nextConversationId=conversationDraft&&!dataset?conversationId:crypto.randomUUID();
    await save();clearSequenceView();if(pendingPlatform){await WorkspaceCache.remove('mixed-pending-platform');pendingPlatform=null;}inspectedPointId=null;$('pointCard').hidden=true;$('pointSamplingStatus').textContent='';dataset={...data,candidates:normalized,import_diagnostics:diagnostics};if(dataset.import_filter){dataset.legacy_import_filter=dataset.import_filter;delete dataset.import_filter;}state=null;recheckMode=false;lastExecution=null;executionHistory.clear();executionBlocks.clear();plotPreview=null;$('modelPlotConsent').checked=false;$('rerunUnknownAck').checked=false;selected=initialObservationIds(dataset);groups=new Set(['']);preview=null;messages=[];customQuestion=false;conversationDraft=false;conversationId=nextConversationId;$('chatConsent').checked=false;$('batchSource').textContent=`已保留${points().length}点，不因单点、缺阵或不同步排除观测。严格同步双阵${diagnostics.strict_dual_group_count}组、单阵时刻${diagnostics.single_array_group_count}组（仅统计，不认定目标配对）。默认预选${selected.size}点；尚未调用模型。`;question();drawExecution({status:'not_started',nodes:[]});render();}

  async function restore(saved){if(saved?.conversationDraft&&!saved.dataset){resetWorkspace();conversationId=saved.conversationId;conversationDraft=true;drawConversations();return;}conversationDraft=false;clearSequenceView();$('sequenceMirrorSide').value=saved?.spatialMirrorSide==='left'?'left':'right';$('sequenceAutoConnect').checked=saved?.spatialAutoConnect!==false;spatialLineSnapshot=saved?.spatialLineSnapshot||null;spatialLineChanges=saved?.spatialLineChanges||[];spatialLineColors=saved?.spatialLineColors||{};$('sequenceReviewLimit').value=String(saved?.reviewLimit||80);$('sequenceGapLimit').value=String(saved?.gapLimit||180);$('useSequential').checked=saved?.useSequential!==false;$('workspaceSource').value=saved?.workspaceSource||'workspace';$('pointImportMode').value=saved?.pointImportMode==='append'?'append':'replace';recheckMode=!!saved?.recheckMode;lastExecution=null;if(!saved?.dataset)throw new Error('没有可恢复的混合观测会话');if(saved.sampleCount!==undefined)$('samplePointCount').value=String(saved.sampleCount);$('samplePointMode').value=saved.sampleModeVersion===2&&['points','synchronous_groups','source_pairs'].includes(saved.sampleMode)?saved.sampleMode:'source_pairs';executionHistory=new Map((saved.executionHistory||[]).map(e=>[e.execution_id,e]));executionBlocks.clear();dataset=saved.dataset;$('sequenceMode').value=['batch-snapshot-v1','sequential'].includes(saved.executionMode)?saved.executionMode:'batch-snapshot-v1';$('sequenceBatchSize').value=saved.batchSize||50;$('sequenceConcurrency').value=saved.batchConcurrency||50;$('sequenceSpatialCards').checked=saved.spatialCards!==false;$('sequenceRetryEnabled').checked=saved.retryEnabled!==false;$('sequenceRetryLimit').value=saved.retryLimit??2;$('sequenceRetryBudget').value=saved.retryBudget??20;inspectedPointId=null;$('pointCard').hidden=true;state=saved.state?await request(API+'/'+saved.state.session_id):null;await syncWorkspaceCatalog();selected=new Set(saved.selected||[]);groups=new Set(saved.groups||['']);messages=saved.messages||[];customQuestion=!!saved.customQuestion;conversationId=saved.conversationId;lastExecution=[...executionHistory.values()].at(-1)||null;preview=null;$('chatQuestion').value=saved.question||'';$('chatConsent').checked=false;$('modelPlotConsent').checked=false;$('rerunUnknownAck').checked=false;plotPreview=null;if(state?.rerun_prepared){if(saved.state?.version!==state.version)restoreRerunForm(state.rerun_prepared);else recheckMode=saved.recheckMode??state.rerun_prepared.recheck;}question();render();if(state)await execution();if(state?.active_sequential_run||state?.last_sequential_run)await refreshSequence();}
  function clearSequenceView(){spatialLineSnapshot=null;spatialLineChanges=[];spatialLineColors={};$('sequenceSpatialLinkChanges').replaceChildren();$('sequenceAutoConnect').checked=true;$('sequenceMirrorSide').value='right';$('sequencePreparationStatus').textContent='';sequenceSpatialView={zoom:1,dx:0,dy:0,scale:1};clearTimeout(sequenceTimer);sequenceTimer=null;sequenceRun=null;sequencePreview=null;sequenceBody=null;sequenceErrors=0;$('sequenceConsent').checked=false;$('useSequential').checked=true;$('sequenceTarget').value='';$('sequenceMode').value='batch-snapshot-v1';$('sequenceBatchSize').value='50';$('sequenceConcurrency').value='50';$('sequenceSpatialCards').checked=true;$('sequenceRetryEnabled').checked=true;$('sequenceRetryLimit').value='2';$('sequenceRetryBudget').value='20';drawSequenceSteps();drawSequenceWorkingPreview();}
  function batchOptions(){const mode=$('sequenceMode').value,enabled=mode==='batch-snapshot-v1'&&$('sequenceRetryEnabled').checked,limit=enabled?Number($('sequenceRetryLimit').value):0,budget=enabled?Number($('sequenceRetryBudget').value):0;if(enabled&&(!Number.isInteger(limit)||limit<1||limit>2||!Number.isInteger(budget)||budget<1||budget>1000))throw Error('启用重试时，每环节额外次数须为1至2、全队列重试预算须为1至1000。');return {mode,batchSize:Number($('sequenceBatchSize').value),retryBudget:budget};}
  function sequenceControls(){
    const budgetHint=$('sequenceBudgetHint');budgetHint.className='note';if(dataset&&!state?.active_sequential_run){try{const local=PointSampling.inspectQueue(PointSampling.groupMarkers(selectedPoints(),catalog()).map(m=>m.observations.map(p=>p.candidate_id)),state,Number($('sequenceBudget').value),batchOptions());budgetHint.textContent=`当前新增${local.units.length}组，最多${local.upper}次关联调用${local.freeSeed?'（首组免费）':''}；正常${local.base}次＋重试预留${local.retryBudget}次；准备队列不调用模型。`;}catch(error){budgetHint.className='association-warning';budgetHint.textContent=error.message;}}else budgetHint.textContent='';
    const active=!!state?.active_sequential_run,enabled=$('useSequential').checked||active;
    if(active)$('useSequential').checked=true;
    for(const row of $('sequencePanel').querySelectorAll('.actions'))row.hidden=!enabled;
    $('sequenceConsent').closest('label').hidden=!enabled;$('sequenceHistory').hidden=!enabled||!sequenceRun?.steps?.length;
    $('useSequential').disabled=busy||active;$('chatPrepare').hidden=$('chatSend').hidden=enabled;
    $('chatConsent').closest('label').hidden=enabled;
    const started=sequenceRun?.worker_active||sequenceRun?.status==='running',canContinue=active&&sequenceRun&&['prepared','paused'].includes(sequenceRun.status)&&!started;
    const target=$('sequenceTarget'),wanted=target.value;target.replaceChildren(new Option('全部已有轨迹（不预设归属）',''));
    for(const [id,t]of Object.entries(state?.confirmed_targets||{})){if(t.review_status==='adopted')target.append(new Option('仅更新 '+id,id));}
    if([...target.options].some(o=>o.value===wanted))target.value=wanted;
    target.disabled=$('sequenceOrder').disabled=$('sequenceBudget').disabled=busy||active||!enabled;
    $('prepareSequence').disabled=busy||!enabled||!sequenceAvailable||!ready||!dataset||active||state&&state.status!=='ready'||!inputPoints().length;
    $('sequenceNext').disabled=$('sequenceStart').disabled=busy||!enabled||!sequenceAvailable||!$('sequenceConsent').checked||(!canContinue&&(!sequencePreview||active));
    $('sequencePause').disabled=!active||!started;$('sequenceRefresh').disabled=busy||!active;
    $('sequenceAdopt').hidden=!active||sequenceRun?.status!=='completed_awaiting_review';$('sequenceAdopt').disabled=busy||started;
    $('sequenceReject').hidden=!active;$('sequenceReject').disabled=busy||started;
    $('sequenceReviewLimit').disabled=$('sequenceGapLimit').disabled=busy||active;const pendingReview=sequenceRun?.working_view?.pending_review_requests?.length;$('sequenceExtendReview').hidden=!pendingReview||sequenceRun?.status!=='completed_awaiting_review';$('sequenceExtendReview').disabled=busy||started;$('sequenceAdopt').disabled=busy||started||!!pendingReview;
    $('sequenceRecover').hidden=!active||sequenceRun?.status!=='failed';$('sequenceRecover').disabled=busy||started;
    const showRun=!!sequenceRun&&(active||!sequencePreview),spec=showRun?sequenceRun.spec:sequencePreview;
    const batch=(active&&sequenceRun?(sequenceRun.spec.execution_mode||'sequential'):(sequencePreview?.execution_mode||$('sequenceMode').value))==='batch-snapshot-v1';
    const oldPinned=active&&sequenceRun&&sequenceRun.spec.execution_mode!=='batch-snapshot-v1';
    $('sequenceModeNotice').className=oldPinned?'association-warning':'note';
    $('sequenceModeNotice').textContent=oldPinned?'当前锁定的是旧逐组队列，不是50组批次。修改调用预算不会改变模式；请先明确结束旧队列再切换。旧原文和原始数据保留。':batch?'50组快照模式：使用全部已知轨迹快照；每组并发判断＋每批1次模型归并。调用预算不是每批组数。':'';
    $('sequenceSwitchBatch').hidden=!oldPinned;$('sequenceSwitchBatch').disabled=busy||started;
    $('sequenceHistory').querySelector(':scope > summary').textContent=(batch?'批次执行记录':'逐组执行记录')+' · 完整模型正文与证据';
    $('sequenceApplyBudget').hidden=!state;$('sequenceApplyBudget').disabled=busy||active||state?.status!=='ready';
    $('sequenceSnapshots').hidden=!batch||!sequenceRun;if(batch&&sequenceRun)$('sequenceSnapshots').href=`${API}/${state.session_id}/runs/${sequenceRun.id}/snapshots`;
    if(batch)$('sequenceAutoConnect').parentElement.title='本模式只连模型归属形成的边，不使用本地按距离分目标';
    for(const id of ['sequenceMode','sequenceBatchSize','sequenceConcurrency','sequenceSpatialCards','sequenceRetryEnabled','sequenceRetryLimit','sequenceRetryBudget'])$(id).disabled=busy||active;
    for(const id of ['sequenceTarget','sequenceReviewLimit','sequenceGapLimit'])$(id).closest('label').hidden=batch;
    $('sequenceSpatialCardsSetup').hidden=!batch;$('sequenceSpatialCards').disabled=busy||active;if(active)$('sequenceSpatialCards').checked=sequenceRun?.spec?.spatial_association_contract==='model-selected-spatial-cards-v1';$('sequenceRetrySetup').hidden=!batch;if(batch&&sequenceRun?.steps?.at(-1)?.answer)$('sequenceRecover').hidden=true;
    const retryPolicy=sequenceRun?.spec?.retry_policy;
    if(active){$('sequenceRetryEnabled').checked=!!retryPolicy?.max_retries;$('sequenceRetryLimit').value=retryPolicy?.max_retries||0;$('sequenceRetryBudget').value=retryPolicy?.budget||0;}
    $('sequencePrepareRetry').hidden=!active||!batch||sequenceRun?.status!=='failed';$('sequencePrepareRetry').disabled=busy||started||!retryAvailable;
    const retryState=sequenceRun?.steps?.at(-1)?.retry_state;
    $('sequenceRetryStatus').textContent=batch?(active?`额外重试已用${sequenceRun?.retry_calls||0}/${retryPolicy?.budget||0}次；每环节最多额外${retryPolicy?.max_retries||0}次；未知结果不重试。${retryState?'当前'+(retryState.kind==='merge'?'批次归并':retryState.unit_id)+'，重试'+retryState.retry+'/'+retryState.maximum:''}`:'总调用上限必须包含重试预留；483组正常493次＋重试20次＝建议总上限513次。'):'';
    $('sequenceBatchSize').closest('label').hidden=$('sequenceConcurrency').closest('label').hidden=!batch;
    $('sequenceNext').textContent=batch?(sequenceRun?.resume_batch?'仅恢复失败环节':sequenceRun?.resume_merge?'仅归并已保存回复':'执行下一批'):'执行下一组';$('sequenceStart').textContent=batch?(sequenceRun?.resume_batch?'恢复后继续按批执行':sequenceRun?.resume_merge?'归并后继续按批执行':'连续按批执行'):'连续逐组执行';
    if(batch){$('sequenceTarget').disabled=$('sequenceReviewLimit').disabled=true;$('sequenceTarget').value='';}
    if(active&&sequenceRun){$('sequenceMode').value=sequenceRun.spec.execution_mode||'sequential';$('sequenceBatchSize').value=sequenceRun.spec.batch_size||50;$('sequenceConcurrency').value=sequenceRun.spec.concurrency||50;$('sequenceBudget').value=String(sequenceRun.spec.call_limit);$('sequenceOrder').value=sequenceRun.spec.order;$('sequenceReviewLimit').value=String(sequenceRun.spec.review_limits?.max_review_points||80);$('sequenceGapLimit').value=String(sequenceRun.spec.review_limits?.long_gap_seconds||180);}
    const labels={prepared:'已准备',running:'正在执行',paused:'已暂停',completed_awaiting_review:'已完成，等待确认',failed:'失败，已停止',result_unknown:'调用结果未知',adopted:'已确认',rejected:'已放弃'};
    if(active&&sequenceRun)$('chatState').textContent='逐组 · '+(labels[sequenceRun.status]||sequenceRun.status);
    $('sequenceStatus').textContent=!enabled?'当前为旧版兼容模式，会整批发送；逐组模式需勾选上方开关。':!sequenceAvailable?'后端尚未加载暂接/疑点及逐组空间接口，请待调用结束后重启后端并刷新；已阻止逐组付费发送。':showRun?
      `运行 ${labels[sequenceRun.status]||sequenceRun.status}（${sequenceRun.status}） · 已处理${sequenceRun.cursor}/${spec.queue.length}组 · 已尝试${sequenceRun.calls}次关联调用（上限${spec.call_limit}）；暂定片段${sequenceRun.working_summary.fragments}、目标${sequenceRun.working_summary.targets}、未归属${sequenceRun.working_summary.unassigned}。${sequenceRun.pause_requested?'已请求暂停，当前批次/步骤完成后停止，不会取消正在计费的调用。':''}${batch?'批间版本快照接续。':spec.continuation_policy==='tentative-local-v1'?'v1.1：先暂接，疑点局部修订。':'旧队列保持原策略；新队列使用v1.1。'}${batch?'快照批次：已完成'+sequenceRun.steps.filter(s=>s.status==='validated').length+'批；批内有效返回'+(sequenceRun.steps.at(-1)?.worker_results?.filter(r=>r.status==='returned').length||0)+'条；未归并不改写快照。':''}${sequenceRun.last_error||''}`:
      spec?`已免费准备${spec.queue.length}组；${batch?'快照模式每批'+spec.batch_size+'组／并发'+spec.concurrency+'；每组判断＋每批归并，最多':'逐组最多'}${spec.model_call_upper_bound}次关联调用，模型${spec.model_label||'已配置模型'}；${spec.retry_policy?'正常流程'+spec.base_model_call_upper_bound+'次＋重试预留'+spec.retry_policy.budget+'次。':''}${spec.free_initial_seed?'首组无历史，将免费建立单阵起始暂定片段。':''}未处理组不会发送到当前模型。`:'请选择观测并免费准备队列；预测/绘图仍需另外授权。';
  }
  function drawSequenceSteps(){
    const host=$('sequenceSteps'),previous=new Map([...host.children].map(n=>{const pre=n.querySelector(':scope > pre');return [n.dataset.index,{open:n.open,raw:n.querySelector(':scope > details[data-step-context]')?.open,top:pre?.scrollTop||0,follow:!pre||pre.scrollHeight-pre.clientHeight-pre.scrollTop<=32}];}));host.replaceChildren();
    for(const step of sequenceRun?.steps||[]){const d=el('details');d.dataset.index=String(step.index);const old=previous.get(String(step.index));d.open=old?.open??step.status==='running';d.append(el('summary',`第${step.index}${step.source==='parallel_batch_model'?'批':'组'} · ${step.status} · ${step.source==='local_seed'?'本地初始化，无模型调用':step.source==='parallel_batch_model'?'并发Agent判断＋模型归并':'Agent判断'}`));
      const c=step.context;d.append(el('p',`新增${c.sequential_step.new_unit_count}组；当前可分配/复核${c.observations.length}条；已处理前缀${c.sequential_step.processed_unit_count}组。历史工作状态仅为暂定；复核此前${c.sequential_step.revisited_provisional_units||0}组暂定历史。${c.sequential_step.confirmed_history_revisit_count?'注意：涉及'+c.sequential_step.confirmed_history_revisit_count+'条原已确认观测的历史修订，须最后人工核对。':''}`,'note'));
      if(c.continuation_policy)d.append(el('p',`v1.1 · ${c.continuation_policy.mode==='local_review'?'疑点局部修订':'先暂接'} · 疑点${c.continuation_policy.issues.length}项 · 可复核${c.editable_observation_ids.length}条；其余历史只读，不再固定重放最近3组。`,'note'));
      if(step.revision){const rev=el('details');rev.append(el('summary','本步归属与接续修订'),el('pre',JSON.stringify(step.revision,null,2)));d.append(rev);}
      if(step.worker_results){const workers=el('details'),ok=step.worker_results.filter(r=>r.status==='returned').length,unknown=step.worker_results.filter(r=>r.status==='result_unknown').length;workers.append(el('summary',`批内有效返回 ${ok}/${c.sequential_step.new_unit_count}；结果未知${unknown}组；记录${step.worker_results.length}条`),el('pre',JSON.stringify(step.worker_results,null,2)));d.append(workers);}
      if(step.error)d.append(el('p',step.error,'association-warning'));
      if(step.warnings?.length){const reasons=[...new Set(step.warnings.map(w=>w.reason))];d.append(el('p',(step.source==='parallel_batch_model'?'批次校验提示（不代表已归并）：':'本步已处理，但有归属待复核：')+reasons.join('；'),'association-warning'));const warnings=el('details');warnings.append(el('summary',`查看校验警告（${step.warnings.length}项，不代表已识别目标）`),el('pre',JSON.stringify(step.warnings,null,2)));d.append(warnings);}
      if(step.attempts?.length){const attempts=el('details');attempts.append(el('summary',`实际调用记录 ${step.attempts.length}次（包含重试）`),el('pre',JSON.stringify(step.attempts,null,2)));d.append(attempts);}
      if(step.merge_wire_input){const wire=el('details');wire.append(el('summary','紧凑归并实际输入（观测不重复传输）'),el('pre',JSON.stringify(step.merge_wire_input,null,2)));d.append(wire);}
      if(step.merge_input){const merge=el('details');merge.append(el('summary','归并模型实际输入'),el('pre',JSON.stringify(step.merge_input,null,2)));d.append(merge);}
      const raw=el('details');raw.dataset.stepContext='true';raw.open=old?.raw||false;raw.append(el('summary','本步匿名输入与证据'),el('pre',JSON.stringify(c,null,2)));d.append(raw);
      const output=el('pre',step.streamed_body||step.answer||'等待模型正文；不会自动重试');d.append(output);const copyActions=el('div',undefined,'context-copy-toolbar');copyActions.append(contextCopyButton('复制第'+step.index+'步上下文',step.index));d.append(copyActions);host.append(d);output.scrollTop=old?.follow===false?old.top:output.scrollHeight;
    }
  }
  async function prepareSequence(){
    const started=performance.now();let stage='检查选择与预算';
    const status=$('sequencePreparationStatus'),paint=()=>{status.textContent=`${stage} · 已用时${((performance.now()-started)/1000).toFixed(1)}秒；免费准备，未调用模型。`;};
    sequencePreview=null;sequenceBody=null;$('sequenceConsent').checked=false;status.className='note';paint();
    const timer=setInterval(paint,400);
    try{
      if(!sequenceAvailable)throw new Error('请重启后端以加载当前逐组接口');
      if($('sequenceSpatialCards').checked&&!spatialCardsAvailable)throw new Error('请重启后端加载联合空间候选协议，旧队列不会自动升级。');
      if($('sequenceMode').value==='batch-snapshot-v1'&&!retryAvailable)throw new Error('请先重启后端加载输出重试与紧凑协议，未发送模型。');
      if($('sequenceMode').value==='batch-snapshot-v1'&&!batchAvailable)throw new Error('后端尚未加载50组快照批次，请先重启后端并刷新。未发送模型。');
      const budget=Number($('sequenceBudget').value),markers=PointSampling.groupMarkers(selectedPoints(),catalog());
      // Reject an impossible budget before saving/uploading the entire dataset.
      const local=PointSampling.inspectQueue(markers.map(m=>m.observations.map(p=>p.candidate_id)),state,budget,batchOptions());
      const limit=Number($('sequenceReviewLimit').value),gap=Number($('sequenceGapLimit').value);
      if(!Number.isInteger(limit)||limit<1||limit>500)throw new Error('局部复核上限须为1至500的整数。');
      if(!Number.isFinite(gap)||gap<1||gap>86400)throw new Error('长缺口提示须为1至86400秒。');
      stage=state?'核对现有会话':'创建免费会话并保存原始观测';paint();
      await ensureWorkspaceState(Math.min(10000,Math.max(20,budget+1)));
      stage='后端校验队列及调用预算';paint();
      sequenceBody={...(spatialCardsAvailable?{spatial_cards:$('sequenceMode').value==='batch-snapshot-v1'&&$('sequenceSpatialCards').checked}:{}),execution_mode:$('sequenceMode').value,batch_size:Number($('sequenceBatchSize').value),compact_output:$('sequenceMode').value==='batch-snapshot-v1',retry_limit:$('sequenceMode').value==='batch-snapshot-v1'&&$('sequenceRetryEnabled').checked?Number($('sequenceRetryLimit').value):0,retry_budget:$('sequenceMode').value==='batch-snapshot-v1'&&$('sequenceRetryEnabled').checked?Number($('sequenceRetryBudget').value):0,concurrency:Number($('sequenceConcurrency').value),version:state.version,units:local.units,order:$('sequenceOrder').value,call_limit:budget,review_point_limit:limit,long_gap_seconds:gap,target_binding:$('sequenceTarget').value?{entity_type:'physical_target',target_id:$('sequenceTarget').value,revision:state.version}:null,question:$('chatQuestion').value};
      sequencePreview=await request(`${API}/${state.session_id}/runs/preview`,sequenceBody);
      $('chatInput').textContent='逐组模式：每步匿名输入与证据在上方执行记录中展示；未处理队列不发送模型。此处不提供整批模型输入。';sequenceRun=null;drawSequenceSteps();drawSequenceWorkingPreview();sequenceControls();
      status.textContent=`准备完成：${sequencePreview.queue.length}组，最多${sequencePreview.model_call_upper_bound}次关联调用；耗时${((performance.now()-started)/1000).toFixed(2)}秒。尚未调用模型，发送仍需重新授权。`;
      note(status.textContent);
    }catch(error){sequencePreview=null;sequenceBody=null;status.className='association-warning';status.textContent=`准备未完成：${error.message}（${((performance.now()-started)/1000).toFixed(2)}秒）`;throw error;}
    finally{clearInterval(timer);sequenceControls();}
  }
  async function ensureSequence(){
    if(state?.active_sequential_run)return;
    if(!sequencePreview||!sequenceBody)throw new Error('请先免费准备队列并核对授权');
    sequenceRun=await request(`${API}/${state.session_id}/runs`,{...sequenceBody,context_digest:sequencePreview.context_digest,request_key:crypto.randomUUID()});
    state=await request(API+'/'+state.session_id);render();
  }
  async function refreshSequence(){
    clearTimeout(sequenceTimer);const sid=state?.session_id,rid=state?.active_sequential_run||state?.last_sequential_run;if(!sid||!rid)return;
    try{
      const url=`${API}/${sid}/runs/${rid}`,{response,data:result}=await fetchJson(url,{},60000);
      if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'状态查询失败');
      if(state?.session_id!==sid||(state?.active_sequential_run||state?.last_sequential_run)!==rid)return;
      sequenceRun=result;sequenceErrors=0;drawSequenceSteps();render();
      // A second root-state failure must not hide the run result just received.
      try{const root=await request(API+'/'+sid);if(state?.session_id===sid)state=root;}catch(error){note('队列状态已更新；会话概要暂未刷新，不影响已保存结果。');}
      render();
      if(result.worker_active||result.status==='running')sequenceTimer=setTimeout(()=>void refreshSequence(),2500);
      else note(`队列状态已刷新：已处理${result.cursor}/${result.spec.queue.length}组；${result.status==='completed_awaiting_review'?'已完成，等待确认。':'状态 '+result.status+'。'}未重新调用模型。`);
    }catch(error){
      if(state?.session_id!==sid||(state?.active_sequential_run||state?.last_sequential_run)!==rid)return;
      const delay=Math.min(30000,2000*2**Math.min(++sequenceErrors-1,4));
      note(`状态查询暂时失败，不代表模型失败：${error.message} ${delay/1000}秒后仅重查状态（免费），不会重发模型。`);
      sequenceTimer=setTimeout(()=>void refreshSequence(),delay);
    }
  }
  async function dispatchSequence(action){
    if(!$('sequenceConsent').checked)throw new Error('请明确勾选逐组调用及费用上限授权');
    await ensureSequence();
    sequenceRun=await request(`${API}/${state.session_id}/runs/${state.active_sequential_run}/${action}`,{expected_cursor:sequenceRun.cursor,context_digest:sequenceRun.spec.context_digest,consent:true});
    $('sequenceConsent').checked=false;await refreshSequence();
  }
  $('useSequential').onchange=()=>{sequencePreview=null;sequenceBody=null;question();controls();void save();};$('sequenceConsent').onchange=sequenceControls;
  for(const id of ['sequenceOrder','sequenceBudget','sequenceTarget','sequenceReviewLimit','sequenceGapLimit','sequenceMode','sequenceBatchSize','sequenceConcurrency','sequenceSpatialCards','sequenceRetryEnabled','sequenceRetryLimit','sequenceRetryBudget'])$(id).onchange=()=>{sequencePreview=null;sequenceBody=null;$('sequenceConsent').checked=false;question();sequenceControls();void save();};
  $('sequenceApplyBudget').onclick=()=>run(async()=>{const budget=Number($('sequenceBudget').value);if(!Number.isInteger(budget)||budget<0||budget>10000||state.calls+budget>10000)throw Error('调用预算须为整数，且累计上限不超过10000');if(!confirm(`将本会话累计调用上限从${state.max_calls}改为${state.calls+budget}次？这里只调整上限，不调用模型；发送仍需另外授权。`))return;state=await request(`${API}/${state.session_id}/call-budget`,{version:state.version,max_calls:state.calls+budget,consent:true});sequencePreview=null;sequenceBody=null;note('预算已明确更新，未调用模型。请重新免费准备队列。');render();});
  $('sequenceSwitchBatch').onclick=()=>{if(!state?.active_sequential_run||!sequenceRun)return;if(!confirm('结束旧逐组队列并切换50组模式？本队列未确认的暂定更新不采纳；原始数据、已确认轨迹和旧执行原文保留。不会调用模型，已发生费用不能撤销。若上次结果未知可能已计费，请先核对服务记录。'))return;void run(async()=>{const sid=state.session_id,rid=state.active_sequential_run;state=await request(`${API}/${sid}/runs/${rid}/review`,{adopt:false,acknowledge_unknown:sequenceRun.status==='result_unknown'});clearSequenceView();question();render();await save();note('旧队列已明确结束；现为50组快照模式。请核对预算并重新免费准备，尚未调用模型。');});};
  $('sequencePrepareRetry').onclick=()=>run(async()=>{
    if(!state?.active_sequential_run||sequenceRun?.status!=='failed')return;
    const rawBudget=prompt('全队列额外重试预算（包含已用重试，默认20次）','20');if(rawBudget===null)return;const budget=Number(rawBudget);if(!Number.isInteger(budget)||budget<1||budget>1000)throw Error('重试预算须为1至1000');
    const rawTotal=prompt('本队列累计调用总上限（包含所有尝试与后续正常调用）',String(sequenceRun.spec.call_limit+budget));if(rawTotal===null)return;const total=Number(rawTotal);if(!Number.isInteger(total)||total<1||total>10000)throw Error('总调用上限须为1至10000');
    if(!confirm(`只恢复失败环节，成功单组和此前快照保留。每环节最多额外2次、重试预算${budget}次、总上限${total}次。此处仅免费准备并调整授权上限，不发模型；随后发送可能额外计费。确认？`))return;
    sequenceRun=await request(`${API}/${state.session_id}/runs/${sequenceRun.id}/prepare-retry`,{expected_cursor:sequenceRun.cursor,context_digest:sequenceRun.spec.context_digest,call_limit:total,retry_limit:2,retry_budget:budget,consent:true});
    $('sequenceConsent').checked=false;await refreshSequence();note('失败环节重试已免费准备，成功结果保留。勾选新的费用授权后点击“仅恢复失败环节”。');
  });
  $('prepareSequence').onclick=()=>run(prepareSequence);$('sequenceNext').onclick=()=>run(()=>dispatchSequence('next'));$('sequenceStart').onclick=()=>run(()=>dispatchSequence('start'));
  $('sequencePause').onclick=async()=>{if(!state?.active_sequential_run)return;try{await request(`${API}/${state.session_id}/runs/${state.active_sequential_run}/pause`,{});await refreshSequence();}catch(e){note(e.message);}};
  $('sequenceRefresh').onclick=()=>run(refreshSequence);
  $('sequenceExtendReview').onclick=()=>run(async()=>{sequenceRun=await request(`${API}/${state.session_id}/runs/${state.active_sequential_run}/extend-review`,{});$('sequenceConsent').checked=false;drawSequenceSteps();render();note('已免费准备1步局部复核，旧授权已清除；请核对后重新授权执行。');});
  $('sequenceRecover').onclick=()=>run(async()=>{await request(`${API}/${state.session_id}/runs/${state.active_sequential_run}/recover`,{});await refreshSequence();});
  for(const [id,adopt]of [['sequenceAdopt',true],['sequenceReject',false]])$(id).onclick=()=>{if(!adopt&&!confirm('结束并放弃本次暂定更新？原始观测和已确认历史保留，已发生的调用不能撤销。若结果未知，上次可能已计费；请先核对服务记录。'))return;void run(async()=>{
    const result=await request(`${API}/${state.session_id}/runs/${state.active_sequential_run}/review`,{adopt,acknowledge_unknown:sequenceRun.status==='result_unknown'});state=result;sequenceRun.status=adopt?'adopted':'rejected';sequenceRun.worker_active=false;clearTimeout(sequenceTimer);sequencePreview=null;sequenceBody=null;$('sequenceConsent').checked=false;
    messages.push({role:'assistant',text:adopt?'逐组运行的暂定更新已由你确认；尚未生成预测，请单独授权模型绘图。':'本次逐组更新已放弃；原始观测和原已确认历史保留。'});render();
  });};
  async function prepare(){const count=inputPoints().length;if(selectionLimit!==null&&count>selectionLimit)throw new Error(`本轮选择了${count}个观测点，当前后端最多支持${selectionLimit}个。请重启后端并刷新页面加载不限点数的新版。尚未调用模型，不会自动分批。`);note(`正在免费准备${count}个观测点，请稍候；尚未调用模型。`);await ensureWorkspaceState();
    preview=await request(`${API}/${state.session_id}/preview`,{version:state.version,selected_ids:inputPoints().map(p=>p.candidate_id),question:$('chatQuestion').value,recheck:recheckMode});$('chatInput').textContent=JSON.stringify(preview.context,null,2);const stats=preview.transport_stats,compact=stats?` 完整保留${stats.observations}点，传输输入${stats.wire_chars}字符（压缩前${stats.canonical_chars}字符）；采用紧凑关联协议，不增加模型调用。`:'';note((preview.context.revision_mode==='historical_replay'?'检测到更早的观测：本轮进入历史补点复核，旧预测不会改写。尚未调用模型。':'输入已匿名化并准备；分组与原始点编号不进入模型关联依据。尚未调用模型。')+compact);}
  function restoreRerunForm(prepared){
    recheckMode=prepared.phase==='association'&&prepared.recheck;
    if(prepared.phase==='association'){selected=new Set(prepared.selected_ids);customQuestion=true;$('chatQuestion').value=prepared.question;}
    $('chatConsent').checked=false;$('modelPlotConsent').checked=false;$('rerunUnknownAck').checked=false;preview=null;plotPreview=null;
  }
  async function prepareRerun(phase){
    const result=await request(`${API}/${state.session_id}/prepare-rerun`,{version:state.version,phase,acknowledge_unknown:$('rerunUnknownAck').checked});
    const {rerun,model_calls,...snapshot}=result;state=snapshot;restoreRerunForm(rerun);
    if(rerun.phase==='association'){preview={context:rerun.context,context_digest:rerun.context_digest};$('chatInput').textContent=JSON.stringify(rerun.context,null,2);}
    else plotPreview={context:rerun.context,context_digest:rerun.context_digest};
    messages.push({role:'assistant',text:rerun.phase==='association'?`已恢复本轮${rerun.selected_ids.length}个观测，重新分析输入已免费准备。请重新勾选授权后发送；旧回复和预测记录保留。`:'本轮绘图资料已免费重新准备。请重新勾选绘图授权后发送；本次准备未调用模型，旧结果仍保留。'});
    render();note('重新运行已准备，尚未调用模型。请重新确认本次发送与费用。');$(rerun.phase==='association'?'chatComposer':'modelPlotPanel').scrollIntoView({block:'nearest'});
  }
  $('prepareRerun').onclick=()=>run(()=>prepareRerun(state.manual_rerun.phase));
  $('rerunAssociation').onclick=()=>run(()=>prepareRerun('association'));
  $('rerunUnknownAck').onchange=controls;
  $('targetTrackSelect').onchange=()=>{groups=new Set([...$('targetTrackSelect').selectedOptions].map(o=>o.value));drawRows();void save();};
  $('selectInitialPoints').onclick=()=>{if(!dataset)return;selected=new Set([...selected,...initialObservationIds(dataset)]);drawRows();invalidate();note('已补选各分组按时间排序的前3个观测，保留原选择并按编号去重；尚未调用模型。');};
  $('supplementEvidence').onclick=()=>{
    if(busy||appendBusy||!dataset||state?.status==='awaiting_review')return;
    const unresolved=new Set(state?.unassigned_ids||[]),seeds=unresolved.size?points().filter(p=>unresolved.has(p.candidate_id)):selectedPoints();
    if(!seeds.length){note('请先选择稀疏观测，或先完成本轮集合确认。');return;}
    const proposed=EvidenceNeighborhood.propose(points(),seeds),combined=new Set([...selected,...proposed]),added=combined.size-selected.size;
    if(!confirm(`将补选${added}个真实观测，共选择${combined.size}点；取同平台前后各2个可用时刻的全部候选，不认定目标配对，不调用模型。是否加入选择并准备复核？`))return;
    selected=combined;recheckMode=!!state?.submitted_ids.length;groups=new Set(['']);customQuestion=true;$('chatQuestion').value=`本轮${recheckMode?'重新复核':'分析'}${selected.size}个真实观测，包括稀疏点及可选补充的真实时刻邻域。请联合历史比较竞争与接续，不按文件预设目标数；不能确定的保留待决或待证据，不因时间空白一律拆目标。先给出归属集合，等待确认后再独立授权绘图。`;
    $('chatConsent').checked=false;drawMenu();drawRows();invalidate();note(`已补选${added}点；请免费准备并检查数量与字符量。没有调用模型，原稀疏实验及旧结果仍保留。`);
  };
  $('samplePointMode').onchange=()=>{updateSamplingMode();$('pointSamplingStatus').textContent='仅切换抽样模式，尚未改变当前选择。';void save();};
  $('samplePoints').onclick=()=>{
    if(busy||state?.status==='awaiting_review'||!dataset)return;
    const status=$('pointSamplingStatus');
    try{
      const count=Number($('samplePointCount').value),items=sorted(visible());
      const paired=$('samplePointMode').value==='source_pairs',grouped=$('samplePointMode').value==='synchronous_groups';const sampled=paired?PointSampling.sampleSourcePairs(items,count,catalog()):grouped?PointSampling.sampleSynchronousGroups(items,count):PointSampling.sampleOrdered(items,count),ids=new Set(items.map(p=>p.candidate_id));
      selected=new Set([...selected].filter(id=>!ids.has(id)));for(const point of sampled)selected.add(point.candidate_id);
      drawRows();invalidate();
      status.textContent=`上次抽样：当前分组${paired?'选取'+count+'组 hull＋tow（每组算1点），共'+sampled.length+'条原始观测':grouped?'选取'+count+'组同步时刻、实际'+sampled.length+'点':'选取'+sampled.length+'个单独观测点（非双阵组）'}，全局已选${selected.size}点；其他分组已选点保留。未调用模型。`;note(status.textContent);
    }catch(error){status.textContent=error.message;note(error.message);}
  };
  $('selectAllPoints').onclick=()=>{if(busy||state?.status==='awaiting_review'||!dataset)return;const items=visible();for(const point of items)selected.add(point.candidate_id);drawRows();invalidate();note(`已全选当前浏览的${items.length}个观测点，保留其他已选点；尚未调用模型。`);};
  $('clearSelection').onclick=()=>{recheckMode=false;selected.clear();drawRows();invalidate();};$('showSelected').onclick=()=>{$('selectedList').open=true;$('selectedList').scrollIntoView({block:'nearest'});};
  $('chatQuestion').oninput=()=>{customQuestion=true;preview=null;void save();};$('chatConsent').onchange=controls;
  $('chatPrepare').onclick=()=>run(prepare);
  $('prepareAssociationRecheck').onclick=()=>run(async()=>{if(selectionLimit!==null&&state.submitted_ids.length>selectionLimit)throw new Error(`已提交${state.submitted_ids.length}个观测点，当前后端最多支持${selectionLimit}个，请重启后端并刷新页面加载不限点数的新版；未调用模型。`);recheckMode=true;selected=new Set(state.submitted_ids);customQuestion=true;$('chatQuestion').value='请重新联合复核所有已提交观测，比较断段接续、折返与弱峰；不要把片段编号数或阵列数当成物理目标数，证据不足保留暂定或未归属。';$('chatConsent').checked=false;render();await prepare();note('历史关联复核已免费准备。请确认授权后发送，届时会产生一次模型调用；旧结果不自动覆盖。');});
  $('recoverPlot').onclick=()=>run(async()=>{state=await request(`${API}/${state.session_id}/recover-model-plot`,{version:state.version});messages.push({role:'assistant',text:'已免费复核保存回复，未再次调用模型。'+(state.plot_status==='awaiting_association_recheck'?'模型提出了关联复核建议，请先复核片段与目标归属；当前不补造空间轨迹。':'可用结果与空间资料缺项说明已保存。')});render();await execution();});
  $('refreshRunningTask').onclick=()=>run(async()=>{if(!state)return;state=await request(API+'/'+state.session_id);render();await execution();note(['pending','plot_pending'].includes(state.status)?'后端仍在处理原调用，请稍后再次刷新；没有重新调用模型。':state.status==='awaiting_review'?'后台关联已完成，请核对并确认集合；本次刷新未调用模型。':'已刷新后台任务状态；本次刷新未调用模型。');});
  $('recoverAssociation').onclick=()=>run(async()=>{state=await request(`${API}/${state.session_id}/recover-association`,{version:state.version});messages.push({role:'assistant',text:'已从保存的模型回复本地恢复关联集合，未再次调用模型。请核对数据集合后确认。',round_revision:state.rounds.at(-1).revision});$('chatConsent').checked=false;preview=null;note('本地恢复完成，等待集合确认；未自动生成预测。');render();await execution();});
  $('chatSend').onclick=()=>run(async()=>{if(!$('chatConsent').checked)throw new Error('请先确认发送授权与API费用');if(!ready)throw new Error('当前后端未加载混合观测接口');await prepare();const sid=state.session_id,selectedIds=inputPoints().map(p=>p.candidate_id);messages.push({role:'user',text:$('chatQuestion').value});drawMessages();
    try{state=await paidStream(`${API}/${sid}/step`,{version:state.version,selected_ids:selectedIds,question:$('chatQuestion').value,recheck:recheckMode,consent:true,context_digest:preview.context_digest});messages.push({role:'assistant',text:'本轮归属判断完成，完整流式正文与执行过程已保留在聊天消息中。请确认集合，再单独授权模型绘图。',round_revision:state.rounds.at(-1).revision});preview=null;plotPreview=null;recheckMode=false;note('关联集合已保存，等待你确认；本轮尚未生成图或预测。');}
    catch(e){try{state=await request(API+'/'+sid);}catch{/* retain last confirmed state */}if(state?.status==='pending'){const hint='页面连接已中断，但后端原调用仍在运行；请点“刷新任务状态”，不要重复发送。';messages.push({role:'assistant',text:hint});throw new Error(hint);}if(state?.status==='awaiting_review'){preview=null;plotPreview=null;recheckMode=false;note('后台关联已完成并保存，请核对集合；无需重新调用模型。');return;}messages.push({role:'assistant',text:'本轮未完成或未通过校验，未自动重试。'+e.message});throw e;}
    finally{clearInterval(pollTimer);$('chatConsent').checked=false;if(state?.workspace_input){state=await request(API+'/'+sid);await syncWorkspaceCatalog();}await execution();render();}});
  for(const [id,adopt]of [['adoptTrajectory',true],['rejectTrajectory',false]])$(id).onclick=()=>run(async()=>{state=await request(`${API}/${state.session_id}/review`,{version:state.version,adopt,reason:$('reviewReason').value});messages.push({role:'assistant',text:adopt?'已确认各目标数据集合。尚未生成本轮轨迹线，请单独授权下方第二次模型调用；后端不再线性代画。':'已整轮打回并恢复此前轨迹、未归属池与待决关联；旧预测记录保留。'});$('modelPlotConsent').checked=false;plotPreview=null;$('reviewReason').value='';customQuestion=false;question();preview=null;render();await execution();});
  async function preparePlot(){plotPreview=await request(`${API}/${state.session_id}/plot-preview`,{version:state.version,purpose:$('modelPlotPurpose').value});drawSpatial();controls();note(plotPreview.can_generate_spatial?'第二次模型绘图资料已免费准备；已有空间候选，仍需核对与单独授权。':$('modelPlotPurpose').value==='bearing'?'方位诊断资料已免费准备；这不是空间路线，发送仍需单独授权。':'空间候选为0，尚未发送模型；请先补充证据并复核归属，不会为必为空的空间图扣一次调用。');}
  $('prepareModelPlot').onclick=()=>run(preparePlot);
  $('modelPlotConsent').onchange=controls;$('modelPlotPurpose').onchange=()=>{plotPreview=null;$('modelPlotConsent').checked=false;controls();};
  $('runModelPlot').onclick=()=>run(async()=>{
    if(!$('modelPlotConsent').checked)throw new Error('请单独确认第二次模型绘图调用与API费用');
    await preparePlot();if($('modelPlotPurpose').value==='spatial'&&!plotPreview.can_generate_spatial)throw new Error('空间证据不足，已阻止本次绘图模型发送；调用次数未增加。');const sid=state.session_id;
    try{state=await paidStream(`${API}/${sid}/plot-step`,{version:state.version,consent:true,context_digest:plotPreview.context_digest,purpose:$('modelPlotPurpose').value});messages.push({role:'assistant',text:state.rounds.at(-1).model_plot.summary+(state.rounds.at(-1).model_plot.input.output_goal==='bearing_diagnostic'?'\n本次按授权生成方位诊断，见下方模型方位诊断区；不是空间路线。':state.rounds.at(-1).model_plot.spatial_result?.status==='spatial_candidates'?'\n已返回通过双阵回投的模型空间候选图；仍需保留镜像和身份不确定性。':'\n本回复尚未形成可用空间图，方位内容仅留在折叠过渡预览。请查看主图区域的具体缺项，不把过渡图当作最终轨迹。'),plot_revision:state.rounds.at(-1).revision});note(state.rounds.at(-1).model_plot.input.output_goal==='bearing_diagnostic'?'方位诊断已保存；本次未申请空间路线，原空间版本保留。':state.rounds.at(-1).model_plot.spatial_result?.status==='spatial_candidates'?'已返回模型空间轨迹候选，尚非唯一真值。':'该回复尚未形成可核对的空间路线；未展示空白空间图，请在执行节点查看具体原因。');}
    catch(e){try{state=await request(API+'/'+sid);}catch{}if(state?.status==='plot_pending'){const hint='页面连接已中断，但后端绘图仍在运行；请点“刷新任务状态”，不要重复发送。';messages.push({role:'assistant',text:hint});throw new Error(hint);}messages.push({role:'assistant',text:'第二次模型绘图未完成或未通过校验；未自动重试，未回退到后端线性预测。'+(state?.status==='plot_invalid_response'?'\n校验说明：'+e.message.slice(0,300):'')});throw e;}
    finally{$('modelPlotConsent').checked=false;plotPreview=null;render();await execution();}
  });
  async function importPointFiles(files){
    const status=$('pointFileImportStatus');
    if(!files.length){status.textContent='未收到可读取的文件。若正在压缩包内拖拽，请先把ZIP解压到普通文件夹，再拖入其中的ZIP或使用选择文件；也可直接选择磁盘上的轨迹ZIP。';note(status.textContent);return;}
    if($('pointImportMode').value==='append'&&dataset){await appendFiles(files);return;}
    if(busy||appendBusy){status.textContent='当前操作尚未结束，本次未导入。请等待状态恢复后重新拖入；初始化或网络超时会显示原因，不会自动发送模型。';note(status.textContent);return;}
    await run(async()=>{
      const status=$('pointFileImportStatus');
      try{
        status.textContent=`正在读取 ${files.length}个文件并识别观测数据……`;
        if(files.length>16)throw new Error('每次请导入1至16个文件');
        if(files.some(f=>f.size>16*1024*1024))throw new Error('单个数据文件不能超过16MiB');
        if(files.reduce((n,f)=>n+f.size,0)>20*1024*1024)throw new Error('本次导入文件总大小不能超过20MiB');
        const body=new FormData();for(const file of files)body.append('files',file);
        const {response,data}=await fetchJson('/api/incremental/import-observations',{method:'POST',body},30000);
        if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'文件导入失败');
        const fileId=await PointFiles.put(data,{manual:true});await drawPointFiles();await load({...data,point_file_id:fileId});
        status.textContent=`已导入 ${files.length}个文件 · ${data.candidates.length}个原始观测 · ${data.target_catalog?.length||0}个浏览分组；下方已显示，未读取真值，未调用模型。`;
        note(status.textContent);
      }catch(error){status.textContent='导入失败：'+error.message;throw error;}
      finally{$('pointFileImport').value='';}
    });
  }
  $('pointFileImport').onchange=()=>void importPointFiles([...$('pointFileImport').files]);
  const dropZone=$('pointFileDropZone');let dragDepth=0;
  for(const type of ['dragenter','dragover'])dropZone.addEventListener(type,event=>{event.preventDefault();if(appendBusy||(busy&&!canAppend())){event.dataTransfer.dropEffect='none';return;}event.dataTransfer.dropEffect='copy';if(type==='dragenter')dragDepth++;dropZone.classList.add('drag-active');});
  dropZone.addEventListener('dragleave',event=>{event.preventDefault();if(--dragDepth<=0){dragDepth=0;dropZone.classList.remove('drag-active');}});
  dropZone.addEventListener('drop',event=>{event.preventDefault();dragDepth=0;dropZone.classList.remove('drag-active');void importPointFiles([...event.dataTransfer.files]);});
  // Prevent accidental navigation when files are dropped outside the import area.
  for(const type of ['dragover','drop'])window.addEventListener(type,event=>{if([...event.dataTransfer.types].includes('Files'))event.preventDefault();});
  $('refreshPointFiles').onclick=()=>run(drawPointFiles);
  window.addEventListener('focus',()=>{if(!busy)void drawPointFiles();});
  $('openNewConversation').onclick=()=>run(async()=>{await save();if(dataset){await load(dataset);}else{resetWorkspace();conversationDraft=true;}const url=new URL(location.href);url.searchParams.delete('source_result');url.searchParams.delete('empty');history.replaceState(null,'',url);await save();note('已新建独立会话（'+conversationId.slice(0,4)+'）。'+(dataset?'保留当前浏览数据，已重置分析记录和授权。':'请从下方独立文件库打开数据，或导入文件。')+'原会话和文件保留；未调用模型。');});
  $('sequenceAutoConnect').onchange=()=>{spatialLineReason='manual';drawSequenceWorkingPreview();void save();};
  $('sequenceMirrorSide').onchange=()=>{spatialLineReason='manual';sequenceSpatialView={zoom:1,dx:0,dy:0,scale:1};drawSequenceWorkingPreview();note('已切换当前镜像显示假设；未修改模型归属，未调用模型。');void save();};
  $('copySequenceContext').onclick=event=>copySequenceContext(event.currentTarget);$('copySequenceContextBottom').onclick=event=>copySequenceContext(event.currentTarget);
  $('sequenceSpatialZoomIn').onclick=()=>{sequenceSpatialView.zoom=Math.min(20,sequenceSpatialView.zoom*1.3);drawSequenceSpatial(sequenceRun?.working_view);};
  $('sequenceSpatialZoomOut').onclick=()=>{sequenceSpatialView.zoom=Math.max(.1,sequenceSpatialView.zoom/1.3);drawSequenceSpatial(sequenceRun?.working_view);};
  $('sequenceSpatialReset').onclick=()=>{sequenceSpatialView={zoom:1,dx:0,dy:0,scale:1};drawSequenceSpatial(sequenceRun?.working_view);};
  let sequenceSpatialDrag=null;
  $('sequenceSpatialChart').onpointerdown=event=>{if(event.button!==0||event.target.tagName==='circle')return;sequenceSpatialDrag={x:event.clientX,y:event.clientY,dx:sequenceSpatialView.dx,dy:sequenceSpatialView.dy};event.currentTarget.setPointerCapture(event.pointerId);};
  $('sequenceSpatialChart').onpointermove=event=>{if(!sequenceSpatialDrag)return;sequenceSpatialView.dx=sequenceSpatialDrag.dx-(event.clientX-sequenceSpatialDrag.x)/sequenceSpatialView.scale;sequenceSpatialView.dy=sequenceSpatialDrag.dy+(event.clientY-sequenceSpatialDrag.y)/sequenceSpatialView.scale;drawSequenceSpatial(sequenceRun?.working_view);};
  $('sequenceSpatialChart').onpointerup=$('sequenceSpatialChart').onpointercancel=()=>{sequenceSpatialDrag=null;};
  $('sharedTrajectoryScene').addEventListener('toggle',()=>{if($('sharedTrajectoryScene').open)drawChart();});
  $('spatialZoomIn').onclick=()=>{spatialView.zoom=Math.min(20,spatialView.zoom*1.25);drawSpatial();};
  $('spatialZoomOut').onclick=()=>{spatialView.zoom=Math.max(.2,spatialView.zoom/1.25);drawSpatial();};
  $('spatialReset').onclick=()=>{spatialView={zoom:1,dx:0,dy:0};drawSpatial();};
  let spatialDrag=null;
  $('spatialChart').addEventListener('wheel',event=>{if(!spatialRenderInfo)return;event.preventDefault();spatialView.zoom=Math.max(.2,Math.min(20,spatialView.zoom*(event.deltaY<0?1.15:1/1.15)));drawSpatial();},{passive:false});
  $('spatialChart').addEventListener('pointerdown',event=>{spatialDrag={x:event.clientX,y:event.clientY};event.currentTarget.setPointerCapture(event.pointerId);});
  $('spatialChart').addEventListener('pointermove',event=>{if(!spatialDrag||!spatialRenderInfo)return;spatialView.dx-=(event.clientX-spatialDrag.x)/spatialRenderInfo.scale;spatialView.dy+=(event.clientY-spatialDrag.y)/spatialRenderInfo.scale;spatialDrag={x:event.clientX,y:event.clientY};drawSpatial();});
  $('spatialChart').addEventListener('pointerup',()=>{spatialDrag=null;});$('spatialChart').addEventListener('pointercancel',()=>{spatialDrag=null;});
  const chartResize=$('chartSidebarResize'),chartWidthKey='mixed-chart-sidebar-width-v1';
  let chartPreferredWidth=460,chartDrag=null;
  try{const value=Number(localStorage.getItem(chartWidthKey));if(Number.isFinite(value)&&value>=280)chartPreferredWidth=value;}catch{}
  function applyChartWidth(value,persist=false){
    const workspace=document.querySelector('.prediction-workspace'),left=innerWidth>760&&!document.body.classList.contains('points-collapsed')?$('pointSidebar').getBoundingClientRect().width:0;
    const maximum=Math.max(280,Math.min(900,innerWidth<=1100?innerWidth-16:workspace.clientWidth-left-300));
    const width=Math.round(Math.max(280,Math.min(maximum,value)));
    document.body.style.setProperty('--chart-sidebar-width',width+'px');chartResize.setAttribute('aria-valuemax',String(Math.floor(maximum)));chartResize.setAttribute('aria-valuenow',String(width));
    if(persist){chartPreferredWidth=width;try{localStorage.setItem(chartWidthKey,String(width));}catch{}}
  }
  function chartSidebarVisible(){return innerWidth<=1100?document.body.classList.contains('charts-open'):!document.body.classList.contains('charts-collapsed');}
  function updateChartSidebarButton(){$('toggleChartSidebar').setAttribute('aria-expanded',String(chartSidebarVisible()));}
  function toggleChartSidebar(show=!chartSidebarVisible()){
    document.body.classList.toggle('charts-open',show);document.body.classList.toggle('charts-collapsed',!show);applyChartWidth(chartPreferredWidth);updateChartSidebarButton();
  }
  $('toggleChartSidebar').onclick=()=>toggleChartSidebar();$('closeChartSidebar').onclick=()=>toggleChartSidebar(false);
  chartResize.onpointerdown=event=>{if(event.button!==0)return;event.preventDefault();chartDrag={x:event.clientX,width:$('chartSidebarShell').getBoundingClientRect().width};chartResize.setPointerCapture(event.pointerId);chartResize.classList.add('dragging');document.body.classList.add('resizing-chart-sidebar');};
  chartResize.onpointermove=event=>{if(chartDrag)applyChartWidth(chartDrag.width+chartDrag.x-event.clientX);};
  const finishChartResize=()=>{if(!chartDrag)return;chartDrag=null;chartResize.classList.remove('dragging');document.body.classList.remove('resizing-chart-sidebar');applyChartWidth(Number(chartResize.getAttribute('aria-valuenow')),true);};
  chartResize.onpointerup=chartResize.onpointercancel=chartResize.onlostpointercapture=finishChartResize;
  chartResize.onkeydown=event=>{const width=Number(chartResize.getAttribute('aria-valuenow'));let next;if(event.key==='ArrowLeft')next=width+24;else if(event.key==='ArrowRight')next=width-24;else if(event.key==='Home')next=280;else if(event.key==='End')next=Number(chartResize.getAttribute('aria-valuemax'));else return;event.preventDefault();applyChartWidth(next,true);};
  chartResize.ondblclick=()=>applyChartWidth(460,true);
  window.addEventListener('resize',()=>{applyChartWidth(chartPreferredWidth);updateChartSidebarButton();});
  applyChartWidth(chartPreferredWidth);updateChartSidebarButton();
  let resizeFrame=0;if(globalThis.ResizeObserver){const observer=new ResizeObserver(()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(()=>{drawSequenceWorkingPreview();if($('sharedTrajectoryScene').open)drawChart();drawSpatial();});});for(const id of ['chatMessages','sharedTrajectoryScene','spatialTrajectoryScene','chartSidebar'])observer.observe($(id));}
  $('togglePointSidebar').onclick=()=>{document.body.classList.toggle(innerWidth<=760?'points-open':'points-collapsed');applyChartWidth(chartPreferredWidth);updateChartSidebarButton();};$('toggleExecutionSidebar').onclick=()=>{const block=executionBlocks.get(lastExecution?.execution_id);if(block){block.open=!block.open;$('toggleExecutionSidebar').setAttribute('aria-expanded',String(block.open));block.scrollIntoView({behavior:'smooth',block:'nearest'});}};
  run(async()=>{try{const [schema,health]=await Promise.all([request('/openapi.json'),request('/api/health')]);sequenceAvailable=!!schema.paths?.[API+'/{session_id}/runs/preview']&&health.mixed_sequential_protocol==='sequential-prefix-v1'&&health.mixed_continuation_policy==='tentative-local-v1'&&health.mixed_sequential_spatial==='sequential-spatial-v1';spatialCardsAvailable=health.mixed_spatial_card_protocol==='model-selected-spatial-cards-v1';retryAvailable=health.mixed_batch_retry_protocol==='retry-v1';batchAvailable=health.mixed_batch_snapshot_protocol==='batch-snapshot-v1';const maxItems=schema.components?.schemas?.MixedSelection?.properties?.selected_ids?.maxItems;selectionLimit=Number.isInteger(maxItems)&&maxItems>0?maxItems:null;ready=!!schema.paths?.[API+'/{session_id}/prepare-rerun']&&health.mixed_identity_protocol==='joint-candidate-v1'&&health.mixed_association_transport==='lossless-table-v1'&&health.mixed_input_policy==='accept-sparse-v1'&&health.mixed_workspace_input==='workspace-ledger-v1'&&health.mixed_plot_preflight==='purpose-capability-v1'&&health.mixed_review_protocol==='typed-review-scope-v2'&&!!schema.paths?.[API+'/{session_id}/spatial-image']&&!!schema.paths?.[API+'/{session_id}/recover-model-plot']&&!!schema.components?.schemas?.MixedSelection?.properties?.recheck&&!!schema.paths?.[API+'/{session_id}/plot-step']&&!!schema.paths?.[API+'/{session_id}/step']?.post?.parameters?.some(p=>p.name==='stream');$('backendReadiness').textContent=ready?`✓ 混合观测联合身份接口已就绪；${selectionLimit===null?'不设观测点数量上限':'旧后端每轮最多'+selectionLimit+'点，请重启升级'}；不会自动发送模型。`:'后端尚未加载当前工作台版本，请重启后端再刷新；已阻止付费发送。';}catch(e){$('backendReadiness').textContent='接口检查失败：'+e.message;}
    $('pointFileImportStatus').textContent='正在读取独立文件库和恢复会话；如旧标签页占用文件库，会显示重试提示。';
    try{pendingPlatform=await WorkspaceCache.get('mixed-pending-platform');await drawPointFiles();conversations=await WorkspaceCache.get('mixed-conversations')||[];const saved=await WorkspaceCache.get('mixed-workspace'),source=new URLSearchParams(location.search).get('source_result'),empty=new URLSearchParams(location.search).get('empty')==='1';if(!empty&&saved&&(!source||source===saved.dataset?.source_result_id))await restore(saved);else if(source){const batch=await request('/api/incremental/batches/'+encodeURIComponent(source));const fileId=await PointFiles.put(batch);await drawPointFiles();if(fileId)await load({...batch,point_file_id:fileId});else{question();render();note('该数据文件已删除，不自动重新导入；如需恢复，请手动导入。');}}else{question();render();}
    $('pointFileImportStatus').textContent='可拖入磁盘上的ZIP / JSON文件。若文件位于外层压缩包中，请先解压到普通文件夹；不会自动调用模型。';
    }catch(error){$('pointFileImportStatus').textContent='初始化未完成：'+error.message;throw error;}
  });
})();

