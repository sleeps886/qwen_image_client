"""Read observation packages in memory; never extract files or consume truth data."""
import copy
import hashlib
import io
import json
import math
from pathlib import PurePosixPath
import zipfile
import zlib

MAX_BYTES = 16 * 1024 * 1024


def read_json(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('观测JSON不能超过16MiB')
    try:
        def reject_constant(value):
            raise ValueError('非有限数值')
        return json.loads(raw.decode('utf-8-sig'), parse_constant=reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise ValueError('观测JSON格式无效') from None


def validate(data):
    if not isinstance(data, dict) or data.get('evaluation_only') or not isinstance(data.get('candidates'), list) or not data['candidates']:
        raise ValueError('请选择 observations.json 数据点文件；轨迹真值或配置文件不能当作观测输入。')
    ids = set()
    for point in data['candidates']:
        if not isinstance(point, dict):
            raise ValueError('观测点格式无效')
        cid = point.get('candidate_id') or point.get('id')
        if not isinstance(cid, str) or not cid or cid in ids:
            raise ValueError('观测编号为空或重复')
        ids.add(cid)
        if (not isinstance(point.get('platform_id'), str) or not point['platform_id']
                or point.get('array') not in ('hull', 'tow')
                or any(isinstance(point.get(k), bool) or not isinstance(point.get(k), (float, int))
                       or not math.isfinite(point[k]) for k in ('t', 'bearing_deg'))):
            raise ValueError('点位缺少有效时间、方位、平台或阵列')
        point['candidate_id'] = cid
    return data


def read_observations(name, raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('单个数据文件不能超过16MiB')
    if name.lower().endswith('.json'):
        return validate(read_json(raw))
    if not name.lower().endswith('.zip'):
        raise ValueError('仅支持ZIP轨迹包或JSON观测文件')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > 2048 or sum(i.file_size for i in infos) > 128 * 1024 * 1024:
                raise ValueError('ZIP条目过多或解压后大小超过128MiB')
            for info in infos:
                path = PurePosixPath(info.filename.replace('\\', '/'))
                if path.is_absolute() or '..' in path.parts or ':' in info.filename or info.flag_bits & 1:
                    raise ValueError('ZIP包含不安全路径或加密条目')
            observations = [i for i in infos if PurePosixPath(i.filename).name.lower() == 'observations.json']
            if len(observations) != 1:
                raise ValueError('ZIP须包含唯一的 observations.json；请导入单轨迹包，而非总压缩包')
            def read(info):
                if info.file_size > MAX_BYTES:
                    raise ValueError('ZIP中的JSON不能超过16MiB')
                with archive.open(info) as stream:
                    return read_json(stream.read(MAX_BYTES + 1))
            data = validate(read(observations[0]))
            if not data.get('platform_config'):
                platform_name = str(PurePosixPath(observations[0].filename).parent / 'platform.json')
                platforms = [i for i in infos if i.filename == platform_name]
                if len(platforms) == 1:
                    data['platform_config'] = read(platforms[0])
            return data
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, OSError, zlib.error):
        raise ValueError('ZIP损坏或压缩格式不支持') from None


def import_observations(files):
    if not files or len(files) > 16:
        raise ValueError('每次请导入1至16个文件')
    if sum(len(raw) for _, raw in files) > 20 * 1024 * 1024:
        raise ValueError('本次导入文件总大小不能超过20MiB')
    datasets = [read_observations(name, raw) for name, raw in files]
    # Mixing incompatible platforms would silently assign the wrong observer motion.
    configs = [d['platform_config'] for d in datasets if d.get('platform_config')]
    if any(c != configs[0] for c in configs[1:]) or (len(datasets) > 1 and configs and len(configs) != len(datasets)):
        raise ValueError('各文件的平台配置不一致或缺失，请分别导入，避免混用平台轨迹')
    result = copy.deepcopy(datasets[0])
    if len(datasets) > 1:
        result = {'version': 'imported-observations-v1', 'candidates': [], 'target_catalog': []}
        if configs:
            result['platform_config'] = configs[0]
    catalog = result.setdefault('target_catalog', [])
    for index, ((name, raw), data) in enumerate(zip(files, datasets), 1):
        label = PurePosixPath(name).stem
        if len(datasets) > 1:
            label = f'{index:02d} · {label}'
            prefix = f'I{index:02d}-'
            result['candidates'].extend({**p, 'candidate_id': prefix + p['candidate_id']} for p in data['candidates'])
            catalog.append({'target_id': label, 'candidate_ids': [prefix + p['candidate_id'] for p in data['candidates']]})
        elif not catalog:
            catalog.append({'target_id': label, 'candidate_ids': [p['candidate_id'] for p in data['candidates']]})
    digest = hashlib.sha256(b''.join(hashlib.sha256(raw).digest() for _, raw in files)).hexdigest()[:24]
    result['batch_id'] = 'import-' + digest
    result.pop('source_result_id', None)
    result['file_name'] = (PurePosixPath(files[0][0]).stem if len(files) == 1 else f'合并观测_{len(files)}个文件_{digest[:8]}') + '.json'
    result['imported_files'] = [name for name, _ in files]
    result['import_note'] = '按来源文件分组，仅用于浏览，不代表模型已确认目标身份；未读取真值。'
    return result
