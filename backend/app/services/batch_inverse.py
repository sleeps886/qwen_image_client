"""Bounded batch folded-bearing inversion and globally exclusive association.

No reference tracks, target coordinates or expected target count are loaded.
All positions are explicitly model estimates; mirrors remain alternatives.
"""
import math

import numpy as np
from scipy.optimize import Bounds,LinearConstraint,least_squares,milp

from backend.app.services.forward_observation import GeoReference,array_location
from backend.app.services.spatial_tracks import intersection


def reference_for(context):
    return GeoReference(context.get('REF_LAT',30.),context.get('REF_LON',122.),context.get('EARTH_R',6371000.))


def evaluate_path(parameters,times,model,t0,span):
    times=np.asarray(times,dtype=float);s=(times-t0)/span
    if model=='cv':basis=np.array([np.ones(len(s)),s]).T
    elif model=='quadratic':basis=np.array([np.ones(len(s)),s,s*s]).T
    elif model=='waypoint':
        knot=float(parameters[-1]);basis=np.array([np.ones(len(s)),s,np.maximum(0,s-knot)]).T
        parameters=parameters[:-1]
    elif model=='harmonic':
        period=float(parameters[-1]);basis=np.array([np.ones(len(s)),s,np.sin(2*np.pi*s/period),np.cos(2*np.pi*s/period)]).T
        parameters=parameters[:-1]
    else:raise ValueError('不支持的运动模型')
    return basis@np.asarray(parameters).reshape(-1,2)*1000.


def predicted_bearings(xy,centers,heading,reference):
    """Vectorized spherical bearings using the generator's projection reference."""
    lat=reference.latitude+np.degrees(xy[:,1]/reference.radius_m)
    lon=reference.longitude+np.degrees(xy[:,0]/(reference.radius_m*np.cos(np.radians(reference.latitude))))
    p1=np.radians(centers[:,0]);p2=np.radians(lat);dl=np.radians(lon-centers[:,1])
    east=np.sin(dl)*np.cos(p2)
    north=np.cos(p1)*np.sin(p2)-np.sin(p1)*np.cos(p2)*np.cos(dl)
    relative=np.degrees(np.arctan2(east,north))-heading
    return np.degrees(np.arccos(np.clip(np.cos(np.radians(relative)),-1,1)))


def _arrays(pair,context,reference):
    platform=context['platforms'][0];records=[]
    for array in ('hull','tow'):
        points=pair[array]['points'];width=float(np.median([p['sigma_deg'] for p in points]))
        trusted=[p for p in points if not p.get('mixed') and .4*width<=p['sigma_deg']<=1.6*width]
        records.extend((array,p) for p in (trusted if len(trusted)>=5 else points))
    records.sort(key=lambda item:(item[1]['t'],item[0]))
    times=np.array([p['t'] for _,p in records]);measured=np.array([p['bearing_deg'] for _,p in records])
    centers=np.array([array_location(platform,platform[array+'_m'],float(p['t']),reference) for array,p in records])
    sigma=np.array([max(.08,min(.8,p.get('error_deg',.3))) for _,p in records])
    return records,times,measured,centers,sigma


def _seeds(pair,context,reference,t0,span):
    platform=context['platforms'][0];h={p['sample_index']:p for p in pair['hull']['points']};t={p['sample_index']:p for p in pair['tow']['points']}
    seeds=[]
    for mirror in (1,-1):
        samples=[]
        for row in pair['common_rows']:
            hp,tp=h[row],t[row];time=hp['t']
            hl=reference.to_xy(array_location(platform,platform['hull_m'],time,reference))
            tl=reference.to_xy(array_location(platform,platform['tow_m'],time,reference))
            solution=intersection(hl,tl,platform['heading_deg']+mirror*hp['unfolded_deg'],
                                  platform['heading_deg']+mirror*tp['unfolded_deg'],.005)
            if solution and solution[2]<80000:samples.append((time,*solution[:2]))
        if len(samples)>=3:
            data=np.array(samples);s=(data[:,0]-t0)/span
            # Robustly trim only geometric seed outliers, not observations.
            distance=np.linalg.norm(data[:,1:]-np.median(data[:,1:],axis=0),axis=1)
            keep=distance<=max(1000,float(np.percentile(distance,80))*2)
            design=np.array([np.ones(keep.sum()),s[keep]]).T
            parameters=np.linalg.lstsq(design,data[keep,1:]/1000,rcond=None)[0].ravel()
            seeds.append(parameters)
    return seeds


