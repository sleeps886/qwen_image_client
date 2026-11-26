"""Durable actual-stage telemetry; never infer completion from elapsed time."""
from contextlib import contextmanager
from datetime import datetime,timezone
from time import perf_counter
import uuid
import json

from backend.app.services.association.incremental_prediction import save_state

NODES=[('input_validation','输入与版本校验','local_validation'),
       ('platform_assessment','平台状态与阵列几何分析','local_analysis'),
       ('context_preparation','本轮点位与上下文准备','local_validation'),
       ('model_prediction','模型判断与轨迹预测','model_gateway'),
       ('answer_validation','回复与候选引用校验','local_validation'),
       ('spatial_planning','平台与目标轨迹融合规划','local_analysis'),
       ('state_commit','保存本轮轨迹与记录','local_storage')]


class PredictionExecution:
    def __init__(self,directory,version,nodes=None):
        self.directory=directory
        self.record={'execution_id':uuid.uuid4().hex,'base_version':version,'status':'running',
            'created_at':datetime.now(timezone.utc).isoformat(),'sequence':0,
            'execution_only_not_accuracy':True,'nodes':[{'node':name,'label':label,'kind':kind,'status':'pending'} for name,label,kind in (nodes or NODES)]}
        self.save()

    def save(self):
        self.record['sequence']+=1
        save_state(self.directory/f"execution_{self.record['execution_id']}.json",self.record)
        save_state(self.directory/'execution.json',self.record)

    @classmethod
    def load(cls,directory):
        trace=cls.__new__(cls);trace.directory=directory
        trace.record=json.loads((directory/'execution.json').read_text(encoding='utf-8'))
        return trace

    def defer(self,name):
        item=next(n for n in self.record['nodes'] if n['node']==name)
        item.update(status='waiting_for_user',outcome='after_association_confirmation')
        self.save()

    def interrupt(self,label='interrupt · 是否采用轨迹'):
        self.record['status']='waiting_for_user'
        self.record['nodes'].append({'node':'trajectory_review','label':label,
                                     'kind':'human_review','status':'waiting_for_user'})
        self.save()

    @contextmanager
    def node(self,name):
        item=next(n for n in self.record['nodes'] if n['node']==name)
        self.record['status']='running'
        item.update(status='running',started_at=datetime.now(timezone.utc).isoformat())
        start=perf_counter();self.save()
        try:yield
        except BaseException as exc:
            item.update(status='failed',error_type=type(exc).__name__)
            self.record['status']='failed'
            for other in self.record['nodes']:
                if other['status']=='pending':other['status']='skipped'
            raise
        else:item['status']='completed'
        finally:
            item.update(elapsed_ms=round((perf_counter()-start)*1000,3),finished_at=datetime.now(timezone.utc).isoformat())
            if all(n['status']=='completed' for n in self.record['nodes']):self.record['status']='completed'
            self.save()
