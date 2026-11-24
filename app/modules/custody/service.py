from collections import defaultdict
from itertools import groupby
from app.modules.intake.contracts import GATE, canonical
from .transitions import change

SEAL = 'SEAL'
DECLARED = 'OUT_DECLARED'


def replay(events, registry, start, end):
    """Replay a shift in effective order, then settle gate receipts.

    Frozen goods that only carry an ``OUT`` declaration stay in their slot and
    keep accruing exposure: a declaration is not proof that the vehicle left.
    A gate receipt (``GATE``) is that proof.  It frees the slot and stops the
    indoor exposure clock, but while the inspection ticket is still active the
    exit is wrongful (误出闸) and the ticket stays pending.

    A late ``RELEASE``/``CANCEL`` is applied at its effective time.  If that is
    before the exit it is replayed before the gate, so recomputation drops the
    wrongful flag; if it is after the exit the flag stays.  Retrying the same
    gate receipt never settles a lot twice.

    Differences between frozen exit declarations and gate confirmations are
    returned in ``differences`` for the custody (查验占位) page.
    """
    events = [dict(row, kind=canonical(row['kind'])) for row in events]
    states, began, high = {}, {}, {}
    periods, activity, isolated = [], [], []
    late = set()
    for row in sorted(events, key=lambda r: (r['arrival'], r['line'])):
        max_time = high.get(row['lot'], row['time'])
        if row['time'] < max_time:
            late.add(row['event'])
        high[row['lot']] = max(max_time, row['time'])
    declarations, gates, pending = [], [], []
    awaiting, departed, settled = {}, {}, set()
    ordered = sorted((row for row in events if row['time'] < end), key=lambda r: (r['time'], r['line']))
    for moment, grouped in groupby(ordered, key=lambda row: row['time']):
        by_lot = defaultdict(list)
        for row in grouped:
            by_lot[row['lot']].append(row)
        for lot, batch in sorted(by_lot.items()):
            actions = [row for row in batch if row['kind'] != SEAL]
            kinds = [row['kind'] for row in actions]
            # Gate retries at one instant are one settlement, and an exit
            # declaration filed at the same second as its gate receipt is a
            # declaration followed by its confirmation, not a conflict.
            retries = actions and all(row['kind'] == GATE for row in actions)
            paired = (actions and len(actions) <= 2 and kinds.count('OUT') <= 1 and
                      all(kind in ('OUT', GATE) for kind in kinds))
            if len(actions) > 1 and not (retries or paired):
                isolated.extend(dict(row, code='SIMULTANEOUS') for row in batch)
                continue
            for row in sorted(batch, key=lambda row: (row['kind'] != 'OUT', row['kind'] == SEAL, row['line'])):
                was_late = row['event'] in late
                previous = states.get(lot)
                if row['kind'] == GATE:
                    gate(row, lot, moment, previous, was_late, registry, states, began, awaiting, departed,
                         settled, periods, activity, isolated, gates, pending, start)
                    continue
                if row['kind'] == 'OUT':
                    leave(row, lot, moment, previous, was_late, registry, states, began, awaiting,
                          departed, declarations, periods, activity, isolated, start)
                    continue
                if previous is None and departed.get(lot) and row['kind'] in ('RELEASE', 'CANCEL', 'SEAL'):
                    # The goods already left: a thaw/seal arriving later is a
                    # no-op for the slot, and never rewrites the exit record.
                    activity.append(dict(row, late=was_late, before=None, after=None, code=None,
                                         departed=True))
                    continue
                next_state, issue = change(row, states, registry)
                if issue:
                    isolated.append(dict(row, code=issue))
                    if issue not in ('SLOT_UNKNOWN', 'SLOT_AMBIGUOUS'):
                        continue
                if previous is not None and previous != next_state:
                    close(periods, lot, previous, began.pop(lot, (moment, row['line'])), moment, start)
                if previous != next_state:
                    began[lot] = (moment, row['line'])
                if next_state is None:
                    states.pop(lot, None)
                else:
                    states[lot] = next_state
                    if row['kind'] == 'IN':
                        departed.pop(lot, None)
                    track(awaiting, lot, next_state)
                activity.append(dict(row, late=was_late, before=previous, after=next_state, code=issue))
    for lot, state in states.items():
        close(periods, lot, state, began.get(lot, (start, None)), end, start)
    differences = compare(declarations, gates)
    counts = dict(applied=len(activity), late=sum(row['late'] for row in activity),
                  isolated=len(isolated), in_stock=len(states),
                  gates=sum(not row['duplicate'] for row in gates), gate_receipts=len(gates),
                  gate_duplicates=sum(row['duplicate'] for row in gates),
                  declared=sum(row['frozen'] for row in declarations),
                  declared_pending=sum(row['frozen'] and not row['settled'] for row in declarations),
                  confirmed=sum(row['status'] == 'confirmed' for row in gates),
                  wrongful=sum(row['wrongful'] for row in gates),
                  unreported=sum(row['status'] == 'unreported' for row in gates),
                  unknown=sum(row['status'] == 'unknown' for row in gates),
                  pending=len(pending), differences=len(differences))
    return dict(rows=activity, periods=periods, states=states, isolated=isolated,
                gates=gates, exits=gates, gate_receipts=list(gates),
                declarations=declarations, pending=pending, pending_tickets=pending,
                wrongful_exits=[row for row in gates if row['wrongful']],
                unconfirmed=[row for row in declarations if row['frozen'] and not row['settled']],
                differences=differences, discrepancies=differences, exit_differences=differences,
                counts=counts)


