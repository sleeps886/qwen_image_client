"""Validate explicitly selected numeric files; never execute JavaScript."""
import json
import math
import re

from backend.app.services.trajectory import parse_tracks, platform_context
from backend.app.services.meta_tracks import reconstruct_meta

MAX_DATA_BYTES = 1024 * 1024
from backend.app.core.prompts import DATA_PROMPT


def read_document(raw, filename='data.json'):
    if not raw or len(raw) > MAX_DATA_BYTES:
        raise ValueError('数据文件不能为空，且不能超过1MiB')
    try:
        text = raw.decode('utf-8-sig').strip()
        if filename.lower().endswith('.js'):
            match = re.fullmatch(r'(?:export\s+default\s+|(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*|module\.exports\s*=\s*)([\s\S]+?)\s*;?', text)
            if match:
                text = match[1]
        if filename.lower().endswith('.jsonl'):
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        return json.loads(text)
    except (ValueError, UnicodeDecodeError):
        raise ValueError('请提供UTF-8 JSON/JSONL；JS仅支持包裹严格JSON的变量赋值或export default，不执行代码') from None


def object_tracks(data):
    """This mode deliberately treats platforms as the user's drawing objects."""
    context = platform_context(json.dumps(data).encode())
    if data.get('platform_motion_model', 'constant_heading_constant_speed') != 'constant_heading_constant_speed':
        raise ValueError('初始参数文件目前只支持constant_heading_constant_speed运动模型')
    duration = context['T_MAX']
    tracks = []
    for index, obj in enumerate(context['platforms']):
        lat, lon = obj['start_ll']
        speed, heading = obj['speed_kn'], obj['heading_deg']
        if not 0 <= speed <= 200 or not 0 <= heading < 360:
            raise ValueError('对象航速应为0～200节，航向应为0～360度（不含360）')
        points = []
        for t in (0, duration):
            # Same local east/north approximation as the existing simulator.
            north = speed * .514444 * t * math.cos(math.radians(heading))
            east = speed * .514444 * t * math.sin(math.radians(heading))
            if abs(lat) >= 89 and speed:
                raise ValueError('极区运动不支持当前局部近似，请提供实际点位')
            y = lat + math.degrees(north / 6371000)
            x = lon + math.degrees(east / (6371000 * max(math.cos(math.radians(lat)), 1e-12)))
            points.append({'t': t, 'x': x, 'y': y})
        tracks.append({'id': str(obj.get('name', f'T{index+1}')), 'branch': 'main', 'points': points})
    return {'coordinate_system': 'lon_lat', 'tracks': tracks}, context


def normalize_points(data):
    if isinstance(data, list):
        # JSONL truth rows or a simple list of named lon/lat points.
        if not data or not all(isinstance(p, dict) for p in data):
            raise ValueError('点位列表不能为空且每项必须是对象')
        tracks = {}
        for row in data:
            key = (str(row.get('target_id', row.get('id', 'T1'))), str(row.get('branch', 'main')))
            point = {'t': row.get('time_s', row.get('t')), 'x': row.get('lon'), 'y': row.get('lat')}
            tracks.setdefault(key, []).append(point)
        return {'coordinate_system': 'lon_lat', 'tracks': [
            {'id': key[0], 'branch': key[1], 'points': points} for key, points in tracks.items()]}
    if not isinstance(data, dict):
        raise ValueError('数据文件需要JSON对象或点位列表')
    if 'tracks' in data:
        return {key: data.get(key) for key in ('coordinate_system', 'tracks')}
    if 'points' in data:
        return {'coordinate_system': data.get('coordinate_system'), 'tracks': [
            {'id': str(data.get('id', 'T1')), 'branch': 'main', 'points': data['points']}]}
    raise ValueError('文件需要meta.json的params/targets、semantic摘要或coordinate_system与tracks/points点位数据')


def semantic_tracks(data):
    """Read explicit summary endpoints, not referenced local files or prose."""
    settings=data.get('sim_settings')
    if not isinstance(settings,dict):raise ValueError('semantic缺少仿真设置')
    duration=settings.get('t_max_s')
    if type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=604800:
        raise ValueError('semantic仿真时长无效')
    platforms=data.get('platforms')
    if not isinstance(platforms,list) or not 1<=len(platforms)<=8:
        raise ValueError('semantic需要1～8个观测平台')
    tracks={};observers=[]
    def ll(value):
        if not isinstance(value,dict) or any(type(value.get(k)) not in (int,float) or not math.isfinite(value[k]) for k in ('lat','lon')):
            raise ValueError('semantic缺少有效经纬度')
        if not -90<=value['lat']<=90 or not -180<=value['lon']<=180:
            raise ValueError('semantic经纬度越界')
        return value['lat'],value['lon']
    for platform in platforms:
        if not isinstance(platform,dict):raise ValueError('semantic平台格式错误')
        own=platform.get('self')
        if not isinstance(own,dict):raise ValueError('semantic缺少平台self信息')
        lat,lon=ll(own.get('position_start'))
        name=platform.get('platform_id')
        if not isinstance(name,str) or not 1<=len(name)<=80:raise ValueError('semantic平台编号无效')
        observers.append({'name':name,'start_ll':[lat,lon]})
        targets=platform.get('targets')
        if not isinstance(targets,list) or not targets or len(targets)>100:
            raise ValueError('semantic需要非空targets目标列表，最多100个目标')
        for target in targets:
            if not isinstance(target,dict) or not isinstance(target.get('kinematics'),dict):
                raise ValueError('semantic目标缺少kinematics')
            tid=target.get('target_id');kin=target['kinematics']
            if not isinstance(tid,str) or not 1<=len(tid)<=80:raise ValueError('semantic目标编号无效')
            first,last=ll(kin.get('position_start')),ll(kin.get('position_end'))
            track={'id':tid,'branch':'main','points':[
                {'t':0,'x':first[1],'y':first[0]}, {'t':duration,'x':last[1],'y':last[0]}]}
            if tid in tracks and tracks[tid]!=track:
                raise ValueError('semantic跨平台的同编号目标起终点不一致，请检查来源')
            tracks[tid]=track
    return {'coordinate_system':'lon_lat','tracks':list(tracks.values())},observers,duration


