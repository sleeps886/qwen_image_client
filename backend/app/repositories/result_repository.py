"""Filesystem persistence. No HTTP exceptions, providers or graph dependencies."""
from datetime import datetime
import json
from pathlib import Path
import re
import uuid

from backend.app.core.exceptions import ResultNotFound,InvalidStoredResult

RESULT_FILES=('model_mirror_candidates.png','model_final_quality.json','backend_candidate.png','backend_tracks.json','model_final_input.json','model_final_answer.txt','model_final_report.json','answer.txt','response.json','request_meta.json','estimated_track.png','mirror_candidates.png','tracks.json',
              'provider_reasoning.txt','stream_state.json','candidate_evidence.json','knowledge_sources.json',
              'agent_trace.json','quality_report.json','resolved_observations.json',
              'spatial_raw.json','tracking_report.json','reprojection_report.json','calibration_context.json',
              'tracklets.json','target_count_report.json','execution_report.json',
              'model_observations.json','identity_report.json','measurement_report.json',
              'inverse_fields.json','inverse_ridges.json','inverse_proposals.json','inverse_fit.json',
              'inverse_selection.json','inverse_iterations.json','inverse_quality.json','inverse_profile_fit.json',
              'hypothesis_review.json','hypothesis_review_answer.txt',
              *(f'inverse_overlay_{i}.png' for i in range(2)),*(f'expected_btr_{i}.png' for i in range(2)),
              *(f'gaussian_{i}.png' for i in range(8)),
              *(f'evidence_{i}.png' for i in range(8)),*(f'selected_{i}.png' for i in range(8)),
              *(f'reprojection_{i}.png' for i in range(8)))
COMPARISON_FILES=('comparison_overlay.png','report.json','evaluation.json')


class ResultRepository:
    def __init__(self,root):self.root=Path(root)

    @staticmethod
    def valid_id(value):return bool(re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{8}',value))

    def create_run(self):
        run=self.root/(datetime.now().strftime('%Y%m%d_%H%M%S_')+uuid.uuid4().hex[:8])
        run.mkdir(parents=True,exist_ok=False)
        return run

    @staticmethod
    def save(run,name,value):
        if '/' in name or '\\' in name or name.startswith('.'):
            raise ValueError('结果文件名无效')
        (Path(run)/name).write_text(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

    def file(self,result_id,filename):
        if not self.valid_id(result_id) or filename not in RESULT_FILES:raise ResultNotFound('文件不存在')
        root=self.root/result_id;path=root/filename
        if root.is_symlink() or path.is_symlink() or not path.is_file():raise ResultNotFound('文件不存在')
        return path

    def comparison_file(self,result_id,comparison_id,filename):
        if not self.valid_id(result_id) or not re.fullmatch(r'[0-9a-f]{32}',comparison_id) or filename not in COMPARISON_FILES:
            raise ResultNotFound('文件不存在')
        root=self.root/result_id;directory=root/'comparisons'/comparison_id;path=directory/filename
        if not path.is_file() or any(p.is_symlink() for p in (path,directory,directory.parent,root)):
            raise ResultNotFound('文件不存在')
        return path

    def history(self,limit=30):
        items=[]
        for root in sorted(self.root.glob('*'),reverse=True):
            if len(items)>=limit:break
            if not self.valid_id(root.name) or root.is_symlink():continue
            track=root/'tracks.json';image=root/'estimated_track.png'
            if not track.is_file() or not image.is_file() or track.is_symlink() or image.is_symlink():continue
            try:
                frame=json.loads(track.read_text(encoding='utf-8')).get('coordinate_system')
                items.append({'result_id':root.name,'trajectory_status':'bearing_time' if frame=='bearing_time' else 'position_estimate'})
            except (ValueError,OSError):continue
        return items

    def historical_result(self,result_id):
        if not self.valid_id(result_id):raise ResultNotFound('结果不存在')
        root=self.root/result_id
        names=('tracks.json','estimated_track.png','answer.txt')
        if root.is_symlink() or any(not (root/n).is_file() or (root/n).is_symlink() for n in names):
            raise ResultNotFound('结果缺少轨迹图、轨迹点或原始正文')
        try:
            usage=None
            response=root/'response.json'
            if response.is_file() and not response.is_symlink() and response.stat().st_size<=16*1024*1024:
                try:
                    saved=json.loads(response.read_text(encoding='utf-8'))
                    if isinstance(saved,dict) and isinstance(saved.get('usage'),dict):usage=saved['usage']
                except (OSError,ValueError):pass
            available=tuple(n for n in RESULT_FILES if (root/n).is_file() and not (root/n).is_symlink())
            return {'result_id':result_id,'document':json.loads((root/'tracks.json').read_text(encoding='utf-8')),
                    'answer':(root/'answer.txt').read_text(encoding='utf-8'),'files':available,'usage':usage}
        except (OSError,ValueError):raise InvalidStoredResult('本地结果格式无效') from None
