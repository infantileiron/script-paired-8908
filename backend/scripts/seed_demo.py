import random
from src.config.database import Base,engine,Session
from src.models.entities import User,Pair,SamplingMember,SamplingPlan
from src.services.sampling import build_members,normalize_groups
from src.utils.security import hash_password
DEMO_PLAN='11月续约话术分层抽检（演示）'
def seed():
 Base.metadata.create_all(engine);rng=random.Random(1115)
 with Session() as s:
  for role in ('admin','analyst','viewer'):
   email=role+'@demo.local'
   if not s.query(User).filter_by(email=email).first():s.add(User(email=email,role=role,password=hash_password('Demo123!')))
  for i in range(40):
   code=f'QY-{i+1:03}';before=round(rng.uniform(58,85),1);after=round(min(100,before+rng.gauss(4 if i<20 else 1,5)),1)
   if not s.query(Pair).filter_by(code=code).first():s.add(Pair(code=code,team='企业一组' if i<20 else '企业二组',before=None if i==38 else before,after=None if i==39 else after,note='前后各抽检5次客户沟通的平均得分；窗口均为7日'))
  if not s.query(SamplingPlan).filter_by(name=DEMO_PLAN).first():
   seed_value=1115
   groups=normalize_groups([
    {'team':'企业一组','roster':[f'QY-{i:03d}' for i in range(1,21)],'sample_size':6},
    {'team':'企业二组','roster':[f'QY-{i:03d}' for i in range(21,41)],'sample_size':6},
   ])
   plan=SamplingPlan(name=DEMO_PLAN,seed=seed_value,groups=groups,state='draft',created_at='2026-11-17T00:00:00+00:00',result=None,error='')
   s.add(plan);s.flush()
   for row in build_members(seed_value,groups):s.add(SamplingMember(plan_id=plan.id,**row))
  s.commit();print({'users':s.query(User).count(),'pairs':s.query(Pair).count(),'plans':s.query(SamplingPlan).count()})
if __name__=='__main__':seed()
