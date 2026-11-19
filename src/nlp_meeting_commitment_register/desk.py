"""核对会议承诺、前置条件与工位占用。

排班只采用结构化字段：负责人来自登记角色，工位和班次来自承诺条目。
原话中的人名和日期只作为核对线索，不会被直接写进日程。一个承诺要
真正占用工位，必须同时满足：

* 责任人本人确认（提议、代述、改口中的原方案都不算）；
* 它所在的替代路线已经被明确选定；
* 所有前置交付和到货等条件都已落实；
* 目标班次没有被独立任务占用。

已经由独立任务占用的班次不会被后来的原计划挤掉，只会标记为需要重排。
"""

from .statements import (
    CONFIRMED,
    FACT,
    HOLD,
    PROPOSAL,
    RELAY,
    RETRACTED,
    WITHDRAWN,
    normalize_decision,
)

STATE_UNCOMMITTED = '未承诺'
STATE_WAITING_CHOICE = '等待选择'
STATE_NOT_CHOSEN = '未选中'
STATE_PREPARING = '准备中'
STATE_WAITING_DELIVERY = '等待前置交付'
STATE_SETTLED = '已兑现'
STATE_HELD = '已暂缓'
STATE_NO_STATION = '无需工位'
STATE_RESCHEDULE = '需要重排'
STATE_UNAVAILABLE = '工位不可安排'
STATE_SCHEDULED = '已预排'

_MET_WORDS = {'已确认', '确认', '已到货', '到货', '已满足', '满足', '已收到', '收到', '已交付',
              'confirmed', 'met', 'arrived', 'done', 'true'}
_UNMET_WORDS = {'未确认', '待确认', '承诺', '承诺中', '预计', '待定', '未到货', '未满足',
                'pending', 'promised', 'false'}


def _truthy_status(value):
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().casefold() in {word.casefold() for word in _MET_WORDS}


def _declared_status(value):
    """正文里明确写了“未确认/待确认”的，不得因为同名字段被当成已确认。"""
    return isinstance(value, str) and str(value).strip().casefold() in {
        word.casefold() for word in _UNMET_WORDS} and str(value).strip() not in {'承诺', '承诺中'}


def _role_of(name, people):
    if not name:
        return None
    if name in people:
        value = people[name]
        return value if isinstance(value, str) else None
    if name in people.values():
        return name
    return None


def _entry_list(entries):
    if isinstance(entries, dict):
        result = []
        for key, value in entries.items():
            item = dict(value)
            item.setdefault('id', key)
            result.append(item)
        return result
    return list(entries)


class CommitmentRegister:
    """登记谁以什么语气、代表哪个责任方说了什么。"""

    def __init__(self, people, entries):
        self.people = people or {}
        self.entries = {entry['id']: entry for entry in _entry_list(entries)}

    def effective_decision(self, item):
        """改口后的说法优先于改口前的说法。"""
        decision = normalize_decision(item.get('decision'))
        revised = normalize_decision(item.get('revised_decision') or item.get('after_decision'))
        return revised if revised else decision

    def owner(self, item):
        return item.get('owner') or _role_of(item.get('speaker'), self.people)

    def agreed(self, item):
        """责任人本人确认才成立；代述和提议都不算。"""
        if item.get('agreed') is True and 'decision' not in item:
            return True
        decision = self.effective_decision(item)
        if decision != CONFIRMED:
            return False
        owner = self.owner(item)
        speaker = item.get('speaker')
        if item.get('confirmed_by'):
            confirmed_by = item['confirmed_by']
            if not isinstance(confirmed_by, (list, tuple, set)):
                confirmed_by = [confirmed_by]
            if owner and any(self.people.get(name) == owner or name == owner for name in confirmed_by):
                return True
        return bool(owner) and _role_of(speaker, self.people) == owner

    def decision_label(self, item):
        decision = self.effective_decision(item)
        return {
            CONFIRMED: '本人确认',
            PROPOSAL: '提议',
            RELAY: '代述',
            RETRACTED: '明确改口',
            WITHDRAWN: '撤回',
            HOLD: '暂缓',
            FACT: '事实陈述',
        }.get(decision, decision or '未记录')

    def reason(self, item):
        if self.agreed(item):
            return '责任人本人确认'
        decision = self.effective_decision(item)
        if decision in {RETRACTED, WITHDRAWN}:
            return '原话已明确改口或撤回，需按改口后的说法重新核对'
        if decision == RELAY:
            return '他人代述，尚未得到责任人本人确认'
        if decision == PROPOSAL:
            return '只是提议或附条件安排，尚未成为承诺'
        if decision == HOLD:
            return '已明确暂缓，先不推进'
        owner = self.owner(item)
        speaker_role = _role_of(item.get('speaker'), self.people)
        if owner and speaker_role and speaker_role != owner:
            return f'发言人属于{speaker_role}，不能代表{owner}确认'
        return '缺少责任人本人确认'

    def evidence_for(self, item):
        evidence = item.get('evidence')
        values = []
        if isinstance(evidence, list):
            values.extend(evidence)
        elif evidence:
            values.append(evidence)
        if item.get('quote'):
            values.append({'quote': item['quote']})
        elif item.get('text') and item.get('source') is None and item.get('record_id') is None:
            values.append({'quote': item['text']})
        if item.get('record_id') or item.get('source'):
            values.append({'source': item.get('source') or item.get('record_id')})
        result = []
        for value in values:
            if isinstance(value, str):
                result.append({'quote': value})
            elif isinstance(value, dict):
                entry = dict(value)
                if entry.get('record_id') and not entry.get('source'):
                    entry['source'] = entry['record_id']
                result.append(entry)
        return result