def leave(row, lot, moment, previous, was_late, registry, states, began, awaiting,
          departed, declarations, periods, activity, isolated, start):
    """An ``OUT`` frees normal goods; on frozen goods it only declares an exit."""
    if lot not in registry['lots']:
        isolated.append(dict(row, code='LOT_UNKNOWN'))
        return
    if previous is None:
        isolated.append(dict(row, code='INVALID_LIFECYCLE'))
        return
    if previous['ticket'] is not None:
        existing = awaiting.get(lot)
        if existing is not None:
            # A second declaration for the same physical exit keeps the first.
            activity.append(dict(row, late=was_late, before=previous, after=previous, code='DUP_EXIT',
                                 declared=True, frozen=True, declaration=existing['event']))
            return
        declaration = dict(kind='frozen', lot=lot, event=row['event'], time=moment, line=row['line'],
                           slot=previous['slot'], ticket=previous['ticket'], frozen=True,
                           settled=False, settled_by=None, settlement=None, wrongful=False)
        declarations.append(declaration)
        awaiting[lot] = declaration
        # Still physically inside: no period closes and no slot is released.
        activity.append(dict(row, late=was_late, before=previous, after=previous, code=DECLARED,
                             declared=True, frozen=True, declaration=row['event']))
        return
    departed[lot] = 'declared'
    # If a frozen exit was declared and the lot was thawed before leaving, the
    # plain OUT realizes that declaration: it is no longer merely pending.
    realized = awaiting.pop(lot, None)
    if realized is not None:
        settle(realized, row['event'], 'confirmed')
    close(periods, lot, previous, began.pop(lot, (moment, row['line'])), moment, start)
    states.pop(lot, None)
    activity.append(dict(row, late=was_late, before=previous, after=None, code=None,
                         declared=True, frozen=False))


def gate(row, lot, moment, previous, was_late, registry, states, began, awaiting, departed,
         settled, periods, activity, isolated, gates, pending, start):
    """Settle one gate receipt; retries are recorded but never settle twice."""
    if lot not in registry['lots']:
        isolated.append(dict(row, code='LOT_UNKNOWN'))
        return
    declaration = awaiting.get(lot)
    if (row['event'], lot) in settled or (previous is None and departed.get(lot) == 'gate'):
        # The receipt was already settled: keep every state, only note a retry.
        retry(row, lot, moment, previous, was_late, declaration, activity, gates)
        return
    if previous is None:
        if departed.get(lot) == 'declared':
            # The goods had already left on a plain OUT: the gate only confirms.
            settled.add((row['event'], lot))
            departed[lot] = 'gate'
            gates.append(record(row, moment, None, None, None, True, False, 'confirmed'))
            activity.append(activity_row(row, was_late, None, None, True, False, False))
            return
        # The lot never was in stock: the receipt cannot free anything.
        settled.add((row['event'], lot))
        gates.append(record(row, moment, None, None, None, False, False, 'unknown'))
        activity.append(dict(row, late=was_late, before=None, after=None, code='INVALID_LIFECYCLE',
                             gate=True, duplicate=False, declared=False, frozen=False, wrongful=False))
        return
    settled.add((row['event'], lot))
    departed[lot] = 'gate'
    awaiting.pop(lot, None)
    ticket = previous['ticket']
    wrongful = ticket is not None
    close(periods, lot, previous, began.pop(lot, (moment, row['line'])), moment, start)
    states.pop(lot, None)
    declared = declaration is not None
    if declared:
        settle(declaration, row['event'], 'wrongful' if wrongful else 'confirmed', wrongful=wrongful)
    status = 'wrongful' if wrongful else ('confirmed' if declared else 'unreported')
    record_ = record(row, moment, declaration, previous, ticket, declared, wrongful, status)
    gates.append(record_)
    if wrongful:
        # The exit freed the slot, but the inspection ticket is still pending.
        pending.append(dict(lot=lot, ticket=ticket, slot=previous['slot'], time=moment,
                            line=row['line'], event=row['event'], status='pending'))
    activity.append(activity_row(row, was_late, previous, None, declared, bool(ticket), wrongful,
                                 record_['code'], previous['slot'], ticket))


