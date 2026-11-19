'use strict';
const $=id=>document.getElementById(id);
let images=[],urls=[],busy=false;
let currentResultId='',currentTrajectoryStatus='',referenceUrl='',comparing=false;
let activeMode='images',promptDrafts={images:'',data:''};
let defaultImagePrompt='';
const nodeDefinitions=[['knowledge_retrieval','知识规则检索'],['pixel_candidates','绘图区与候选提取'],['tracklet_linking','亮带连续性分析'],['count_hypothesis','数量线索判定'],['association_context','关联上下文组装'],['model_association','模型清单与双阵关联'],['pair_validation','配对归属与同步校验'],['count_audit','数量与覆盖复核'],['identity_reconciliation','跨时段身份与串线复核'],['spatial_localization','双阵空间定位'],['continuous_trajectory_estimation','连续轨迹估计'],['reprojection_quality_check','重投影质检'],['result_summary','数量与定位汇总'],['render','空间候选绘图']];
let executionNodes=new Map();
const inverseNodeDefinitions=[['inverse_input','反演输入与标定'],['inverse_field','完整亮带剖面测量'],['inverse_paths','展开角路径与交叉假设'],['inverse_association','双阵轨迹级关联候选'],['inverse_fit','批量运动模型反演'],['inverse_refine','全局归属与有界迭代'],['inverse_review','可选模型假设复核'],['inverse_quality','正向BTR与可辨识性检查'],['inverse_render','空间候选绘图与汇总']];
let agentResultMetadata=null;
function updateAgentOverview(){
  const entries=[...executionNodes.entries()];
  const inverse=entries.some(([id])=>id.startsWith('inverse_'))||(!entries.length&&forwardMode());
  const definitions=inverse?inverseNodeDefinitions:nodeDefinitions;
  const isData=agentResultMetadata?.input_mode==='data'||(!entries.length&&dataMode());
  const finished=entries.filter(([,event])=>['completed','failed','skipped','stopped'].includes(event.status)).length;
  const current=[...entries].reverse().find(([,event])=>event.status==='running')||[...entries].reverse().find(([,event])=>event.status==='failed'||event.status==='stopped');
  const label=current?definitions.find(([id])=>id===current[0])?.[1]:null;
  $('agentModeName').textContent=isData?'单文件绘图':inverse?'完整本地反演':'模型关联';
  const state=$('resultPanel').dataset.state;
  $('agentCurrentNode').textContent=state==='error'?'任务未完成 · 查看日志':label||(state==='completed'?'任务已结束 · 结果待核验':state==='preflight'?'本地预检已结束':entries.length?'等待后续事件':'尚未收到节点事件');
  $('agentNodeCount').textContent=isData?finished+' 个记录 · 直接绘图':finished+' / '+definitions.length;
  $('agentNodeProgress').max=isData?Math.max(entries.length,1):definitions.length;
  $('agentNodeProgress').value=finished;
  const model=executionNodes.get(inverse?'inverse_review':'model_association');
  const calls=agentResultMetadata?.new_model_calls;
  $('agentModelState').textContent=Number.isInteger(calls)?calls+' 次（本次记录）':model?({running:'调用中',completed:'调用已结束',failed:'调用失败',skipped:'未调用 · 已跳过',stopped:'本地接收已停止'}[model.status]||'等待事件'):inverse?'本地计算 · 复核可选':'尚未收到调用记录';
}
function drawNodes(){
  $('nodePanel').hidden=!executionNodes.size;$('nodeList').replaceChildren();
  const statusNames={pending:'待执行',running:'执行中',completed:'已执行',failed:'失败',skipped:'已跳过',stopped:'已停止'};
  const definitions=[...executionNodes.keys()].some(id=>id.startsWith('inverse_'))?inverseNodeDefinitions:nodeDefinitions;
  $('nodeHelp').textContent=definitions===inverseNodeDefinitions?'完整正向反演由本地程序执行；只有明确申请的“可选模型假设复核”可能调用一次云端模型，跳过表示没有调用。节点完成和BTR一致性不代表独立位置精度合格。':'仅“模型清单与双阵关联”调用大模型；其余为内置规则、启发式或数值程序。记录执行不等于验证正确，重投影不是独立准确性评测；未自动付费重试。';
  for(const [id,label] of definitions){const event=executionNodes.get(id)||{status:'pending'},row=document.createElement('li'),title=document.createElement('strong'),state=document.createElement('span'),time=document.createElement('small'),detail=document.createElement('p');row.className='node-row '+event.status;title.textContent=label;state.textContent=statusNames[event.status]||'未知';time.textContent=Number.isFinite(event.elapsed_ms)?(event.elapsed_ms<10?(event.elapsed_ms===0?'＜1毫秒（旧记录取整）':event.elapsed_ms.toFixed(3)+'毫秒'):(event.elapsed_ms/1000).toFixed(2)+'秒'):'';detail.textContent=(['model_association','inverse_review'].includes(id)?'[模型网关] ':['knowledge_retrieval'].includes(id)?'[内置规则] ':['pixel_candidates','tracklet_linking','count_hypothesis','identity_reconciliation'].includes(id)?'[本地启发式] ':'[本地程序] ')+(event.summary||'');row.append(title,state,time,detail);$('nodeList').append(row);}
  updateAgentOverview();
}
function receiveNode(event){
  if(![...nodeDefinitions,...inverseNodeDefinitions].some(([id])=>id===event.node)||!['running','completed','failed','skipped','stopped'].includes(event.status))return;
  executionNodes.set(event.node,{status:event.status,elapsed_ms:typeof event.elapsed_ms==='number'?event.elapsed_ms:undefined,summary:typeof event.summary==='string'?event.summary.slice(0,300):''});drawNodes();
}
function showExecution(events){executionNodes=new Map();for(const event of events||[])receiveNode(event);drawNodes();}
function showTargetCount(report){
  $('targetCountPanel').hidden=!report;$('targetCountMetrics').replaceChildren();if(!report)return;
  const range=report.image_hypothesis,number=v=>Number.isInteger(v)?String(v):'—';
  for(const [label,value] of [['亮带数量线索（未确认）',range?number(range.min)+'～'+number(range.max):'—'],['清单疑似目标（含未配对）',number(report.inventory_target_count)],[report.count_origin==='numeric_hypotheses'?'程序关联假设（非模型判断）':'模型原始关联ID',number(report.associated_target_count)],['连续身份假设（非真实目标数）',number(report.continuous_identity_count)],['可定位身份假设（不含镜像重复）',number(report.localized_target_count)]]){const card=document.createElement('div'),small=document.createElement('small'),strong=document.createElement('strong');small.textContent=label;strong.textContent=value;card.append(small,strong);$('targetCountMetrics').append(card);}
  $('targetCountExplanation').textContent=(report.warnings||[]).join('；')||'数量与关联暂一致，但未经过独立真值确认，不能将镜像或碎片计为新增目标。';
  if(report.identity_association?.identities?.length)$('targetCountExplanation').textContent+=' 身份映射：'+report.identity_association.identities.map(x=>x.id+' ← 模型 '+x.model_ids.join('/')).join('；');
  if(report.clutter_classification_reviews?.length)$('targetCountExplanation').textContent+=' 杂波判定待复核：'+report.clutter_classification_reviews.map(x=>x.id+'（模型原判杂波，程序未确认，不据此排除）').join('；')+'。保留清单不代表已完成配对或空间定位。';
  if(report.shared_inventory_evidence?.length)$('targetCountExplanation').textContent+=' 共享数量证据已隔离：'+report.shared_inventory_evidence.map(x=>x.candidate_id+'（'+x.target_ids.join('/')+'）').join('；')+'。不能据这些点重复确认目标数。';
}
const dataMode=()=>$('inputMode').value==='data';
const forwardMode=()=>!dataMode()&&$('processingPipeline').value==='forward_v1';
function updatePipeline(){
  if(forwardMode()){$('rendererProfile').value='btr_gui_v1';$('send').textContent='执行完整正向反演 ↗';}
  else $('send').textContent=dataMode()?'发送数据并生成轨迹图 ↗':'流式识别并生成轨迹 ↗';
  $('consent').checked=false;
}
$('processingPipeline').addEventListener('change',updatePipeline);
$('reviewHypotheses').addEventListener('change',()=>{$('consent').checked=false;});
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
  $('dataFileLabel').textContent=mode==='data'?'目标数据文件（必选，仅一份）':'平台参数（空间定位必需）';
  $('platform').accept=mode==='data'?'.json,.jsonl,.js,application/json,application/javascript':'.json,application/json';
  $('dataFileHint').textContent=mode==='data'?'完整轨迹请选meta.json（最多1MiB）；semantic.json只有起终点摘要，platform.json只有平台参数。也支持点位JSON/JSONL。':'请选择同一样本 input/platform.json；用于定位，不是目标真值。';
  $('send').textContent=mode==='data'?'发送数据并生成轨迹图 ↗':'流式识别并生成轨迹 ↗';
  $('inspect').textContent=mode==='data'?'仅预检数据':'仅预检图片';
  $('consent').checked=false;
  syncPlatformRemoval();
  updatePipeline();
  status(mode==='data'?'请选择下方目标数据文件；本模式不会发送已选图片。':'图片模式：请选择声呐图片，可先预检。');
}
$('inputMode').addEventListener('change',updateInputMode);
function autoSelectDataMode(keepConsent=false){
  const file=$('platform').files[0];
  const targetData=!!file && (/^(meta|semantic)\.json$/i.test(file.name||'') || /\.jsonl$/i.test(file.name||''));
  if(dataMode() || !file || (images.length && !targetData))return;
  const consent=$('consent').checked,currentPrompt=$('prompt').value;
  $('inputMode').value='data';updateInputMode();
  if(currentPrompt.trim() && currentPrompt!==defaultImagePrompt)$('prompt').value=currentPrompt;
  if(keepConsent)$('consent').checked=consent;
  status(targetData?'已检测到目标数据文件，自动切换为单文件绘图；之前选择的图片不会发送。':'已检测到仅选择数据文件，自动切换为单文件绘图，无需上传图片。');
}
function status(text,kind=''){ $('status').textContent=text;$('badge').textContent=kind==='ok'?'已完成':kind==='error'?'需要处理':kind==='warning'?'正文已返回':kind==='stopped'?'已停止':busy?'处理中':'等待输入';$('badge').className='badge '+kind; }
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
  $('runLogsDetails').open=true;$('logPhase').textContent='运行中';
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
function renderUsage(usage){
  $('usage').replaceChildren();$('usageRaw').textContent='';$('usageDetails').hidden=!usage;
  if(!usage){const note=document.createElement('p');note.className='usage-unavailable';note.textContent='本次结果未提供用量信息';$('usage').append(note);return;}
  $('usageRaw').textContent=JSON.stringify(usage,null,2);
  const valid=value=>typeof value==='number' && Number.isFinite(value) && value>=0;
  const input=valid(usage.input_tokens)?usage.input_tokens:usage.prompt_tokens;
  const output=valid(usage.output_tokens)?usage.output_tokens:usage.completion_tokens;
  const total=valid(usage.total_tokens)?usage.total_tokens:valid(input)&&valid(output)?input+output:null;
  const formatted=value=>valid(value)?value.toLocaleString('zh-CN'):'—';
  const cost=typeof usage.cost==='string' || valid(usage.cost)?String(usage.cost).slice(0,60):'未提供';
  for(const [title,value] of [['输入 Token',formatted(input)],['输出 Token',formatted(output)],['合计 Token',formatted(total)],['服务返回费用',cost]]){
    const card=document.createElement('div'),label=document.createElement('small'),amount=document.createElement('strong');
    label.textContent=title;amount.textContent=value;card.append(label,amount);$('usage').append(card);
  }
}
function resetResultPresentation(){
  agentResultMetadata=null;
  executionNodes=new Map();drawNodes();showTargetCount(null);
  $('evidenceSection').hidden=true;$('evidenceImages').replaceChildren();
  $('resultPanel').dataset.state='running';$('completionSummary').hidden=true;
  $('agentConsoleBody').scrollTop=0;
  $('resultPanel').scrollIntoView?.({behavior:'smooth',block:'start'});
  $('bodyDetails').open=true;$('bodySummary').textContent='模型正文 · 实时输出';
  $('providerReasoningSection').open=false;
  $('warningsDetails').hidden=true;$('warningsDetails').open=false;
  $('warningCount').textContent='';$('downloadDetails').hidden=true;$('downloadDetails').open=false;
  $('debugDownloads').replaceChildren();$('usageRaw').textContent='';$('usageDetails').hidden=true;
  updateAgentOverview();
}
function showEvidence(data){
  $('evidenceImages').replaceChildren();
  const evidenceFiles=Object.entries(data.downloads||{}).filter(([name])=>/^selected_\d\.png$/.test(name));
  const displayEvidence=[...(evidenceFiles.length?evidenceFiles:Object.entries(data.downloads||{}).filter(([name])=>/^evidence_\d\.png$/.test(name))),...Object.entries(data.downloads||{}).filter(([name])=>/^(reprojection|gaussian|inverse_overlay|expected_btr)_\d\.png$/.test(name))];
  $('evidenceSection').hidden=!displayEvidence.length;
  for(const [name,url] of displayEvidence){const figure=document.createElement('figure'),img=document.createElement('img'),caption=document.createElement('figcaption'),button=document.createElement('button');img.src=url;img.alt='声呐测量诊断叠加，点击放大';caption.textContent=name+(name.startsWith('gaussian_')?' · 实验拟合，非目标结论':'')+' · 点击放大';button.type='button';button.className='evidence-open';button.ariaLabel='放大查看 '+name;button.addEventListener('click',()=>openEvidencePreview(url,name));button.append(img);figure.append(caption,button);$('evidenceImages').append(figure);}
}

