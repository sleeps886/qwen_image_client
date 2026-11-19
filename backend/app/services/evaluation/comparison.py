"""Local visual outline comparison, not trajectory positioning accuracy."""
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, UnidentifiedImageError

SIZE=256
TOLERANCE=3


def normalized_outline(raw):
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width*source.height>20_000_000 or min(source.size)<16:
                raise ValueError('图片尺寸过大或过小，最多2000万像素')
            source.thumbnail((1600,1600))
            image=source.convert('RGBA')
            background=Image.new('RGBA',image.size,(0,0,0,255))
            rgb=np.asarray(Image.alpha_composite(background,image).convert('RGB'),dtype=np.int16)
    except (OSError,UnidentifiedImageError,Image.DecompressionBombError) as exc:
        raise ValueError('参考图不是可解码图片，或像素数超限') from None
    # Saturated, luminous foreground suppresses dark background and gray axes.
    mask=(rgb.max(axis=2)>110)&((rgb.max(axis=2)-rgb.min(axis=2))>75)
    count=int(mask.sum())
    if count<20 or count/mask.size>.4:
        raise ValueError('无法可靠提取彩色轨迹：请上传黑底彩色线条轨迹图，不要上传声呐热力图或整屏截图')
    ys,xs=np.nonzero(mask)
    crop=mask[ys.min():ys.max()+1,xs.min():xs.max()+1]
    if min(crop.shape)<2:
        # A single horizontal/vertical line still has a valid visual outline.
        crop=np.pad(crop,((1,1),(1,1)))
    normalized=Image.fromarray(crop.astype('uint8')*255).resize((SIZE-16,SIZE-16),Image.Resampling.LANCZOS)
    canvas=Image.new('L',(SIZE,SIZE),0);canvas.paste(normalized,(8,8))
    return np.asarray(canvas)>20


def compare_images(reference, prediction, directory):
    truth=normalized_outline(reference)
    predicted=normalized_outline(prediction)
    def dilate(mask):
        image=Image.fromarray(mask.astype('uint8')*255)
        return np.asarray(image.filter(ImageFilter.MaxFilter(2*TOLERANCE+1)))>0
    # Two-way foreground coverage avoids high scores from shared black background.
    precision=float((predicted & dilate(truth)).sum()/predicted.sum())
    recall=float((truth & dilate(predicted)).sum()/truth.sum())
    score=2*precision*recall/(precision+recall) if precision+recall else 0
    overlay=np.zeros((SIZE,SIZE,3),dtype=np.uint8)
    overlay[:]=[9,15,25]
    overlay[truth]=[0,220,235]
    overlay[predicted]=[255,80,180]
    overlay[truth & predicted]=[255,255,255]
    report={'metric':'normalized_color_outline_f1','similarity_percent':round(score*100,2),
        'ai_outline_match_percent':round(precision*100,2),'reference_coverage_percent':round(recall*100,2),
        'normalized_size':[SIZE,SIZE],'tolerance_px':TOLERANCE,
        'meaning':'归一化彩色轮廓相似度；不是位置准确率或模型正确概率。',
        'warnings':['仅适用于同一样本的空间轨迹图；不自动确认样本一致性。',
                    '按彩色前景包围盒分别缩放对齐，忽略绝对经纬度、距离、平移和尺度；不能识别目标ID互换、运动方向或时间误差。',
                    '图例、标记、线宽、裁剪和额外彩色内容会影响分数；不是客观定位精度。'],
        'overlay_legend':{'cyan':'仿真参考轮廓','magenta':'AI轮廓','white':'像素直接重叠'}}
    Image.fromarray(overlay).resize((768,768),Image.Resampling.NEAREST).save(Path(directory)/'comparison_overlay.png')
    return report
