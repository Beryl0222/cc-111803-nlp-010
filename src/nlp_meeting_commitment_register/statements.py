"""从会议原话中辨认发言的性质。

这里只做“这句话是谁说的、能不能代表责任人”的判断。原话里出现的
人名和日期只作为线索保留，绝不直接变成负责人或排期时段；负责人取
自登记的角色，排期时段必须来自结构化字段。
"""

import re

CONFIRMED = '确认'
PROPOSAL = '建议'
RELAY = '代述'
RETRACTED = '改口'
WITHDRAWN = '撤回'
HOLD = '暂缓'
FACT = '事实'

_DECISION_ALIASES = {
    '确认': CONFIRMED,
    '本人确认': CONFIRMED,
    '亲自确认': CONFIRMED,
    'confirmed': CONFIRMED,
    'agree': CONFIRMED,
    'agreed': CONFIRMED,
    '建议': PROPOSAL,
    '提议': PROPOSAL,
    '备选': PROPOSAL,
    '待定': PROPOSAL,
    'proposal': PROPOSAL,
    'proposed': PROPOSAL,
    '代述': RELAY,
    '转述': RELAY,
    '转告': RELAY,
    '替述': RELAY,
    'relay': RELAY,
    'reported': RELAY,
    '改口': RETRACTED,
    '更正': RETRACTED,
    '变更': RETRACTED,
    'retracted': RETRACTED,
    'retract': RETRACTED,
    '撤回': WITHDRAWN,
    '收回': WITHDRAWN,
    'withdrawn': WITHDRAWN,
    '暂缓': HOLD,
    '暂停': HOLD,
    'hold': HOLD,
    'suspended': HOLD,
}

_SPEECH_ACT = {
    CONFIRMED: '本人确认',
    PROPOSAL: '提议',
    RELAY: '代述',
    RETRACTED: '明确改口',
    WITHDRAWN: '撤回',
    HOLD: '暂缓',
    FACT: '事实陈述',
}

_RETRACT_WORDS = ('改口', '更正', '撤回', '收回', '不算', '不按原', '不按之前', '取消刚才', '重新说')
_RELAY_WORDS = ('转告', '转述', '代述', '让我告诉', '让我转告', '要我转告', '替他', '替她')
_PROPOSAL_WORDS = (
    '建议', '提议', '可以', '不妨', '考虑', '估一下', '先估', '暂缓', '先不',
    '待定', '备选', '两套', '两条路', '另一条路', '如果', '若', '要是',
)
_ROUTE_WORDS = ('另一条路', '另一条路线', '换一条', '换条路', '改用', '改成', '改为',
                '替代路线', '备选路线', '另一方案', '另一个方案')
_REPLACEMENT_WORDS = ('换掉', '替换', '换前置', '更换前置')
_SELF_WORDS = ('我来', '我负责', '我确认', '我保证', '我会', '我安排', '我交付', '我发', '我跟')
_NAME = r'[㐀-鿿]{2,4}'
_RELAY_PATTERN = re.compile(rf'(?P<name>{_NAME})(?:说|表示|称|讲|让我|要我)')
_DATE_PATTERN = re.compile(
    r'周[一二三四五六日天]|星期[一二三四五六日天]|明天|后天|今天|\d{1,2}月\d{1,2}[日号]|\d{1,2}[日号]'
)
_CONDITION_PATTERN = re.compile(
    r'(?:如果|若|要是|只要|等|待)(?P<condition>[^，。；,;]+?)(?:，|,|。|;|；|$)'
)


def normalize_decision(value):
    """把常见的近义说法归一为四种发言性质。"""
    if value is None:
        return None
    return _DECISION_ALIASES.get(str(value).strip(), str(value).strip())


def date_mentions(text):
    """原话中的日期只是线索，不能直接作为工位时段。"""
    if not isinstance(text, str):
        return []
    return _DATE_PATTERN.findall(text)


def mentions(text, people=None):
    """列出原话中出现的人名或责任方；不代表任何排期授权。"""
    if not isinstance(text, str):
        return []
    people = people or {}
    candidates = list(people) + [role for role in people.values() if isinstance(role, str)]
    found = [name for name in dict.fromkeys(candidates) if name and name in text]
    for match in _RELAY_PATTERN.finditer(text):
        name = match.group('name')
        if name not in found:
            found.append(name)
    return found


