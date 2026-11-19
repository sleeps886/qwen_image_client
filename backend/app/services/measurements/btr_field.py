"""Full-profile measurements for the explicit simulator profile, without labels."""
from pathlib import Path
import hashlib
import re

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks

from backend.app.services.measurements.btr_measurements import PROFILE,decode_relative,fit_profile
from backend.app.services.measurements.sonar_evidence import runs


def image_geometry(path,index,context):
    path=Path(path)
    with Image.open(path) as source:
        if source.width*source.height>16000000:raise ValueError('声呐原图超过1600万像素')
        image=source.convert('RGB');width,height=image.size
        rgb=np.asarray(image,dtype=float)/255.
    if width<100 or height<100:raise ValueError('声呐图片尺寸过小')
    colored=(rgb.max(2)-rgb.min(2)>.01)&(rgb.max(2)>.02)
    columns=runs(colored.sum(0)>height*.12)
    if not columns:raise ValueError('未找到工具声呐绘图区')
    span=max(columns,key=len);left,right=int(span[0]),int(span[-1])
    rows=runs(colored[:,left:right+1].sum(1)>(right-left+1)*.12)
    if not rows:raise ValueError('未找到工具声呐时间边界')
    span=max(rows,key=len);top,bottom=int(span[0]),int(span[-1])
    if right-left<width*.25 or bottom-top<height*.25:raise ValueError('绘图区过小，不能可靠标定')
    role=re.search(r'(?:^|_)(hull|tow)(?:\.|_|$)',path.name,re.I)
    if role is None:raise ValueError('正向反演图片需保留hull/tow阵列标记')
    return {'image_index':index,'filename':path.name,'array':role.group(1).lower(),
            'platform_id':context['platforms'][0]['name'],'roi':[left,top,right,bottom],
            'size':[width,height],'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'roi_method':'low_threshold_color_extent_v1'}


def extract_field(path,index,context,control,rows=65):
    control.check()
    if context.get('renderer_profile')!=PROFILE:raise ValueError('正向反演要求显式声明btr_gui_v1工具图配置')
    if type(rows) is not int or not 17<=rows<=129:raise ValueError('反演时间网格必须为17～129整数')
    geometry=image_geometry(path,index,context)
    left,top,right,bottom=geometry['roi']
    with Image.open(path) as source:rgb=np.asarray(source.convert('RGB').crop((left,top,right+1,bottom+1)))
    field=decode_relative(rgb,PROFILE)
    if field['valid_fraction']<.9:raise ValueError('输入图与声明配色不一致，拒绝按工具强度模型反演')
    angles=np.linspace(context.get('B_MIN',0),context.get('B_MAX',180),rgb.shape[1]);step=float(angles[1]-angles[0])
    points=[];profiles=[];profile_values=[];rejected=0
    for row,fraction in enumerate(np.linspace(.02,.98,rows)):
        control.check()
        y=round((1-fraction)*(bottom-top) if context.get('image_time_axis','bottom_to_top')=='bottom_to_top' else fraction*(bottom-top))
        lo,hi=max(0,y-1),min(rgb.shape[0],y+2)
        valid=np.mean(field['valid'][lo:hi],axis=0)>=.66
        values=np.ma.median(np.ma.array(field['relative'][lo:hi],mask=~field['valid'][lo:hi]),axis=0).filled(np.nan)
        if valid.sum()<len(angles)*.8:continue
        baseline=float(np.nanmedian(values[valid]));noise=1.4826*float(np.nanmedian(np.abs(values[valid]-baseline)))
        clean=np.where(valid,values,baseline)
        smoothed=gaussian_filter1d(clean,1.2)
        prominence=max(.5,2.5*noise)
        peaks,properties=find_peaks(np.r_[baseline,smoothed,baseline],prominence=prominence,
            height=baseline+prominence,distance=max(2,round(1./step)),width=max(2,.35/step))
        peaks=peaks-1;peaks=peaks[(peaks>=0)&(peaks<len(angles))]
        peaks=sorted(sorted(peaks,key=lambda p:smoothed[p],reverse=True)[:8])
        clusters=[]
        for peak in peaks:
            if clusters and len(clusters[-1])<2 and angles[peak]-angles[clusters[-1][-1]]<12:clusters[-1].append(peak)
            else:clusters.append([peak])
        lower=np.mean(field['lower_clipped'][lo:hi],axis=0)>=.66
        upper=np.mean(field['upper_clipped'][lo:hi],axis=0)>=.66
        components=[]
        for cluster in clusters:
            control.check()
            mask=valid&(angles>=angles[cluster[0]]-9)&(angles<=angles[cluster[-1]]+9)
            if mask.sum()<12:continue
            centers=np.clip(angles[cluster],angles[mask][0]+step/2,angles[mask][-1]-step/2)
            try:fit=fit_profile(angles[mask],values[mask],centers,lower=lower[mask],upper=upper[mask])
            except ValueError:rejected+=1;continue
            if not fit['optimizer_success'] or fit['normalized_rmse']>.25:
                rejected+=1;continue
            for c in fit['components']:
                if c['relative_amplitude']<prominence or c['sigma_deg']>.49*(angles[mask][-1]-angles[mask][0]):continue
                components.append((c,fit))
        for number,(c,fit) in enumerate(sorted(components,key=lambda p:p[0]['center_deg'])):
            points.append({'id':f'F{index}R{row}P{number}','sample_index':row,'t':round(float(fraction*context['T_MAX']),6),
                'bearing_deg':c['center_deg'],'sigma_deg':c['sigma_deg'],
                'error_deg':max(.08,min(1.,step/2+fit['normalized_rmse']*c['sigma_deg'])),
                'amplitude':c['relative_amplitude'],'quality':fit['status'],
                'mixed':not fit['independently_resolved'],'x_px':float(left+(c['center_deg']-angles[0])/step),'y_px':int(top+y)})
        profiles.append({'sample_index':row,'t':round(float(fraction*context['T_MAX']),6),
                         'background_relative':baseline,'noise_proxy':noise,'detected_components':len(components)})
        profile_values.append(clean.astype(np.float32))
    if len(points)<6:raise ValueError('完整剖面提取的有效亮带测量不足，未调用模型')
    return {**{k:v for k,v in geometry.items() if k!='candidates'},'candidates':points,'profiles':profiles,
            'measurement_method':'relative_intensity_gaussian_profiles_v1','time_slices':rows,
            'rejected_fit_groups':rejected,'statistical_confidence':False,
            '_profile_values':np.array(profile_values),'_angles':angles,
            '_raster_relative':field['relative'],'_raster_valid':field['valid']}


def extract_fields(paths,context,control):
    if len(paths)!=2 or len(context.get('platforms',[]))!=1:raise ValueError('正向反演首期只支持同平台hull/tow各一张图')
    images=[extract_field(path,i,context,control) for i,path in enumerate(paths)]
    if {im['array'] for im in images}!={'hull','tow'} or len({im['platform_id'] for im in images})!=1:
        raise ValueError('两图必须属于同一平台，且分别为hull/tow')
    return {'version':'forward-field-v1','images':images,'warnings':[
        '按显式声明的工具配色及−5～16dB解释；恢复相对强度，不是绝对声压。',
        '测量误差、分峰和模型选择指标为工程假设，不是标定置信区间。',
        '理论叠加线、未知配色或超出支持的生成分布不能保证正确处理。']}