def fit_pair(pair,context,control,initial_models=None):
    reference=reference_for(context);platform=context['platforms'][0]
    records,times,measured,centers,sigma=_arrays(pair,context,reference)
    t0=float(np.mean(times));span=max(30.,float(times.max()-times.min()))
    if initial_models:t0=initial_models[0]['t0'];span=initial_models[0]['span']
    seeds=_seeds(pair,context,reference,t0,span)
    if not seeds:return None
    train=np.array([p['sample_index']%5!=0 for _,p in records]);validation=~train
    if train.sum()<10 or validation.sum()<4:train[:]=True;validation[:]=True
    variants=[];origin=reference.to_xy(platform['start_ll'])
    limits=np.array([origin[0]/1000,origin[1]/1000])
    for seed in seeds[:2]:
        control.check();candidates=[]
        def fit_model(model,initial,active=None,family=None):
            control.check();count=len(initial);lo=np.full(count,-100.);hi=np.full(count,100.)
            lo[:2]=limits-100;hi[:2]=limits+100
            if model=='harmonic':lo[-1]=.25;hi[-1]=4.
            if model=='waypoint':lo[-1]=-.45;hi[-1]=.45
            initial=np.clip(initial,lo+1e-5,hi-1e-5)
            active=np.arange(count) if active is None else np.array(active)
            template=np.array(initial)
            def expand(values):
                parameters=template.copy();parameters[active]=values
                return parameters
            def residual(parameters,mask=train):
                control.check()
                parameters=expand(parameters)
                xy=evaluate_path(parameters,times,model,t0,span)
                prediction=predicted_bearings(xy,centers,platform['heading_deg'],reference)
                angular=(prediction-measured)/sigma
                # Physical priors are evaluated in seconds on a fixed time grid,
                # not amplified just because more measurements were extracted.
                grid=np.linspace(float(times.min()),float(times.max()),25)
                trajectory=evaluate_path(parameters,grid,model,t0,span)
                dt=np.diff(grid);velocity=np.diff(trajectory,axis=0)/dt[:,None]
                speed=np.maximum(0,np.linalg.norm(velocity,axis=1)-50)/5
                acceleration=np.diff(velocity,axis=0)/((dt[:-1]+dt[1:])/2)[:,None]
                smooth=acceleration.ravel()/.4 if model not in ('cv','waypoint') else np.zeros(0)
                return np.r_[angular[mask],speed,smooth]
            fit=least_squares(residual,initial[active],bounds=(lo[active],hi[active]),loss='soft_l1',f_scale=1.,max_nfev=90,x_scale='jac')
            if not fit.success:return None
            parameters=expand(fit.x);xy=evaluate_path(parameters,times,model,t0,span)
            error=predicted_bearings(xy,centers,platform['heading_deg'],reference)-measured
            rmse=float(np.sqrt(np.mean(error**2)));vrmse=float(np.sqrt(np.mean(error[validation]**2)))
            normalized=error[validation]/sigma[validation]
            score=len(normalized)*math.log(float(np.mean(np.minimum(normalized**2,100)))+.01)+len(active)*math.log(max(2,len(normalized)))
            distances=np.linalg.norm(xy-np.array([reference.to_xy(tuple(map(float,p))) for p in centers]),axis=1)
            if rmse>1.5 or distances.max()>100000 or distances.min()<1:return None
            # Data-only Jacobian; prior rows are not evidence of observability.
            jac=fit.jac[:int(train.sum())];singular=np.linalg.svd(jac,compute_uv=False)
            condition=float(singular[0]/max(1e-12,singular[-1])) if len(singular) else 1e15
            return {'model':model,'parameters':parameters.tolist(),'effective_parameters':len(active),'parameter_family':family or model,'t0':t0,'span':span,'rmse_deg':rmse,
                    'validation_rmse_deg':vrmse,'score':score,'condition_proxy':condition,
                    'position_information_weak':bool(condition>1e6),'optimizer_success':True}
        cv=fit_model('cv',seed)
        if cv:candidates.append(cv)
        if initial_models:
            for previous in initial_models[:1]:
                refined=fit_model(previous['model'],np.array(previous['parameters']))
                if refined:candidates.append(refined)
        if cv is None or cv['validation_rmse_deg']>.25:
            quadratic=fit_model('quadratic',np.r_[seed,0.,0.])
            if quadratic:candidates.append(quadratic)
            waypoint=fit_model('waypoint',np.r_[seed,0.,0.,0.])
            if waypoint:candidates.append(waypoint)
            if len(records)>=30:
                for period in (.8,1.5):
                    harmonic=fit_model('harmonic',np.r_[seed,0.,0.,0.,0.,period])
                    if harmonic:candidates.append(harmonic)
                    # The generator accepts independent lat/lon formulae. Compare
                    # the simpler one-axis families rather than always spending
                    # four oscillation coefficients on small parallax noise.
                    for active,family in (([0,1,2,3,5,7,8],'latitude_harmonic'),([0,1,2,3,4,6,8],'longitude_harmonic')):
                        harmonic=fit_model('harmonic',np.r_[seed,0.,0.,0.,0.,period],active,family)
                        if harmonic:candidates.append(harmonic)
        if candidates:variants.append(min(candidates,key=lambda c:c['score']))
    if not variants:return None
    variants.sort(key=lambda c:c['score']);best=variants[0]
    variants=[v for v in variants if v['validation_rmse_deg']<=best['validation_rmse_deg']+.15]
    # Multiple initializations may converge to the same path, not two mirrors.
    distinct=[];grid=np.linspace(float(times.min()),float(times.max()),25)
    for variant in variants:
        trajectory=evaluate_path(variant['parameters'],grid,variant['model'],variant['t0'],variant['span'])
        if all(np.sqrt(np.mean(np.sum((trajectory-evaluate_path(v['parameters'],grid,v['model'],v['t0'],v['span']))**2,axis=1)))>=10 for v in distinct):
            distinct.append(variant)
    variants=distinct
    if len(variants)==1 and abs(platform['hull_m'][1])+abs(platform['tow_m'][1])<1e-8:
        # Recover the competing reflection even if one initial ray seed failed.
        # It is retained only after the same data fit, not merely copied as truth.
        vector=np.array([math.sin(math.radians(platform['heading_deg'])),math.cos(math.radians(platform['heading_deg']))])
        reflection=2*np.outer(vector,vector)-np.eye(2)
        parameters=np.array(best['parameters']);tail=parameters[-1:] if best['model'] in ('harmonic','waypoint') else np.zeros(0)
        coefficients=parameters[:-1].reshape(-1,2) if len(tail) else parameters.reshape(-1,2)
        reflected=coefficients@reflection.T
        reflected[0]=(np.array(origin)/1000)+reflection@(coefficients[0]-np.array(origin)/1000)
        mirror=fit_model(best['model'],np.r_[reflected.ravel(),tail])
        if mirror and mirror['validation_rmse_deg']<=best['validation_rmse_deg']+.15:
            separation=np.linalg.norm(evaluate_path(mirror['parameters'],grid,mirror['model'],mirror['t0'],mirror['span'])-
                                      evaluate_path(best['parameters'],grid,best['model'],best['t0'],best['span']),axis=1)
            if float(np.sqrt(np.mean(separation**2)))>=10:variants.append(mirror)
    return {'id':pair['id'],'platform_id':platform['name'],'source_ridges':[pair['hull']['id'],pair['tow']['id']],
            'variants':variants,'start_t':float(times.min()),'end_t':float(times.max()),
            'source_measurement_ids':[p['id'] for _,p in records],
            'synchronous_times':len(pair['common_rows']),'validation_is_truth':False}


