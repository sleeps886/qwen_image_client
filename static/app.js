'use strict';
const $=id=>document.getElementById(id);
let images=[],urls=[],busy=false;
function status(text,kind=''){ $('status').textContent=text;$('badge').textContent=kind==='ok'?'已完成':kind==='error'?'需要处理':busy?'处理中':'等待输入';$('badge').className='badge '+kind; }
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
  if(busy)return;
  if(!images.length){status('请先选择图片。','error');return;}
  if(!$('prompt').value.trim()){status('提示词不能为空。','error');return;}
  if(!dryRun && !$('consent').checked){status('请先勾选云端传输与费用确认。','error');return;}
  const form=new FormData();images.forEach(file=>form.append('files',file));
  form.append('prompt',$('prompt').value);
  form.append('dry_run',String(dryRun));form.append('consent',String($('consent').checked));
  const platform=$('platform').files[0];
  if(platform){if(platform.size>100000){status('平台JSON超过100KB限制。','error');return;} form.append('platform',platform);}
  busy=true;document.querySelectorAll('input,textarea,button').forEach(x=>x.disabled=true);
  $('trackFigure').hidden=true;$('trackImage').removeAttribute('src');$('trackWarnings').textContent='';
  $('downloads').replaceChildren();$('usage').textContent='';status(dryRun?'正在本地预检，不会调用模型服务…':'请求已提交本地服务，等待模型服务返回；请勿重复发送或关闭页面…');
  try{
    const response=await fetch('/api/recognize',{method:'POST',body:form});
    const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'请求失败，请检查表单。');
    if(data.dry_run){status(data.message,'ok');$('answer').textContent=JSON.stringify(data.images,null,2);}
    else{
      status('已保存结果，编号：'+data.result_id,'ok');$('answer').textContent=data.answer;
      $('trackWarnings').textContent=(data.warnings||[]).join('\n');
      if(data.trajectory_image){$('trackImage').src=data.trajectory_image;$('trackFigure').hidden=false;$('trackCaption').textContent=data.trajectory_status==='bearing_time'?'仅方位—时间轨迹：不是地理空间轨迹':'模型估计的目标位置轨迹：未通过真值或物理一致性验证';}
      $('usage').textContent=data.usage?'Token用量：'+JSON.stringify(data.usage):'服务未返回Token用量';
      for(const [name,path] of Object.entries(data.downloads)){const link=document.createElement('a');link.textContent='下载 '+name;link.href=path;link.download=name;$('downloads').append(link);}
    }
  }catch(error){status(error.message==='Failed to fetch'?'连接中断。云端请求可能仍在执行，请核对调用记录后再决定是否重试。':error.message,'error');}
  finally{busy=false;document.querySelectorAll('input,textarea,button').forEach(x=>x.disabled=false);}
}
$('platform').addEventListener('change',()=>{$('consent').checked=false;});
$('send').addEventListener('click',()=>submit(false));$('inspect').addEventListener('click',()=>submit(true));
fetch('/api/health').then(r=>{if(!r.ok)throw new Error('服务不可用');return r.json();}).then(data=>{
  $('prompt').value=data.default_prompt;
  if($('consentText'))$('consentText').textContent='同意将所选图片、已选平台参数与提示词发送至'+data.provider_label+'，可能产生API费用。';
  $('keyState').textContent=data.key_configured?'后端配置已就绪':'请配置后端 .env 并重启';
}).catch(()=>status('服务连接失败，请重启本地FastAPI服务。','error'));
