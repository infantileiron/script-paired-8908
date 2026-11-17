import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

import numpy as np
from scipy import stats

from src.constants.enums import TEAMS
from src.services.analysis import evaluate as paired_evaluate

SELECTION_RULE = (
    '两组名册分别按 code 排序后，以 sha256(seed|team|code) 升序排列，'
    '每组前 n_h 名入选；组内不放回，名册行序不影响入选名单'
)


class SamplingError(Exception):
    """业务规则错误，由路由转换为 422。"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def selection_key(seed: int, team: str, code: str) -> str:
    payload = json.dumps([int(seed), team, code], ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def dispatch_token(plan_id: int, seed: int, team: str, code: str) -> str:
    payload = f'dispatch|{int(plan_id)}|{int(seed)}|{team}|{code}'
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]


def normalize_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """校验并规范化两组完整名册、抽样额；名册固定排序，保证行序不影响抽样。"""
    if len(groups) != 2:
        raise SamplingError('必须同时登记企业一组和企业二组的完整名册')
    by_team: dict[str, dict[str, Any]] = {}
    for raw in groups:
        team = str(raw.get('team', '')).strip()
        if team not in TEAMS:
            raise SamplingError('业务组只能是企业一组或企业二组')
        if team in by_team:
            raise SamplingError(f'{team}名册重复登记')
        roster = [str(code).strip() for code in raw.get('roster', [])]
        if any(not code for code in roster):
            raise SamplingError(f'{team}名册存在空编号')
        if any(len(code) > 60 for code in roster):
            raise SamplingError(f'{team}名册存在超过60字符的顾问编号')
        if len(set(roster)) != len(roster):
            raise SamplingError(f'{team}名册存在重复顾问编号')
        if len(roster) < 1:
            raise SamplingError(f'{team}名册不能为空')
        try:
            sample_size = int(raw.get('sample_size'))
        except (TypeError, ValueError):
            raise SamplingError(f'{team}抽样额必须是整数')
        if sample_size < 1:
            raise SamplingError(f'{team}抽样额至少为1')
        if sample_size > len(roster):
            raise SamplingError(f'{team}抽样额不能超过该组名册人数')
        roster = sorted(roster)
        by_team[team] = {
            'team': team,
            'roster': roster,
            'population': len(roster),
            'sample_size': sample_size,
            'inclusion_probability': sample_size / len(roster),
            'design_weight': len(roster) / sample_size,
            'fpc': 1 - sample_size / len(roster),
            'selection_rule': SELECTION_RULE,
        }
    if set(by_team) != set(TEAMS):
        raise SamplingError('必须同时登记企业一组和企业二组的完整名册')
    seen: set[str] = set()
    for team in TEAMS:
        for code in by_team[team]['roster']:
            if code in seen:
                raise SamplingError(f'顾问编号跨组重复：{code}')
            seen.add(code)
    return [by_team[team] for team in TEAMS]


def build_members(seed: int, groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按稳定哈希键在组内不放回抽取；返回完整名册及入选标记。"""
    rows: list[dict[str, Any]] = []
    for group in groups:
        keyed = sorted(
            (selection_key(seed, group['team'], code), code)
            for code in group['roster']
        )
        for rank, (key, code) in enumerate(keyed, start=1):
            rows.append({
                'team': group['team'],
                'code': code,
                'selection_key': key,
                'rank': rank,
                'selected': rank <= group['sample_size'],
                'inclusion_probability': group['inclusion_probability'],
                'design_weight': group['design_weight'],
            })
    return rows


def _observation_reason(observation: dict[str, Any] | None) -> str:
    if not observation:
        return '未回填'
    before = observation.get('before')
    after = observation.get('after')
    if before is None and after is None:
        return '前后测均未回填'
    if before is None:
        return '缺少前测'
    if after is None:
        return '缺少后测'
    return ''


def _group_config(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        'team': group['team'],
        'population': group['population'],
        'sample_size': group['sample_size'],
        'inclusion_probability': group['inclusion_probability'],
        'design_weight': group['design_weight'],
        'fpc': group['fpc'],
        'selection_rule': group['selection_rule'],
    } for group in groups]


