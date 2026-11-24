from collections import defaultdict
from .contracts import COLUMNS, GATE, normalize
from .csv_rows import rows


def intake(files):
    accepted, isolation, metrics, copies = {}, [], {}, []
    for source, fields in COLUMNS.items():
        parsed, rejected, counts = rows(files[source], fields, source)
        valid, by_event, repeated = [], defaultdict(list), []
        for row in parsed:
            reason = normalize(row)
            if reason:
                rejected.append(dict(row, code=reason))
            elif source == 'moves':
                by_event[row['event']].append(row)
            else:
                valid.append(row)
        for group in by_event.values():
            signatures = {(r['lot'], r['kind'], r['slot'], r['ticket'], r['time']) for r in group}
            # A gate receipt that is retried may carry a new received time; it
            # is still one physical exit for one lot, so settle it once at the
            # earliest arrival instead of isolating it as a contradiction.
            retry = all(row['kind'] == GATE for row in group) and len({row['lot'] for row in group}) == 1
            if len(signatures) > 1 and not retry:
                rejected.extend(dict(row, code='EVENT_CONFLICT') for row in group)
            else:
                # Identical receipts settle once at the earliest arrival; the
                # rest stay as audit duplicates and never settle twice.
                ordered = sorted(group, key=lambda row: (row['arrival'], row['line']))
                valid.append(ordered[0])
                repeated.extend(dict(row, code='DUP_PACKET') for row in ordered[1:])
        accepted[source] = valid
        copies.extend(repeated)
        isolation.extend(rejected)
        counts.update(accepted=len(valid), isolated=len(rejected), duplicates=len(repeated))
        counts['balanced'] = counts['total'] == sum(counts[key] for key in ('accepted','isolated','duplicates','blanks','repeated_headers'))
        metrics[source] = counts
    return dict(tables=accepted, isolated=isolation, copies=copies, counts=metrics)
