"""Render validated numeric model output, never execute model-generated code."""
import json
import math
from pathlib import Path
import re
import threading

PLOT_LOCK=threading.Lock()
from backend.app.core.prompts import TRAJECTORY_PROMPT


def platform_context(raw):
    if len(raw)>100000:raise ValueError('platform.json超过100KB限制')
    data=json.loads(raw)
    allowed={'T_MAX','DT','B_MIN','B_MAX','REF_LAT','REF_LON','EARTH_R','platforms',
             'platform_motion_model','image_time_axis','bearing_convention','left_right_ambiguous'}
    if not isinstance(data,dict) or set(data)-allowed or not isinstance(data.get('platforms'),list) or not 1<=len(data['platforms'])<=8:
        raise ValueError('请选择input/platform.json，不接受含targets、params或真值的文件')
    duration=data.get('T_MAX',600)
    if type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=604800:
        raise ValueError('平台观测时长无效')
    dt=data.get('DT',1)
    bmin,bmax=data.get('B_MIN',0),data.get('B_MAX',180)
    if type(dt) not in (int,float) or not math.isfinite(dt) or dt<=0:
        raise ValueError('平台时间步长无效')
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in (bmin,bmax)) or not 0<=bmin<bmax<=180:
        raise ValueError('平台方位范围无效')
    direction=data.get('image_time_axis','bottom_to_top')
    if direction not in ('bottom_to_top','top_to_bottom'):
        raise ValueError('图片时间方向无效')
    if data.get('platform_motion_model','constant_heading_constant_speed')!='constant_heading_constant_speed':
        raise ValueError('当前仅支持恒航向恒航速平台运动模型')
    if data.get('bearing_convention','relative_heading_folded_0_180')!='relative_heading_folded_0_180':
        raise ValueError('当前仅支持相对船首0～180度折叠方位')
    georeference={}
    for key,lo,hi in (('REF_LAT',-84.999,84.999),('REF_LON',-180,180),('EARTH_R',1000000,10000000)):
        if key in data:
            value=data[key]
            if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi:
                raise ValueError('投影参考字段无效：'+key)
            georeference[key]=value
    for p in data['platforms']:
        if not isinstance(p,dict) or set(p)-{'name','start_ll','heading_deg','speed_kn','hull_m','tow_m','bounds','color'}:
            raise ValueError('平台字段不符合input/platform.json格式')
        for field in ('start_ll','hull_m','tow_m'):
            if not isinstance(p.get(field),list) or len(p[field])!=2 or not all(type(x) in (int,float) and math.isfinite(x) for x in p[field]):
                raise ValueError('平台缺少有效坐标或阵列几何参数')
        if not -90<=p['start_ll'][0]<=90 or not -180<=p['start_ll'][1]<=180:raise ValueError('平台经纬度无效')
        for field in ('heading_deg','speed_kn'):
            if type(p.get(field)) not in (int,float) or not math.isfinite(p[field]):raise ValueError('平台航向或航速无效')
    # Drop non-essential fields rather than sending arbitrary strings to the model.
    return {'T_MAX':duration,'DT':dt,'B_MIN':bmin,'B_MAX':bmax,**georeference,
            'image_time_axis':direction,'calibration_source':'input/platform.json（缺省字段按本项目工具约定）',
            'bearing_convention':'relative_heading_folded_0_180',
            'platforms':[{k:p[k] for k in ('name','start_ll','heading_deg','speed_kn','hull_m','tow_m') if k in p} for p in data['platforms']]}


