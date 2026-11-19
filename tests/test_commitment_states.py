import json

import pytest

from nlp_meeting_commitment_register.desk import assess
from nlp_meeting_commitment_register.statements import classify_statement, classify_text

PEOPLE = {'周工': '生产', '王工': '设计', '乔经理': '采购'}
STATIONS = {'stations': {'P1': ['周四上午', '周五上午', '周五下午'], 'P2': ['周五上午']},
            'bookings': [], 'people': PEOPLE}


def _case(commitments, **extra):
    return {'commitments': commitments, 'delivered': [], **extra}


def _row(rows, identifier):
    return next(row for row in rows if row['id'] == identifier)


# --- 语气辨认 -------------------------------------------------------------

def test_conditional_proposal_is_not_a_confirmation():
    result = classify_text('设计先估一下，若供应商承诺周三到货，生产再排周五', speaker='乔经理', people=PEOPLE)
    assert result['decision'] == '建议'
    assert result['owner'] == '采购'
    assert result['self_confirmed'] is False


def test_relay_cannot_confirm_for_someone_else():
    result = classify_statement({'speaker': '乔经理', 'text': '周工说生产可以排周五。'}, PEOPLE)
    assert result['decision'] == '代述'
    assert result['owner'] == '生产'
    assert result['mentioned'] == ['周工', '生产']


def test_explicit_retraction_is_reported_and_revised_decision_wins():
    result = classify_text('我改口，不按之前的周五排。', speaker='周工', people=PEOPLE)
    assert result['decision'] == '改口'
    assert '改口' in result['reason']


def test_own_confirmation_by_responsible_speaker():
    result = classify_text('我确认周三到货。', speaker='乔经理', people=PEOPLE)
    assert result['decision'] == '确认'
    assert result['self_confirmed'] is True


def test_names_and_dates_are_only_hints():
    result = classify_text('乔经理说王工周三交设计稿，周工周五排。', speaker='乔经理', people=PEOPLE)
    assert '周三' in result['dates'] and '周五' in result['dates']
    assert result['self_confirmed'] is False


# --- 承诺与排班 -----------------------------------------------------------

def test_proposal_and_missing_delivery_do_not_reserve_station():
    case = _case([
        {'id': 'DESIGN', 'speaker': '王工', 'owner': '设计', 'decision': '确认'},
        {'id': 'ORDER', 'speaker': '乔经理', 'owner': '采购', 'decision': '确认', 'prerequisites': ['DESIGN']},
        {'id': 'LINE-A', 'speaker': '周工', 'owner': '生产', 'decision': '确认',
         'prerequisites': ['ORDER'], 'station': 'P1', 'slot': '周五下午'},
        {'id': 'LINE-B', 'speaker': '周工', 'owner': '生产', 'decision': '建议',
         'station': 'P1', 'slot': '周五上午'},
    ], delivered=['DESIGN'])
    rows = assess(case, STATIONS)
    assert _row(rows, 'LINE-A')['state'] == '等待前置交付'
    assert _row(rows, 'LINE-A')['blocking'] == 'ORDER'
    assert _row(rows, 'LINE-A')['root_blocking'] == 'ORDER'
    assert _row(rows, 'LINE-B')['state'] == '未承诺'


def test_relayed_statement_does_not_count_as_agreement():
    case = _case([
        {'id': 'LINE', 'speaker': '乔经理', 'owner': '生产', 'decision': '代述',
         'station': 'P1', 'slot': '周五上午'},
    ])
    row = _row(assess(case, STATIONS), 'LINE')
    assert row['state'] == '未承诺'
    assert '代述' in row['reason']


def test_speaker_from_another_role_cannot_commit():
    case = _case([
        {'id': 'ORDER', 'speaker': '周工', 'owner': '采购', 'decision': '确认'},
    ])
    row = _row(assess(case, STATIONS), 'ORDER')
    assert row['state'] == '未承诺'


def test_promised_arrival_is_not_an_arrival():
    case = _case([
        {'id': 'ORDER', 'speaker': '乔经理', 'owner': '采购', 'decision': '确认',
         'conditions': [{'id': 'ARRIVE', 'kind': '到货', 'status': '承诺'}]},
    ])
    row = _row(assess(case, STATIONS), 'ORDER')
    assert row['state'] == '等待前置交付'
    assert row['root_blocking'] == 'ARRIVE'


