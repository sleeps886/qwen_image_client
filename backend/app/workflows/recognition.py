"""Evidence graph around the existing model gateway. Data-file mode remains direct."""
import json
from pathlib import Path
from backend.app.core.exceptions import ClientError
from backend.app.agents.sonar_agent import SonarAssociationAgent
from backend.app.services.sonar_evidence import save_evidence,save_selected,save_reprojection
from backend.app.services.continuous_tracks import tracking_options
from backend.app.services.target_count import finalize_count
from backend.app.workflows.progress import observed,LABELS,start_record,end_record
from backend.app.workflows.evidence_graph import build_preflight,build_review
from backend.app.workflows.tracking_graph import build_tracking_graph
from backend.app.infrastructure.llm.gateway import ConfiguredModelGateway,ModelGateway
from backend.app.workflows.nodes import invoke_model_node,stream_model_node,render_node
from backend.app.services.trajectory import render_tracks,render_document


class RecognitionWorkflow:
    def __init__(self,gateway:ModelGateway|None=None,renderer=None):
        self.gateway=gateway if gateway is not None else ConfiguredModelGateway()
        self.agent=SonarAssociationAgent(self.gateway)
        self.renderer=renderer if renderer is not None else render_tracks
        self.evidence_state=None
        self.node_events=[];self.node_callback=None;self.count_report=None

    def emit(self,event):
        self.node_events.append(dict(event))
        if self.node_callback:self.node_callback(dict(event))

    def result_metadata(self):
        count=self.count_report
        if count is None and self.evidence_state:
            count={'image_hypothesis':self.evidence_state.get('count_hypothesis'),
                   'status':'local_preflight_only','associated_target_count':None,'localized_target_count':None,
                   'warnings':['仅本地持续亮带线索，尚未运行模型数量清单与双阵关联。']}
        return {'node_events':self.node_events,'target_count':count}

    def prepare(self,images,prompt,mode,context):
        self.evidence_state=None
        self.node_events=[];self.count_report=None
        if mode!='images' or not context or not context.get('require_spatial') or not context.get('platforms'):
            return prompt
        try:
            if len(images)<2:raise ValueError('空间Agent流程需要同平台的hull/tow两张图')
            context={**context,'tracking_options':tracking_options(context['T_MAX']),'use_candidate_uncertainty':True}
            self.evidence_state=build_preflight(self.emit).invoke({'images':[str(p) for p in images],
                                                        'context':context,'prompt':prompt})
        except (ValueError,OSError) as exc:
            raise ClientError('本地候选预检失败，未调用模型：'+str(exc)) from None
        return self.evidence_state['prompt']

    def persist_evidence(self,directory):
        if not self.evidence_state:return
        state=self.evidence_state;root=Path(directory)
        save_evidence(state['evidence'],state['images'],root)
        (root/'calibration_context.json').write_text(json.dumps(state['context'],ensure_ascii=False,indent=2),encoding='utf-8')
        (root/'knowledge_sources.json').write_text(json.dumps(state['rules'],ensure_ascii=False,indent=2),encoding='utf-8')
        (root/'tracklets.json').write_text(json.dumps({'tracklets':state.get('tracklets'),
            'count_hypothesis':state.get('count_hypothesis')},ensure_ascii=False,indent=2),encoding='utf-8')
        (root/'execution_report.json').write_text(json.dumps(self.result_metadata(),ensure_ascii=False,indent=2),encoding='utf-8')
        (root/'agent_trace.json').write_text(json.dumps({'engine':'langgraph','nodes':state['trace'],
            'model_calls_budget':1,'automatic_paid_retry':False,'persistent_resume':False},ensure_ascii=False,indent=2),encoding='utf-8')

    @staticmethod
    def input_state(images,prompt,mode):
        return {'images':[str(path) for path in images],'prompt':prompt,'mode':mode}

    def invoke(self,images,prompt,settings,output_root,mode,context=None):
        prompt=self.prepare(images,prompt,mode,context)
        def invoke(state):
            if self.evidence_state:return self.agent.invoke_state(state,settings,output_root)
            return invoke_model_node(state,gateway=self.gateway,settings=settings,output_root=output_root)
        update=(observed('model_association',invoke,self.emit) if self.evidence_state else invoke)(self.input_state(images,prompt,mode))
        self.persist_evidence(update['model_result']['output_dir'])
        return update['model_result']

    async def stream(self,images,prompt,settings,output_root,mode,context=None):
        import asyncio
        if mode=='images' and context and context.get('platforms'):
            yield {'event':'phase','phase':'evidence','message':'LangGraph：检索本地规则并提取像素候选；尚未调用模型。'}
        prompt=await asyncio.to_thread(self.prepare,images,prompt,mode,context)
        token,start=start_record('model_association');complete=False
        if self.evidence_state:self.emit(start)
        try:
            state=self.input_state(images,prompt,mode)
            events=self.agent.stream_state(state,settings,output_root) if self.evidence_state else stream_model_node(
                state,gateway=self.gateway,settings=settings,output_root=output_root)
            async for event in events:
                if event.get('event')=='model_complete':
                    complete=True
                    if self.evidence_state:self.emit(end_record('model_association',token,'completed','模型正文已返回，待程序验证数量与配对'))
                    if self.evidence_state:await asyncio.to_thread(self.persist_evidence,event['result']['output_dir'])
                yield event
        finally:
            if not complete and self.evidence_state:self.emit(end_record('model_association',token,'failed','模型请求未完整结束，不伪造后续节点结果'))

    def render(self,answer,output_dir,context=None):
        state=self.evidence_state
        report=None
        try:
            if state:
                update=build_review(self.emit).invoke({**state,'answer':answer})
                state['trace']=update['trace']
                self.count_report=update['count_report']
                save_selected(state['evidence'],update['model_observations'],output_dir)
                for value,name in [(update['model_observations'],'model_observations.json'),(update['identity_report'],'identity_report.json')]:
                    (Path(output_dir)/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
                (Path(output_dir)/'resolved_observations.json').write_text(json.dumps(update['observations'],ensure_ascii=False,indent=2),encoding='utf-8')
                answer=json.dumps(update['observations'],ensure_ascii=False)
                context=state['context']
                tracked=build_tracking_graph(self.emit).invoke({'observations':update['observations'],'context':context,
                    'evidence':state['evidence'],'trace':state['trace']})
                state['trace']=tracked['trace']
                for key,name in [('raw_document','spatial_raw.json'),('tracking_report','tracking_report.json'),('reprojection_report','reprojection_report.json')]:
                    if key in tracked:(Path(output_dir)/name).write_text(json.dumps(tracked[key],ensure_ascii=False,indent=2),encoding='utf-8')
                if tracked.get('reprojection_report',{}).get('rows'):
                    save_reprojection(state['evidence'],tracked['reprojection_report'],output_dir)
                def count_result(_):
                    self.count_report=finalize_count(self.count_report,tracked.get('document') if not tracked.get('failure') else None,update['identity_report'])
                    return {'observations':update['observations'],'count_report':self.count_report}
                observed('result_summary',count_result,self.emit)({})
                if tracked.get('failure'):raise ValueError(tracked['failure'])
                document=observed('render',lambda s:{'document':render_document(s['document'],output_dir,context)},self.emit)({'document':tracked['document']})['document']
            else:
                document=render_node({'answer':answer,'output_dir':str(output_dir),'context':context},
                    renderer=self.renderer)['tracks_document']
            if state:
                total=sum(len(im['candidates']) for im in state['evidence']['images'])
                counts=[len(t['points']) for t in document['tracks']]
                report={'status':'partial_candidates','pixel_candidates':total,'spatial_branch_points':counts,
                        'unique_solution':False,'notes':state['evidence']['warnings'],
                        'statistical_confidence_available':False,'full_tracking_implemented':False,
                        'continuous_estimation_implemented':True,'reprojection_check_implemented':True,
                        'target_count':self.count_report,
                        'tracking':tracked['tracking_report'],'reprojection':{k:v for k,v in tracked['reprojection_report'].items() if k!='rows'}}
                state['trace']+=['report_quality_without_paid_retry']
                document['warnings']+=state['evidence']['warnings']
                document['warnings']+=self.count_report['warnings']
                document['warnings'].append('已运行连续估计与重投影节点，但通过一致性检查不代表目标关联正确或完整轨迹恢复；镜像仍保留。')
                # Keep all distinct limitations without repeating the same
                # identity warnings from observations and the count report.
                document['warnings']=list(dict.fromkeys(document['warnings']))
                (Path(output_dir)/'tracks.json').write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf-8')
            return document
        except (ValueError,TypeError,KeyError) as exc:
            report={'status':'needs_review','reason':str(exc)[:500],'automatic_retry':False}
            raise
        finally:
            if state:
                if report and report.get('status')=='needs_review':
                    known={event['node'] for event in self.node_events}
                    for name in LABELS:
                        if name not in known:self.emit({'event':'node','node':name,'label':LABELS[name],'status':'skipped','elapsed_ms':0,'summary':'上游未通过，未执行本节点'})
                (Path(output_dir)/'execution_report.json').write_text(json.dumps(self.result_metadata(),ensure_ascii=False,indent=2),encoding='utf-8')
                if self.count_report:(Path(output_dir)/'target_count_report.json').write_text(json.dumps(self.count_report,ensure_ascii=False,indent=2),encoding='utf-8')
                (Path(output_dir)/'quality_report.json').write_text(json.dumps(report or {'status':'failed'},ensure_ascii=False,indent=2),encoding='utf-8')
                (Path(output_dir)/'agent_trace.json').write_text(json.dumps({'engine':'langgraph','nodes':state['trace'],
                    'model_calls_budget':1,'automatic_paid_retry':False,'persistent_resume':False},ensure_ascii=False,indent=2),encoding='utf-8')