class ConditionCheck:
    """到货、交付等条件；承诺到货不等于已经到货。

    条件可以写在承诺条目的 ``conditions`` 或 ``condition`` 字段里，也可以
    单独放在 ``case['conditions']`` 中按编号引用。只有状态为已确认/已到货
    之类的条件才算满足。
    """

    def __init__(self, case, delivered):
        self.case = case
        self.delivered = set(delivered or ())
        conditions = case.get('conditions') or case.get('arrivals') or {}
        if isinstance(conditions, list):
            self.conditions = {}
            for value in conditions:
                if isinstance(value, dict):
                    key = value.get('id') or value.get('condition') or value.get('text')
                    if key:
                        self.conditions[key] = value
        else:
            self.conditions = dict(conditions)
        self.confirmed = set(case.get('confirmed_conditions') or ())
        self.confirmed.update(case.get('confirmed_arrivals') or ())
        self.confirmed.update(case.get('delivered') or ())

    def _values(self, item):
        raw = item.get('conditions')
        if raw is None:
            raw = item.get('condition')
        if raw is None and item.get('arrival') is not None:
            raw = [item['arrival']]
        if raw is None:
            return []
        return raw if isinstance(raw, list) else [raw]

    def for_item(self, item):
        results = []
        seen = set()
        for raw in self._values(item):
            if isinstance(raw, str):
                key, local = raw, {}
            elif isinstance(raw, dict):
                key = raw.get('id') or raw.get('condition') or raw.get('text') or raw.get('kind')
                local = dict(raw)
            else:
                continue
            if key in seen:
                continue
            seen.add(key)
            details = {}
            if key in self.conditions:
                value = self.conditions[key]
                details.update(value if isinstance(value, dict) else {'status': value})
            details.update(local)
            status = details.get('status', details.get('state'))
            met = details.get('met')
            if met is None:
                met = _truthy_status(status)
            if not met and (str(key) in self.confirmed or details.get('confirmed') is True):
                met = True
            if _declared_status(status) and str(key) not in self.confirmed:
                # 正文明确写“未确认/待确认”的，不因同名的已交付记录被当成已到货。
                met = False
            if not met and str(key) in self.delivered and not _declared_status(status):
                met = True
            kind = details.get('kind') or details.get('type')
            if not kind:
                text = ' '.join(str(details.get(field) or '') for field in ('text', 'note', 'label'))
                kind = '到货' if any(word in text for word in ('到货', '收货', '供货', '发货')) else '条件'
            results.append({
                'id': key,
                'met': bool(met),
                'status': status,
                'kind': kind,
                # 到货等条件默认归到承诺的责任人，除非原话或登记指定了别人。
                'owner': details.get('owner') or item.get('owner'),
                'quote': details.get('quote'),
                'source': details.get('source') or details.get('record_id'),
            })
        return results

    def unmet(self, item):
        return [condition for condition in self.for_item(item) if not condition['met']]