def test_root_blocker_points_at_the_arrival_decision():
    case = _case([
        {'id': 'DESIGN', 'speaker': '王工', 'owner': '设计', 'decision': '确认'},
        {'id': 'ORDER', 'speaker': '乔经理', 'owner': '采购', 'decision': '确认',
         'prerequisites': ['DESIGN'], 'conditions': [{'id': 'ARRIVE', 'kind': '到货', 'status': '未确认'}]},
        {'id': 'LINE', 'speaker': '周工', 'owner': '生产', 'decision': '确认',
         'prerequisites': ['ORDER'], 'station': 'P1', 'slot': '周五上午'},
    ], delivered=['DESIGN'])
    rows = assess(case, STATIONS)
    assert _row(rows, 'LINE')['root_blocking'] == 'ARRIVE'
    assert _row(rows, 'ORDER')['blocks'] == ['LINE']


# --- 可替代路线 -----------------------------------------------------------

def _route_case(selected=None, **extra):
    commitments = [
        {'id': 'LINE-A', 'speaker': '周工', 'owner': '生产', 'decision': '确认',
         'station': 'P1', 'slot': '周五下午'},
        {'id': 'LINE-B', 'speaker': '周工', 'owner': '生产', 'decision': '确认',
         'station': 'P1', 'slot': '周五上午'},
    ]
    case = _case(commitments, route_groups=[{'id': 'LINE', 'options': ['LINE-A', 'LINE-B']}])
    if selected:
        case['selections'] = {'LINE': selected}
    case.update(extra)
    return case


def test_alternative_routes_wait_for_an_explicit_choice():
    rows = assess(_route_case(), STATIONS)
    assert {_row(rows, 'LINE-A')['state'], _row(rows, 'LINE-B')['state']} == {'等待选择'}
    assert _row(rows, 'LINE-A')['root_blocking'] == '选择:LINE'


def test_only_the_chosen_route_may_take_the_station():
    rows = assess(_route_case(selected='LINE-A'), STATIONS)
    assert _row(rows, 'LINE-A')['state'] == '已预排'
    assert _row(rows, 'LINE-B')['state'] == '未选中'


def test_preparation_work_continues_before_the_choice():
    case = _route_case()
    case['commitments'].append({'id': 'PREP', 'speaker': '周工', 'owner': '生产',
                                'decision': '确认', 'preparation': True})
    row = _row(assess(case, STATIONS), 'PREP')
    assert row['state'] == '准备中'
    assert row['blocking'] is None


def test_selected_route_can_replace_one_prerequisite():
    case = {
        'commitments': [
            {'id': 'ORDER', 'speaker': '乔经理', 'owner': '采购', 'decision': '确认'},
            {'id': 'LINE-A', 'speaker': '周工', 'owner': '生产', 'decision': '确认',
             'prerequisites': ['ORDER'], 'replaces': ['ORDER'],
             'station': 'P1', 'slot': '周五下午', 'route_group': 'D'},
            {'id': 'LINE-B', 'speaker': '周工', 'owner': '生产', 'decision': '确认',
             'station': 'P1', 'slot': '周五上午', 'route_group': 'D'},
        ],
        'selections': {'D': 'LINE-A'},
    }
    rows = assess(case, STATIONS)
    assert _row(rows, 'LINE-A')['state'] == '已预排'
    assert _row(rows, 'LINE-B')['state'] == '未选中'


# --- 后续兑现与工位不被挤占 -----------------------------------------------