def fit_proposals(proposals,evidence,context,control):
    fitted=[]
    for pair in proposals['pairs']:
        control.check();result=fit_pair(pair,context,control)
        if result:fitted.append(result)
    return {'candidates':fitted,'attempted':len(proposals['pairs']),'rejected':len(proposals['pairs'])-len(fitted),
            'budget_excluded':proposals['budget_excluded'],'method':'batch_folded_spherical_bearings_v1'}


def refine_associations(fitted,evidence,context,control,iterations=1):
    from backend.app.services.ridge_hypotheses import nearest_unfolded
    lookup={p['id']:(im,p) for im in evidence['images'] for p in im['candidates']}
    current=fitted;reports=[]
    for iteration in range(iterations):
        selection=select_exclusive(current,evidence,context,control);updates={}
        for c in selection['selected']:
            control.check();groups={'hull':[],'tow':[]}
            for cid in c['supported_measurement_ids']:
                im,p=lookup[cid];groups[im['array']].append(dict(p))
            for array,points in groups.items():
                points.sort(key=lambda p:p['t'])
                for i,p in enumerate(points):
                    prediction=points[i-1]['unfolded_deg'] if i else p['bearing_deg']
                    p['unfolded_deg']=nearest_unfolded(p['bearing_deg'],prediction)
            common=sorted({p['sample_index'] for p in groups['hull']}&{p['sample_index'] for p in groups['tow']})
            if len(common)<6:continue
            pair={'id':c['id'],'common_rows':common,
                  **{a:{'id':c['source_ridges'][i],'points':groups[a]} for i,a in enumerate(('hull','tow'))}}
            refined=fit_pair(pair,context,control,initial_models=c['variants'])
            if refined:updates[c['id']]=refined
        current={**current,'candidates':[updates.get(c['id'],c) for c in current['candidates']]}
        reports.append({'iteration':iteration+1,'refitted_hypotheses':sorted(updates),'candidate_count':len(current['candidates'])})
    return current,reports


