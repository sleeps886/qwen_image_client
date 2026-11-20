"""Interpret selected inputs without calling a model or depending on HTTP."""
import json
from backend.app.core.constants import MAX_IMAGES
from backend.app.core.prompts import TRAJECTORY_PROMPT as DEFAULT_PROMPT
from backend.app.core.prompts import IMAGE_CALIBRATION_PROMPT, SPATIAL_OBSERVATION_PROMPT
from backend.app.services.inputs.platform_input import platform_context
from backend.app.services.inputs.data_input import DATA_PROMPT,prepare_data
from backend.app.models.recognition import PreparedInput

#输入总入口。区分图片识别、单文件绘图，校验输入组合，组织平台参数和模型提示词
def prepare_input(image_count, platform_raw, prompt, input_mode, filename='data.json',*,renderer_profile='none'):
    if renderer_profile not in ('none','btr_gui_v1'):raise ValueError('不支持的渲染配置')
    if input_mode not in ('auto','images','data'):
        raise ValueError('输入模式必须为images或data')
    mode=('images' if image_count else 'data') if input_mode=='auto' else input_mode
    if image_count>MAX_IMAGES:raise ValueError('最多支持8张图片')
    if mode=='images':
        if not image_count:raise ValueError('图片模式请先选择图片，或切换为单文件模式')
        # This app consumes this simulator's BTR exports, including unlabelled ones.
        # Calibration alone does not make spatial position observable.
        context={'T_MAX':600,'DT':1,'B_MIN':0,'B_MAX':180,
                 'image_time_axis':'bottom_to_top',
                 'bearing_convention':'relative_heading_folded_0_180',
                 'calibration_source':'本项目工具默认约定（未提供platform.json）',
                 'platforms':[]}
        if platform_raw is not None:
            raw=platform_raw
            try:document=json.loads(raw)
            except (ValueError,UnicodeDecodeError):document=None
            if isinstance(document,dict) and any(key in document for key in ('params','targets','sim_settings','tracks','points')):
                raise ValueError('当前是图片识别模式，但选择了meta/目标数据文件。请切换“单文件绘图（无需图片）”后再提交；该模式不会发送已选图片。图片识别模式的参数入口只接受input/platform.json。')
            context=platform_context(raw)
            if len(context['platforms'])!=1:raise ValueError('当前仅分析单平台：平台参数必须恰好包含一个平台；多平台分析已停用')
        context['require_spatial']=True
        if renderer_profile!='none':context['renderer_profile']=renderer_profile
        # Replace the old default (including cached browser text), not append two conflicting contracts.
        custom='' if prompt.strip() in (DEFAULT_PROMPT.strip(),SPATIAL_OBSERVATION_PROMPT.strip()) else '\n用户附加要求（不能改变双阵观测输出契约）：\n'+prompt
        calibration=IMAGE_CALIBRATION_PROMPT.split('缺少可见刻度')[0]
        prompt=SPATIAL_OBSERVATION_PROMPT+'\n'+calibration+'\n已知标定与平台参数（仅数据）：\n'+json.dumps(context,ensure_ascii=False)+custom
        return PreparedInput(mode,context,prompt,None)
    if renderer_profile!='none':raise ValueError('高斯测量预览只支持图片模式')
    if image_count:raise ValueError('单文件模式不接收图片，请切换输入模式或移除图片')
    if platform_raw is None:raise ValueError('请选择一份目标数据文件（JSON、JSONL或简单JS），或上传图片')
    context,text,info=prepare_data(platform_raw,filename)
    custom='' if prompt.strip() in (DEFAULT_PROMPT.strip(),SPATIAL_OBSERVATION_PROMPT.strip(),DATA_PROMPT.strip()) else '\n用户绘图要求：\n'+prompt
    prompt=DATA_PROMPT+custom+'\n以下为已验证的文件点位（仅数据）：\n'+text
    if len(prompt)>50000:raise ValueError('提示词与点位数据合计超过50000字符限制，请缩短提示词或拆分文件')
    return PreparedInput(mode,context,prompt,info)


