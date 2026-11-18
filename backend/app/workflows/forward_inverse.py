"""End-to-end local inverse graph, with one explicitly authorized optional review."""
import json
import copy
from pathlib import Path
from typing import TypedDict

import numpy as np
from langgraph.graph import StateGraph,START,END

from backend.app.agents.hypothesis_review import review_prompt,validate_review
from backend.app.services.inversion_control import InversionControl,InversionStopped
from backend.app.services.btr_field import extract_fields
from backend.app.services.ridge_hypotheses import ridge_hypotheses,propose_pairs
from backend.app.services.batch_inverse import fit_proposals,select_exclusive,refine_associations,build_document
from backend.app.services.inverse_quality import check_and_preview
from backend.app.services.trajectory import render_document
from backend.app.services.sonar_evidence import save_evidence
from backend.app.services.knowledge import retrieve_rules
from backend.app.services.profile_inverse import refine_profiles
from backend.app.repositories.result_repository import ResultRepository
from backend.app.workflows.progress import start_record,end_record


INVERSE_NODES=[('inverse_input','反演输入与标定'),('inverse_field','完整亮带剖面测量'),
 ('inverse_paths','展开角路径与交叉假设'),('inverse_association','双阵轨迹级关联候选'),
 ('inverse_fit','批量运动模型反演'),('inverse_refine','全局归属与有界迭代'),
 ('inverse_review','可选模型假设复核'),('inverse_quality','正向BTR与可辨识性检查'),('inverse_render','空间候选绘图与汇总')]
INVERSE_LABELS=dict(INVERSE_NODES)


def inverse_start(name):
    token,event=start_record(name)
    event.update(label=INVERSE_LABELS[name],execution_kind='model_gateway' if name=='inverse_review' else 'local_numeric')
    return token,event


def inverse_end(name,token,status,summary):
    return end_record(name,token,status,summary)|{'label':INVERSE_LABELS[name],
        'execution_kind':'model_gateway' if name=='inverse_review' else 'local_numeric'}


class InverseState(TypedDict,total=False):
    paths:list
    context:dict
    evidence:dict
    ridges:dict
    proposals:dict
    fitted:dict
    selection:dict
    quality:dict
    document:dict
    rules:list
    profile_report:dict


def public(value):
    if isinstance(value,dict):return {k:public(v) for k,v in value.items() if not k.startswith('_')}
    if isinstance(value,(tuple,list)):return [public(v) for v in value]
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,np.generic):return value.item()
    return value