def support_for(candidate,evidence,context):
    reference=reference_for(context);platform=context['platforms'][0];v=candidate['variants'][0];support=[];residuals=[]
    for image in evidence['images']:
        points=image['candidates']
        rows={}
        for p in points:rows.setdefault(p['sample_index'],[]).append(p)
        for row,values in rows.items():
            time=values[0]['t'];xy=evaluate_path(v['parameters'],[time],v['model'],v['t0'],v['span'])
            center=np.array([array_location(platform,platform[image['array']+'_m'],time,reference)])
            prediction=float(predicted_bearings(xy,center,platform['heading_deg'],reference)[0])
            ranked=sorted(values,key=lambda p:abs(p['bearing_deg']-prediction))
            error=abs(ranked[0]['bearing_deg']-prediction)
            if error>max(.5,3*ranked[0]['error_deg']) or ranked[0].get('mixed'):continue
            if len(ranked)>1 and abs(ranked[1]['bearing_deg']-prediction)-error<.15:continue
            support.append(ranked[0]['id']);residuals.append(error)
    return support,float(np.mean(residuals)) if residuals else 999.


def select_exclusive(fitted,evidence,context,control):
    candidates=[]
    for candidate in fitted['candidates']:
        control.check();ids,error=support_for(candidate,evidence,context)
        if len(ids)<12:continue
        candidates.append({**candidate,'supported_measurement_ids':ids,'mean_supported_error_deg':error,
                           'gain':len(ids)-8-4*error-len(candidate['variants'][0]['parameters'])*.15})
    if not candidates:return {'selected':[],'candidates':[],'uncovered_ids':[p['id'] for im in evidence['images'] for p in im['candidates']],
                             'status':'needs_review','target_count_confirmed':False}
    # A few crossing measurements must not make two whole tracks mutually
    # exclusive. Substantially overlapping explanations compete; small shared
    # crossing supports are symmetrically excluded after selection.
    rows=[]
    for i,a in enumerate(candidates):
        for j in range(i+1,len(candidates)):
            b=candidates[j];shared=set(a['supported_measurement_ids'])&set(b['supported_measurement_ids'])
            if len(shared)/min(len(a['supported_measurement_ids']),len(b['supported_measurement_ids']))>.3:
                row=np.zeros(len(candidates));row[i]=row[j]=1;rows.append(row)
    control.check()
    optimization=milp(-np.array([c['gain'] for c in candidates]),integrality=np.ones(len(candidates)),
        bounds=Bounds(0,1),constraints=LinearConstraint(np.array(rows) if rows else np.zeros((1,len(candidates))),0,1),
        options={'time_limit':5.,'node_limit':1000})
    if optimization.x is None:raise ValueError('全局关联未得到可行解，不以局部重复计数代替')
    selected=[c for c,x in zip(candidates,optimization.x) if x>.5]
    owners={}
    for c in selected:
        for i in c['supported_measurement_ids']:owners.setdefault(i,[]).append(c['id'])
    shared={i for i,v in owners.items() if len(v)>1}
    selected=[{**c,'all_supported_measurement_ids':c['supported_measurement_ids'],
               'excluded_shared_ids':sorted(set(c['supported_measurement_ids'])&shared),
               'supported_measurement_ids':[i for i in c['supported_measurement_ids'] if i not in shared]} for c in selected]
    selected=[c for c in selected if len(c['supported_measurement_ids'])>=12]
    used={i for c in selected for i in c['supported_measurement_ids']}
    return {'selected':selected,'candidates':candidates,'uncovered_ids':[p['id'] for im in evidence['images'] for p in im['candidates'] if p['id'] not in used],
            'status':'candidate_hypotheses','target_count_confirmed':False,'solver_optimal':bool(optimization.success),
            'shared_crossing_ids_excluded':sorted(shared),
            'method':'exclusive_measurement_set_packing','objective':sum(c['gain'] for c in selected)}