def build_frozen_result(
    seed: int,
    groups: list[dict[str, Any]],
    members: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """结案时构建冻结结果。缺测、未入选回填、名单损坏或组内不足均返回失败。"""
    obs_by_code = {str(row['code']): row for row in observations}
    selected = [member for member in members if member['selected']]
    selected_codes = {member['code'] for member in selected}
    unexpected = sorted(set(obs_by_code) - selected_codes)

    missing: list[dict[str, Any]] = []
    integrity: list[str] = []
    group_rows: list[dict[str, Any]] = []
    for group in groups:
        team_members = sorted(
            (member for member in members if member['team'] == group['team']),
            key=lambda member: member['rank'],
        )
        team_selected = [member for member in team_members if member['selected']]
        if len(team_members) != group['population']:
            integrity.append(
                f"{group['team']}名册人数 {len(team_members)} 与登记人数 {group['population']} 不一致"
            )
        if len(team_selected) != group['sample_size']:
            integrity.append(
                f"{group['team']}入选人数 {len(team_selected)} 与抽样额 {group['sample_size']} 不一致"
            )
        rows: list[dict[str, Any]] = []
        for member in team_selected:
            observation = obs_by_code.get(member['code'])
            reason = _observation_reason(observation)
            if reason:
                missing.append({
                    'team': member['team'],
                    'code': member['code'],
                    'reason': reason,
                })
                continue
            before = float(observation['before'])
            after = float(observation['after'])
            rows.append({
                'team': member['team'],
                'code': member['code'],
                'before': before,
                'after': after,
                'difference': after - before,
                'inclusion_probability': member['inclusion_probability'],
                'design_weight': member['design_weight'],
            })
        group_rows.append({
            'team': group['team'],
            'population': group['population'],
            'sample_size': group['sample_size'],
            'selected': len(team_selected),
            'complete': len(rows),
            'inclusion_probability': group['inclusion_probability'],
            'design_weight': group['design_weight'],
            'fpc': group['fpc'],
            'rows': rows,
        })

    insufficient = [{
        'team': row['team'],
        'population': row['population'],
        'sample_size': row['sample_size'],
        'complete': row['complete'],
        'reason': f"完整回填 {row['complete']} 份，少于2份，无法估计组内方差",
    } for row in group_rows if row['complete'] < 2]

    population = {
        'total': sum(group['population'] for group in groups),
        'groups': [{
            'team': group['team'],
            'population': group['population'],
            'sample_size': group['sample_size'],
            'inclusion_probability': group['inclusion_probability'],
            'design_weight': group['design_weight'],
            'fpc': group['fpc'],
        } for group in groups],
    }
    sample = {
        'selected': len(selected),
        'complete': sum(row['complete'] for row in group_rows),
        'groups': [{
            'team': row['team'],
            'selected': row['selected'],
            'complete': row['complete'],
        } for row in group_rows],
    }

    if missing or insufficient or unexpected or integrity:
        reasons: list[str] = []
        if missing:
            reasons.append(f'{len(missing)} 位入选顾问缺测')
        if insufficient and not missing:
            reasons.append('；'.join(item['reason'] for item in insufficient))
        elif insufficient:
            reasons.append('组内完整回填不足2份，无法估计方差')
        if unexpected:
            reasons.append(f"发现 {len(unexpected)} 条未入选顾问回填")
        if integrity:
            reasons.append('；'.join(integrity))
        message = '结案失败：' + '；'.join(reasons) + '。缺测不补零、不换人，本计划已冻结为失败。'
        diagnostics = [{
            'team': row['team'],
            'population': row['population'],
            'sample_size': row['sample_size'],
            'selected': row['selected'],
            'complete': row['complete'],
            'population_share': row['population'] / max(1, sum(item['population'] for item in group_rows)),
            'contributes': False,
            'reason': '缺测或组内样本不足，未产生总体估计贡献',
        } for row in group_rows]
        return {
            'outcome': 'failed',
            'frozen': True,
            'message': message,
            'missing': missing,
            'insufficient_groups': insufficient,
            'unexpected_observations': unexpected,
            'integrity_issues': integrity,
            'population': population,
            'sample': sample,
            'design_based': None,
            'paired_test': None,
            'group_contributions': diagnostics,
            'groups': _group_config(groups),
        }

    total_population = sum(row['population'] for row in group_rows)
    estimate = 0.0
    variance = 0.0
    degrees_of_freedom = 0
    contributions: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in group_rows:
        values = np.asarray([item['difference'] for item in row['rows']], dtype=float)
        n = row['complete']
        population_h = row['population']
        mean_h = float(values.mean())
        sample_variance = float(values.var(ddof=1))
        fpc = 1 - n / population_h
        share = population_h / total_population
        variance_component = share * share * fpc * sample_variance / n
        standard_error_h = math.sqrt(max(0.0, fpc * sample_variance / n))
        t_h = float(stats.t.ppf(0.975, n - 1))
        estimate += share * mean_h
        variance += variance_component
        degrees_of_freedom += n - 1
        if fpc == 0:
            warnings.append(f"{row['team']}为全查（n_h=N_h），该组不贡献抽样方差。")
        contributions.append({
            'team': row['team'],
            'population': population_h,
            'sample_size': row['sample_size'],
            'complete': n,
            'population_share': share,
            'inclusion_probability': n / population_h,
            'design_weight': population_h / n,
            'fpc': fpc,
            'stratum_mean': mean_h,
            'stratum_sample_variance': sample_variance,
            'stratum_standard_error': standard_error_h,
            'stratum_ci95': [mean_h - t_h * standard_error_h, mean_h + t_h * standard_error_h],
            'estimate_contribution': share * mean_h,
            'variance_component': variance_component,
            'standard_error_component': math.sqrt(max(0.0, variance_component)),
            'variance_share': None,
        })
        pairs.extend(row['rows'])

    standard_error = math.sqrt(max(0.0, variance))
    t_critical = float(stats.t.ppf(0.975, degrees_of_freedom))
    interval = [estimate - t_critical * standard_error, estimate + t_critical * standard_error]
    if variance == 0:
        warnings.append('两层组内方差均为0，区间退化为点估计。')
    for item in contributions:
        item['variance_share'] = (item['variance_component'] / variance) if variance > 0 else 0.0

    design_based = {
        'estimator': '分层简单随机抽样（组内不放回）的 Horvitz-Thompson 均值',
        'target': '全体顾问的前后测平均改善（后测−前测）',
        'estimate': estimate,
        'standard_error': standard_error,
        'confidence_level': 0.95,
        'ci95': interval,
        'df': degrees_of_freedom,
        'fpc_applied': True,
        'population_total': total_population,
        'sample_total': sum(row['complete'] for row in group_rows),
        'formula': (
            '估计=Σ_h (N_h/N)·ȳ_h=(1/N)·Σ_hΣ_i w_i·d_i，w_i=N_h/n_h；'
            'Var=Σ_h (N_h/N)²·(1−n_h/N_h)·s_h²/n_h；'
            '95%CI=估计±t(0.975, df)·SE，df=Σ(n_h−1)'
        ),
        'group_contributions': contributions,
        'pairs': pairs,
        'warnings': warnings,
        'note': '总体估计使用入样概率与有限总体修正；未入选顾问不进入计算，缺测不补零、不换人。',
    }

    paired_rows = [{
        'code': item['code'],
        'before': item['before'],
        'after': item['after'],
    } for item in pairs]
    try:
        paired = paired_evaluate(paired_rows)
        paired_test: dict[str, Any] = {
            'available': True,
            'test': '普通配对 t 检验（仅入选顾问、未加权）',
            'n': paired['n'],
            'difference': paired['difference'],
            'sd': paired['sd'],
            't': paired['t'],
            'df': paired['df'],
            'p_value': paired['p_value'],
            'ci95': paired['ci'],
            'cohen_dz': paired['cohen_dz'],
            'permutation_p': paired['permutation_p'],
            'permutation_method': paired['permutation_method'],
            'iterations': paired['iterations'],
            'seed': paired['seed'],
            'warning': paired['warning'],
            'note': (
                '普通配对 t 检验只把入选顾问当作普通样本，未使用分层抽样权重，'
                '不能代表全体顾问；总体结论以 design_based 为准。'
            ),
        }
    except Exception as exc:
        paired_test = {
            'available': False,
            'test': '普通配对 t 检验（仅入选顾问、未加权）',
            'reason': str(exc),
            'note': '普通配对检验不可用不影响分层总体估计。',
        }

    return {
        'outcome': 'done',
        'frozen': True,
        'message': '结案完成，收样已冻结。',
        'population': population,
        'sample': sample,
        'design_based': design_based,
        'group_contributions': contributions,
        'paired_test': paired_test,
        'groups': _group_config(groups),
    }


def frozen_checksum(
    seed: int,
    groups: list[dict[str, Any]],
    members: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> str:
    payload = {
        'seed': int(seed),
        'groups': groups,
        'members': sorted([{
            'team': member['team'],
            'code': member['code'],
            'selection_key': member['selection_key'],
            'rank': member['rank'],
            'selected': bool(member['selected']),
            'inclusion_probability': member['inclusion_probability'],
            'design_weight': member['design_weight'],
        } for member in members], key=lambda item: item['code']),
        'observations': sorted([{
            'code': row['code'],
            'before': row['before'],
            'after': row['after'],
        } for row in observations], key=lambda item: item['code']),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()
