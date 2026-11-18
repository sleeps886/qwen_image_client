"""Experimental, declared-profile BTR measurements; never replace observations.

Uses the GUI colour map to recover relative S/P0, not absolute acoustic levels.
Diagnostics are separate from current candidate IDs and localization input.
"""
from pathlib import Path

import numpy as np
from PIL import Image,ImageDraw
from scipy.optimize import least_squares
from scipy.spatial import cKDTree

PROFILE='btr_gui_v1'
COLORS=np.array([(0,0,0),(0,0,.35),(0,.15,.5),(0,.45,.75),(.2,.85,.85),
                 (.95,.95,.25),(1,.55,.1),(1,.2,0),(1,0,0)],dtype=float)
GRID=np.linspace(0,1,256)
PALETTE=np.stack([np.interp(GRID,np.linspace(0,1,len(COLORS)),COLORS[:,i]) for i in range(3)],axis=1)
PALETTE=np.floor(PALETTE*255).astype(np.uint8)
TREE=cKDTree(PALETTE.astype(float))
MAX_ROI_PIXELS=2000000
MAX_FIT_GROUPS=128


def decode_relative(rgb,profile):
    if profile!=PROFILE:raise ValueError('仅支持显式声明的btr_gui_v1渲染配置')
    rgb=np.asarray(rgb)
    if rgb.ndim!=3 or rgb.shape[2]!=3 or rgb.dtype!=np.uint8:raise ValueError('颜色测量需要uint8 RGB图像')
    if not rgb.shape[0] or not rgb.shape[1] or rgb.shape[0]*rgb.shape[1]>MAX_ROI_PIXELS:
        raise ValueError('实验测量绘图区必须为1～200万像素')
    distance,index=TREE.query(rgb.reshape(-1,3).astype(float),workers=1)
    shape=rgb.shape[:2];index=index.reshape(shape)
    db=-5.+21.*index/255.
    return {'relative':(10**(db/10)).astype(np.float32),'valid':(distance.reshape(shape)<=12),
            'lower_clipped':index==0,'upper_clipped':index==255,
            'valid_fraction':float(np.mean(distance<=12)),
            'meaning':'relative_S_over_unknown_global_median','db_range_assumption':[-5,16]}


def fit_profile(angles,intensity,centers,*,lower=None,upper=None):
    """One/two positive Gaussian components with censored endpoint residuals.

    Component count is supplied by observed candidates, NOT inferred target count.
    Residual/width are engineering diagnostics, not calibrated confidence.
    """
    x=np.asarray(angles,dtype=float);y=np.asarray(intensity,dtype=float)
    centers=np.sort(np.asarray(centers,dtype=float));n=len(centers)
    if x.ndim!=1 or y.shape!=x.shape or len(x)<12 or n not in (1,2):
        raise ValueError('高斯拟合需要至少12个有效样本和1～2个中心初值')
    if not np.isfinite(x).all() or not np.isfinite(y).all() or not np.isfinite(centers).all() or np.any(y<0) or np.any(np.diff(x)<=0):
        raise ValueError('高斯拟合样本必须有限、非负且方位递增')
    if not x[0]<centers[0] or not centers[-1]<x[-1]:raise ValueError('拟合初值超出测量窗口')
    lower=np.zeros(len(x),dtype=bool) if lower is None else np.asarray(lower,dtype=bool)
    upper=np.zeros(len(x),dtype=bool) if upper is None else np.asarray(upper,dtype=bool)
    if lower.shape!=x.shape or upper.shape!=x.shape or np.any(lower&upper):raise ValueError('截断标志格式错误')
    if np.count_nonzero(~(lower|upper))<8:raise ValueError('非饱和测量不足，不能拟合亮带')
    scale=max(float(np.percentile(y,95)),1e-6);z=y/scale
    step=float(np.median(np.diff(x)));span=x[-1]-x[0]
    baseline=max(0.,float(np.percentile(z,15)));amp=max(.05,float(z.max()-baseline))
    sigma=max(step,min(2.,span/5))
    seed=np.r_[baseline,np.tile(amp/n,n),centers,np.full(n,sigma)]
    lo=np.r_[0.,np.zeros(n),np.full(n,x[0]+step/4),np.full(n,max(step/2,.05))]
    hi=np.r_[max(2.,float(z.max())*2),np.full(n,max(4.,float(z.max())*4)),
             np.full(n,x[-1]-step/4),np.full(n,max(step,span/2))]
    seed=np.clip(seed,lo+1e-8,hi-1e-8)
    def prediction(p):
        return p[0]+np.sum(p[1:1+n,None]*np.exp(-.5*((x[None,:]-p[1+n:1+2*n,None])/p[1+2*n:,None])**2),axis=0)
    def residual(p):
        r=prediction(p)-z
        r[lower]=np.maximum(r[lower],0);r[upper]=np.minimum(r[upper],0)
        return r
    fit=least_squares(residual,seed,bounds=(lo,hi),loss='soft_l1',f_scale=.1,max_nfev=100)
    components=sorted([{'center_deg':float(fit.x[1+n+i]),'sigma_deg':float(fit.x[1+2*n+i]),
                        'relative_amplitude':float(fit.x[1+i]*scale)} for i in range(n)],key=lambda p:p['center_deg'])
    rmse=float(np.sqrt(np.mean(residual(fit.x)**2)))
    separated=n==1 or components[1]['center_deg']-components[0]['center_deg']>max(step, min(c['sigma_deg'] for c in components))
    contrast=max(c['relative_amplitude'] for c in components)/scale
    return {'status':'fitted' if fit.success and rmse<=.15 and separated and contrast>.05 else 'needs_review',
            'components':components,'normalized_rmse':rmse,'optimizer_success':bool(fit.success),
            'component_count_from_candidates':n,'independently_resolved':bool(separated),
            'clipped_fraction':float(np.mean(lower|upper)),'statistical_confidence':False}


