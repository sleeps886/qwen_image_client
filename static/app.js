'use strict';
const $=id=>document.getElementById(id);
let images=[],urls=[],busy=false;
let currentResultId='',currentTrajectoryStatus='',referenceUrl='',comparing=false;
let activeMode='images',promptDrafts={images:'',data:''};
let defaultImagePrompt='';
const dataMode=()=>$('inputMode').value==='data';
function syncPlatformRemoval(){
  const selected=!!$('platform').files[0];
  $('removePlatform').hidden=!selected;
  $('removePlatform').disabled=busy||comparing||!selected;
  if(dataMode())$('fileCount').textContent=selected?'已选 1 份数据，无需图片':'0 / 1 份数据，无需图片';
}
function updateInputMode(){
  const mode=dataMode()?'data':'images';
  promptDrafts[activeMode]=$('prompt').value;activeMode=mode;
  $('prompt').value=promptDrafts[mode];
  $('imageInputs').hidden=mode==='data';$('dataInputNote').hidden=mode!=='data';
  $('fileCount').textContent=mode==='data'?'仅 1 份数据，无需图片':images.length+' / 8 张';
  $('dataFileLabel').textContent=mode==='data'?'目标数据文件（必选，仅一份）':'已知平台参数（可选，建议提供）';
  $('platform').accept=mode==='data'?'.json,.jsonl,.js,application/json,application/javascript':'.json,application/json';
  $('dataFileHint').textContent=mode==='data'?'完整轨迹请选meta.json（最多1MiB）；semantic.json只有起终点摘要，platform.json只有平台参数。也支持点位JSON/JSONL。':'请选择同一样本 input/platform.json；用于定位，不是目标真值。';
  $('send').textContent=mode==='data'?'发送数据并生成轨迹图 ↗':'流式识别并生成轨迹 ↗';
  $('inspect').textContent=mode==='data'?'仅预检数据':'仅预检图片';
  $('consent').checked=false;
  syncPlatformRemoval();
  status(mode==='data'?'请选择下方目标数据文件；本模式不会发送已选图片。':'图片模式：请选择声呐图片，可先预检。');
}
$('inputMode').addEventListener('change',updateInputMode);
function autoSelectDataMode(keepConsent=false){
  if(dataMode() || images.length || !$('platform').files[0])return;
  const consent=$('consent').checked,currentPrompt=$('prompt').value;
  $('inputMode').value='data';updateInputMode();
  if(currentPrompt.trim() && currentPrompt!==defaultImagePrompt)$('prompt').value=currentPrompt;
  if(keepConsent)$('consent').checked=consent;
  status('已检测到仅选择数据文件，自动切换为单文件绘图，无需上传图片。');
}
function status(text,kind=''){ $('status').textContent=text;$('badge').textContent=kind==='ok'?'已完成':kind==='error'?'需要处理':kind==='stopped'?'已停止':busy?'处理中':'等待输入';$('badge').className='badge '+kind; }
let requestLogId='',logCursor=0,logTimer=null;
function logLine(text){
  const output=$('logOutput');output.textContent+=(output.textContent?'\n':'')+text;
  if(output.textContent.length>80000)output.textContent=output.textContent.slice(-60000);
  output.scrollTop=output.scrollHeight;
}
async function pollLogs(id){
  if(id!==requestLogId)return;
  try{
    const response=await fetch('/api/logs/'+encodeURIComponent(id)+'?after='+logCursor,{signal:AbortSignal.timeout(6000)});
    if(response.ok){
      const data=await response.json();if(id!==requestLogId)return;
      for(const event of data.events){
        logCursor=Math.max(logCursor,event.seq);
        logLine(event.time+' +'+event.elapsed_s+'s ['+event.level+'] '+event.message);
      }
      if(data.done)return;
    }
  }catch{/* Network status is also reported by the main request; avoid log spam. */}
  if(id===requestLogId && busy)logTimer=setTimeout(()=>pollLogs(id),800);
}
function beginLogs(){
  clearTimeout(logTimer);requestLogId=crypto.randomUUID();logCursor=0;
  $('logOutput').textContent='';logLine('[浏览器] 正在上传，等待后端接收。');
  const download=$('downloadLogs');download.href='/api/logs/'+requestLogId+'/download';download.hidden=false;download.download='request.log';
  return requestLogId;
}
$('clearLogs').addEventListener('click',()=>{$('logOutput').textContent='';});
function selectImages(files){
  if(busy)return;
  const selected=Array.from(files);
  if(!selected.length)return;
  if(selected.length>8 || selected.some(x=>x.size>10*1024*1024) || selected.reduce((s,x)=>s+x.size,0)>20*1024*1024){status('最多8张图片；单图10MiB、合计20MiB。','error');return;}
  if(selected.some(x=>!['image/png','image/jpeg','image/webp'].includes(x.type))){status('只支持PNG、JPEG、WebP图片。','error');return;}
  urls.forEach(URL.revokeObjectURL);urls=[];images=selected;$('consent').checked=false;$('previews').replaceChildren();
  for(const file of images){
    const box=document.createElement('div');box.className='preview';
    const img=document.createElement('img');img.src=URL.createObjectURL(file);urls.push(img.src);img.alt=file.name;
    const name=document.createElement('div');name.textContent=file.name;name.title=file.name;
    const size=document.createElement('small');size.textContent=(file.size/1024/1024).toFixed(2)+' MiB';
    box.append(img,name,size);$('previews').append(box);
  }
  $('fileCount').textContent=images.length+' / 8 张';status('已选择 '+images.length+' 张图片，尚未调用模型。');
}
$('files').addEventListener('change',event=>selectImages(event.target.files));
$('dropZone').addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();if(!busy)$('files').click();}});
for(const kind of ['dragover','dragleave','drop'])$('dropZone').addEventListener(kind,event=>{
  event.preventDefault();$('dropZone').classList.toggle('dragover',kind==='dragover');if(kind==='drop')selectImages(event.dataTransfer.files);
});
let streamAbort=null;
function showModelResult(data){
  $('bodyOutputHint').textContent='模型正文已接收完毕，绘图状态见结果和告警。';
  status('已保存结果，编号：'+data.result_id,'ok');$('answer').textContent=data.answer.slice(0,4000000);
  currentResultId=data.result_id;currentTrajectoryStatus=data.trajectory_status;
  if(data.trajectory_image){$('aiComparePreview').src=data.trajectory_image;$('aiComparePreview').hidden=false;$('aiCompareEmpty').hidden=true;}
  else $('aiCompareEmpty').textContent='本次未生成轨迹图';
  $('aiCoordinateLabel').textContent=data.input_mode==='data'?'（用户数据绘图，非声呐反演）':data.trajectory_status==='bearing_time'?'（方位—时间，非地理轨迹）':'（位置估计）';
  $('trackWarnings').textContent=(data.warnings||[]).join('\n');
  if(data.trajectory_image){$('trackImage').src=data.trajectory_image;$('trackFigure').hidden=false;$('trackCaption').textContent=data.trajectory_status==='bearing_time'?'仅方位—时间轨迹：不是地理空间轨迹':'模型估计位置轨迹，未通过真值或物理一致性验证';}
  if(data.trajectory_image && data.input_mode==='data')$('trackCaption').textContent='用户文件数据图：静止对象显示为一个点；不是声呐反演或独立盲测结果。';
  if(data.input_mode==='data' && data.input_data?.plot_kind==='endpoints'){
    $('aiCoordinateLabel').textContent='（仿真参考起终点，非完整轨迹）';
    $('trackCaption').textContent='仅目标起终点示意：圆点为t=0，叉号为t=T_MAX；方块为观测平台。没有中间点位，不连成完整轨迹。';
  }
  if(data.input_mode==='data' && data.input_data?.plot_kind==='meta_reconstruction'){
    $('aiCoordinateLabel').textContent='（meta完整轨迹重建，非声呐反演）';
    $('trackCaption').textContent='按meta.json中的航点／曲线公式和DT完整采样绘图，含中间曲线；模型仅核对少量采样点，不补造轨迹。';
  }
  $('usage').textContent=data.usage?'Token用量：'+JSON.stringify(data.usage):'服务未返回Token用量';
  for(const [name,path] of Object.entries(data.downloads)){const a=document.createElement('a');a.textContent='下载 '+name;a.href=path;a.download=name;$('downloads').append(a);}
}
async function readModelStream(response){
  if(!response.body)throw new Error('浏览器未提供流式读取能力');
  const reader=response.body.getReader(),decoder=new TextDecoder('utf-8');
  let buffer='',completed=false,bodyChars=0,reasonChars=0,phase='connecting';
  function receive(frame){
    const payload=frame.split('\n').filter(x=>x.startsWith('data:')).map(x=>x.slice(5).trimStart()).join('\n');
    if(!payload)return;
    const event=JSON.parse(payload);
    if(event.event==='phase'){
      phase=event.phase;
      logLine('[流式] '+event.message);status(event.message);
      if(event.phase==='buffered')$('trackWarnings').textContent='中转站未提供真正SSE，当前只能整包返回，非逐片输出。';
    }else if(event.event==='heartbeat'){
      logLine('[连接心跳] 已等待 '+event.elapsed_s+' 秒；尚未收到新的可显示事件。');
      if(!completed && phase!=='rendering'){
        if(reasonChars>0 && bodyChars===0)status('已收到 '+reasonChars+' 字符模型推理文本，但尚未收到最终正文；还不能生成轨迹图。已等待 '+event.elapsed_s+' 秒。');
        else if(bodyChars===0)status((phase==='connecting'?'等待中转站HTTP响应头':'上游连接已建立，尚未收到正文')+'；已等待 '+event.elapsed_s+' 秒。');
        else status('已收到正文 '+bodyChars+' 字符，等待上游继续输出或结束标记；已等待 '+event.elapsed_s+' 秒。');
      }
    }else if(event.event==='delta'){
      $('bodyOutputHint').textContent='正在实时接收最终正文；收到完整JSON并结束后才绘图。';
      $('answer').dataset.waiting='false';
      if(bodyChars===0)logLine('[流式] 收到首个正文片段。');
      const text=event.text.slice(0,Math.max(0,4000000-bodyChars));bodyChars+=text.length;
      $('answer').append(document.createTextNode(text));
      status('模型正文正在实时返回，已显示 '+bodyChars+' 字符；结束后自动解析轨迹。');
    }else if(event.event==='reasoning'){
      if(reasonChars===0)logLine('[流式] 收到模型服务返回的推理文本，正文可能尚未开始。');
      $('providerReasoningSection').hidden=false;
      const text=event.text.slice(0,Math.max(0,1000000-reasonChars));reasonChars+=text.length;
      $('providerReasoning').append(document.createTextNode(text));
      if(bodyChars===0){
        status('模型正在返回推理文本（'+reasonChars+'字符），最终正文尚未开始，轨迹图尚不能生成。');
        $('answer').dataset.waiting='true';
        $('bodyOutputHint').textContent='当前只有推理文本，尚未返回可用于绘图的正文JSON。';
      }
    }else if(event.event==='error'){
      throw new Error(event.message+' 已显示文本可能不完整，未自动重试。');
    }else if(event.event==='result'){
      completed=true;showModelResult(event.data);logLine('[流式] 已接收最终结果并完成本地处理。');
    }
  }
  try{
    while(true){
      const {done,value}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});
      let end;while((end=buffer.indexOf('\n\n'))>=0){const frame=buffer.slice(0,end);buffer=buffer.slice(end+2);receive(frame);}
      if(buffer.length>32000000)throw new Error('单个流式事件过大，已停止本地接收');
      if(done)break;
    }
    if(buffer.trim())receive(buffer);
    if(!completed)throw new Error('响应流结束，但未收到最终结果；已显示内容可能不完整。');
  }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
}
$('stopStream').addEventListener('click',()=>{
  if(!streamAbort || streamAbort.signal.aborted)return;
  $('stopStream').disabled=true;$('stopStream').textContent='正在停止…';
  status('正在停止本地接收，已收到内容将保留；不保证上游停止计费。');
  streamAbort.abort();
});
async function submit(dryRun){
  if(busy || comparing)return;
  autoSelectDataMode(true);
  const isData=dataMode(),platform=$('platform').files[0];
  if(isData && !platform){status('请先选择一份目标数据文件。','error');return;}
  if(!isData && !images.length){status('请先选择图片，或切换到单文件绘图模式。','error');return;}
  if(!$('prompt').value.trim()){status('提示词不能为空。','error');return;}
  if(!dryRun && !$('consent').checked){status('请先勾选云端传输与费用确认。','error');return;}
  const form=new FormData();if(!isData)images.forEach(file=>form.append('files',file));
  form.append('input_mode',isData?'data':'images');
  form.append('prompt',$('prompt').value);
  form.append('dry_run',String(dryRun));form.append('consent',String($('consent').checked));
  if(platform){if(platform.size>(isData?1024*1024:100000)){status(isData?'数据文件超过1MiB限制。':'平台JSON超过100KB限制。','error');return;} form.append('platform',platform);}
  busy=true;document.querySelectorAll('input,textarea,button,select').forEach(x=>x.disabled=true);
  resetEvaluation();currentResultId='';currentTrajectoryStatus='';$('aiComparePreview').hidden=true;$('aiComparePreview').removeAttribute('src');$('aiCompareEmpty').hidden=false;resetComparison();
  $('trackFigure').hidden=true;$('trackImage').removeAttribute('src');$('trackWarnings').textContent='';
  $('downloads').replaceChildren();$('usage').textContent='';status(dryRun?'正在本地预检，不会调用模型服务…':'请求已提交本地服务，等待模型服务返回；请勿重复发送或关闭页面…');
  const correlationId=beginLogs();
  if(dryRun)logTimer=setTimeout(()=>pollLogs(correlationId),300);
  $('bodyOutputHint').textContent='等待模型正文；推理文本不作为轨迹数据。';
  $('answer').textContent='';$('providerReasoning').textContent='';$('providerReasoningSection').hidden=true;
  streamAbort=new AbortController();$('stopStream').disabled=dryRun;
  try{
    const response=await fetch(dryRun?'/api/recognize':'/api/recognize/stream',{method:'POST',body:form,headers:{'X-Request-ID':correlationId},signal:streamAbort.signal});
    if(!response.ok){
      let message='请求失败（HTTP '+response.status+'）';
      try{const data=await response.json();if(typeof data.detail==='string')message=data.detail;}catch{}
      if(response.status===404 && !dryRun)message='当前后端尚未加载流式接口，请停止旧服务并重新启动。';
      throw new Error(message);
    }
    if(dryRun){const data=await response.json();status(data.message,'ok');$('answer').textContent=JSON.stringify(data.input_data||data.images,null,2);}
    else if(!(response.headers.get('content-type')||'').includes('text/event-stream'))throw new Error('本地服务未返回SSE，请确认已重启新版后端。');
    else await readModelStream(response);
  }catch(error){
    if(error.name==='AbortError'){
      logLine('[浏览器] 用户已停止本地接收，保留已收到的文本。');
      $('bodyOutputHint').textContent='已停止接收。以下为不完整输出，不作为完整轨迹结果。';
      status('已停止本地接收，已收到的内容保留；不保证上游停止处理或计费。','stopped');
    }else{
      logLine('[浏览器] 请求失败：'+error.message);status(error.message==='Failed to fetch'?'连接中断。云端请求可能仍在执行，请核对调用记录后再决定是否重试。':error.message,'error');
    }
  }
  finally{clearTimeout(logTimer);busy=false;streamAbort=null;document.querySelectorAll('input,textarea,button,select').forEach(x=>x.disabled=false);$('stopStream').disabled=true;$('stopStream').textContent='■ 停止生成';syncPlatformRemoval();}
}
$('platform').addEventListener('change',()=>{
  $('consent').checked=false;
  autoSelectDataMode();
  syncPlatformRemoval();
  if(dataMode() && $('platform').files[0])status('已选择目标数据文件，可先预检，或勾选确认后发送生成图片。');
});
$('removePlatform').addEventListener('click',()=>{
  if(busy||comparing)return;
  $('platform').value='';
  $('consent').checked=false;
  syncPlatformRemoval();
  status(dataMode()?'已移除数据文件，请重新选择。磁盘上的原文件未删除。':'已移除平台参数文件，可继续仅用图片识别。磁盘上的原文件未删除。');
});
$('send').addEventListener('click',()=>submit(false));$('inspect').addEventListener('click',()=>submit(true));
fetch('/api/health').then(r=>{if(!r.ok)throw new Error('服务不可用');return r.json();}).then(data=>{
  $('prompt').value=data.default_prompt;
  promptDrafts={images:data.default_prompt,data:data.data_prompt||''};
  defaultImagePrompt=data.default_prompt;
  if(dataMode())$('prompt').value=promptDrafts.data;
  autoSelectDataMode();
  syncPlatformRemoval();
  if($('consentText'))$('consentText').textContent='同意将当前模式所选图片或数据文件、参数与提示词发送至'+data.provider_label+'，可能产生API费用；单文件绘图不属于独立盲测。';
  const apiLabel=data.api_format==='anthropic'?'Anthropic Messages':'OpenAI兼容';
  $('keyState').textContent=data.key_configured?'后端配置已就绪 · '+apiLabel:'请配置后端 .env 并重启';
  logLine('[启动检查] 当前接口格式：'+apiLabel+'。修改.env后必须重启后端，刷新页面不会重载进程配置。');
  logLine('[启动检查] 本地服务在线；后端密钥'+(data.key_configured?'已配置。':'未配置，请编辑.env后重启。'));
}).catch(()=>{logLine('[启动检查] 无法连接本地服务。');status('服务连接失败，请重启本地FastAPI服务。','error');});