def prepare_data(raw, filename='data.json'):
    data = read_document(raw, filename)
    meta = isinstance(data,dict) and ('params' in data or 'targets' in data and 'platforms' in data)
    semantic = isinstance(data, dict) and 'sim_settings' in data and 'platforms' in data
    derived = isinstance(data, dict) and 'platforms' in data and not semantic and not meta
    platform = None
    observers=[]
    if meta:
        doc,observers,duration,colors=reconstruct_meta(data)
    elif semantic:
        doc,observers,duration=semantic_tracks(data)
    elif derived:
        doc, platform = object_tracks(data)
    else:
        doc = normalize_points(data)
    frame = doc.get('coordinate_system')
    context = {'input_kind': 'data', 'coordinate_system': frame}
    if meta:
        context.update(T_MAX=duration,platforms=observers,plot_kind='meta_reconstruction',target_colors=colors)
    elif semantic:
        context.update(T_MAX=duration,platforms=observers,plot_kind='endpoints')
    if platform:
        context['T_MAX'] = platform['T_MAX']
    doc.update(position_observable=frame != 'bearing_time', warnings=[])
    validated = parse_tracks(json.dumps(doc), context)
    # Whitelist numeric data; never forward arbitrary script/string fields.
    source_tracks = [
        {'id': t['id'], 'branch': t.get('branch', 'main'), 'points': [
            {k: p[k] for k in ('t', 'x', 'y')} for p in t['points']]} for t in validated['tracks']]
    model_tracks=source_tracks
    if meta:
        # Only checkpoints go to the model; all DT samples remain for local drawing.
        # This avoids making a model echo thousands of known coordinates or silently
        # dropping the full-resolution reconstruction to fit its context/output limit.
        model_tracks=[]
        count=min(9,max(2,120//len(source_tracks)))
        for track in source_tracks:
            points=track['points'];n=min(count,len(points))
            indexes=sorted({round(i*(len(points)-1)/(n-1)) for i in range(n)})
            model_tracks.append({'id':track['id'],'branch':track['branch'],'points':[
                {k:round(points[i][k],9 if k=='t' else 8) for k in ('t','x','y')} for i in indexes]})
        context['model_tracks']=model_tracks
    clean = {'coordinate_system': frame, 'position_observable': frame != 'bearing_time', 'tracks':model_tracks}
    if meta:
        clean['source_note']='meta已按DT在本地完整重建；这里只发送核对点。原样返回核对点，程序将使用本地完整采样生成连续轨迹图，而非仅连这些稀疏点。'
    if semantic:
        clean['source_note']='仿真参考目标的全时段首尾点，不是观测窗端点；仅生成起终点示意图，不含完整轨迹。'
    text = json.dumps(clean, ensure_ascii=False, separators=(',', ':'))
    if len(text) > 40000:
        raise ValueError('有效点位超过40000字符的单次模型输入限制，请分段提供，程序不会静默删点')
    context['source_tracks'] = source_tracks
    context['data_warnings'] = [
        '单文件数据绘图：不是声呐反演，也不是独立盲测结果。',
        '根据meta.params.targets中的航点或曲线公式，按DT从全局t=0重建全时段轨迹（与仿真参考轨迹图一致，不按t_on/t_off截断）。公式仅用白名单数学解释器计算，不执行脚本；完整采样用于本地绘图，模型仅核对少量采样点。这是仿真参数重建，不是模型识别结果。'
        if meta else '仿真参考摘要：A等platforms.self是观测平台，targets中的T1等才是目标。仅画全仿真t=0和t=T_MAX的目标起终点，不连线、不补造中间轨迹；observation_window_s不是这些端点的时间。'
        if semantic else '这里只包含平台初始参数，将平台对象单独绘图；不是T1等被观测目标的数据。按恒定航向恒定航速推算，静止平台显示为一个点。'
        if derived else '使用用户提供的点位；未独立验证其物理真实性。']
    info = {'mode': 'data', 'coordinate_system': frame, 'objects': len(source_tracks),
            'points': sum(len(t['points']) for t in source_tracks), 'derived_from_initial_state': derived,
            'source_type':'meta_parameters' if meta else 'semantic_summary' if semantic else 'platform_initial_state' if derived else 'points',
            'plot_kind':'meta_reconstruction' if meta else 'endpoints' if semantic else 'tracks','platform_count':len(observers)}
    if meta:info['model_checkpoint_points']=sum(len(t['points']) for t in model_tracks)
    return context, text, info
