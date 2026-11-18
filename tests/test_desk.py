from pathlib import Path

from nlp_meeting_commitment_register.desk_cli import load_case


def test_proposal_and_missing_delivery_do_not_reserve_station():
    fixture = Path(__file__).parents[1] / 'fixtures'
    output = load_case(fixture / 'work.json', fixture / 'business.json')
    assert output[2]['waiting'] == ['ORDER']
    assert output[2]['state'] == '等待前置交付'
    assert output[3]['state'] == '未承诺'
    assert output[4]['state'] == '已预排'