def _has_any(text, words):
    return any(word in text for word in words)


def classify_text(text, speaker=None, people=None, owner=None):
    """根据原话判断发言性质。

    返回 ``decision``、``owner``、``reason`` 等字段。``decision`` 只表示
    这句话本身的性质：

    * ``确认``：责任人本人确认；
    * ``建议``：提议、条件式的“如果……再……”或备选路线；
    * ``代述``：替别人转述，必须再等本人确认；
    * ``改口``：明确改变此前说法；是否重新确认看 ``revised_decision``。

    原话里提到的人名和日期只放进 ``mentioned`` / ``dates``，不会自动成为
    负责人或排期时段。``route_kind`` 用来区分“在原计划上换掉一项前置”与
    “另提一条备选路线”：前者是 ``replacement``，后者是 ``alternative``。
    """
    text = text or ''
    people = people or {}
    owner_role = owner

    if _has_any(text, _RETRACT_WORDS):
        decision = RETRACTED
        reason = '原话明确改口，需按改口后的说法重新核对'
    elif _has_any(text, _RELAY_WORDS) or _RELAY_PATTERN.search(text):
        decision = RELAY
        reason = '他人代述，未经责任人本人确认'
    elif _has_any(text, ('暂缓', '暂停', '先不', '待定')):
        decision = HOLD
        reason = '明确暂缓，先不推进'
    elif _has_any(text, _PROPOSAL_WORDS):
        decision = PROPOSAL
        if _has_any(text, ('如果', '若', '要是', '只要', '等')):
            reason = '附条件的提议，条件未落实前只是提议'
        else:
            reason = '只是提议或备选路线，尚未成为承诺'
    elif _has_any(text, _SELF_WORDS) or '确认' in text:
        decision = CONFIRMED
        reason = '责任人本人确认'
    else:
        decision = PROPOSAL
        reason = '没有本人确认的表述，按提议处理'

    mentioned = mentions(text, people)
    resolved_owner = owner_role
    if not resolved_owner:
        if decision == RELAY:
            for name in mentioned:
                if name in people:
                    resolved_owner = people[name]
                    break
            if resolved_owner is None:
                resolved_owner = next((name for name in mentioned if name in people.values()), None)
        elif speaker in people:
            resolved_owner = people[speaker]
    if decision == RELAY and resolved_owner is None:
        resolved_owner = next((name for name in mentioned if name in people.values()), None)

    speaker_role = people.get(speaker)
    if decision == CONFIRMED and owner_role and speaker_role and speaker_role != owner_role:
        decision = RELAY
        reason = '发言人属于其他责任方，不能代替责任人确认'

    conditions = [match.group('condition').strip() for match in _CONDITION_PATTERN.finditer(text)]
    return {
        'decision': decision,
        'label': _SPEECH_ACT.get(decision, decision),
        'owner': resolved_owner,
        'speaker': speaker,
        'reason': reason,
        'mentioned': mentioned,
        'dates': date_mentions(text),
        'conditions': [value for value in conditions if value],
        'route_kind': ('replacement' if _has_any(text, _REPLACEMENT_WORDS)
                       else 'alternative' if _has_any(text, _ROUTE_WORDS) else None),
        'self_confirmed': decision == CONFIRMED and (owner_role in (None, speaker_role)) and bool(speaker_role),
    }


def classify_statement(statement, people=None):
    """对一条结构化发言（含 speaker/text）做同样的判断。"""
    if not isinstance(statement, dict):
        raise TypeError('发言必须是对象')
    people = people or {}
    explicit = normalize_decision(statement.get('decision'))
    result = classify_text(
        statement.get('text') or statement.get('quote') or '',
        speaker=statement.get('speaker'),
        people=people,
        owner=statement.get('owner'),
    )
    if explicit:
        result['decision'] = explicit
        result['label'] = _SPEECH_ACT.get(explicit, explicit)
        result['reason'] = '结构化记录为' + _SPEECH_ACT.get(explicit, explicit)
        result['self_confirmed'] = explicit == CONFIRMED and statement.get('speaker') in people and (
            statement.get('owner') in (None, people.get(statement.get('speaker'))))
    if statement.get('revised_decision'):
        result['revised_decision'] = normalize_decision(statement['revised_decision'])
    if statement.get('route_kind'):
        result['route_kind'] = statement['route_kind']
    return result
