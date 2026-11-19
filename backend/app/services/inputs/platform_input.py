"""Validate platform metadata without rendering or inference."""
import json
import math


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