function resetComparison(){
  $('similarityScore').textContent='—';$('comparisonDetails').hidden=true;
  $('comparisonStatus').textContent='等待比较。请确认参考图与AI输入来自同一个样本。';
}
$('referenceKind').addEventListener('change',resetComparison);
$('referenceTrack').addEventListener('change',()=>{
  resetComparison();
  if(referenceUrl)URL.revokeObjectURL(referenceUrl);
  const file=$('referenceTrack').files[0];
  $('referencePreview').hidden=!file;$('referenceEmpty').hidden=!!file;
  if(file){referenceUrl=URL.createObjectURL(file);$('referencePreview').src=referenceUrl;}
});
$('compareButton').addEventListener('click',async()=>{
  if(comparing || busy)return;
  const file=$('referenceTrack').files[0];
  if(!currentResultId || !['position_estimate','bearing_time'].includes(currentTrajectoryStatus)){$('comparisonStatus').textContent='请先生成或导入AI轨迹图。';return;}
  const expected=currentTrajectoryStatus==='bearing_time'?'bearing_time':'position';
  if($('referenceKind').value!==expected){$('comparisonStatus').textContent='两图类型不一致：方位—时间不能和地理轨迹评分。请选择同类型参考图，并核对参考图类型。';return;}
  if(!file){$('comparisonStatus').textContent='请选择同一样本的仿真参考轨迹图。';return;}
  if(file.size>10*1024*1024){$('comparisonStatus').textContent='参考图不能超过10MiB。';return;}
  comparing=true;$('compareButton').disabled=true;$('referenceTrack').disabled=true;$('send').disabled=true;$('inspect').disabled=true;
  resetComparison();$('comparisonStatus').textContent='正在本地提取彩色轮廓并计算相似度，不调用模型…';
  const form=new FormData();form.append('result_id',currentResultId);form.append('reference',file);form.append('reference_kind',$('referenceKind').value);
  try{
    const response=await fetch('/api/compare',{method:'POST',body:form});
    const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'比较失败');
    $('similarityScore').textContent=data.similarity_percent.toFixed(2);
    $('comparisonStatus').textContent='轮廓比较完成。此分数不是坐标准确率，也不代表目标识别正确率。';
    $('comparisonMetrics').textContent='AI轮廓匹配率 '+data.ai_outline_match_percent+'% · 参考轮廓覆盖率 '+data.reference_coverage_percent+'% · 256×256归一化，容差3像素';
    $('comparisonWarnings').textContent=data.warnings.join(' ');
    $('comparisonOverlay').src=data.overlay_url;$('comparisonReport').href=data.report_url;$('comparisonReport').download='comparison.json';
    $('comparisonDetails').hidden=false;
  }catch(error){$('comparisonStatus').textContent=error.message;}
  finally{comparing=false;$('compareButton').disabled=false;$('referenceTrack').disabled=false;$('send').disabled=false;$('inspect').disabled=false;}
});

