"""Extract pixel-grounded peak candidates; never treat every peak as a target."""
import hashlib
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image,ImageDraw
from backend.app.services.inputs.model_json import model_object


def runs(mask):
    indices=np.flatnonzero(mask)
    if not len(indices):return []
    return [g for g in np.split(indices,np.where(np.diff(indices)>1)[0]+1) if len(g)]


def extract_candidates(path,index,context,*,time_slices=16):
    if type(time_slices) is not int or not 16<=time_slices<=64:
        raise ValueError('候选时间切片数量必须为16～64的整数')
    path=Path(path)
    with Image.open(path) as source:
        if source.width*source.height>16000000:raise ValueError('声呐图像素数量超过1600万限制')
        im=source.convert('RGB')
    rgb=np.asarray(im).astype(float)/255
    height,width=rgb.shape[:2]
    if width<100 or height<100:raise ValueError('声呐图尺寸过小，无法提取候选点')
    # Longest contiguous coloured area excludes separated narrow colourbar.
    saturated=(rgb.max(2)-rgb.min(2)>.12)&(rgb.max(2)>.16)
    columns=runs(saturated.sum(0)>height*.12)
    if not columns:raise ValueError('未找到有效声呐绘图区，请检查图片或人工确认裁剪')
    span=max(columns,key=len);left,right=int(span[0]),int(span[-1])
    rows=runs(saturated[:,left:right+1].sum(1)>(right-left+1)*.12)
    if not rows:raise ValueError('无法确定声呐绘图区时间边界')
    span=max(rows,key=len);top,bottom=int(span[0]),int(span[-1])
    if right-left<width*.25 or bottom-top<height*.25:
        raise ValueError('检测绘图区过小，不能可靠自动标定')
    name=path.name.lower()
    array='hull' if 'hull' in name else 'tow' if 'tow' in name else None
    platforms=context.get('platforms',[])
    platform_id=None
    for platform in platforms:
        name_id=str(platform.get('name',''))
        if name_id and re.search(r'(?:^|_)'+re.escape(name_id)+r'_(?:hull|tow)',path.stem,re.I):
            platform_id=name_id;break
    if platform_id is None and len(platforms)==1:platform_id=platforms[0].get('name')
    if not array or not platform_id:
        raise ValueError('图片文件名需包含平台和hull/tow阵列标记，无法自动配对时请保留原文件名')
    candidates=[]
    rejected={'low_prominence':0,'no_temporal_support':0,'too_narrow':0}
    def profile(y):
        band=rgb[max(top,y-2):min(bottom+1,y+3),left:right+1]
        values=np.median(np.maximum(band[:,:,0],band[:,:,1])-.65*band[:,:,2],axis=0)
        return np.convolve(values,np.ones(5)/5,mode='same')
    def robust_floor(values):
        median=float(np.median(values))
        sigma=1.4826*float(np.median(np.abs(values-median)))
        return max(.18,median+.10,median+4*sigma)
    # Common time bins for both arrays. Pixel values are computed locally, not by the model.
    for row_idx,fraction in enumerate(np.linspace(.03,.97,time_slices)):
        y=round(bottom-fraction*(bottom-top) if context.get('image_time_axis')=='bottom_to_top' else top+fraction*(bottom-top))
        strength=profile(y)
        threshold=robust_floor(strength)
        groups=runs(strength>threshold)
        merged=[]
        for group in groups:
            if merged and group[0]-merged[-1][-1]<max(4,(right-left)*.008):
                merged[-1]=np.arange(merged[-1][0],group[-1]+1)
            else:merged.append(group)
        valid=[]
        for group in merged:
            start,stop=int(group[0]),int(group[-1])+1
            if stop-start<3:
                rejected['too_narrow']+=1;continue
            peak=int(group[np.argmax(strength[group])])
            radius=max(12,round((right-left)*.025))
            flank=np.concatenate((strength[max(0,start-radius-3):max(0,start-3)],
                                  strength[min(len(strength),stop+3):min(len(strength),stop+radius+3)]))
            if not len(flank) or strength[peak]-float(np.median(flank))<.08:
                rejected['low_prominence']+=1;continue
            # Independent nearby rows, outside the central median window. No
            # forced candidate quota: isolated speckle is discarded, not labelled.
            dy=max(5,round((bottom-top)*.01));support=0;tested=0
            for yy in (y-2*dy,y-dy,y+dy,y+2*dy):
                if top+2<=yy<=bottom-2:
                    other=profile(yy);tested+=1
                    margin=max(3,round((right-left)*.008))*max(1,abs(yy-y)/dy)
                    margin=round(margin)
                    neighbourhood=other[max(0,start-margin):min(len(other),stop+margin)]
                    if len(neighbourhood) and float(neighbourhood.max())>robust_floor(other):support+=1
            if not tested or support<tested:
                rejected['no_temporal_support']+=1;continue
            valid.append(group)
        selected=sorted(sorted(valid,key=lambda g:float(strength[g].max()),reverse=True)[:6],key=lambda g:int(g[0]))
        for peak_idx,group in enumerate(selected):
            start,stop=int(group[0]),int(group[-1])+1
            peak=int(group[np.argmax(strength[group])])
            weights=np.maximum(strength[start:stop]-threshold,0)
            center=float(np.average(np.arange(start,stop),weights=weights)) if weights.sum() else float(peak)
            x=left+center
            # Engineering angular-error proxy from pixel resolution and local
            # ridge-centre scatter after removing linear drift. Not calibrated CI.
            ridge_y=[];ridge_x=[]
            for yy in (y-2*dy,y-dy,y,y+dy,y+2*dy):
                if not top+2<=yy<=bottom-2:continue
                other=profile(yy);margin=round(max(3,(right-left)*.008)*max(1,abs(yy-y)/dy))
                lo,hi=max(0,start-margin),min(len(other),stop+margin)
                w=np.maximum(other[lo:hi]-robust_floor(other),0)
                if w.sum():ridge_y.append(yy);ridge_x.append(float(np.average(np.arange(lo,hi),weights=w)))
            scatter=0.
            if len(ridge_x)>=3:
                fitted=np.polyval(np.polyfit(ridge_y,ridge_x,1),ridge_y)
                scatter=float(np.sqrt(np.mean((np.array(ridge_x)-fitted)**2)))
            error_px=max(1.5,scatter,(stop-start)*.025)
            error_deg=max(.15,min(2.,error_px/(right-left)*(context.get('B_MAX',180)-context.get('B_MIN',0))))
            candidates.append({'id':f'I{index}R{row_idx}P{peak_idx}',
                'sample_index':row_idx,
                't':round(float(fraction*context['T_MAX']),6),'x_px':round(x,2),'y_px':y,
                'bearing_deg':round(context.get('B_MIN',0)+center/(right-left)*(context.get('B_MAX',180)-context.get('B_MIN',0)),5),
                'strength':round(float(strength[peak]),4),'bearing_error_assumption_deg':round(error_deg,5)})
    if not candidates:raise ValueError('噪声过滤后未提取到受邻近时间支持的亮带候选，不调用模型猜测观测')
    return {'image_index':index,'filename':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'array':array,'platform_id':platform_id,'roi':[left,top,right,bottom],
            'size':[width,height],'roi_method':'longest_coloured_region_heuristic_v1',
            'filter_version':'robust_background_temporal_v2','rejected':rejected,
            'time_slices':time_slices,
            'candidates':candidates}