class DependencyCheck:
    def __init__(self, register, delivered, conditions=None, prerequisites=None):
        self.register = register
        self.delivered = set(delivered or ())
        self.conditions = conditions
        self.prerequisites = prerequisites or (lambda item: item.get('prerequisites') or ())

    def waiting_for(self, item):
        waiting = []
        for required in self.prerequisites(item):
            predecessor = self.register.entries.get(required)
            if predecessor is None:
                waiting.append(required)
                continue
            if not (self.register.agreed(predecessor) and required in self.delivered):
                waiting.append(required)
        return waiting

    def waiting_conditions(self, item):
        if not self.conditions:
            return []
        return self.conditions.unmet(item)

    def held_predecessors(self, item):
        result = []
        for required in self.prerequisites(item):
            predecessor = self.register.entries.get(required)
            if predecessor is not None and self.register.effective_decision(predecessor) == HOLD:
                result.append(required)
        return result

    def root_blocking(self, item_id, seen=None):
        """沿着前置链找出真正卡住工作的那一项。

        交付本身尚未兑现的前置是根因；如果前置已经交付、只是还没被确认，
        则前置本身是根因；如果前置的到货条件没落实，到货条件才是根因。
        """
        seen = set() if seen is None else seen
        if item_id in seen:
            return item_id
        seen.add(item_id)
        item = self.register.entries.get(item_id)
        if item is None:
            return item_id
        for required in self.prerequisites(item):
            predecessor = self.register.entries.get(required)
            if predecessor is None:
                return required
            for condition in self.waiting_conditions(predecessor):
                return condition['id']
            if self.register.effective_decision(predecessor) == HOLD:
                return required
            if not self.register.agreed(predecessor) or required not in self.delivered:
                return required
            deeper = self.root_blocking(required, seen)
            if deeper:
                return deeper
        for condition in self.waiting_conditions(item):
            return condition['id']
        return None


class RoutePlan:
    """同一交付可能有两条可替代路线；未选定前两条都保留进度。"""

    def __init__(self, entries, case):
        entries = _entry_list(entries)
        self.by_id = {entry['id']: entry for entry in entries}
        self.options = {}
        self.selected = {}
        self._collect(entries, case)

    def _group(self, key):
        return self.options.setdefault(key, [])

    def _add(self, key, identifier):
        if identifier and identifier not in self._group(key):
            self._group(key).append(identifier)

    def _choose(self, key, identifier):
        if identifier and identifier in self.by_id and identifier in self.options.get(key, []):
            self.selected[key] = identifier

    def _collect(self, entries, case):
        groups = case.get('route_groups') or case.get('alternatives') or []
        if isinstance(groups, dict):
            groups = [{'id': key, **(value if isinstance(value, dict) else {'options': value})}
                      for key, value in groups.items()]
        for group in groups:
            if not isinstance(group, dict):
                continue
            key = group.get('id') or group.get('group') or group.get('delivery') or group.get('name')
            if not key:
                continue
            option_ids = group.get('options') or group.get('routes') or group.get('commitments') or []
            if isinstance(option_ids, dict):
                option_ids = list(option_ids)
            for identifier in option_ids:
                self._add(key, identifier)
            chosen = group.get('selected') or group.get('chosen') or group.get('selected_route')
            self._choose(key, chosen)

        selections = case.get('selections') or case.get('selected_routes') or {}
        if isinstance(selections, dict):
            for key, identifier in selections.items():
                self._add(key, identifier)
                self._choose(key, identifier)

        for item in entries:
            identifier = item['id']
            key = item.get('route_group') or item.get('alternative_group')
            if item.get('alternative_to'):
                original = item['alternative_to']
                key = item.get('route_group') or f'alt:{original}'
                self._add(key, original)
            if item.get('delivery') and (item.get('route') or item.get('option') or item.get('role') == 'route'):
                key = item.get('route_group') or f'delivery:{item["delivery"]}'
            if key:
                self._add(key, identifier)
            for field in ('selected_route', 'chosen_route', 'selection'):
                if item.get(field):
                    self._choose(key or f'delivery:{item[field]}', item[field])
            if item.get('route_group') and any(item.get(flag) for flag in ('selected', 'chosen', 'active')):
                self._choose(item['route_group'], identifier)

        for key in list(self.options):
            self.options[key] = list(dict.fromkeys(self.options[key]))
            if len(self.options[key]) < 2:
                self.options.pop(key)
                self.selected.pop(key, None)

    def group_for(self, identifier):
        for key, options in self.options.items():
            if identifier in options:
                return key
        return None

    def status(self, identifier):
        key = self.group_for(identifier)
        if key is None:
            return 'single', None
        selected = self.selected.get(key)
        if selected is None:
            return 'waiting-choice', key
        if selected == identifier:
            return 'selected', key
        return 'not-chosen', key

    def selection(self, key):
        return self.selected.get(key)

    def options_for(self, key):
        return list(self.options.get(key, ()))

    def replaces(self, identifier):
        """选定这条路线时要替换掉的前置；用于“在原计划上换掉一项前置”。"""
        item = self.by_id.get(identifier) or {}
        raw = item.get('replaces') or item.get('replaces_prerequisites') or ()
        if isinstance(raw, str):
            return {raw}
        return set(raw)

    def prerequisites(self, item):
        """当前实际生效的前置。

        两条路线都没选定时，各自的前置都保留；路线选定后，只有被选中路线
        的前置算数，它声明替换掉的原计划前置会从等待链里去掉。
        """
        values = list(item.get('prerequisites') or ())
        route_status, route_group = self.status(item['id'])
        if route_status == 'not-chosen':
            return values
        removed = self.replaces(item['id'])
        return [value for value in values if value not in removed]