def parse_tracks(answer,context=None):
    text=answer.strip()
    if text.startswith('```'):
        text=re.sub(r'^```(?:json)?\s*','',text,flags=re.I)
        text=re.sub(r'\s*```$','',text)
    doc=json.loads(text)
    if not isinstance(doc,dict):raise ValueError('模型未返回轨迹JSON对象')
    frame=doc.get('coordinate_system')
    if frame not in ('local_xy_m','lon_lat','bearing_time'):raise ValueError('轨迹坐标系无效')
    data_mode=bool(context and context.get('input_kind')=='data')
    if data_mode and frame!=context['coordinate_system']:
        raise ValueError('模型返回坐标系与数据文件不一致')
    if frame!='bearing_time' and (not context or (not data_mode and not context.get('platforms')) or doc.get('position_observable') is not True):
        raise ValueError('缺少已知平台信息或模型未确认位置可观，不能绘制空间轨迹')
    tracks=doc.get('tracks')
    if not isinstance(tracks,list) or not 1<=len(tracks)<=100:raise ValueError('没有可绘制轨迹或轨迹数量超限')
    warnings=doc.get('warnings',[])
    if not isinstance(warnings,list) or len(warnings)>100 or any(not isinstance(s,str) or len(s)>2000 for s in warnings):
        raise ValueError('warnings格式无效')
    total=0
    for track in tracks:
        if not isinstance(track,dict) or not isinstance(track.get('id'),str) or not 1<=len(track['id'])<=80:raise ValueError('目标编号无效')
        branch=track.get('branch','main')
        if not isinstance(branch,str) or len(branch)>80:raise ValueError('分支编号无效')
        points=track.get('points');previous=-1
        if not isinstance(points,list) or not (1 if data_mode else 2)<=len(points)<=2000:raise ValueError('轨迹点数量无效：图片模式至少2点，数据模式至少1点，最多2000点')
        total+=len(points)
        if total>20000:raise ValueError('轨迹总点数超限')
        for point in points:
            if not isinstance(point,dict) or any(type(point.get(k)) not in (int,float) or not math.isfinite(point[k]) for k in ('t','x','y')):
                raise ValueError('轨迹点必须是有限数值t/x/y，不能为空')
            t,x,y=(point[k] for k in ('t','x','y'))
            duration=context.get('T_MAX',604800) if context else 604800
            if type(duration) not in (int,float) or not math.isfinite(duration) or not 0<=t<=duration+1e-6 or t<=previous:
                raise ValueError('轨迹时间必须递增并在观测范围内')
            previous=t
            if frame=='lon_lat' and not (-180<=x<=180 and -90<=y<=90):raise ValueError('经纬度越界')
            if frame=='local_xy_m' and max(abs(x),abs(y))>1e7:raise ValueError('局部坐标越界')
            if frame=='bearing_time' and (not 0<=x<=180 or abs(t-y)>1e-6):raise ValueError('方位—时间点范围或时间不一致')
            if frame=='bearing_time' and context and not context.get('input_kind')=='data' and not context.get('B_MIN',0)<=x<=context.get('B_MAX',180):
                raise ValueError('方位点超出图片标定范围')
    if data_mode and context.get('source_tracks') is not None:
        source=context.get('model_tracks',context['source_tracks'])
        if len(source)!=len(tracks):raise ValueError('模型更改了数据文件的对象数量')
        for expected,actual in zip(source,tracks):
            if (expected['id'],expected.get('branch','main'))!=(actual['id'],actual.get('branch','main')) or len(expected['points'])!=len(actual['points']):
                raise ValueError('模型更改了数据文件的目标、分支或点位数量')
            for a,b in zip(expected['points'],actual['points']):
                if any(not math.isclose(a[k],b[k],rel_tol=1e-10,abs_tol=1e-8) for k in ('t','x','y')):
                    raise ValueError('模型更改了文件中的点位；不绘制无依据的轨迹')
    if data_mode and context.get('plot_kind')=='meta_reconstruction' and context.get('source_tracks'):
        tracks=context['source_tracks']
    prefix=context.get('data_warnings',[]) if data_mode else ['模型预测，未通过真值或物理一致性核验。']
    if not data_mode and frame=='bearing_time' and context and context.get('calibration_source'):
        prefix=prefix+[f"标定来源：{context['calibration_source']}；方位{context['B_MIN']}～{context['B_MAX']}度，时间0～{context['T_MAX']}秒，时间方向{context['image_time_axis']}。"]
    return {'coordinate_system':frame,'position_observable':frame!='bearing_time',
            'warnings':prefix+warnings,'tracks':tracks,**({'input_mode':'data','plot_kind':context.get('plot_kind','tracks')} if data_mode else {})}


def render_tracks(answer,directory,context=None):
    if context and context.get('require_spatial'):
        from backend.app.services.spatial_tracks import solve_spatial
        text=re.sub(r'^```(?:json)?\s*|\s*```$','',answer.strip(),flags=re.I)
        document=json.loads(text)
        if not isinstance(document,dict):raise ValueError('模型未返回双阵观测JSON对象')
        doc=solve_spatial(document,context)
    else:
        doc=parse_tracks(answer,context)
    return render_document(doc,directory,context)


