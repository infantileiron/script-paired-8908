from sqlalchemy import Column,Integer,String,Float,Boolean,JSON,Text,ForeignKey,UniqueConstraint
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True,nullable=False);password=Column(String(200));role=Column(String(20))
class Pair(Base):
 __tablename__='pairs'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True,nullable=False);team=Column(String(40));before=Column(Float,nullable=True);after=Column(Float,nullable=True);note=Column(Text,default='')
class Evaluation(Base):
 __tablename__='evaluations'
 id=Column(Integer,primary_key=True);name=Column(String(100));team=Column(String(40));state=Column(String(20),default='queued');result=Column(JSON,nullable=True);error=Column(Text,default='')
class Adoption(Base):
 __tablename__='adoptions'
 id=Column(Integer,primary_key=True);evaluation_id=Column(Integer,ForeignKey('evaluations.id'));decision=Column(String(30));note=Column(Text);status=Column(String(20),default='draft')
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40))
class SamplingPlan(Base):
 __tablename__='sampling_plans'
 id=Column(Integer,primary_key=True);name=Column(String(100));seed=Column(Integer,nullable=False);groups=Column(JSON,nullable=False)
 state=Column(String(20),default='draft');result=Column(JSON,nullable=True);error=Column(Text,default='')
 created_at=Column(String(40));dispatched_at=Column(String(40),nullable=True);closed_at=Column(String(40),nullable=True);checksum=Column(String(64),default='')
class SamplingMember(Base):
 __tablename__='sampling_members'
 id=Column(Integer,primary_key=True);plan_id=Column(Integer,ForeignKey('sampling_plans.id'),nullable=False)
 team=Column(String(40),nullable=False);code=Column(String(60),nullable=False);selection_key=Column(String(64),nullable=False)
 rank=Column(Integer,nullable=False);selected=Column(Boolean,default=False);inclusion_probability=Column(Float,nullable=False);design_weight=Column(Float,nullable=False)
 __table_args__=(UniqueConstraint('plan_id','code',name='sampling_members_plan_code_key'),)
class SamplingDispatch(Base):
 __tablename__='sampling_dispatches'
 id=Column(Integer,primary_key=True);plan_id=Column(Integer,ForeignKey('sampling_plans.id'),nullable=False)
 member_id=Column(Integer,ForeignKey('sampling_members.id'),nullable=False);token=Column(String(40),nullable=False);created_at=Column(String(40))
 __table_args__=(UniqueConstraint('plan_id','member_id',name='sampling_dispatches_plan_member_key'),)
class SamplingObservation(Base):
 __tablename__='sampling_observations'
 id=Column(Integer,primary_key=True);plan_id=Column(Integer,ForeignKey('sampling_plans.id'),nullable=False)
 member_id=Column(Integer,ForeignKey('sampling_members.id'),nullable=False);before=Column(Float,nullable=True);after=Column(Float,nullable=True);updated_at=Column(String(40))
 __table_args__=(UniqueConstraint('plan_id','member_id',name='sampling_observations_plan_member_key'),)
