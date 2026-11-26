"""Allowlisted blind measurements; never promote source labels to identity."""
import math

QUALITY = ('strength', 'quality', 'mixed', 'sigma_deg', 'bearing_error_assumption_deg')
PRIVATE_KEYS = {'api_key','apikey','authorization','password','access_token','refresh_token','secret','token','credentials'}


def audit_value(value):
    """Do not persist recognized authentication fields in supplied raw audits."""
    if isinstance(value,dict):
        return {k: '[redacted]' if str(k).lower().replace('-','_') in PRIVATE_KEYS else audit_value(v) for k,v in value.items()}
    if isinstance(value,list):
        return [audit_value(v) for v in value]
    return value


def normalize_live(row):
    if not isinstance(row, dict):
        raise ValueError('观测必须是对象')
    source_id = row.get('source_observation_id', row.get('candidate_id', row.get('id')))
    if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 160:
        raise ValueError('缺少有效来源观测编号')
    event_time = row.get('event_time', row.get('t'))
    for value in (event_time, row.get('bearing_deg')):
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError('时间和方位必须是有限数字')
    if not 0 <= row['bearing_deg'] <= 180:
        raise ValueError('折叠相对方位须在0至180度')
    if row.get('array') not in ('hull', 'tow'):
        raise ValueError('阵列须为hull或tow')
    if not isinstance(row.get('platform_id'), str) or not row['platform_id'].strip() or len(row['platform_id']) > 80:
        raise ValueError('平台编号无效')
    result = dict(source_observation_id=source_id, event_time=float(event_time),
                  bearing_deg=float(row['bearing_deg']), platform_id=row['platform_id'], array=row['array'])
    for key in QUALITY:
        if key not in row:
            continue
        value = row[key]
        if key == 'quality':
            if not isinstance(value, str) or len(value) > 80:
                raise ValueError('质量标记无效')
        elif key == 'mixed':
            if not isinstance(value, bool):
                raise ValueError('mixed须为布尔值')
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('质量数值无效')
        result[key] = value
    return result


def blind_point(row):
    return {k: row[k] for k in ('observation_id', 'event_time', 'platform_id', 'array', *QUALITY, 'bearing_deg') if k in row}

