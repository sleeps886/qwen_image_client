"""Local visual outline comparison, not trajectory positioning accuracy."""
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageDraw, UnidentifiedImageError
from scipy.ndimage import label, find_objects
from scipy.optimize import linear_sum_assignment

SIZE=256
TOLERANCE=3


def trajectory_outlines(raw):
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
    # Group by hue rather than palette order: reference and AI colors may differ.
    hue=np.asarray(Image.fromarray(rgb.astype('uint8')).convert('HSV'))[:,:,0].astype(float)/255
    bins=np.floor(hue*24).astype(int)%24
    occupied={int(i) for i in np.unique(bins[mask])}
    groups=[]
    while occupied:
        group={occupied.pop()};pending=list(group)
        while pending:
            b=pending.pop()
            for other in ((b-1)%24,(b+1)%24):
                if other in occupied:occupied.remove(other);group.add(other);pending.append(other)
        groups.append(group)
    tracks=[]
    for group in groups:
        colored=mask & np.isin(bins,list(group))
        components,n=label(colored,np.ones((3,3)))
        clean=np.zeros_like(mask)
        for component,box in enumerate(find_objects(components),1):
            if box is None:continue
            h,w=box[0].stop-box[0].start,box[1].stop-box[1].start
            # Reject isolated markers and the short swatches in plot legends.
            if max(w/rgb.shape[1],h/rgb.shape[0])<.06:continue
            clean[box]|=components[box]==component
        if clean.sum()>=10:tracks.append(clean)
    if not tracks:raise ValueError('无法可靠分离彩色轨迹线，请使用原始轨迹图')
    combined=np.logical_or.reduce(tracks);ys,xs=np.nonzero(combined)
    y0,y1,x0,x1=ys.min(),ys.max()+1,xs.min(),xs.max()+1
    normalized=[]
    for track in tracks:
        crop=track[y0:y1,x0:x1]
        if min(crop.shape)<2:crop=np.pad(crop,((1,1),(1,1)))
        resized=Image.fromarray(crop.astype('uint8')*255).resize((SIZE-16,SIZE-16),Image.Resampling.LANCZOS)
        canvas=Image.new('L',(SIZE,SIZE),0);canvas.paste(resized,(8,8))
        normalized.append(np.asarray(canvas)>20)
    # Stable image-local numbering, top to bottom. These are NOT semantic IDs.
    return sorted(normalized,key=lambda m:(np.nonzero(m)[0].mean(),np.nonzero(m)[1].mean()))


def normalized_outline(raw):
    return np.logical_or.reduce(trajectory_outlines(raw))


def dilate(mask):
    return np.asarray(Image.fromarray(mask.astype('uint8')*255).filter(ImageFilter.MaxFilter(2*TOLERANCE+1)))>0


def outline_score(truth,predicted):
    precision=float((predicted & dilate(truth)).sum()/max(1,predicted.sum()))
    recall=float((truth & dilate(predicted)).sum()/max(1,truth.sum()))
    score=2*precision*recall/(precision+recall) if precision+recall else 0
    return score,precision,recall


def compare_images(reference, prediction, directory):
    references=trajectory_outlines(reference);predictions=trajectory_outlines(prediction)
    truth=np.logical_or.reduce(references);predicted=np.logical_or.reduce(predictions)
    # Two-way foreground coverage avoids high scores from shared black background.
    score,precision,recall=outline_score(truth,predicted)
    scores=np.array([[outline_score(r,p)[0] for p in predictions] for r in references])
    rows,cols=linear_sum_assignment(-scores)
    matches={int(r):int(c) for r,c in zip(rows,cols) if scores[r,c]>0}
    per_track=[]
    for r in range(len(references)):
        c=matches.get(r);value=float(scores[r,c])*100 if c is not None else 0.
        per_track.append({'reference_id':f'R{r+1}','prediction_id':f'A{c+1}' if c is not None else None,
                          'similarity_percent':round(value,2),'status':'missing' if c is None else 'low_similarity' if value<70 else 'ok'})
    for c in range(len(predictions)):
        if c not in matches.values():per_track.append({'reference_id':None,'prediction_id':f'A{c+1}',
                                                     'similarity_percent':0.,'status':'extra'})
    overall=sum(row['similarity_percent'] for row in per_track)/len(per_track)
    overlay=np.zeros((SIZE,SIZE,3),dtype=np.uint8)
    overlay[:]=[9,15,25]
    overlay[truth]=[0,220,235]
    overlay[predicted]=[255,80,180]
    overlay[truth & predicted]=[255,255,255]
    report={'metric':'one_to_one_track_macro_outline_f1','similarity_percent':round(overall,2),
        'global_outline_similarity_percent':round(score*100,2),'per_track':per_track,
        'reference_track_count':len(references),'prediction_track_count':len(predictions),'problem_threshold_percent':70,
        'aggregation':'逐条一对一匹配，所有匹配及漏检、多余条目等权平均；漏检、多余记0分。',
        'ai_outline_match_percent':round(precision*100,2),'reference_coverage_percent':round(recall*100,2),
        'normalized_size':[SIZE,SIZE],'tolerance_px':TOLERANCE,
        'meaning':'归一化彩色轮廓相似度；不是位置准确率或模型正确概率。',
        'warnings':['仅适用于同一样本的空间轨迹图；不自动确认样本一致性。',
                    '按彩色前景包围盒分别缩放对齐，忽略绝对经纬度、距离、平移和尺度；不能识别目标ID互换、运动方向或时间误差。',
                    '按颜色分线并抑制短图例和标记；同色多目标可能合并，碎片可能误分，短轨迹可能被过滤。',
                    'R/A编号仅为图内从上到下的线条编号，不是已确认目标ID。匹配忽略线条颜色，低于70分标红。',
                    '图例、标记、线宽、裁剪和额外彩色内容会影响分数；不是客观定位精度。'],
        'overlay_legend':{'cyan':'仿真参考轮廓','magenta':'AI轮廓','white':'像素直接重叠'}}
    annotated=Image.fromarray(overlay).resize((768,768),Image.Resampling.NEAREST)
    draw=ImageDraw.Draw(annotated)
    labels=[]
    for row in per_track:
        bad=row['status']!='ok'
        for key,masks,prefix in [('reference_id',references,'R'),('prediction_id',predictions,'A')]:
            if row[key] is None:continue
            m=masks[int(row[key][1:])-1];ys,xs=np.nonzero(m)
            if bad:
                red=Image.new('RGB',annotated.size,(255,65,65))
                alpha=Image.fromarray(m.astype('uint8')*255).resize(annotated.size,Image.Resampling.NEAREST)
                annotated.paste(red,(0,0),alpha)
            labels.append(((min(735,int(xs.mean()*3)),min(746,int(ys.mean()*3)+(-12 if prefix=='R' else 12))),row[key],bad))
    # Paint all masks first, otherwise a later overlapping line erases labels.
    for position,text,bad in labels:
        draw.text(position,text,fill=(255,100,100) if bad else (220,255,235),stroke_width=1,stroke_fill=(0,0,0))
    annotated.save(Path(directory)/'comparison_overlay.png')
    report['overlay_legend']['red']='低分、漏检或多余轨迹；R参考线，A估计线'
    return report