class StationSchedule:
    def __init__(self, stations, bookings):
        self.stations = {station: list(slots) for station, slots in (stations or {}).items()}
        self.bookings = {}
        for booking in bookings or ():
            if booking.get('cancelled'):
                continue
            key = (booking.get('station'), booking.get('slot'))
            self.bookings[key] = booking
        self.held = set(self.bookings)

    def conflict(self, item):
        key = (item.get('station'), item.get('slot'))
        booking = self.bookings.get(key)
        if not booking:
            return None
        references = {booking.get('commitment'), booking.get('task'), booking.get('id')}
        if item.get('id') in references:
            return None
        return booking

    def _slot_exists(self, item):
        return item.get('slot') in self.stations.get(item.get('station'), ())

    def reserve(self, item):
        key = (item.get('station'), item.get('slot'))
        if not self._slot_exists(item):
            return STATE_UNAVAILABLE, None
        conflict = self.conflict(item)
        if conflict:
            blocker = conflict.get('task') or conflict.get('id') or conflict.get('commitment') or key
            return STATE_RESCHEDULE, blocker
        if key not in self.bookings:
            self.bookings[key] = {'station': item.get('station'), 'slot': item.get('slot'),
                                  'commitment': item.get('id')}
            self.held.add(key)
        return STATE_SCHEDULED, None

    def release(self, item):
        """撤销本条目自己预占的班次，不动批准记账或别的任务的占用。"""
        key = (item.get('station'), item.get('slot'))
        booking = self.bookings.get(key)
        if booking and booking.get('commitment') == item.get('id'):
            del self.bookings[key]
            self.held.discard(key)

    def consider(self, agreed, waiting, item):
        """保留原有入口，供单独核对一条承诺时使用。"""
        if not agreed:
            return STATE_UNCOMMITTED
        if waiting:
            return STATE_WAITING_DELIVERY
        if not item.get('station'):
            return STATE_NO_STATION
        state, _ = self.reserve(item)
        return state


def _dependents(register):
    mapping = {identifier: [] for identifier in register.entries}
    for identifier, item in register.entries.items():
        for required in item.get('prerequisites') or ():
            if required in mapping and identifier not in mapping[required]:
                mapping[required].append(identifier)
    return mapping