function resetEvaluation(){
  $('evaluationDetails').hidden=true;
  $('evaluationStatus').textContent='至少选择一种参考文件，且确认与模型输入属于同一样本。';
}
for(const id of ['semanticReference','truthReference','matchGate','interpolationGap'])$(id).addEventListener('change',resetEvaluation);
$('evaluateButton').addEventListener('click',async()=>{
  if(busy || comparing)return;
  if(!currentResultId){$('evaluationStatus').textContent='请先生成本次AI轨迹数据。';return;}
  const semantic=$('semanticReference').files[0],truth=$('truthReference').files[0];
  if(!semantic && !truth){$('evaluationStatus').textContent='至少选择semantic.json或逐时刻truth.jsonl。';return;}
  if((semantic && semantic.size>2*1024*1024)||(truth && truth.size>16*1024*1024)){$('evaluationStatus').textContent='semantic最多2MiB，truth最多16MiB。';return;}
  if(!$('matchGate').reportValidity() || !$('interpolationGap').reportValidity())return;
  const form=new FormData();form.append('result_id',currentResultId);form.append('gate_m',$('matchGate').value);form.append('max_gap_s',$('interpolationGap').value);
  if(semantic)form.append('semantic',semantic);if(truth)form.append('truth',truth);
  comparing=true;for(const id of ['evaluateButton','compareButton','send','inspect'])$(id).disabled=true;
  resetEvaluation();$('evaluationStatus').textContent='正在本地核对参考、匹配航迹并计算误差，不调用模型…';
  try{
    const response=await fetch('/api/evaluate',{method:'POST',body:form});const data=await response.json();
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'评测失败，请检查文件与参数');
    $('evaluationStatus').textContent='结构化评测完成；请同时查看误差、未匹配目标和时间覆盖率。';
    const metrics=data.position_metrics;
    const items=[['目标数量（参考 / AI）',data.reference_target_count+' / '+data.predicted_target_count],['计数绝对误差',data.target_count_absolute_error]];
    if(metrics)items.push(['匹配目标数',metrics.matched_targets],['位置RMSE（米）',metrics.rmse_m??'无匹配'],['P95误差（米）',metrics.p95_error_m??'无匹配'],['总体时间覆盖率',metrics.overall_time_coverage_percent+'%']);
    $('evaluationSummary').replaceChildren();
    for(const [label,value] of items){const box=document.createElement('div');const title=document.createElement('small');title.textContent=label;const strong=document.createElement('strong');strong.textContent=value;box.append(title,strong);$('evaluationSummary').append(box);}
    $('evaluationPositionStatus').textContent='位置评测：'+(data.position_status||'未执行');
    $('evaluationPairs').replaceChildren();
    for(const pair of metrics?.matches||[]){const row=document.createElement('tr');for(const value of [pair.truth_id,pair.predicted_id,pair.rmse_m,pair.p95_error_m,pair.time_coverage_percent+'%']){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}$('evaluationPairs').append(row);}
    $('evaluationUnmatched').textContent=metrics?'未匹配真值目标：'+(metrics.unmatched_truth_ids.join('、')||'无')+'；未匹配AI目标：'+(metrics.unmatched_predicted_ids.join('、')||'无'):'';
    $('evaluationWarnings').textContent=data.warnings.join(' ');
    $('evaluationRaw').textContent=JSON.stringify(data,null,2);$('evaluationDownload').href=data.report_url;$('evaluationDownload').download='evaluation.json';$('evaluationDetails').hidden=false;
  }catch(error){$('evaluationStatus').textContent=error.message;}
  finally{comparing=false;for(const id of ['evaluateButton','compareButton','send','inspect'])$(id).disabled=false;}
});

async function refreshHistory(){
  try{
    const response=await fetch('/api/history');if(!response.ok)return;
    const data=await response.json();const select=$('historyResults');
    for(const item of data.items){const option=document.createElement('option');option.value=item.result_id;option.textContent=item.result_id+' · '+(item.trajectory_status==='bearing_time'?'方位—时间图':'位置轨迹图');select.append(option);}
  }catch{/* History is optional; never trigger a paid retry. */}
}
$('loadHistory').addEventListener('click',async()=>{
  if(busy || comparing)return;
  const id=$('historyResults').value;if(!id){$('comparisonStatus').textContent='请先选择已生成的结果。';return;}
  $('loadHistory').disabled=true;
  try{
    const response=await fetch('/api/history/'+encodeURIComponent(id));const data=await response.json();
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'导入失败');
    resetComparison();resetEvaluation();$('downloads').replaceChildren();showModelResult(data);
    $('comparisonStatus').textContent='已导入生成结果。请选择同一样本、同一坐标类型的参考图；未调用模型。';
  }catch(error){$('comparisonStatus').textContent=error.message;}
  finally{$('loadHistory').disabled=false;}
});
refreshHistory();
