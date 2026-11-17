import json
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from src.models.entities import (
    SamplingDispatch,
    SamplingMember,
    SamplingObservation,
    SamplingPlan,
)
from src.services.business import audit
from src.services.sampling import (
    build_frozen_result,
    build_members,
    dispatch_token,
    frozen_checksum,
    normalize_groups,
    now_iso,
)


class PlanConflict(Exception):
    """状态冲突，由路由转换为 409。"""


class PlanRejected(Exception):
    """回填资格或输入错误，由路由转换为 422。"""


class PlanNotFound(Exception):
    pass


def plan_groups(plan: SamplingPlan) -> list[dict[str, Any]]:
    return json.loads(json.dumps(plan.groups, ensure_ascii=False))


def _members(s, plan_id: int) -> list[SamplingMember]:
    return s.query(SamplingMember).filter_by(plan_id=plan_id).order_by(
        SamplingMember.team, SamplingMember.rank
    ).all()


def _observations(s, plan_id: int) -> list[SamplingObservation]:
    return s.query(SamplingObservation).filter_by(plan_id=plan_id).order_by(
        SamplingObservation.id
    ).all()


def _dispatches(s, plan_id: int) -> list[SamplingDispatch]:
    return s.query(SamplingDispatch).filter_by(plan_id=plan_id).order_by(
        SamplingDispatch.id
    ).all()


def create_plan(s, actor, data) -> SamplingPlan:
    groups = normalize_groups([group.model_dump() for group in data.groups])
    members = build_members(data.seed, groups)
    plan = SamplingPlan(
        name=data.name,
        seed=data.seed,
        groups=groups,
        state='draft',
        created_at=now_iso(),
        result=None,
        error='',
    )
    s.add(plan)
    s.flush()
    for row in members:
        s.add(SamplingMember(plan_id=plan.id, **row))
    s.commit()
    audit(s, actor, f'登记分层抽检计划 {plan.id}（seed={plan.seed}）')
    return plan


def get_plan(s, plan_id: int, lock: bool = False) -> SamplingPlan:
    query = s.query(SamplingPlan).filter_by(id=plan_id)
    if lock:
        query = query.with_for_update()
    plan = query.first()
    if not plan:
        raise PlanNotFound()
    return plan


def dispatch_plan(s, actor, plan_id: int) -> SamplingPlan:
    plan = get_plan(s, plan_id, lock=True)
    if plan.state in ('done', 'failed'):
        raise PlanConflict('计划已结案冻结，不能再发单')
    members = _members(s, plan_id)
    selected = [member for member in members if member.selected]
    if len(selected) != sum(group['sample_size'] for group in plan_groups(plan)):
        raise PlanConflict('入选名单与抽样额不一致，请重新登记计划')
    existing = {row.member_id: row for row in _dispatches(s, plan_id)}
    created = False
    for member in selected:
        if member.id in existing:
            continue
        s.add(SamplingDispatch(
            plan_id=plan.id,
            member_id=member.id,
            token=dispatch_token(plan.id, plan.seed, member.team, member.code),
            created_at=now_iso(),
        ))
        created = True
    plan.state = 'collecting'
    if not plan.dispatched_at:
        plan.dispatched_at = now_iso()
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        raise PlanConflict('发单并发冲突，原名单与凭据保持不变，请重试')
    if created:
        audit(s, actor, f'发单分层抽检计划 {plan.id}（{len(selected)} 人）')
    else:
        audit(s, actor, f'重试发单分层抽检计划 {plan.id}，沿用原名单，未重复发单')
    return plan


def backfill(s, actor, plan_id: int, code: str, before, after) -> SamplingPlan:
    plan = get_plan(s, plan_id, lock=True)
    if plan.state == 'draft':
        raise PlanConflict('请先发单再逐个回填')
    if plan.state in ('done', 'failed'):
        raise PlanConflict('计划已结案冻结，晚到分数不再接收')
    member = s.query(SamplingMember).filter_by(plan_id=plan_id, code=code).first()
    if not member or not member.selected:
        raise PlanRejected('该顾问未入选本次抽检，拒收回填；不换人、不补录计划外样本')
    timestamp = now_iso()
    values = {
        'plan_id': plan_id,
        'member_id': member.id,
        'before': before,
        'after': after,
        'updated_at': timestamp,
    }
    statement = pg_insert(SamplingObservation).values(**values)
    update = {'updated_at': statement.excluded.updated_at}
    if before is not None:
        update['before'] = statement.excluded.before
    if after is not None:
        update['after'] = statement.excluded.after
    statement = statement.on_conflict_do_update(
        constraint='sampling_observations_plan_member_key', set_=update
    )
    try:
        s.execute(statement)
        s.commit()
    except IntegrityError:
        s.rollback()
        raise PlanConflict('并发回填冲突，请重试；系统不会为同一顾问保留两条记录')
    audit(s, actor, f'回填抽检计划 {plan.id} 顾问 {code}')
    return plan


