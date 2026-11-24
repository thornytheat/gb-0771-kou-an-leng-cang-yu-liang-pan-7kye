from app.utc import second

COLUMNS = {
    'moves': ('event', 'lot', 'kind', 'slot', 'ticket', 'effective', 'received'),
    'doors': ('door', 'signal', 'effective'),
    'temperatures': ('door', 'celsius', 'effective'),
}
MISSING = {'', '-', 'N/A', 'NULL', '未知'}
GATE = 'GATE'
# Gate receipts may arrive under different names; all settle a physical exit.
GATE_ALIASES = frozenset(('GATE', 'GATEOUT', 'GATE_OUT', 'GATEOUTS', 'EXIT', 'GATEPASS', 'GATE_PASS'))
MOVES = frozenset(('IN', 'MOVE', 'OUT', 'HOLD', 'RELEASE', 'CANCEL', 'SEAL')) | GATE_ALIASES


def canonical(kind):
    return GATE if kind in GATE_ALIASES else kind


def normalize(row):
    source = row['source']
    required = set(COLUMNS[source]) - {'slot', 'ticket'}
    if any(row[key] in MISSING for key in required):
        return 'MISSING'
    if source == 'moves':
        row['kind'] = canonical(row['kind'])
        # A gate receipt only needs its lot and effective time; slot and
        # ticket are optional cross-references.
        if row['kind'] not in MOVES or (
                row['kind'] in ('IN', 'MOVE') and row['slot'] in MISSING) or (
                row['kind'] in ('HOLD', 'CANCEL', 'RELEASE', 'SEAL') and row['ticket'] in MISSING):
            return 'MOVE_FIELDS'
    if source == 'doors' and row['signal'] not in ('OPEN', 'CLOSED'):
        return 'SIGNAL'
    try:
        row['time'] = second(row['effective'])
        if source == 'moves':
            row['arrival'] = second(row['received'])
            if row['arrival'] < row['time']:
                return 'TIME'
    except ValueError:
        return 'TIME'
    return None