def assess(case, registry):
    entries = _entry_list(case.get('commitments') or [])
    register = CommitmentRegister(registry.get('people'), entries)
    delivered = set(case.get('delivered') or ())
    condition_check = ConditionCheck(case, delivered)
    routes = RoutePlan(entries, case)
    dependencies = DependencyCheck(register, delivered, condition_check, routes.prerequisites)
    schedule = StationSchedule(registry.get('stations'), registry.get('bookings'))
    dependents = _dependents(register)

    rows = []
    for item in entries:
        identifier = item['id']
        waiting = dependencies.waiting_for(item)
        unmet = dependencies.waiting_conditions(item)
        waiting_ids = waiting + [condition['id'] for condition in unmet]
        agreed = register.agreed(item)
        delivered_flag = identifier in delivered
        route_status, route_group = routes.status(identifier)
        blocking = None

        if not agreed:
            state = STATE_HELD if register.effective_decision(item) == HOLD else STATE_UNCOMMITTED
            blocking = identifier
            reason = register.reason(item)
        elif delivered_flag:
            state = STATE_SETTLED
            reason = '该交付已登记兑现，可以结清'
        elif route_status == 'waiting-choice':
            state = STATE_WAITING_CHOICE
            blocking = f'选择:{route_group}'
            reason = '存在可替代路线，尚未明确选定'
        elif route_status == 'not-chosen':
            state = STATE_NOT_CHOSEN
            blocking = routes.selection(route_group)
            reason = '同一交付选择了另一条路线'
        elif dependencies.held_predecessors(item) and item.get('station'):
            held = dependencies.held_predecessors(item)[0]
            state = STATE_RESCHEDULE
            blocking = held
            reason = '前置已明确暂缓，原排期需要重排，且不会继续占用该工位'
        elif item.get('preparation') and not item.get('station'):
            state = STATE_PREPARING
            reason = '替代路线尚未选定，该准备工作两条路线都可以继续推进'
        elif waiting_ids:
            state = STATE_WAITING_DELIVERY
            blocking = waiting[0] if waiting else unmet[0]['id']
            reason = '前置交付或到货条件尚未落实'
        elif not item.get('station'):
            state = STATE_NO_STATION
            reason = '该承诺不需要工位'
        else:
            state, blocker = schedule.reserve(item)
            blocking = blocker
            if state == STATE_SCHEDULED:
                reason = '本人确认、路线已选定且条件已满足，可以排入工位'
            elif state == STATE_RESCHEDULE:
                reason = '目标班次已被独立任务占用，不能挤占，需要重排'
            else:
                reason = '工位或班次不在可用排期表内'

        if state in {STATE_RESCHEDULE, STATE_NOT_CHOSEN}:
            # 不能排入的路线不应继续占着班次，后面的独立任务仍可正常使用。
            schedule.release(item)

        root = dependencies.root_blocking(identifier) if not delivered_flag else None
        if state in {STATE_WAITING_CHOICE, STATE_NOT_CHOSEN, STATE_UNCOMMITTED,
                     STATE_HELD, STATE_RESCHEDULE}:
            root = blocking
        if state == STATE_WAITING_DELIVERY and not root:
            root = blocking

        conditions = condition_check.for_item(item)
        # ready 表示“这条现在就能推进”，和状态保持一致：已兑现的交付只结清，不再占工位。
        ready = state in {STATE_SCHEDULED, STATE_NO_STATION, STATE_PREPARING}
        rows.append({
            'id': identifier,
            'waiting': waiting,
            'state': state,
            'owner': register.owner(item),
            'speaker': item.get('speaker'),
            'decision': register.effective_decision(item),
            'decision_label': register.decision_label(item),
            'settled': bool(delivered_flag),
            'ready': ready,
            'blocking': blocking,
            'root_blocking': root,
            'blocks': list(dependents.get(identifier, ())),
            'conditions': conditions,
            'route': None if route_group is None else {
                'group': route_group,
                'status': route_status,
                'selected': routes.selection(route_group),
                'options': routes.options_for(route_group),
            },
            'evidence': register.evidence_for(item),
            'reason': reason,
        })
    return rows
