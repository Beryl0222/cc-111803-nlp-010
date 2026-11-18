import json
from pathlib import Path

def load_facts(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def summarize(facts):
    taken = {(entry['station'], entry['slot']) for entry in facts['bookings'] if not entry['cancelled']}
    return {station: [slot for slot in slots if (station, slot) not in taken]
            for station, slots in facts['stations'].items()}