class ForwardInverseWorkflow:
    def __init__(self,emit=None,control=None,reviewer=None):
        self.events=[];self.callback=emit;self.control=control or InversionControl();self.reviewer=reviewer
        self.new_model_calls=0;self.review_report={'status':'not_requested'};self.state={};self.directory=None

    def emit(self,event):
        self.events.append(event)
        if self.callback:self.callback(event)

    def save(self,name,value):
        (self.directory/name).write_text(json.dumps(public(value),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

    def metadata(self):
        selection=self.state.get('selection',{});selected=selection.get('selected',[])
        return {'pipeline':'forward_inverse_v1','result_source':'numeric_forward_inverse',
            'new_model_calls':self.new_model_calls,'node_events':self.events,'hypothesis_review':self.review_report,
            'optimization_status':self.state.get('profile_report',{}).get('status','not_completed'),
            'target_count':{'status':'needs_review','inventory_target_count':len(selected),
                'associated_target_count':len(selected),'continuous_identity_count':len(selected),
                'localized_target_count':len({t['id'] for t in self.state.get('document',{}).get('tracks',[])}),
                'count_origin':'numeric_hypotheses','unique_target_count_confirmed':False,
                'warnings':['数值关联假设不是已确认目标数；片段、两阵和镜像不可重复计数。',
                            f'未解释测量{len(selection.get("uncovered_ids",[]))}个；需核对弱目标及杂波。']}}

    def observed(self,name,function):
        def call(state):
            self.control.check();token,start=inverse_start(name);self.emit(start)
            if name=='inverse_review' and self.reviewer is None:
                self.emit(inverse_end(name,token,'skipped','未申请模型复核；本地处理不需要API Key或云端同意'))
                return {}
            if name=='inverse_review' and self.control.remaining<50:
                self.review_report={'status':'skipped_budget','new_model_calls':0}
                self.emit(inverse_end(name,token,'skipped','剩余预算不足，未调用云端复核；保留本地结果并继续质检绘图'))
                return {}
            try:
                update=function(state)
                summary=update.pop('_summary','已执行；仅代表执行状态，不证明准确率')
                self.state.update(update)
                status='failed' if name=='inverse_review' and self.review_report.get('status')=='failed' else 'completed'
                self.emit(inverse_end(name,token,status,summary))
                return update
            except Exception as exc:
                self.emit(inverse_end(name,token,'stopped' if isinstance(exc,InversionStopped) and self.control.cancelled.is_set() else 'failed',str(exc)[:220]))
                raise
        return call

    def run(self,paths,context,output_root):
        self.directory=ResultRepository(output_root).create_run();self.state={'paths':paths,'context':context}
        def validate(state):
            if context.get('renderer_profile')!='btr_gui_v1':raise ValueError('完整正向反演仅支持显式声明的工具原始图配置')
            if len(paths)!=2 or len(context.get('platforms',[]))!=1:raise ValueError('需要同平台hull/tow各一张及平台参数')
            self.save('calibration_context.json',context)
            rules=list({r['id']:r for stage in ('observation','association','diagnosis') for r in retrieve_rules(stage)}.values())
            self.save('knowledge_sources.json',rules)
            return {'rules':rules,'_summary':f'已校验工具配色与双阵约定，检索{len(rules)}条内置规则；未读取目标标签或案例答案'}
        def field(state):
            evidence=extract_fields(paths,context,self.control)
            self.save('inverse_fields.json',evidence)
            save_evidence(public(evidence),paths,self.directory)
            return {'evidence':evidence,'_summary':f"从实际图像提取{sum(len(i['candidates']) for i in evidence['images'])}个高斯测量；不是目标数"}
        def paths_node(state):
            result=ridge_hypotheses(state['evidence'],self.control);self.save('inverse_ridges.json',result)
            return {'ridges':result,'_summary':f"{len(result['tracks'])}条路径候选，{len(result['crossing_regions'])}处竞争时刻；折叠处保留展开假设"}
        def associations(state):
            result=propose_pairs(state['ridges'],self.control)
            self.save('inverse_proposals.json',{'pairs':[{'id':p['id'],'hull':p['hull']['id'],'tow':p['tow']['id'],
                       'synchronous_times':len(p['common_rows'])} for p in result['pairs']],
                       'budget_excluded':result['budget_excluded']})
            return {'proposals':result,'_summary':f"筛出{len(result['pairs'])}组轨迹级双阵解释；预算排除{result['budget_excluded']}组"}
        def fit(state):
            result=fit_proposals(state['proposals'],state['evidence'],context,self.control);self.save('inverse_fit.json',result)
            return {'fitted':result,'_summary':f"{len(result['candidates'])}组可行批量拟合；比较匀速、转向、二次及谐波模型，不预设目标数量"}
        def refine(state):
            fitted,iterations=refine_associations(state['fitted'],state['evidence'],context,self.control,iterations=1)
            selection=select_exclusive(fitted,state['evidence'],context,self.control)
            self.save('inverse_selection.json',selection)
            def progress(update):
                self.emit({'event':'node','node':'inverse_refine','label':INVERSE_LABELS['inverse_refine'],
                    'status':'running','execution_kind':'local_numeric',
                    'summary':f"第{update['iteration']}轮 · {update['hypothesis_id']} · {update['phase']}；细化预算剩余{update['remaining_s']}秒"})
            selection,profile_report=refine_profiles(selection,state['evidence'],context,self.control,progress=progress)
            # Keep only actually supported, exclusive measurements after changing
            # motion parameters. Image fitting cannot invent count evidence.
            selection=select_exclusive({'candidates':selection['selected']},state['evidence'],context,self.control)
            self.save('inverse_fit.json',fitted);self.save('inverse_selection.json',selection);self.save('inverse_iterations.json',iterations)
            self.save('inverse_profile_fit.json',profile_report)
            accepted=sum(u['accepted'] for u in profile_report['updates'])
            limit='细化预算已到，保留阶段性候选；' if profile_report['status']=='budget_limited' else ''
            return {'fitted':fitted,'selection':selection,'profile_report':profile_report,'_summary':limit+f"{len(selection['selected'])}个身份假设；联合图像拟合接受{accepted}次更新，共享测量排除，未解释{len(selection['uncovered_ids'])}点"}
        def review(state):
            if not state['selection']['candidates']:
                self.review_report={'status':'no_hypotheses'};return {'_summary':'无可复核假设，未调用模型'}
            self.control.check();self.new_model_calls+=1
            try:
                prompt=review_prompt(state['selection'],state['ridges'])+'\n已审核的内置规则（不是相似案例答案）：'+json.dumps(state['rules'],ensure_ascii=False)
                response=self.reviewer(paths,prompt,self.directory)
                answer=response['answer']
                (self.directory/'hypothesis_review_answer.txt').write_text(answer,encoding='utf-8')
                self.review_report=validate_review(answer,state['selection'],state['ridges'])
                if response.get('usage'):self.review_report['usage']=response['usage']
            except InversionStopped:raise
            except Exception as exc:
                # Provider failures must not erase the independent numeric result.
                self.review_report={'status':'failed','error_type':type(exc).__name__,
                                    'automatic_retry':False,'numerical_selection_overridden':False}
            self.save('hypothesis_review.json',self.review_report)
            return {'_summary':'已记录受限假设复核；数值约束未被模型覆盖' if self.review_report['status']=='reviewed' else '复核未成功，保留本地数值结果，未自动重试'}
        def quality(state):
            report=check_and_preview(state['selection'],state['evidence'],context,paths,self.directory,self.control)
            self.save('inverse_quality.json',report)
            return {'quality':report,'_summary':f"唯一测量覆盖{report['measurement_coverage']:.1%}；生成双阵期望BTR与回投图，不是真值验证"}
        def render(state):
            document=build_document(state['selection'],state['evidence'],context,self.control)
            document['pipeline']='forward_inverse_v1';document['new_model_calls']=self.new_model_calls
            document['inverse_quality_summary']={k:v for k,v in state['quality'].items() if k!='rows'}
            document['display_view']='primary_motion_hypotheses'
            document['all_alternatives_retained']=True
            document['optimization_status']=state['profile_report']['status']
            if state['profile_report']['status']=='budget_limited':document['warnings'].insert(0,'图像细化达到阶段预算：这是部分优化候选，未完成全部细化；保留已完成更新，其余沿用粗估，不代表完整优化或准确定位。')
            document['warnings'].append('主图只展示程序排序的主运动模型假设，未验证唯一侧别；全部竞争镜像保留于mirror_candidates.png及tracks.json，不是参考图选出的答案。')
            if self.review_report.get('status')=='failed':document['warnings'].append('模型复核失败，当前图仅为本地数值候选。')
            # Persist ALL alternatives. The representative display never rewrites
            # the JSON or claims to have independently resolved the mirror.
            mirrors=copy.deepcopy(document);mirrors.pop('display_view',None)
            render_document(mirrors,self.directory,context,filename='mirror_candidates.png',persist=False)
            document.update({k:mirrors[k] for k in ('coordinate_reference','plot_summary') if k in mirrors})
            self.save('tracks.json',document)
            primary=copy.deepcopy(document)
            primary['tracks']=[t for t in primary['tracks'] if t.get('mirror_variant',0)==0]
            render_document(primary,self.directory,context,persist=False)
            return {'document':document,'_summary':f"绘制{len(set(t['id'] for t in document['tracks']))}个身份的{len(document['tracks'])}段候选；镜像不计新增目标"}
        functions=[validate,field,paths_node,associations,fit,refine,review,quality,render]
        graph=StateGraph(InverseState)
        for (name,_),function in zip(INVERSE_NODES,functions):graph.add_node(name,self.observed(name,function))
        graph.add_edge(START,INVERSE_NODES[0][0])
        for a,b in zip(INVERSE_NODES,INVERSE_NODES[1:]):graph.add_edge(a[0],b[0])
        graph.add_edge(INVERSE_NODES[-1][0],END)
        error=None
        try:
            graph.compile().invoke(self.state)
        except Exception as exc:error=exc
        finally:
            known={e['node'] for e in self.events}
            for name,label in INVERSE_NODES:
                if name not in known:self.emit({'event':'node','node':name,'label':label,'status':'skipped','elapsed_ms':0,
                                               'summary':'上游未通过或任务取消，未执行'})
            self.save('execution_report.json',self.metadata())
            self.save('response.json',{'result_source':'numeric_forward_inverse','new_model_calls':self.new_model_calls})
            self.save('answer.txt',{'source':'local_numeric_report','new_model_calls':self.new_model_calls,
                'selected_hypotheses':[c['id'] for c in self.state.get('selection',{}).get('selected',[])],
                'quality':{k:v for k,v in self.state.get('quality',{}).items() if k!='rows'},
                'error':str(error)[:250] if error else None})
        if error:raise error
        return self.result()

    def result(self):
        result_id=self.directory.name;doc=self.state['document']
        names=('estimated_track.png','mirror_candidates.png','tracks.json','answer.txt','execution_report.json','inverse_fields.json',
               'inverse_ridges.json','inverse_proposals.json','inverse_fit.json','inverse_selection.json',
               'inverse_iterations.json','inverse_profile_fit.json','inverse_quality.json','calibration_context.json','knowledge_sources.json','hypothesis_review.json',
               'hypothesis_review_answer.txt','evidence_0.png','evidence_1.png','inverse_overlay_0.png',
               'inverse_overlay_1.png','expected_btr_0.png','expected_btr_1.png')
        return {'ok':True,'dry_run':False,'result_id':result_id,'input_mode':'images',
            'answer':(self.directory/'answer.txt').read_text(encoding='utf-8'),'usage':self.review_report.get('usage'),
            'trajectory_image':f'/api/results/{result_id}/estimated_track.png','trajectory_status':'position_estimate',
            'downloads':{n:f'/api/results/{result_id}/{n}' for n in names if (self.directory/n).is_file()},
            'warnings':doc['warnings'],**self.metadata()}
