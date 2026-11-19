"""Validate numeric trajectory contracts without rendering."""
import json
import math
import re


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
