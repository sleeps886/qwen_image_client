"""Forward BTR consistency and explicit residual/uncovered evidence reporting."""
from pathlib import Path

import numpy as np
from PIL import Image,ImageDraw

from backend.app.services.localization.batch_inverse import evaluate_path,predicted_bearings,reference_for
from backend.app.services.localization.forward_observation import array_location
from backend.app.services.measurements.btr_measurements import PALETTE


def check_and_preview(selection,evidence,context,paths,directory,control):
    reference=reference_for(context);platform=context['platforms'][0];lookup={p['id']:(im,p) for im in evidence['images'] for p in im['candidates']}
    reports=[];rows=[];colors=['#ffa600','#f000d0','#00d9e8','#a2ff00','#6aa5ff','#ff6d7c']
    for image,path in zip(evidence['images'],paths):
        control.check();angles=image['_angles'];expected=[];tracks=[]
        with Image.open(path) as original:canvas=original.convert('RGB')
        draw=ImageDraw.Draw(canvas);left,top,right,bottom=image['roi']
        for idx,c in enumerate(selection['selected']):
            v=c['variants'][0];ids=[i for i in c['supported_measurement_ids'] if lookup[i][0]['array']==image['array']]
            measurements=[lookup[i][1] for i in ids]
            if not measurements:continue
            width=c.get('profile_widths_deg',{}).get(image['array'],float(np.median([p['sigma_deg'] for p in measurements])))
            tracks.append((idx,c,v,width,float(np.median([p['amplitude'] for p in measurements]))))
        last={}
        for profile in image['profiles']:
            time=profile['t'];signal=np.full(len(angles),max(.01,profile['background_relative']))
            for idx,c,v,sigma,amplitude in tracks:
                if not c['start_t']<=time<=c['end_t']:continue
                xy=evaluate_path(v['parameters'],[time],v['model'],v['t0'],v['span'])
                center=np.array([array_location(platform,platform[image['array']+'_m'],time,reference)])
                bearing=float(predicted_bearings(xy,center,platform['heading_deg'],reference)[0])
                signal+=amplitude*np.exp(-.5*((angles-bearing)/max(.1,sigma))**2)
                x=left+(bearing-context.get('B_MIN',0))/(context.get('B_MAX',180)-context.get('B_MIN',0))*(right-left)
                y=bottom-time/context['T_MAX']*(bottom-top) if context.get('image_time_axis','bottom_to_top')=='bottom_to_top' else top+time/context['T_MAX']*(bottom-top)
                if idx in last:draw.line((*last[idx],x,y),fill=colors[idx%len(colors)],width=2)
                last[idx]=(x,y)
                measured=[p for p in image['candidates'] if p['sample_index']==profile['sample_index']]
                nearest=min(measured,key=lambda p:abs(p['bearing_deg']-bearing)) if measured else None
                error=abs(nearest['bearing_deg']-bearing) if nearest else None
                rows.append({'hypothesis_id':c['id'],'array':image['array'],'t':time,'predicted_bearing_deg':bearing,
                             'nearest_measurement_id':nearest['id'] if nearest else None,'error_deg':error,
                             'supported':bool(nearest and nearest['id'] in c['supported_measurement_ids'])})
            expected.append(signal)
        expected=np.array(expected)
        observed=image['_profile_values']
        # Work in the rendered dB space to avoid letting one bright peak dominate.
        delta=np.clip(10*np.log10(np.maximum(expected,.0001)),-5,16)-np.clip(10*np.log10(np.maximum(observed,.0001)),-5,16)
        reports.append({'image_index':image['image_index'],'array':image['array'],
                        'expected_btr_db_rmse':float(np.sqrt(np.mean(delta**2))),
                        'random_noise_reproduced':False,'comparison_is_independent_truth':False})
        pixels=PALETTE[np.clip(((np.clip(10*np.log10(np.maximum(expected,.0001)),-5,16)+5)/21*255).round().astype(int),0,255)]
        if context.get('image_time_axis','bottom_to_top')=='bottom_to_top':pixels=pixels[::-1]
        raster=Image.fromarray(pixels).resize((right-left+1,bottom-top+1),Image.Resampling.BILINEAR)
        prediction=Image.new('RGB',canvas.size,'black');prediction.paste(raster,(left,top))
        prediction.save(Path(directory)/f"expected_btr_{image['image_index']}.png")
        canvas.save(Path(directory)/f"inverse_overlay_{image['image_index']}.png")
    used={i for c in selection['selected'] for i in c['supported_measurement_ids']}
    return {'status':'needs_review','images':reports,'rows':rows,'supplied_measurements':len(lookup),
            'retained_unique_measurements':len(used),'measurement_coverage':len(used)/max(1,len(lookup)),
            'uncovered_measurement_ids':selection['uncovered_ids'],
            'excluded_shared_crossing_ids':selection.get('shared_crossing_ids_excluded',[]),
            'independent_position_accuracy_verified':False,'statistical_confidence':False}