def render_document(doc,directory,context=None,*,filename='estimated_track.png',persist=True):
    """Draw an already validated local result; never re-solve its observations."""
    if context and context.get('platforms') and doc['coordinate_system']!='bearing_time':
        plat=context['platforms'][0]
        doc['coordinate_reference']={'platform_id':plat.get('name','A'),
            'origin_lat':plat['start_ll'][0],'origin_lon':plat['start_ll'][1],
            'earth_radius_m':context.get('EARTH_R',6371000.0),'projection_lat':context.get('REF_LAT',plat['start_ll'][0])}
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    frame=doc['coordinate_system']
    colors=['#ffa600','#f000d0','#00d9e8','#a2ff00','#6aa5ff','#ff6d7c']
    spatial=doc.get('plot_kind')=='spatial_candidates'
    identity_colors={identity:colors[i%len(colors)] for i,identity in enumerate(dict.fromkeys(t['id'] for t in doc['tracks']))}
    seen_labels=set()
    if spatial:
        doc['plot_summary']={'identity_hypotheses':len(identity_colors),'candidate_segments':len(doc['tracks']),
                             'target_count_confirmed':False,'colors_grouped_by_identity':True,
                             'mirror_styles_not_unique_solutions':True}
    with PLOT_LOCK:
        fig=Figure(figsize=(11,6.5),dpi=150,facecolor='#050810');FigureCanvasAgg(fig)
        ax=fig.add_subplot(111);ax.set_facecolor('#0a0f1f')
        for i,track in enumerate(doc['tracks']):
            pts=track['points'];fallback=identity_colors[track['id']] if spatial else colors[i%len(colors)]
            color=context.get('target_colors',{}).get(track['id'],fallback) if context else fallback
            # Display labels are generated locally; never interpret model LaTeX.
            endpoints=doc.get('plot_kind')=='endpoints'
            linestyle='None' if endpoints else '-'
            label=f'Target {i+1} endpoints' if endpoints else f'Track {i+1}'
            if spatial:
                side=tuple(track.get('handedness',()))
                if 'mirror_variant' in track:
                    alternative=track['mirror_variant'];side=('alternative',alternative)
                    linestyle=['-','--',':','-.'][alternative%4]
                    sides=f'alternative {alternative+1}'
                else:
                    linestyle={(1,1):'-',(-1,-1):'--',(1,-1):':',(-1,1):'-.'}.get(side,'-')
                    sides='side '+('/'.join(f'{sign:+d}' for sign in side) if side else 'unspecified')
                key=(track['id'],side)
                label='_nolegend_' if key in seen_labels else f"{track['id']} | {sides} (candidate)"
                seen_labels.add(key)
            ax.plot([p['x'] for p in pts],[p['y'] for p in pts],color=color,lw=1.4,
                    linestyle=linestyle,marker='o' if endpoints else None,label=label)
            ax.plot(pts[0]['x'],pts[0]['y'],'o',color=color,ms=4)
            if endpoints:ax.plot(pts[-1]['x'],pts[-1]['y'],'x',color=color,ms=7)
        if frame!='bearing_time' and context.get('platforms'):
            start=context['platforms'][0]['start_ll']
            x,y=(start[1],start[0]) if frame=='lon_lat' else (0,0)
            ax.plot(x,y,'s',color='#ffd400',label='Platform initial position',ms=6)
            if frame=='lon_lat':
                for plat in context['platforms'][1:]:
                    ax.plot(plat['start_ll'][1],plat['start_ll'][0],'s',color='#ffd400',ms=6)
            if frame=='local_xy_m':ax.set_aspect('equal',adjustable='datalim')
            elif abs(start[0])<89:ax.set_aspect(1/math.cos(math.radians(start[0])),adjustable='datalim')
        elif frame=='local_xy_m':
            ax.set_aspect('equal',adjustable='datalim')
        elif frame=='lon_lat':
            latitude=doc['tracks'][0]['points'][0]['y']
            if abs(latitude)<89:ax.set_aspect(1/math.cos(math.radians(latitude)),adjustable='datalim')
        labels={'local_xy_m':('East (m), relative to platform initial position','North (m)'),
                'lon_lat':('Longitude (deg)','Latitude (deg)'),
                'bearing_time':('Relative folded bearing (deg)','Time (s)')}
        if doc.get('input_mode')=='data':labels['local_xy_m']=('East (m), supplied local coordinates','North (m)')
        ax.set_xlabel(labels[frame][0],color='#dce3ee');ax.set_ylabel(labels[frame][1],color='#dce3ee')
        ax.tick_params(colors='#a6b4c8');ax.grid(color='#566070',alpha=.3,linestyle=':')
        if frame=='bearing_time':ax.set_xlim(0,180)
        title='USER DATA PLOT - NOT SONAR INFERENCE' if doc.get('input_mode')=='data' else 'MODEL ESTIMATE - NOT GROUND TRUTH'
        if doc.get('plot_kind')=='endpoints':title='REFERENCE ENDPOINTS ONLY - NOT COMPLETE TRACKS'
        if doc.get('plot_kind')=='meta_reconstruction':title='META RECONSTRUCTION - FULL SIMULATION TRACKS'
        if doc.get('plot_kind')=='spatial_candidates':title='DUAL-ARRAY SPATIAL CANDIDATES - MIRROR AMBIGUITY'
        if doc.get('display_view')=='primary_motion_hypotheses':title='PRIMARY MOTION HYPOTHESES - SIDE NOT VERIFIED'
        if spatial:title+=f"\n{len(identity_colors)} identity hypotheses / {len(doc['tracks'])} fragments - NOT confirmed targets"
        ax.set_title(title+(' / BEARING-TIME ONLY' if frame=='bearing_time' else ''),color='#dce3ee',fontsize=11)
        ax.legend(facecolor='#101828',labelcolor='#dce3ee',fontsize=8)
        directory=Path(directory)
        if filename not in ('estimated_track.png','mirror_candidates.png'):raise ValueError('轨迹视图文件名无效')
        fig.savefig(directory/filename,bbox_inches='tight',facecolor=fig.get_facecolor())
        if persist:(directory/'tracks.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')
        fig.clear()
    return doc