def save_evidence(pack,paths,directory):
    directory=Path(directory)
    (directory/'candidate_evidence.json').write_text(json.dumps(pack,ensure_ascii=False,indent=2),encoding='utf-8')
    for evidence,path in zip(pack['images'],paths):
        with Image.open(path) as source:im=source.convert('RGB')
        draw=ImageDraw.Draw(im);draw.rectangle(evidence['roi'],outline='#ffffff',width=2)
        for point in evidence['candidates']:
            x,y=point['x_px'],point['y_px']
            draw.ellipse((x-3,y-3,x+3,y+3),outline='#ff55ff',width=2)
            draw.text((x+4,y-10),point['id'],fill='#ffffff',stroke_width=1,stroke_fill='#000000')
        im.save(directory/f"evidence_{evidence['image_index']}.png")


def resolve_pairs(answer,pack,*,isolate_unknown=False,isolate_unsynchronized=False):
    document=model_object(answer,'candidate_pairs')
    if not isinstance(document,dict) or document.get('coordinate_system')!='candidate_pairs':
        raise ValueError('Agent必须返回candidate_pairs候选编号，拒绝模型自行填写角度或位置')
    tracks=document.get('tracks')
    if not isinstance(tracks,list) or not 1<=len(tracks)<=50:raise ValueError('Agent未选出可用双阵候选关联')
    lookup={p['id']:(im,p) for im in pack['images'] for p in im['candidates']}
    output=[];used=set();rejected=[]
    for track in tracks:
        if not isinstance(track,dict):raise ValueError('候选轨迹格式无效')
        if not isinstance(track.get('id'),str) or not 1<=len(track['id'])<=80:raise ValueError('候选目标ID无效')
        if not isinstance(track.get('branch','main'),str) or len(track.get('branch','main'))>80:raise ValueError('候选分支编号无效')
        pairs=track.get('pairs')
        if not isinstance(pairs,list) or not 2<=len(pairs)<=500:raise ValueError('候选轨迹至少需要2个同步配对')
        points=[];previous=-1;track_used=set()
        for pair_index,pair in enumerate(pairs):
            if not isinstance(pair,dict):raise ValueError('候选配对格式无效')
            hid,tid=pair.get('hull_id'),pair.get('tow_id')
            if not isinstance(hid,str) or not isinstance(tid,str) or hid not in lookup or tid not in lookup:
                if isolate_unknown:
                    rejected.append({'model_id':track['id'],'pair_index':pair_index,
                                     'hull_id':hid[:80] if isinstance(hid,str) else None,
                                     'tow_id':tid[:80] if isinstance(tid,str) else None,
                                     'reason':'unknown_candidate_reference_not_replaced'})
                    continue
                raise ValueError('Agent使用了不存在的候选编号')
            hi,hp=lookup[hid];ti,tp=lookup[tid]
            if hi['array']!='hull' or ti['array']!='tow' or hi['platform_id']!=ti['platform_id'] or hi['platform_id']!=track.get('platform_id'):
                raise ValueError('候选配对的阵列或平台不一致')
            if abs(hp['t']-tp['t'])>1e-5:
                if not isolate_unsynchronized:
                    raise ValueError(f"两阵候选不同步：{hid} t={hp['t']}，{tid} t={tp['t']}")
                rejected.append({'model_id':track['id'],'pair_index':pair_index,
                                 'hull_id':hid,'tow_id':tid,'hull_t':hp['t'],'tow_t':tp['t'],
                                 'reason':'unsynchronized_pair_not_replaced'})
                continue
            if hp['t']<=previous:raise ValueError('同一轨迹候选时间重复或未递增；未自动排序或选择替代点')
            if hid in used or tid in used or hid in track_used or tid in track_used:
                raise ValueError('同一候选不能重复分配给多个目标；歧义需先人工确认')
            track_used.update((hid,tid));previous=hp['t']
            points.append({'t':hp['t'],'hull_deg':hp['bearing_deg'],'tow_deg':tp['bearing_deg'],
                           'hull_error_deg':hp.get('bearing_error_assumption_deg',1.),
                           'tow_error_deg':tp.get('bearing_error_assumption_deg',1.),
                           'sample_index':hp.get('sample_index'),
                           'hull_candidate_id':hid,'tow_candidate_id':tid})
        if len(points)>=2:
            used.update(track_used)
            output.append({'id':track.get('id'),'branch':track.get('branch','main'),
                           'platform_id':track.get('platform_id'),'points':points})
        else:
            for point in points:
                rejected.append({'model_id':track['id'],'hull_id':point['hull_candidate_id'],
                                 'tow_id':point['tow_candidate_id'],'t':point['t'],
                                 'reason':'insufficient_remaining_synchronous_times'})
    if not output:raise ValueError('隔离无效候选配对后，没有至少两个合法同步时刻；不能生成轨迹')
    warnings=document.get('warnings',[])
    if not isinstance(warnings,list):raise ValueError('模型warnings必须是数组')
    if rejected:warnings=warnings+[f'已隔离{len(rejected)}组无效或剩余时刻不足的候选配对（原因见校验报告）；未替换编号、调整时间或补点，剩余为部分观测。']
    return {'coordinate_system':'dual_array_bearings','warnings':warnings,'tracks':output,
            'candidate_validation':{'policy':'isolate_unknown' if isolate_unknown else 'strict',
                                    'unsynchronized_policy':'isolate_without_replacement' if isolate_unsynchronized else 'strict',
                                    'rejected_pair_count':len(rejected),'rejected_pairs':rejected}}