def close_plan(s, actor, plan_id: int) -> SamplingPlan:
    plan = get_plan(s, plan_id, lock=True)
    if plan.state in ('done', 'failed'):
        s.rollback()
        return plan
    if plan.state != 'collecting':
        s.rollback()
        raise PlanConflict('计划尚未发单收样，不能结案')
    members = _members(s, plan_id)
    observations = _observations(s, plan_id)
    member_by_id = {member.id: member for member in members}
    obs_rows = [{
        'code': member_by_id[row.member_id].code,
        'before': row.before,
        'after': row.after,
    } for row in observations if row.member_id in member_by_id]
    member_rows = [{
        'team': member.team,
        'code': member.code,
        'selection_key': member.selection_key,
        'rank': member.rank,
        'selected': bool(member.selected),
        'inclusion_probability': member.inclusion_probability,
        'design_weight': member.design_weight,
    } for member in members]
    groups = plan_groups(plan)
    result = build_frozen_result(plan.seed, groups, member_rows, obs_rows)
    result['plan'] = {
        'id': plan.id,
        'name': plan.name,
        'seed': plan.seed,
        'dispatched_at': plan.dispatched_at,
        'closed_at': now_iso(),
    }
    result['checksum'] = frozen_checksum(plan.seed, groups, member_rows, obs_rows)
    result['observations'] = sorted(obs_rows, key=lambda row: row['code'])
    plan.result = result
    plan.checksum = result['checksum']
    plan.closed_at = result['plan']['closed_at']
    plan.error = '' if result['outcome'] == 'done' else result['message']
    plan.state = result['outcome']
    s.commit()
    audit(s, actor, f"结案分层抽检计划 {plan.id}：{result['outcome']}")
    return plan


def plan_detail(s, plan_id: int) -> dict[str, Any]:
    plan = get_plan(s, plan_id)
    members = _members(s, plan_id)
    member_by_id = {member.id: member for member in members}
    observations = _observations(s, plan_id)
    dispatches = _dispatches(s, plan_id)
    obs_by_member = {row.member_id: row for row in observations}
    dispatch_by_member = {row.member_id: row for row in dispatches}
    return {
        'id': plan.id,
        'name': plan.name,
        'seed': plan.seed,
        'state': plan.state,
        'created_at': plan.created_at,
        'dispatched_at': plan.dispatched_at,
        'closed_at': plan.closed_at,
        'checksum': plan.checksum,
        'error': plan.error,
        'groups': plan_groups(plan),
        'members': [{
            'id': member.id,
            'team': member.team,
            'code': member.code,
            'selection_key': member.selection_key,
            'rank': member.rank,
            'selected': bool(member.selected),
            'inclusion_probability': member.inclusion_probability,
            'design_weight': member.design_weight,
            'dispatched': member.id in dispatch_by_member,
            'before': obs_by_member[member.id].before if member.id in obs_by_member else None,
            'after': obs_by_member[member.id].after if member.id in obs_by_member else None,
        } for member in members],
        'dispatches': [{
            'member_id': row.member_id,
            'code': member_by_id[row.member_id].code if row.member_id in member_by_id else '',
            'token': row.token,
            'created_at': row.created_at,
        } for row in dispatches],
        'result': plan.result,
    }


def plan_list(s) -> list[dict[str, Any]]:
    return [{
        'id': plan.id,
        'name': plan.name,
        'seed': plan.seed,
        'state': plan.state,
        'created_at': plan.created_at,
        'dispatched_at': plan.dispatched_at,
        'closed_at': plan.closed_at,
        'checksum': plan.checksum,
        'error': plan.error,
        'population': sum(group['population'] for group in plan_groups(plan)),
        'sample_size': sum(group['sample_size'] for group in plan_groups(plan)),
        'groups': [{
            'team': group['team'],
            'population': group['population'],
            'sample_size': group['sample_size'],
            'inclusion_probability': group['inclusion_probability'],
        } for group in plan_groups(plan)],
    } for plan in s.query(SamplingPlan).order_by(SamplingPlan.id.desc()).all()]


def export_payload(s, plan_id: int) -> dict[str, Any]:
    detail = plan_detail(s, plan_id)
    detail['exported_at'] = now_iso()
    detail['export_note'] = (
        '导出包含总体名册、抽样键与排名、各组抽样额、发单凭据、回填记录与冻结结果；'
        '未入选顾问不参与估计，缺测不补零、不换人。'
    )
    return detail