def test_delivered_design_settles_while_procurement_needs_rescheduling():
    case = _case([
        {'id': 'DESIGN', 'speaker': '王工', 'owner': '设计', 'decision': '确认'},
        {'id': 'ORDER', 'speaker': '乔经理', 'owner': '采购', 'decision': '暂缓',
         'prerequisites': ['DESIGN'], 'quote': '乔经理尚未确认供货，请暂缓下单。'},
        {'id': 'LINE', 'speaker': '周工', 'owner': '生产', 'decision': '确认',
         'prerequisites': ['ORDER'], 'station': 'P1', 'slot': '周五上午'},
    ], delivered=['DESIGN'])
    rows = assess(case, STATIONS)
    design = _row(rows, 'DESIGN')
    assert design['state'] == '已兑现' and design['settled'] is True
    assert _row(rows, 'ORDER')['state'] == '已暂缓'
    line = _row(rows, 'LINE')
    assert line['state'] == '需要重排'
    assert line['root_blocking'] == 'ORDER'


def test_independent_task_keeps_its_station():
    registry = dict(STATIONS)
    registry['bookings'] = [{'station': 'P1', 'slot': '周五上午', 'task': '独立任务'}]
    case = _case([
        {'id': 'LINE', 'speaker': '周工', 'owner': '生产', 'decision': '确认',
         'station': 'P1', 'slot': '周五上午'},
        {'id': 'OTHER', 'speaker': '周工', 'owner': '生产', 'decision': '确认',
         'station': 'P2', 'slot': '周五上午'},
    ])
    rows = assess(case, registry)
    assert _row(rows, 'LINE')['state'] == '需要重排'
    assert _row(rows, 'LINE')['root_blocking'] == '独立任务'
    assert _row(rows, 'OTHER')['state'] == '已预排'


def test_cancelled_booking_does_not_block():
    registry = dict(STATIONS)
    registry['bookings'] = [{'station': 'P1', 'slot': '周五上午', 'task': '旧任务', 'cancelled': True}]
    case = _case([
        {'id': 'LINE', 'speaker': '周工', 'owner': '生产', 'decision': '确认',
         'station': 'P1', 'slot': '周五上午'},
    ])
    assert _row(assess(case, registry), 'LINE')['state'] == '已预排'


def test_two_routes_both_reserved_request_does_not_steal_each_other():
    rows = assess(_route_case(selected='LINE-A'), STATIONS)
    assert _row(rows, 'LINE-A')['state'] == '已预排'
    assert _row(rows, 'LINE-B')['state'] == '未选中'


def test_audit_fields_include_quote_owner_and_blocker():
    case = _case([
        {'id': 'ORDER', 'speaker': '乔经理', 'owner': '采购', 'decision': '确认',
         'quote': '若供货商确认周三收货，生产再排到周五。', 'source': 'minutes-52',
         'conditions': [{'id': 'ARRIVE', 'kind': '到货', 'status': '承诺'}]},
    ])
    row = _row(assess(case, STATIONS), 'ORDER')
    assert row['owner'] == '采购'
    assert row['blocking'] == 'ARRIVE'
    assert row['evidence'][0]['quote'].startswith('若供货商')
    assert any(item.get('source') == 'minutes-52' for item in row['evidence'])


def test_condition_is_attributed_to_the_committing_owner():
    case = _case([
        {'id': 'ORDER', 'speaker': '乔经理', 'owner': '采购', 'decision': '确认',
         'conditions': [{'id': 'ARRIVE', 'kind': '到货', 'status': '承诺'}]},
    ])
    row = _row(assess(case, STATIONS), 'ORDER')
    assert row['conditions'][0]['owner'] == '采购'
    assert row['conditions'][0]['met'] is False


def test_swapping_a_prerequisite_reads_as_replacement_not_alternative():
    swapped = classify_text('先按另一条路，换掉采购前置，直接用现有夹具。', speaker='周工', people=PEOPLE)
    standby = classify_text('先按另一条路，用 P1 周五上午。', speaker='周工', people=PEOPLE)
    assert swapped['route_kind'] == 'replacement'
    assert standby['route_kind'] == 'alternative'


def test_unknown_prerequisite_is_waiting_not_silently_satisfied():
    case = _case([
        {'id': 'LINE', 'speaker': '周工', 'owner': '生产', 'decision': '确认',
         'prerequisites': ['NOT-REGISTERED'], 'station': 'P1', 'slot': '周五上午'},
    ])
    row = _row(assess(case, STATIONS), 'LINE')
    assert row['state'] == '等待前置交付'
    assert row['root_blocking'] == 'NOT-REGISTERED'
