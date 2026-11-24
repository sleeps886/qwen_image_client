"""Local inverse Agent lifecycle, budget, execution events and persistence."""
import json
from backend.app.agents.graphs.inverse import build_inverse_graph
from backend.app.agents.progress import INVERSE_NODES,inverse_end,inverse_start
from backend.app.agents.serialization import public
from backend.app.repositories.result_repository import ResultRepository
from backend.app.services.jobs.inversion_control import InversionControl,InversionStopped


class ForwardInverseAgent:
    def __init__(self,emit=None,control=None,reviewer=None,finalizer=None):
        self.events=[];self.callback=emit;self.control=control or InversionControl();self.reviewer=reviewer
        self.finalizer=finalizer;self.final_report={'status':'not_requested'}
        self.new_model_calls=0;self.review_report={'status':'not_requested'};self.state={};self.directory=None

    def emit(self,event):
        self.events.append(event)
        if self.callback:self.callback(event)

    def save(self,name,value):
        (self.directory/name).write_text(json.dumps(public(value),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

    def metadata(self):
        selection=self.state.get('selection',{});selected=selection.get('selected',[])
        return {'pipeline':'forward_inverse_v1','result_source':('model_visual_fusion' if self.final_report['status']=='generated' else 'model_visual_fusion_failed' if self.finalizer else 'numeric_forward_inverse'),
            'answer_source':'model_output' if self.directory and (self.directory/'model_final_answer.txt').is_file() else 'local_numeric_report',
            'model_final_requested':self.finalizer is not None,'model_final':self.final_report,
            'new_model_calls':self.new_model_calls,'node_events':self.events,'hypothesis_review':self.review_report,
            'optimization_status':self.state.get('profile_report',{}).get('status','not_completed'),
            'gap_continuity':self.state.get('gap_report',{}),
            'target_count':{'status':'needs_review','inventory_target_count':len(selected),
                'associated_target_count':len(selected),'continuous_identity_count':len(selected),
                'localized_target_count':len({t['id'] for t in self.state.get('document',{}).get('tracks',[])}),
                'count_origin':'numeric_hypotheses','unique_target_count_confirmed':False,
                'warnings':['数值关联假设不是已确认目标数；片段、两阵和镜像不可重复计数。',
                            f'未解释测量{len(selection.get("uncovered_ids",[]))}个；需核对弱目标及杂波。']+
                           (['模型未保留来源身份：'+','.join(self.final_report['identity_audit']['omitted_source_identity_ids'])] if self.final_report.get('identity_audit',{}).get('omitted_source_identity_ids') else [])}}

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
                status='failed' if (name=='inverse_review' and self.review_report.get('status')=='failed') or (name=='inverse_model_final' and self.final_report.get('status')=='failed') else 'completed'
                self.emit(inverse_end(name,token,status,summary))
                return update
            except Exception as exc:
                self.emit(inverse_end(name,token,'stopped' if isinstance(exc,InversionStopped) and self.control.cancelled.is_set() else 'failed',str(exc)[:220]))
                raise
        return call

    def run(self,paths,context,output_root):
        self.directory=ResultRepository(output_root).create_run();self.state={'paths':paths,'context':context}
        graph=build_inverse_graph(self)
        error=None
        try:
            graph.compile().invoke(self.state)
        except Exception as exc:error=exc
        finally:
            known={e['node'] for e in self.events}
            for name,label in INVERSE_NODES+([('inverse_model_final','三类图模型最终轨迹生成')] if self.finalizer else []):
                if name not in known:self.emit({'event':'node','node':name,'label':label,'status':'skipped','elapsed_ms':0,
                                               'summary':'上游未通过或任务取消，未执行'})
            self.save('execution_report.json',self.metadata())
            self.save('response.json',{'result_source':self.metadata()['result_source'],'new_model_calls':self.new_model_calls,
                'usage':self.final_report.get('usage') or self.review_report.get('usage')})
            self.save('answer.txt',{'source':'local_numeric_report','new_model_calls':self.new_model_calls,
                'selected_hypotheses':[c['id'] for c in self.state.get('selection',{}).get('selected',[])],
                'quality':{k:v for k,v in self.state.get('quality',{}).items() if k!='rows'},
                'error':str(error)[:250] if error else None})
        if self.finalizer and (self.directory/'model_final_answer.txt').is_file():
            (self.directory/'answer.txt').write_text((self.directory/'model_final_answer.txt').read_text(encoding='utf-8'),encoding='utf-8')
        if error:raise error
        return self.result()

    def result(self):
        result_id=self.directory.name;doc=self.state['document']
        names=('model_mirror_candidates.png','model_final_quality.json','backend_candidate.png','backend_tracks.json','model_final_input.json','model_final_answer.txt','model_final_report.json','gaussian_0.png','gaussian_1.png','estimated_track.png','mirror_candidates.png','tracks.json','answer.txt','execution_report.json','inverse_fields.json',
               'inverse_ridges.json','inverse_gaps.json','inverse_proposals.json','inverse_fit.json','inverse_selection.json',
               'inverse_iterations.json','inverse_profile_fit.json','inverse_quality.json','calibration_context.json','knowledge_sources.json','hypothesis_review.json',
               'hypothesis_review_answer.txt','evidence_0.png','evidence_1.png','inverse_overlay_0.png',
               'inverse_overlay_1.png','expected_btr_0.png','expected_btr_1.png')
        return {'ok':True,'dry_run':False,'result_id':result_id,'input_mode':'images',
            'answer':(self.directory/'answer.txt').read_text(encoding='utf-8'),'usage':self.final_report.get('usage') or self.review_report.get('usage'),
            'trajectory_image':f'/api/results/{result_id}/estimated_track.png' if (self.directory/'estimated_track.png').is_file() else None,'trajectory_status':'position_estimate' if (self.directory/'estimated_track.png').is_file() else 'not_generated',
            'downloads':{n:f'/api/results/{result_id}/{n}' for n in names if (self.directory/n).is_file()},
            'warnings':doc['warnings'],'identity_continuity':doc.get('identity_continuity'),
            'model_display_continuity':doc.get('model_display_continuity'),**self.metadata()}