def measure_image(path,evidence,context):
    left,top,right,bottom=evidence['roi']
    with Image.open(path) as source:
        if source.width*source.height>16000000:raise ValueError('原图像素超过1600万限制')
        rgb=np.asarray(source.convert('RGB').crop((left,top,right+1,bottom+1)))
    decoded=decode_relative(rgb,PROFILE)
    if decoded['valid_fraction']<.9:raise ValueError('图像与声明的工具配色不一致，未生成高斯测量')
    width=rgb.shape[1];bmin,bmax=context.get('B_MIN',0),context.get('B_MAX',180)
    bearings=np.linspace(bmin,bmax,width);rows={}
    for p in evidence['candidates']:rows.setdefault(p['sample_index'],[]).append(p)
    groups=[];fitted=0
    for row,points in sorted(rows.items()):
        points=sorted(points,key=lambda p:p['bearing_deg']);clusters=[]
        for p in points:
            if clusters and len(clusters[-1])<2 and p['bearing_deg']-clusters[-1][-1]['bearing_deg']<14:clusters[-1].append(p)
            else:clusters.append([p])
        for cluster in clusters:
            result={'candidate_ids':[p['id'] for p in cluster],'sample_index':row,'t':cluster[0]['t']}
            if fitted>=MAX_FIT_GROUPS:
                groups.append(result|{'status':'budget_exceeded'});continue
            fitted+=1;y=int(cluster[0]['y_px'])-top;y0,y1=max(0,y-2),min(rgb.shape[0],y+3)
            valid=np.mean(decoded['valid'][y0:y1],axis=0)>=.8
            valid &= (bearings>=cluster[0]['bearing_deg']-10)&(bearings<=cluster[-1]['bearing_deg']+10)
            values=np.ma.median(np.ma.array(decoded['relative'][y0:y1],mask=~decoded['valid'][y0:y1]),axis=0).filled(np.nan)
            lower=np.mean(decoded['lower_clipped'][y0:y1],axis=0)>=.6
            upper=np.mean(decoded['upper_clipped'][y0:y1],axis=0)>=.6
            try:
                fit=fit_profile(bearings[valid],values[valid],[p['bearing_deg'] for p in cluster],lower=lower[valid],upper=upper[valid])
                groups.append(result|fit)
            except ValueError as exc:groups.append(result|{'status':'needs_review','reason':str(exc)[:160]})
    return {'image_index':evidence['image_index'],'array':evidence['array'],'platform_id':evidence['platform_id'],
            'valid_color_fraction':decoded['valid_fraction'],'groups':groups,
            'fitted_groups':sum(g['status']=='fitted' for g in groups),'candidate_count':len(evidence['candidates'])}


def measure_pack(paths,evidence,context):
    profile=context.get('renderer_profile','none')
    if profile!=PROFILE:raise ValueError('必须明确选择工具图渲染配置后才能启用实验测量')
    if len(paths)!=len(evidence['images']):raise ValueError('测量图片与证据数量不一致')
    images=[]
    for path,im in zip(paths,evidence['images']):
        try:images.append(measure_image(path,im,context))
        except (ValueError,OSError) as exc:
            images.append({'image_index':im['image_index'],'array':im['array'],'status':'unavailable',
                           'reason':str(exc)[:200],'groups':[],'fitted_groups':0})
    return {'version':'btr-gaussian-diagnostic-v1','renderer_profile':profile,'status':'diagnostic_only',
            'observations_modified':False,'target_count_estimated':False,'db_range_assumption':[-5,16],
            'max_fit_groups_per_image':MAX_FIT_GROUPS,'max_roi_pixels':MAX_ROI_PIXELS,
            'images':images,'warnings':['实验高斯测量不替换当前候选、不决定目标数、不参与本次定位。',
                '配色和dB范围按显式选定的工具配置解释；理论叠加线、饱和和混合峰仍需复核。',
                '只估计相对强度及候选支持的1～2峰，不能从已合并的单峰保证恢复两个目标。']}


def save_measurement_previews(report,evidence,paths,directory,context):
    for result,im,path in zip(report['images'],evidence['images'],paths):
        if result.get('status')=='unavailable':continue
        with Image.open(path) as source:canvas=source.convert('RGB')
        draw=ImageDraw.Draw(canvas);left,top,right,bottom=im['roi'];bmin,bmax=context.get('B_MIN',0),context.get('B_MAX',180)
        lookup={p['id']:p for p in im['candidates']}
        for group in result['groups']:
            if not group.get('components'):continue
            y=lookup[group['candidate_ids'][0]]['y_px'];color='#00ff88' if group['status']=='fitted' else '#ffb84d'
            for c in group['components']:
                x=left+(c['center_deg']-bmin)/(bmax-bmin)*(right-left)
                radius=c['sigma_deg']/(bmax-bmin)*(right-left)
                draw.line((max(left,x-radius),y,min(right,x+radius),y),fill=color,width=2)
                draw.ellipse((x-3,y-3,x+3,y+3),outline=color,width=2)
        canvas.save(Path(directory)/f"gaussian_{im['image_index']}.png")
