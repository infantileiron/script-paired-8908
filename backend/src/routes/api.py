import os,json
import redis
from fastapi import APIRouter,Depends,HTTPException,Response
from sqlalchemy.exc import IntegrityError
from src.config.database import db
from src.models.entities import User,Pair,Evaluation,Adoption,Audit
from src.schemas.business import Login,PairInput,EvaluationInput,AdoptionInput
from src.schemas.sampling import SamplingPlanInput,ObservationInput
from src.middlewares.auth import current,editor,admin
from src.utils.security import verify,token
from src.services.business import audit,dump
from src.services.sampling import SamplingError
from src.services import sampling_plans as plans
from src.controllers.business import ensure_evaluation
router=APIRouter()
@router.post('/auth/login')
def login(data:Login,s=Depends(db)):
 u=s.query(User).filter_by(email=data.email).first()
 if not u or not verify(data.password,u.password):raise HTTPException(401,'账号或密码错误')
 return {'token':token(u),'user':dump(u)}
@router.get('/me')
def me(u=Depends(current)):return dump(u)
@router.get('/pairs')
def pairs(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Pair).order_by(Pair.id).all()]
@router.post('/pairs')
def create_pair(data:PairInput,u=Depends(editor),s=Depends(db)):
 r=Pair(**data.model_dump());s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'顾问编号已存在，每位顾问仅保留一组前后测')
 audit(s,u,'录入顾问配对 '+r.code);return dump(r)
@router.put('/pairs/{i}')
def edit_pair(i:int,data:PairInput,u=Depends(editor),s=Depends(db)):
 r=s.get(Pair,i)
 if not r:raise HTTPException(404,'配对记录不存在')
 for k,v in data.model_dump().items():setattr(r,k,v)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'顾问编号重复')
 audit(s,u,'修订配对 '+str(i));return dump(r)
@router.get('/evaluations')
def evaluations(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Evaluation).order_by(Evaluation.id.desc()).all()]
@router.post('/evaluations')
def evaluate(data:EvaluationInput,u=Depends(editor),s=Depends(db)):
 rows=[dump(r) for r in s.query(Pair).order_by(Pair.id).all() if data.team=='全部' or r.team==data.team]
 r=Evaluation(**data.model_dump(),result={'snapshot':rows});s.add(r);s.commit()
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush('paired:evaluations',r.id)
 except redis.RedisError:r.state='failed';r.error='队列暂不可用，请稍后重新提交';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'提交评估 '+str(r.id));return dump(r)
@router.get('/evaluations/{i}')
def detail(i:int,u=Depends(current),s=Depends(db)):return dump(ensure_evaluation(s,i))
@router.get('/evaluations/{i}/export')
def export(i:int,u=Depends(current),s=Depends(db)):
 r=ensure_evaluation(s,i)
 if r.state!='done':raise HTTPException(409,'仅成功评估可导出')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="script-paired-{i}.json"'})
@router.get('/adoptions')
def adoptions(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Adoption).order_by(Adoption.id.desc()).all()]
@router.post('/adoptions')
def adoption(data:AdoptionInput,u=Depends(editor),s=Depends(db)):
 if ensure_evaluation(s,data.evaluation_id).state!='done':raise HTTPException(409,'评估完成后才能起草采纳意见')
 r=Adoption(**data.model_dump());s.add(r);s.commit();audit(s,u,'起草采纳意见 '+str(r.id));return dump(r)
@router.post('/adoptions/{i}/approve')
def approve(i:int,u=Depends(admin),s=Depends(db)):
 r=s.get(Adoption,i)
 if not r:raise HTTPException(404,'采纳意见不存在')
 if r.status!='draft':raise HTTPException(409,'已签核意见不可重复签核')
 r.status='approved';s.commit();audit(s,u,'签核意见 '+str(i));return dump(r)
@router.get('/audits')
def audits(u=Depends(admin),s=Depends(db)):return [dump(r) for r in s.query(Audit).order_by(Audit.id.desc()).limit(100)]

def _plan_error(exc,status=409):
 if isinstance(exc,plans.PlanNotFound):raise HTTPException(404,'抽检计划不存在')
 if isinstance(exc,plans.PlanRejected):raise HTTPException(422,str(exc))
 raise HTTPException(status,str(exc))

@router.get('/sampling-plans')
def sampling_plans(u=Depends(current),s=Depends(db)):return plans.plan_list(s)
@router.post('/sampling-plans')
def create_sampling_plan(data:SamplingPlanInput,u=Depends(editor),s=Depends(db)):
 try:plan=plans.create_plan(s,u,data)
 except SamplingError as exc:raise HTTPException(422,str(exc))
 return plans.plan_detail(s,plan.id)
@router.get('/sampling-plans/{i}')
def sampling_plan(i:int,u=Depends(current),s=Depends(db)):
 try:return plans.plan_detail(s,i)
 except plans.PlanNotFound:raise HTTPException(404,'抽检计划不存在')
@router.post('/sampling-plans/{i}/dispatch')
def dispatch_sampling_plan(i:int,u=Depends(editor),s=Depends(db)):
 try:plans.dispatch_plan(s,u,i);return plans.plan_detail(s,i)
 except (plans.PlanConflict,plans.PlanNotFound) as exc:_plan_error(exc)
@router.post('/sampling-plans/{i}/observations')
def backfill_sampling_plan(i:int,data:ObservationInput,code:str,u=Depends(editor),s=Depends(db)):
 if data.before is None and data.after is None:raise HTTPException(422,'至少提供前测或后测，缺测保留为空，不补零')
 try:plans.backfill(s,u,i,code,data.before,data.after);return plans.plan_detail(s,i)
 except (plans.PlanConflict,plans.PlanRejected,plans.PlanNotFound) as exc:_plan_error(exc)
@router.post('/sampling-plans/{i}/close')
def close_sampling_plan(i:int,u=Depends(editor),s=Depends(db)):
 try:plans.close_plan(s,u,i);return plans.plan_detail(s,i)
 except (plans.PlanConflict,plans.PlanNotFound) as exc:_plan_error(exc)
@router.get('/sampling-plans/{i}/export')
def export_sampling_plan(i:int,u=Depends(current),s=Depends(db)):
 try:payload=plans.export_payload(s,i)
 except plans.PlanNotFound:raise HTTPException(404,'抽检计划不存在')
 return Response(json.dumps(payload,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="sampling-plan-{i}.json"'})
