class CommitmentRegister:
    def __init__(self, people, entries):
        self.people = people
        self.entries = {entry['id']: entry for entry in entries}

    def agreed(self, item):
        return item['decision'] == '确认' and self.people.get(item['speaker']) == item['owner']


class DependencyCheck:
    def __init__(self, register, delivered):
        self.register = register
        self.delivered = set(delivered)

    def waiting_for(self, item):
        waiting = []
        for required in item['prerequisites']:
            predecessor = self.register.entries[required]
            if not (self.register.agreed(predecessor) and required in self.delivered):
                waiting.append(required)
        return waiting


class StationSchedule:
    def __init__(self, stations, bookings):
        self.stations = stations
        self.held = {(b['station'], b['slot']) for b in bookings if not b['cancelled']}

    def consider(self, agreed, waiting, item):
        if not agreed:
            return '未承诺'
        if waiting:
            return '等待前置交付'
        if not item.get('station'):
            return '无需工位'
        station, slot = item['station'], item['slot']
        key = station, slot
        if slot not in self.stations[station] or key in self.held:
            return '工位不可安排'
        self.held.add(key)
        return '已预排'


def assess(case, registry):
    register = CommitmentRegister(registry['people'], case['commitments'])
    dependencies = DependencyCheck(register, case['delivered'])
    schedule = StationSchedule(registry['stations'], registry['bookings'])
    rows = []
    for item in case['commitments']:
        waiting = dependencies.waiting_for(item)
        rows.append({'id': item['id'], 'waiting': waiting, 'state': schedule.consider(register.agreed(item), waiting, item)})
    return rows
