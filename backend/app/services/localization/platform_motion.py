"""Platform-only coordinates from saved navigation configuration; never target positions."""
import json
import math

from backend.app.services.inputs.platform_input import platform_context
from backend.app.services.localization.forward_observation import GeoReference,array_location


def platform_metadata(raw):
    if not isinstance(raw,dict):return None
    allowed=('T_MAX','DT','B_MIN','B_MAX','REF_LAT','REF_LON','EARTH_R','platforms',
             'platform_motion_model','image_time_axis','bearing_convention')
    try:
        data=platform_context(json.dumps({key:raw[key] for key in allowed if key in raw}).encode('utf-8'))
        if any(not isinstance(p.get('name'),str) or not 1<=len(p['name'])<=80 for p in data['platforms']):return None
        return {key:data[key] for key in ('platforms','EARTH_R') if key in data}|{
            'platform_motion_model':'constant_heading_constant_speed','source_file':'calibration_context.json' if raw.get('source_file')=='calibration_context.json' else 'dataset.platform_config'}
    except (ValueError,TypeError,KeyError,OverflowError):return None


def platform_snapshot(config,platform_id,times):
    if not config:return None
    platform=next((p for p in config.get('platforms',[]) if p['name']==platform_id),None)
    if not platform:return None
    try:
        reference=GeoReference(*platform['start_ll'],config.get('EARTH_R',6371000))
        points=[]
        for t in sorted({0.,*times}):
            if type(t) not in (int,float) or not math.isfinite(t) or t<0:raise ValueError('平台时间无效')
            center=array_location(platform,[0,0],t,reference);east,north=reference.to_xy(center)
            sensors={}
            for array in ('hull','tow'):
                ll=array_location(platform,platform[array+'_m'],t,reference);xy=reference.to_xy(ll)
                sensors[array]={'latitude':ll[0],'longitude':ll[1],'east_m':xy[0],'north_m':xy[1]}
            points.append({'t':t,'latitude':center[0],'longitude':center[1],'east_m':east,'north_m':north,
                'displacement_m':math.hypot(east,north),'sensors':sensors})
        return {'platform_id':platform_id,'position_source':'configured_constant_heading_speed_model',
            'source_file':config.get('source_file','calibration_context.json'),
            'coordinate_reference':'platform_initial_position','initial_latitude':platform['start_ll'][0],
            'initial_longitude':platform['start_ll'][1],'heading_deg':platform['heading_deg'],
            'speed_kn':platform['speed_kn'],'points':points}
    except (ValueError,TypeError,KeyError,OverflowError):return None
