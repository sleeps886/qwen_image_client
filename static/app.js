'use strict';
const $=id=>document.getElementById(id);
let images=[],urls=[],busy=false;
let currentResultId='',currentTrajectoryStatus='',referenceUrl='',comparing=false;
function status(text,kind=''){ $('status').textContent=text;$('badge').textContent=kind==='ok'?'已完成':kind==='error'?'需要处理':busy?'处理中':'等待输入';$('badge').className='badge '+kind; }
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
async function submit(dryRun){
  if(busy || comparing)return;
  if(!images.length){status('请先选择图片。','error');return;}
  if(!$('prompt').value.trim()){status('提示词不能为空。','error');return;}
  if(!dryRun && !$('consent').checked){status('请先勾选云端传输与费用确认。','error');return;}
  const form=new FormData();images.forEach(file=>form.append('files',file));
  form.append('prompt',$('prompt').value);
  form.append('dry_run',String(dryRun));form.append('consent',String($('consent').checked));
  const platform=$('platform').files[0];
  if(platform){if(platform.size>100000){status('平台JSON超过100KB限制。','error');return;} form.append('platform',platform);}
  busy=true;document.querySelectorAll('input,textarea,button').forEach(x=>x.disabled=true);
  resetEvaluation();currentResultId='';currentTrajectoryStatus='';$('aiComparePreview').hidden=true;$('aiComparePreview').removeAttribute('src');$('aiCompareEmpty').hidden=false;resetComparison();
  $('trackFigure').hidden=true;$('trackImage').removeAttribute('src');$('trackWarnings').textContent='';
  $('downloads').replaceChildren();$('usage').textContent='';status(dryRun?'正在本地预检，不会调用模型服务…':'请求已提交本地服务，等待模型服务返回；请勿重复发送或关闭页面…');
  const correlationId=beginLogs();logTimer=setTimeout(()=>pollLogs(correlationId),300);
  try{
    const response=await fetch('/api/recognize',{method:'POST',body:form,headers:{'X-Request-ID':correlationId}});
    const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'请求失败，请检查表单。');
    if(data.dry_run){status(data.message,'ok');$('answer').textContent=JSON.stringify(data.images,null,2);}
    else{
      status('已保存结果，编号：'+data.result_id,'ok');$('answer').textContent=data.answer;
      currentResultId=data.result_id;currentTrajectoryStatus=data.trajectory_status;
      if(data.trajectory_image && data.trajectory_status==='position_estimate'){$('aiComparePreview').src=data.trajectory_image;$('aiComparePreview').hidden=false;$('aiCompareEmpty').hidden=true;}
      else $('aiCompareEmpty').textContent=data.trajectory_status==='bearing_time'?'当前仅有方位—时间图，不可与空间轨迹图评分':'本次未生成可比较的位置轨迹图';
      $('trackWarnings').textContent=(data.warnings||[]).join('\n');
      if(data.trajectory_image){$('trackImage').src=data.trajectory_image;$('trackFigure').hidden=false;$('trackCaption').textContent=data.trajectory_status==='bearing_time'?'仅方位—时间轨迹：不是地理空间轨迹':'模型估计的目标位置轨迹：未通过真值或物理一致性验证';}
      $('usage').textContent=data.usage?'Token用量：'+JSON.stringify(data.usage):'服务未返回Token用量';
      for(const [name,path] of Object.entries(data.downloads)){const link=document.createElement('a');link.textContent='下载 '+name;link.href=path;link.download=name;$('downloads').append(link);}
    }
  }catch(error){logLine('[浏览器] 请求失败：'+error.message);status(error.message==='Failed to fetch'?'连接中断。云端请求可能仍在执行，请核对调用记录后再决定是否重试。':error.message,'error');}
  finally{clearTimeout(logTimer);await pollLogs(correlationId);busy=false;clearTimeout(logTimer);document.querySelectorAll('input,textarea,button').forEach(x=>x.disabled=false);}
}
$('platform').addEventListener('change',()=>{$('consent').checked=false;});
$('send').addEventListener('click',()=>submit(false));$('inspect').addEventListener('click',()=>submit(true));
fetch('/api/health').then(r=>{if(!r.ok)throw new Error('服务不可用');return r.json();}).then(data=>{
  $('prompt').value=data.default_prompt;
  if($('consentText'))$('consentText').textContent='同意将所选图片、已选平台参数与提示词发送至'+data.provider_label+'，可能产生API费用。';
  $('keyState').textContent=data.key_configured?'后端配置已就绪':'请配置后端 .env 并重启';
  logLine('[启动检查] 本地服务在线；后端密钥'+(data.key_configured?'已配置。':'未配置，请编辑.env后重启。'));
}).catch(()=>{logLine('[启动检查] 无法连接本地服务。');status('服务连接失败，请重启本地FastAPI服务。','error');});

function resetComparison(){
  $('similarityScore').textContent='—';$('comparisonDetails').hidden=true;
  $('comparisonStatus').textContent='等待比较。请确认参考图与AI输入来自同一个样本。';
}
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
  if(!currentResultId || currentTrajectoryStatus!=='position_estimate'){$('comparisonStatus').textContent='请先生成AI位置轨迹图；方位—时间图不能与地理轨迹图比较。';return;}
  if(!file){$('comparisonStatus').textContent='请选择同一样本的仿真参考轨迹图。';return;}
  if(file.size>10*1024*1024){$('comparisonStatus').textContent='参考图不能超过10MiB。';return;}
  comparing=true;$('compareButton').disabled=true;$('referenceTrack').disabled=true;$('send').disabled=true;$('inspect').disabled=true;
  resetComparison();$('comparisonStatus').textContent='正在本地提取彩色轮廓并计算相似度，不调用模型…';
  const form=new FormData();form.append('result_id',currentResultId);form.append('reference',file);
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