let evidenceZoom=1,evidenceFitWidth=1,evidenceReturnFocus=null,evidenceBodyOverflow='';
function setEvidenceZoom(value){
  evidenceZoom=Math.max(.25,Math.min(8,value));
  $('evidenceFullImage').style.width=Math.round(evidenceFitWidth*evidenceZoom)+'px';
  $('evidenceZoomLabel').textContent=Math.round(evidenceZoom*100)+'%';
  $('evidenceZoomOut').disabled=evidenceZoom<=.25;$('evidenceZoomIn').disabled=evidenceZoom>=8;
}
function fitEvidenceImage(){
  const image=$('evidenceFullImage'),viewport=$('evidenceViewport');
  evidenceFitWidth=Math.max(1,Math.min(image.naturalWidth||1024,viewport.clientWidth-24));
  setEvidenceZoom(1);viewport.scrollTop=0;viewport.scrollLeft=0;
}
function openEvidencePreview(url,name){
  const dialog=$('evidenceDialog'),image=$('evidenceFullImage');
  evidenceReturnFocus=document.activeElement;
  $('evidenceDialogTitle').textContent=name;
  $('evidenceOriginal').href=url;$('evidenceDownload').href=url;$('evidenceDownload').download=name;
  image.onload=fitEvidenceImage;image.src=url;
  evidenceBodyOverflow=document.body.style.overflow;document.body.style.overflow='hidden';
  if(!dialog.open)dialog.showModal();
  fitEvidenceImage();$('closeEvidence').focus();
}
$('closeEvidence').addEventListener('click',()=>$('evidenceDialog').close());
$('evidenceDialog').addEventListener('click',event=>{if(event.target===$('evidenceDialog'))$('evidenceDialog').close();});
$('evidenceDialog').addEventListener('close',()=>{document.body.style.overflow=evidenceBodyOverflow;evidenceReturnFocus?.focus?.();});
$('evidenceZoomIn').addEventListener('click',()=>setEvidenceZoom(evidenceZoom*1.25));
$('evidenceZoomOut').addEventListener('click',()=>setEvidenceZoom(evidenceZoom/1.25));
$('evidenceFit').addEventListener('click',fitEvidenceImage);
$('evidenceActual').addEventListener('click',()=>setEvidenceZoom(($('evidenceFullImage').naturalWidth||evidenceFitWidth)/evidenceFitWidth));
$('evidenceDialog').addEventListener('keydown',event=>{
  if(event.key==='+'||event.key==='='){event.preventDefault();setEvidenceZoom(evidenceZoom*1.25);}
  else if(event.key==='-'){event.preventDefault();setEvidenceZoom(evidenceZoom/1.25);}
  else if(event.key==='0'){event.preventDefault();fitEvidenceImage();}
});
function showModelResult(data,{fromHistory=false}={}){
  agentResultMetadata=data;
  showExecution(data.node_events);showTargetCount(data.target_count);
  showEvidence(data);
  const forwardResult=data.pipeline==='forward_inverse_v1';
  const candidateResult=data.input_mode!=='data' && !!data.trajectory_image && (!!data.downloads?.['quality_report.json']||forwardResult);
  $('bodyOutputHint').textContent='模型正文已接收完毕，绘图状态见结果和告警。';
  status('已保存结果，编号：'+data.result_id,data.trajectory_image?'ok':'warning');$('answer').textContent=data.answer.slice(0,4000000);
  if(candidateResult){status('已生成空间候选，尚未验证完整性与准确率；请核对原图叠加和质量报告。','warning');$('badge').textContent='候选待核验';}
  currentResultId=data.result_id;currentTrajectoryStatus=data.trajectory_status;
  if(data.trajectory_image){$('aiComparePreview').src=data.trajectory_image;$('aiComparePreview').hidden=false;$('aiCompareEmpty').hidden=true;}
  else {$('aiCompareEmpty').textContent='本次未生成轨迹图';$('aiCompareEmpty').hidden=false;$('aiComparePreview').hidden=true;$('aiComparePreview').removeAttribute('src');$('trackFigure').hidden=true;$('trackImage').removeAttribute('src');}
  $('aiCoordinateLabel').textContent=data.input_mode==='data'?'（用户数据绘图，非声呐反演）':data.trajectory_status==='bearing_time'?'（方位—时间，非地理轨迹）':'（空间候选解，可能存在镜像）';
  $('trackWarnings').textContent=(data.warnings||[]).join('\n');
  $('warningsDetails').hidden=!(data.warnings||[]).length;
  $('warningCount').textContent=(data.warnings||[]).length?'('+data.warnings.length+')':'';
  if(data.trajectory_image){$('trackImage').src=data.trajectory_image;$('trackFigure').hidden=false;$('trackCaption').textContent=data.trajectory_status==='bearing_time'?'仅方位—时间轨迹：不是地理空间轨迹':'空间候选轨迹：模型提取方位、程序双阵定位；镜像分支不代表多个目标，未通过真值核验';}
  if(data.trajectory_image && data.input_mode==='data')$('trackCaption').textContent='用户文件数据图：静止对象显示为一个点；不是声呐反演或独立盲测结果。';
  if(data.input_mode==='data' && data.input_data?.plot_kind==='endpoints'){
    $('aiCoordinateLabel').textContent='（仿真参考起终点，非完整轨迹）';
    $('trackCaption').textContent='仅目标起终点示意：圆点为t=0，叉号为t=T_MAX；方块为观测平台。没有中间点位，不连成完整轨迹。';
  }
  if(data.input_mode==='data' && data.input_data?.plot_kind==='meta_reconstruction'){
    $('aiCoordinateLabel').textContent='（meta完整轨迹重建，非声呐反演）';
    $('trackCaption').textContent='按meta.json中的航点／曲线公式和DT完整采样绘图，含中间曲线；模型仅核对少量采样点，不补造轨迹。';
  }
  renderUsage(data.usage);
  $('downloads').replaceChildren();$('debugDownloads').replaceChildren();
  const primaryNames={'estimated_track.png':forwardResult?'下载主模型假设 PNG（未消歧）':'下载轨迹图 PNG','mirror_candidates.png':'查看全部镜像候选 PNG','tracks.json':'下载轨迹点 JSON','answer.txt':forwardResult?'下载本地反演报告':'下载模型正文'};
  let debugFiles=0;
  const rank=name=>name==='estimated_track.png'?0:name==='tracks.json'?1:name==='answer.txt'?2:3;
  for(const [name,path] of Object.entries(data.downloads||{}).sort((a,b)=>rank(a[0])-rank(b[0]))){
    const a=document.createElement('a');a.textContent=primaryNames[name]||name;a.href=path;a.download=name;
    if(name==='mirror_candidates.png'){a.removeAttribute('download');a.target='_blank';a.rel='noopener';}
    a.className=name==='estimated_track.png'?'download-link download-primary':'download-link';
    (primaryNames[name]?$('downloads'):$('debugDownloads')).append(a);if(!primaryNames[name])debugFiles++;
  }
  $('downloadDetails').hidden=!debugFiles&&!data.usage;
  $('downloadDetails').open=false;$('usageDetails').open=false;
  const hasImage=!!data.trajectory_image,kind=data.input_data?.plot_kind;
  $('completionTitle').textContent=!hasImage?'模型已返回，未生成轨迹图':kind==='meta_reconstruction'?'meta 完整轨迹已生成':kind==='endpoints'?'目标起终点图已生成':data.trajectory_status==='bearing_time'?'方位—时间图已生成':'轨迹图已生成';
  $('completionSubtitle').textContent=!hasImage?'模型正文已保存，请展开正文和告警检查格式；未自动重试。':kind==='meta_reconstruction'?'保留本地完整采样，不是声呐反演；可直接下载或继续对比。':kind==='endpoints'?'仅有起终点，不代表完整运动路径。':data.trajectory_status==='bearing_time'?'只表示方位随时间的变化，不代表地理定位。':'结果已保存。模型估计尚未经过独立核验。';
  $('completionMeta').textContent=(fromHistory?'历史结果 · ':'结果编号 · ')+data.result_id;
  $('completionSummary').className='completion-summary'+(hasImage?'':' incomplete');$('completionSummary').hidden=false;
  $('completionIcon').textContent=hasImage?'✓':'!';
  if(candidateResult){$('completionTitle').textContent='空间候选已生成 · 待核验';$('completionSubtitle').textContent='不是完整轨迹恢复；绿色圈表示Agent选点，不表示真值或定位正确。';$('completionIcon').textContent='!';$('completionSummary').className='completion-summary incomplete';}
  if(candidateResult && data.target_count?.status==='needs_review'){$('completionTitle').textContent='未达到完整目标识别要求';$('completionSubtitle').textContent='下面仅为诊断候选图：目标数量、身份或定位仍存在疑点，不能作为可靠空间轨迹使用。';$('badge').textContent='未达验收';$('trackCaption').textContent='诊断候选图，未达到完整目标识别要求；不是经核验的真实空间轨迹。';}
    if(data.offline_reprocessed)$('completionSubtitle').textContent+=' 本地重处理，新增模型调用0次；原始用量属于源记录 '+data.source_result_id+'。';
  $('resultPanel').dataset.state='completed';
  $('providerReasoningSection').open=false;$('bodyDetails').open=!hasImage;$('warningsDetails').open=!hasImage;
  if(candidateResult)$('warningsDetails').open=true;
  $('bodySummary').textContent='模型原始正文 · 查看 JSON';
  if(forwardResult){
    $('bodySummary').textContent='程序反演报告 · 查看 JSON';
    $('bodyOutputHint').textContent='完整正向反演已结束；坐标为模型估计，不是当前模型凭图生成。';
    $('completionTitle').textContent='正向反演候选已生成 · 待独立核验';
    $('completionSubtitle').textContent='完整剖面、全局归属、运动模型及BTR回投已执行；新增模型调用 '+(data.new_model_calls||0)+' 次。镜像和身份仍可能有歧义。';
    $('badge').textContent='待核验';$('trackCaption').textContent=data.downloads?.['mirror_candidates.png']?'主运动模型代表假设，不是唯一空间解；全部镜像请点“查看全部镜像候选PNG”，JSON保留所有分支。未通过独立位置精度验收。':'整段折叠方位的数值反演候选；同色为同一身份，镜像不计为新增目标，未通过独立位置精度验收。';
    $('aiCoordinateLabel').textContent=data.downloads?.['mirror_candidates.png']?'（主模型代表假设，未验证唯一侧别）':'（本地正向反演候选，镜像未唯一消歧）';
    if(data.identity_continuity?.merged_fragment_count>0)$('trackCaption').textContent+=' 同一身份假设已归并 '+data.identity_continuity.merged_fragment_count+' 个片段，输出完整轨迹；缺测区间仍为模型估计，不属于观测。';
    if(data.optimization_status==='budget_limited'){
      $('completionTitle').textContent='已生成部分优化候选 · 细化预算已到';
      $('completionSubtitle').textContent='保留了已完成的更新，其余沿用粗估，已继续执行质检与绘图；不是完整细化或可靠定位结果。';
      $('badge').textContent='部分优化';
    }
  }
  $('runLogsDetails').open=false;$('logPhase').textContent=fromHistory?'历史结果，未发起新请求':'已结束';
  $('stopStream').hidden=true;$('stopStream').disabled=true;
  if(fromHistory){$('providerReasoningSection').hidden=true;$('providerReasoning').textContent='';}
  updateAgentOverview();
  $('completionSummary').scrollIntoView?.({behavior:'smooth',block:'nearest'});
  if(fromHistory)$('resultPanel').scrollIntoView?.({behavior:'smooth',block:'start'});
}
async function readModelStream(response){
  if(!response.body)throw new Error('浏览器未提供流式读取能力');
  const reader=response.body.getReader(),decoder=new TextDecoder('utf-8');
  let buffer='',completed=false,bodyChars=0,reasonChars=0,phase='connecting';
  function receive(frame){
    const payload=frame.split('\n').filter(x=>x.startsWith('data:')).map(x=>x.slice(5).trimStart()).join('\n');
    if(!payload)return;
    const event=JSON.parse(payload);
    if(event.event==='node'){
      receiveNode(event);
      if(phase==='local_inverse' && event.node==='inverse_refine' && event.status==='running' && event.summary){
        status('本地细化：'+event.summary);logLine('[本地进度] '+event.summary);
      }
    }else if(event.event==='phase'){
      phase=event.phase;
      logLine('[流式] '+event.message);status(event.message);
      if(phase==='local_inverse'){
        $('bodySummary').textContent='本地数值报告 · 生成中';
        $('bodyOutputHint').textContent='正在本地计算，不等待大模型正文；完成后显示数值报告和空间候选。';
      }
      if(event.phase==='buffered'){$('trackWarnings').textContent='中转站未提供真正SSE，当前只能整包返回，非逐片输出。';$('warningsDetails').hidden=false;$('warningsDetails').open=true;}
    }else if(event.event==='heartbeat'){
      logLine('[连接心跳] 已等待 '+event.elapsed_s+' 秒；尚未收到新的可显示事件。');
      if(phase==='local_inverse'){status('本地数值反演仍在计算；可查看真实节点状态，耗时不代表准确率。');return;}
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
      $('providerReasoningSection').open=true;
      const text=event.text.slice(0,Math.max(0,1000000-reasonChars));reasonChars+=text.length;
      $('providerReasoning').append(document.createTextNode(text));
      if(bodyChars===0){
        status('模型正在返回推理文本（'+reasonChars+'字符），最终正文尚未开始，轨迹图尚不能生成。');
        $('answer').dataset.waiting='true';
        $('bodyOutputHint').textContent='当前只有推理文本，尚未返回可用于绘图的正文JSON。';
      }
    }else if(event.event==='error'){
      throw new Error(event.message+(phase==='local_inverse'?' 本地诊断保留，未自动重试。':' 已显示文本可能不完整，未自动重试。'));
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
  const isData=dataMode(),platform=$('platform').files[0],forward=forwardMode()&&!dryRun;
  if(isData && !platform){status('请先选择一份目标数据文件。','error');return;}
  if(!isData && !images.length){status('请先选择图片，或切换到单文件绘图模式。','error');return;}
  if(forward && !platform){status('完整正向反演需要同一样本的platform.json。','error');return;}
  if(!forward && !$('prompt').value.trim()){status('提示词不能为空。','error');return;}
  const needsCloud=!dryRun&&(!forward||$('reviewHypotheses').checked);
  if(needsCloud && !$('consent').checked){status('请先勾选云端传输与费用确认。','error');return;}
  const form=new FormData();if(!isData)images.forEach(file=>form.append('files',file));
  form.append('input_mode',isData?'data':'images');
  form.append('renderer_profile',isData?'none':($('rendererProfile').value||'none'));
  if(forward)form.append('review_model',String(!!$('reviewHypotheses').checked));
  form.append('prompt',$('prompt').value);
  form.append('dry_run',String(dryRun));form.append('consent',String($('consent').checked));
  if(platform){if(platform.size>(isData?1024*1024:100000)){status(isData?'数据文件超过1MiB限制。':'平台JSON超过100KB限制。','error');return;} form.append('platform',platform);}
  busy=true;document.querySelectorAll('input,textarea,button,select').forEach(x=>x.disabled=true);
  resetResultPresentation();
  resetEvaluation();currentResultId='';currentTrajectoryStatus='';$('aiComparePreview').hidden=true;$('aiComparePreview').removeAttribute('src');$('aiCompareEmpty').hidden=false;resetComparison();
  $('trackFigure').hidden=true;$('trackImage').removeAttribute('src');$('trackWarnings').textContent='';
  $('downloads').replaceChildren();$('usage').textContent='';status(dryRun?'正在本地预检，不会调用模型服务…':forward?'正在执行完整正向反演；默认本地计算，不调用模型…':'请求已提交本地服务，等待模型服务返回；请勿重复发送或关闭页面…');
  const correlationId=beginLogs();
  if(dryRun)logTimer=setTimeout(()=>pollLogs(correlationId),300);
  $('bodyOutputHint').textContent='等待模型正文；推理文本不作为轨迹数据。';
  $('answer').textContent='';$('providerReasoning').textContent='';$('providerReasoningSection').hidden=true;
  streamAbort=new AbortController();$('stopStream').disabled=dryRun;$('stopStream').hidden=dryRun;
  try{
    const response=await fetch(dryRun?'/api/recognize':forward?'/api/invert/stream':'/api/recognize/stream',{method:'POST',body:form,headers:{'X-Request-ID':correlationId},signal:streamAbort.signal});
    if(!response.ok){
      let message='请求失败（HTTP '+response.status+'）';
      try{const data=await response.json();if(typeof data.detail==='string')message=data.detail;}catch{}
      if(response.status===404 && !dryRun)message='当前后端尚未加载流式接口，请停止旧服务并重新启动。';
      throw new Error(message);
    }
    if(dryRun){const data=await response.json();showEvidence(data);showExecution(data.node_events);showTargetCount(data.target_count);status(data.message,'ok');$('answer').textContent=JSON.stringify(data.input_data||data.images,null,2);$('resultPanel').dataset.state='preflight';updateAgentOverview();$('bodySummary').textContent='本地预检结果';$('logPhase').textContent='预检完成';for(const [name,url] of Object.entries(data.downloads||{})){const a=document.createElement('a');a.href=url;a.textContent='下载 '+name;$('downloads').append(a);}}
    else if(!(response.headers.get('content-type')||'').includes('text/event-stream'))throw new Error('本地服务未返回SSE，请确认已重启新版后端。');
    else await readModelStream(response);
  }catch(error){
    $('resultPanel').dataset.state='error';updateAgentOverview();$('runLogsDetails').open=true;$('logPhase').textContent='请求未完成';
    if(error.name==='AbortError'){
      for(const [id,event] of executionNodes)if(event.status==='running')executionNodes.set(id,{...event,status:'stopped',summary:'本地接收已停止，不保证上游停止计费'});drawNodes();
      logLine('[浏览器] 用户已停止本地接收，保留已收到的文本。');
      $('bodyOutputHint').textContent='已停止接收。以下为不完整输出，不作为完整轨迹结果。';
      status(forward&&!needsCloud?'已停止接收并请求取消本地求解；计算在下一个预算检查处退出。':'已停止本地接收，已收到的内容保留；不保证上游停止处理或计费。','stopped');
    }else{
      logLine('[浏览器] 请求失败：'+error.message);status(error.message==='Failed to fetch'?(forward&&!needsCloud?'本地连接中断；未发起云端模型请求，请查看本地诊断。':'连接中断。云端请求可能仍在执行，请核对调用记录后再决定是否重试。'):error.message,'error');
    }
  }
  finally{clearTimeout(logTimer);busy=false;streamAbort=null;document.querySelectorAll('input,textarea,button,select').forEach(x=>x.disabled=false);$('stopStream').disabled=true;$('stopStream').hidden=true;$('stopStream').textContent='■ 停止生成';syncPlatformRemoval();}
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
    resetComparison();resetEvaluation();$('downloads').replaceChildren();showModelResult(data,{fromHistory:true});
    $('comparisonStatus').textContent='已导入生成结果。请选择同一样本、同一坐标类型的参考图；未调用模型。';
  }catch(error){$('comparisonStatus').textContent=error.message;}
  finally{$('loadHistory').disabled=false;}
});
refreshHistory();
