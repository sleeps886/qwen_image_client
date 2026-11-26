"""Export measured Gaussian centres, never relabel pixel peaks or inferred tracks."""
import hashlib
import json

from backend.app.services.association.incremental_prediction import number


def gaussian_batch(report,result_id,source_file):
    points=[];excluded=0
    if source_file=='inverse_fields.json':
        if report.get('version')!='forward-field-v1':raise ValueError('不是支持的高斯剖面测量版本')
        for image in report['images']:
            if image.get('measurement_method')!='relative_intensity_gaussian_profiles_v1':
                raise ValueError('文件包含非高斯测量，拒绝转换像素候选')
            for p in image['candidates']:
                points.append({'candidate_id':p['id'],'t':p['t'],'bearing_deg':p['bearing_deg'],
                    'array':image['array'],'platform_id':image['platform_id'],
                    'sigma_deg':p['sigma_deg'],'strength':p['amplitude'],
                    'bearing_error_assumption_deg':p['error_deg'],'quality':p['quality'],
                    'mixed':p['mixed'],'source_kind':'gaussian_profile',
                    'source_candidate_ids':[p['id']]})
    elif source_file=='measurement_report.json':
        if report.get('version')!='btr-gaussian-diagnostic-v1':raise ValueError('不是支持的高斯诊断版本')
        for image in report['images']:
            for group_index,group in enumerate(image['groups']):
                if group.get('status')!='fitted':
                    excluded+=1;continue
                for component_index,c in enumerate(group['components']):
                    points.append({'candidate_id':f"G{image['image_index']}K{group_index}P{component_index}",
                        't':group['t'],'bearing_deg':c['center_deg'],'array':image['array'],
                        'platform_id':image['platform_id'],'sigma_deg':c['sigma_deg'],
                        'strength':c['relative_amplitude'],'quality':'fitted','mixed':False,
                        'source_kind':'gaussian_diagnostic','source_candidate_ids':group['candidate_ids']})
    else:raise ValueError('只允许高斯测量文件，不允许像素候选或空间轨迹')
    if not points:raise ValueError('该结果没有可导出的高斯测量点，未使用像素候选替代')
    ids=set()
    for p in points:
        if not isinstance(p['candidate_id'],str) or not 1<=len(p['candidate_id'])<=80 or p['candidate_id'] in ids:
            raise ValueError('高斯测量编号无效或重复')
        ids.add(p['candidate_id'])
        if p['array'] not in ('hull','tow') or not isinstance(p['platform_id'],str) or not p['platform_id']:
            raise ValueError('高斯点缺少阵列或平台')
        for key in ('t','bearing_deg','sigma_deg','strength','bearing_error_assumption_deg'):
            if key in p:p[key]=number(p[key])
        if p['t']<0 or not 0<=p['bearing_deg']<=180 or p['sigma_deg']<=0 or p['strength']<0:
            raise ValueError('高斯点数值超出支持范围')
        if 'bearing_error_assumption_deg' in p and p['bearing_error_assumption_deg']<=0:
            raise ValueError('角度误差代理无效')
        if p['quality'] not in ('fitted','needs_review') or type(p['mixed']) is not bool:
            raise ValueError('高斯测量质量标记无效')
        if not isinstance(p['source_candidate_ids'],list) or any(not isinstance(cid,str) or not 1<=len(cid)<=80 for cid in p['source_candidate_ids']):
            raise ValueError('高斯点来源编号无效')
    points.sort(key=lambda p:(p['platform_id'],p['array'],p['t'],p['candidate_id']))
    digest=hashlib.sha256(json.dumps(points,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return {'version':'gaussian-prediction-batch-v1','batch_id':digest,'source_result_id':result_id,
        'source_file':source_file,'measurement_kind':points[0]['source_kind'],'candidates':points,
        'excluded_diagnostic_groups':excluded,'point_count':len(points),
        'review_point_count':sum(p['quality']!='fitted' or p['mixed'] for p in points),
        'warnings':['这是方位—时间高斯测量批次，不是已确认的目标或空间轨迹。',
            '高斯宽度不是角度置信区间；needs_review及混合峰保留待复核标记。',
            '批次传递不调用模型；后续预测仍需选种子并确认API费用。']}