def build_document(selection,evidence,context,control):
    reference=reference_for(context);platform=context['platforms'][0]
    lookup={p['id']:p for im in evidence['images'] for p in im['candidates']};output=[];quality=[]
    for index,c in enumerate(selection['selected']):
        identity=f'N{index+1}';times=sorted({lookup[i]['t'] for i in c['supported_measurement_ids']})
        for alternative,v in enumerate(c['variants']):
            control.check();xy=evaluate_path(v['parameters'],times,v['model'],v['t0'],v['span'])
            points=[]
            for time,position in zip(times,xy):
                lat,lon=reference.to_ll(tuple(map(float,position)))
                points.append({'t':time,'x':lon,'y':lat,'point_source':'model_estimate_at_observed_time'})
            segments=[];part=[];step=context['T_MAX']*.96/64
            for p in points:
                if part and p['t']-part[-1]['t']>step*3:segments.append(part);part=[]
                part.append(p)
            if part:segments.append(part)
            for segment_index,segment in enumerate(segments):
                if len(segment)>=2:output.append({'id':identity,'branch':f'{v["model"]}:alternative{alternative}:segment{segment_index}',
                    'platform_id':platform['name'],'points':segment,'candidate':True,'mirror_variant':alternative,
                    'association_hypothesis_id':c['id'],'motion_model':v['model'],'model_parameters':v})
        quality.append({'id':identity,'hypothesis_id':c['id'],'variants':len(c['variants']),
                        'supported_measurements':len(c['supported_measurement_ids']),
                        'mean_angle_error_deg':c['mean_supported_error_deg'],
                        'validation_rmse_deg':c['variants'][0]['validation_rmse_deg'],
                        'position_information_weak':c['variants'][0]['position_information_weak']})
    if not output:raise ValueError('正向反演没有足够的可绘制候选；未补造轨迹')
    return {'coordinate_system':'lon_lat','position_observable':True,'plot_kind':'spatial_candidates','tracks':output,
            'result_source':'numeric_forward_inverse','localization':{'method':'batch_folded_spherical_bearings_v1','unique_solution':False},
            'inverse_quality':quality,'warnings':[
                '程序以整段折叠方位拟合空间运动，位置为模型估计，不是独立测量或真值。',
                '镜像及等价运动解释可能同时存在，候选身份数不是已确认目标数。',
                '只在有观测支持的时刻与范围绘制；未恢复不可见全时段，也未自动选择唯一镜像。',
                '轨迹关联、运动模型、门限与误差代理仍需独立样本验证；回投通过不证明真实位置正确。',
                f'仍有{len(selection["uncovered_ids"])}个测量未被选中解释，可能包括弱目标、杂波和交叉歧义。']}