def retry(row, lot, moment, previous, was_late, declaration, activity, gates):
    known = declaration or previous or {}
    ticket = known.get('ticket')
    gates.append(dict(event=row['event'], receipt=row['event'], lot=lot, time=moment, line=row['line'],
                      slot=known.get('slot'), ticket=ticket, declared=declaration is not None,
                      frozen=bool(ticket), wrongful=False, wrong_exit=False, duplicate=True,
                      status='duplicate', code='GATE_DUPLICATE',
                      declaration_event=declaration['event'] if declaration else None))
    activity.append(dict(row, late=was_late, before=previous,
                         after=None if previous is None else previous, code='GATE_DUPLICATE',
                         gate=True, duplicate=True, declared=declaration is not None,
                         frozen=bool(ticket), wrongful=False))


def record(row, moment, declaration, previous, ticket, declared, wrongful, status):
    known = declaration or previous or {}
    codes = {'wrongful': 'WRONGFUL_EXIT', 'unreported': 'GATE_UNREPORTED',
             'unknown': 'INVALID_LIFECYCLE'}
    return dict(event=row['event'], receipt=row['event'], lot=row['lot'], time=moment, line=row['line'],
                slot=known.get('slot'), ticket=ticket, declared=declared, frozen=bool(ticket),
                wrongful=wrongful, wrong_exit=wrongful, duplicate=False, status=status,
                code=codes.get(status), declaration_event=declaration['event'] if declaration else None)


def activity_row(row, was_late, before, after, declared, frozen, wrongful,
                 code=None, slot=None, ticket=None):
    return dict(row, late=was_late, before=before, after=after, code=code, gate=True,
                duplicate=False, declared=declared, frozen=frozen, wrongful=wrongful,
                slot=slot, ticket=ticket)


def settle(declaration, event, settlement, wrongful=False):
    declaration.update(settled=True, settled_by=event, settlement=settlement, wrongful=wrongful)


def track(awaiting, lot, state):
    """Keep a pending frozen exit pointed at the lot's actual slot and ticket."""
    declaration = awaiting.get(lot)
    if declaration is not None:
        declaration.update(slot=state['slot'], ticket=state['ticket'])


def compare(declarations, gates):
    """List differences that need a human decision on the custody page.

    Frozen goods declared out but not yet confirmed by a gate receipt still
    occupy their slot and keep exposure running; a gate-confirmed exit whose
    inspection ticket was still active is a wrongful exit.  An exit confirmed
    by the gate with no matching declaration is listed too, so no movement is
    silently dropped.
    """
    differences = []
    for row in declarations:
        if row['frozen'] and not row['settled']:
            differences.append(dict(kind='declared_not_confirmed', code='EXIT_DECLARED',
                                    label='仅申报未出闸', lot=row['lot'], event=row['event'],
                                    time=row['time'], line=row['line'], slot=row['slot'],
                                    ticket=row['ticket'], frozen=True, occupying=True))
    for row in gates:
        if row['duplicate']:
            continue
        if row['wrongful']:
            differences.append(dict(kind='wrongful_exit', code='WRONGFUL_EXIT', label='误出闸',
                                    lot=row['lot'], event=row['event'], time=row['time'],
                                    line=row['line'], slot=row['slot'], ticket=row['ticket'],
                                    frozen=True, occupying=False))
        elif row['status'] == 'unreported':
            differences.append(dict(kind='unreported_exit', code='GATE_UNREPORTED', label='未申报出闸',
                                    lot=row['lot'], event=row['event'], time=row['time'],
                                    line=row['line'], slot=row['slot'], ticket=row['ticket'],
                                    frozen=False, occupying=False))
    return differences


def close(periods, lot, state, opening, end, start):
    left = max(opening[0], start)
    if left < end:
        periods.append(dict(lot=lot, start=left, end=end, source_line=opening[1], **state))
