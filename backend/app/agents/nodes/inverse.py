"""Local inverse node actions; numeric algorithms remain in services."""
import copy
import json
from backend.app.agents.hypothesis_review import review_prompt,validate_review
from backend.app.agents.progress import INVERSE_LABELS
from backend.app.agents.serialization import public
from backend.app.services.association.ridge_hypotheses import propose_pairs,ridge_hypotheses
from backend.app.services.jobs.inversion_control import InversionStopped
from backend.app.services.knowledge.rules import retrieve_rules
from backend.app.services.localization.batch_inverse import build_document,fit_proposals,refine_associations,select_exclusive
from backend.app.services.localization.profile_inverse import refine_profiles
from backend.app.services.measurements.btr_field import extract_fields
from backend.app.services.measurements.sonar_evidence import save_evidence
from backend.app.services.quality.inverse_quality import check_and_preview
from backend.app.services.rendering.trajectory import render_document


class InverseNodes:
    """Bound node actions; execution control and persistence belong to the Agent."""
    def __init__(self,agent):
        self.agent=agent

    def model_final(self,state):
        from backend.app.agents.model_trajectory import evidence_bundle,validate_final,PROMPT_VERSION
        directory=self.agent.directory;context=state['context']
        # Numeric products remain explicitly separate; never pass them off as model output.
        self.agent.save('backend_tracks.json',state['document'])
        (directory/'estimated_track.png').replace(directory/'backend_candidate.png')
        (directory/'tracks.json').unlink(missing_ok=True)
        self.agent.final_report={'status':'preparing','automatic_retry':False}
        stage='evidence_preparation'
        try:
            images,prompt,manifest=evidence_bundle(state['paths'],state['evidence'],state['document'],context,directory,state.get('selection'))
            self.agent.save('model_final_input.json',{'images':manifest,'prompt':prompt,'prompt_version':PROMPT_VERSION})
            # Local optimization keeps its original 120s budget. Cloud stage gets a separate allowance.
            self.agent.control.seconds+=90
            self.agent.control.check()
            self.agent.new_model_calls+=1
            stage='model_gateway'
            response=self.agent.finalizer(images,prompt,directory)
            answer=response['answer']
            (directory/'model_final_answer.txt').write_text(answer,encoding='utf-8')
            self.agent.final_report['usage']=response.get('usage')
            self.agent.control.check()
            stage='coordinate_validation'
            document=validate_final(answer,context,state['document'])
            from backend.app.services.quality.model_final_quality import audit_geometry
            stage='model_reprojection'
            quality=audit_geometry(document,state['document'],state['evidence'],context,state.get('selection'))
            self.agent.save('model_final_quality.json',quality)
            document['model_geometry_audit']=quality
            if quality['warning_track_count']:document['warnings'].append('模型修订坐标回投检查有'+str(quality['warning_track_count'])+'条分支不匹配或观测不足；不能当作可靠位置。')
            stage='rendering'
            from backend.app.services.rendering.model_views import render_model_views
            render_model_views(document,directory,context,state['document'])
            self.agent.final_report.update(status='generated',coordinate_source='model_output',
                                          physical_validation='observation_consistency_only',prompt_version=PROMPT_VERSION,identity_audit=document['model_identity_audit'],
                                          geometry_audit=quality)
            self.agent.save('model_final_report.json',self.agent.final_report)
            return {'document':document,'_summary':'已由大模型综合三类图输出最终轨迹点；已做逐身份覆盖审计和模型坐标回投；非真值验证'}
        except InversionStopped:raise
        except Exception as exc:
            message=str(exc)[:250] if stage=='coordinate_validation' and isinstance(exc,ValueError) else '最终轨迹阶段失败：'+stage+'（'+type(exc).__name__+'）'
            self.agent.final_report.update(status='failed',error_type=type(exc).__name__,numeric_fallback=False,
                                           failed_stage=stage,error_message=message)
            self.agent.save('model_final_report.json',self.agent.final_report)
            # Do not show backend coordinates as a successful model final trajectory.
            (directory/'estimated_track.png').unlink(missing_ok=True)
            (directory/'tracks.json').unlink(missing_ok=True)
            document={'coordinate_system':'local_xy_m','tracks':[],
                      'warnings':['模型最终轨迹未生成或校验未通过：'+message+'；后端候选单独保留，未替代模型结果，未自动重试。']}
            return {'document':document,'_summary':'模型最终轨迹生成失败；只保留独立后端候选，不回退冒充模型结果'}

    def validate(self,state):
        context=self.agent.state['context'];paths=self.agent.state['paths']
        if context.get('renderer_profile')!='btr_gui_v1':raise ValueError('完整正向反演仅支持显式声明的工具原始图配置')
        if len(paths)!=2 or len(context.get('platforms',[]))!=1:raise ValueError('需要同平台hull/tow各一张及平台参数')
        self.agent.save('calibration_context.json',context)
        rules=list({r['id']:r for stage in ('observation','association','diagnosis') for r in retrieve_rules(stage)}.values())
        self.agent.save('knowledge_sources.json',rules)
        return {'rules':rules,'_summary':f'已校验工具配色与双阵约定，检索{len(rules)}条内置规则；未读取目标标签或案例答案'}


    def field(self,state):
        context=self.agent.state['context'];paths=self.agent.state['paths']
        evidence=extract_fields(paths,context,self.agent.control)
        self.agent.save('inverse_fields.json',evidence)
        save_evidence(public(evidence),paths,self.agent.directory)
        return {'evidence':evidence,'_summary':f"从实际图像提取{sum(len(i['candidates']) for i in evidence['images'])}个高斯测量；不是目标数"}


    def paths_node(self,state):
        context=self.agent.state['context'];paths=self.agent.state['paths']
        result=ridge_hypotheses(state['evidence'],self.agent.control);self.agent.save('inverse_ridges.json',result)
        return {'ridges':result,'_summary':f"{len(result['tracks'])}条路径候选，{len(result['crossing_regions'])}处竞争时刻；折叠处保留展开假设"}


    def associations(self,state):
        context=self.agent.state['context'];paths=self.agent.state['paths']
        result=propose_pairs(state['ridges'],self.agent.control)
        self.agent.save('inverse_proposals.json',{'pairs':[{'id':p['id'],'hull':p['hull']['id'],'tow':p['tow']['id'],
                   'synchronous_times':len(p['common_rows'])} for p in result['pairs']],
                   'budget_excluded':result['budget_excluded']})
        return {'proposals':result,'_summary':f"筛出{len(result['pairs'])}组轨迹级双阵解释；预算排除{result['budget_excluded']}组"}


    def fit(self,state):
        context=self.agent.state['context'];paths=self.agent.state['paths']
        result=fit_proposals(state['proposals'],state['evidence'],context,self.agent.control);self.agent.save('inverse_fit.json',result)
        return {'fitted':result,'_summary':f"{len(result['candidates'])}组可行批量拟合；比较匀速、转向、二次及谐波模型，不预设目标数量"}


    def refine(self,state):
        context=self.agent.state['context'];paths=self.agent.state['paths']
        fitted,iterations=refine_associations(state['fitted'],state['evidence'],context,self.agent.control,iterations=1)
        selection=select_exclusive(fitted,state['evidence'],context,self.agent.control)
        self.agent.save('inverse_selection.json',selection)
        def progress(update):
            self.agent.emit({'event':'node','node':'inverse_refine','label':INVERSE_LABELS['inverse_refine'],
                'status':'running','execution_kind':'local_numeric',
                'summary':f"第{update['iteration']}轮 · {update['hypothesis_id']} · {update['phase']}；细化预算剩余{update['remaining_s']}秒"})
        selection,profile_report=refine_profiles(selection,state['evidence'],context,self.agent.control,progress=progress)
        # Keep only actually supported, exclusive measurements after changing
        # motion parameters. Image fitting cannot invent count evidence.
        selection=select_exclusive({'candidates':selection['selected']},state['evidence'],context,self.agent.control)
        self.agent.save('inverse_fit.json',fitted);self.agent.save('inverse_selection.json',selection);self.agent.save('inverse_iterations.json',iterations)
        self.agent.save('inverse_profile_fit.json',profile_report)
        accepted=sum(u['accepted'] for u in profile_report['updates'])
        limit='细化预算已到，保留阶段性候选；' if profile_report['status']=='budget_limited' else ''
        return {'fitted':fitted,'selection':selection,'profile_report':profile_report,'_summary':limit+f"{len(selection['selected'])}个身份假设；联合图像拟合接受{accepted}次更新，共享测量排除，未解释{len(selection['uncovered_ids'])}点"}


    def review(self,state):
        context=self.agent.state['context'];paths=self.agent.state['paths']
        if not state['selection']['candidates']:
            self.agent.review_report={'status':'no_hypotheses'};return {'_summary':'无可复核假设，未调用模型'}
        self.agent.control.check();self.agent.new_model_calls+=1
        try:
            prompt=review_prompt(state['selection'],state['ridges'])+'\n已审核的内置规则（不是相似案例答案）：'+json.dumps(state['rules'],ensure_ascii=False)
            response=self.agent.reviewer(paths,prompt,self.agent.directory)
            answer=response['answer']
            (self.agent.directory/'hypothesis_review_answer.txt').write_text(answer,encoding='utf-8')
            self.agent.review_report=validate_review(answer,state['selection'],state['ridges'])
            if response.get('usage'):self.agent.review_report['usage']=response['usage']
        except InversionStopped:raise
        except Exception as exc:
            # Provider failures must not erase the independent numeric result.
            self.agent.review_report={'status':'failed','error_type':type(exc).__name__,
                                'automatic_retry':False,'numerical_selection_overridden':False}
        self.agent.save('hypothesis_review.json',self.agent.review_report)
        return {'_summary':'已记录受限假设复核；数值约束未被模型覆盖' if self.agent.review_report['status']=='reviewed' else '复核未成功，保留本地数值结果，未自动重试'}


    def quality(self,state):
        context=self.agent.state['context'];paths=self.agent.state['paths']
        report=check_and_preview(state['selection'],state['evidence'],context,paths,self.agent.directory,self.agent.control)
        self.agent.save('inverse_quality.json',report)
        return {'quality':report,'_summary':f"唯一测量覆盖{report['measurement_coverage']:.1%}；生成双阵期望BTR与回投图，不是真值验证"}


    def render(self,state):
        context=self.agent.state['context'];paths=self.agent.state['paths']
        document=build_document(state['selection'],state['evidence'],context,self.agent.control)
        document['pipeline']='forward_inverse_v1';document['new_model_calls']=self.agent.new_model_calls
        document['inverse_quality_summary']={k:v for k,v in state['quality'].items() if k!='rows'}
        document['display_view']='primary_motion_hypotheses'
        document['all_alternatives_retained']=True
        document['optimization_status']=state['profile_report']['status']
        weak=[item['id'] for item in document.get('inverse_quality',[]) if (item.get('range_sensitivity') or {}).get('range_sensitive')]
        if weak:document['warnings'].append('以下身份的双阵视差与角误差代理接近或更小，距离高度敏感，不能将低回投误差当作可靠位置：'+','.join(weak)+'；这是工程诊断，不是统计置信区间。')
        if state['profile_report']['status']=='budget_limited':document['warnings'].insert(0,'图像细化达到阶段预算：这是部分优化候选，未完成全部细化；保留已完成更新，其余沿用粗估，不代表完整优化或准确定位。')
        document['warnings'].append('主图只展示程序排序的主运动模型假设，未验证唯一侧别；全部竞争镜像保留于mirror_candidates.png及tracks.json，不是参考图选出的答案。')
        if self.agent.review_report.get('status')=='failed':document['warnings'].append('模型复核失败，当前图仅为本地数值候选。')
        # Persist ALL alternatives. The representative display never rewrites
        # the JSON or claims to have independently resolved the mirror.
        mirrors=copy.deepcopy(document);mirrors.pop('display_view',None)
        render_document(mirrors,self.agent.directory,context,filename='mirror_candidates.png',persist=False)
        document.update({k:mirrors[k] for k in ('coordinate_reference','plot_summary','warnings') if k in mirrors})
        self.agent.save('tracks.json',document)
        primary=copy.deepcopy(document)
        primary['tracks']=[t for t in primary['tracks'] if t.get('mirror_variant',0)==0]
        render_document(primary,self.agent.directory,context,persist=False)
        merged=document.get('identity_continuity',{}).get('merged_fragment_count',0)
        return {'document':document,'_summary':f"输出{len(set(t['id'] for t in document['tracks']))}个身份的{len(document['tracks'])}条候选轨迹（含镜像）；同一双阵身份归并{merged}个片段，镜像不计新增目标"}