def save_selected(pack,observations,directory):
    selected={point[k] for track in observations['tracks'] for point in track['points']
              for k in ('hull_candidate_id','tow_candidate_id')}
    for evidence in pack['images']:
        path=Path(directory)/f"evidence_{evidence['image_index']}.png"
        with Image.open(path) as source:im=source.convert('RGB')
        draw=ImageDraw.Draw(im)
        for p in evidence['candidates']:
            if p['id'] in selected:
                x,y=p['x_px'],p['y_px'];draw.ellipse((x-7,y-7,x+7,y+7),outline='#00ff00',width=3)
        im.save(Path(directory)/f"selected_{evidence['image_index']}.png")


def save_reprojection(pack,report,directory):
    """Cyan circles are projections; never connect across rejected observations."""
    for evidence in pack['images']:
        base=Path(directory)/f"selected_{evidence['image_index']}.png"
        if not base.is_file():base=Path(directory)/f"evidence_{evidence['image_index']}.png"
        with Image.open(base) as source:im=source.convert('RGB')
        draw=ImageDraw.Draw(im)
        for row in report.get('rows',[]):
            for projection in row['arrays']:
                if projection['image_index']!=evidence['image_index']:continue
                x,y=projection['x_px'],projection['y_px']
                if not row['passed']:
                    # Mark rejected observations at their original candidate, not
                    # a potentially unbounded failed projected coordinate.
                    candidate=next(p for p in evidence['candidates'] if p['id']==projection['candidate_id'])
                    x,y=candidate['x_px'],candidate['y_px']
                    draw.line((x-5,y-5,x+5,y+5),fill='#ff4444',width=2)
                    draw.line((x-5,y+5,x+5,y-5),fill='#ff4444',width=2)
                else:draw.ellipse((x-4,y-4,x+4,y+4),outline='#00ffff',width=2)
        im.save(Path(directory)/f"reprojection_{evidence['image_index']}.png")
