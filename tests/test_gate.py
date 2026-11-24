from app.modules.intake.contracts import normalize
from app.pipeline import review
from app.parcels import load_example
from app.utc import second

FILES = dict(load_example('clean'))


def clock(text):
    return second(f'2026-11-21T{text}+08:00')


def pack(moves):
    files = dict(FILES)
    files['moves'] = 'event,lot,kind,slot,ticket,effective,received\n' + '\n'.join(moves) + '\n'
    return files


def run(moves):
    return review(pack(moves))


BASE = [
    'M1,F01,IN,A1,,2026-11-21T08:00:00+08:00,2026-11-21T08:00:00+08:00',
    'M2,F01,HOLD,,H09,2026-11-21T08:05:00+08:00,2026-11-21T08:05:00+08:00',
]


def test_out_declaration_keeps_frozen_goods_occupying():
    result = run(BASE + ['M3,F01,OUT,,,2026-11-21T08:10:00+08:00,2026-11-21T08:10:00+08:00'])
    custody = result['custody']
    assert custody['states'] == {'F01': {'slot': 'A1', 'ticket': 'H09'}}
    assert custody['counts']['declared_pending'] == 1
    assert result['capacity']['counts']['frozen'] == 4
    # Exposure keeps running to the end of the window.
    assert result['custody']['periods'][-1]['end'] == result['window']['end']
    assert [row['kind'] for row in custody['differences']] == ['declared_not_confirmed']


def test_gate_confirmation_frees_slot_and_stops_exposure():
    result = run(BASE + [
        'M3,F01,OUT,,,2026-11-21T08:10:00+08:00,2026-11-21T08:10:00+08:00',
        'G1,F01,GATE,,,2026-11-21T08:20:00+08:00,2026-11-21T08:20:00+08:00',
    ])
    custody = result['custody']
    assert custody['states'] == {}
    assert result['capacity']['counts']['frozen'] == 0
    assert all(row['end'] - row['start'] <= 1200 for row in custody['periods'])
    # The inspection ticket is still pending after the physical exit.
    assert custody['pending'] == [dict(lot='F01', ticket='H09', slot='A1',
                                       time=clock('08:20:00'), line=5, event='G1', status='pending')]
    assert [(row['kind'], row['code']) for row in custody['differences']] == [('wrongful_exit', 'WRONGFUL_EXIT')]


def test_gate_without_declaration_is_still_a_wrongful_exit():
    result = run(BASE + ['G1,F01,GATE,,,2026-11-21T08:20:00+08:00,2026-11-21T08:20:00+08:00'])
    custody = result['custody']
    assert custody['states'] == {}
    assert custody['gates'][0]['declared'] is False and custody['gates'][0]['wrongful']
    assert custody['counts']['wrongful'] == 1


def test_late_release_before_exit_removes_wrongful_flag():
    result = run(BASE + [
        'M3,F01,OUT,,,2026-11-21T08:10:00+08:00,2026-11-21T08:10:00+08:00',
        'R1,F01,RELEASE,,H09,2026-11-21T08:12:00+08:00,2026-11-21T08:30:00+08:00',
        'G1,F01,GATE,,,2026-11-21T08:20:00+08:00,2026-11-21T08:20:00+08:00',
    ])
    custody = result['custody']
    assert custody['gates'][0]['status'] == 'confirmed'
    assert custody['counts']['wrongful'] == 0 and custody['counts']['pending'] == 0
    assert custody['differences'] == []
    assert any(row['event'] == 'R1' and row['late'] for row in custody['rows'])


def test_late_release_after_exit_keeps_wrongful_flag():
    result = run(BASE + [
        'M3,F01,OUT,,,2026-11-21T08:10:00+08:00,2026-11-21T08:10:00+08:00',
        'R1,F01,RELEASE,,H09,2026-11-21T08:22:00+08:00,2026-11-21T08:30:00+08:00',
        'G1,F01,GATE,,,2026-11-21T08:20:00+08:00,2026-11-21T08:20:00+08:00',
    ])
    custody = result['custody']
    assert custody['gates'][0]['status'] == 'wrongful'
    assert custody['counts']['wrongful'] == 1
    assert [row['kind'] for row in custody['differences']] == ['wrongful_exit']


def test_retried_gate_receipt_settles_once():
    result = run(BASE + [
        'M3,F01,OUT,,,2026-11-21T08:10:00+08:00,2026-11-21T08:10:00+08:00',
        'G1,F01,GATE,,,2026-11-21T08:20:00+08:00,2026-11-21T08:20:00+08:00',
        'G1,F01,GATE,,,2026-11-21T08:20:00+08:00,2026-11-21T08:26:00+08:00',
    ])
    # The retried copy is collapsed at intake and only one gate settles.
    assert [row['event'] for row in result['intake']['copies']] == ['G1']
    assert result['custody']['counts']['gate_receipts'] == 1
    assert [row['status'] for row in result['custody']['gates']] == ['wrongful']
    assert len(result['custody']['periods']) == 2  # IN -> HOLD, then HOLD -> gate
    # The stock is released exactly once: nothing is left in the slot.
    assert result['capacity']['counts']['frozen'] == 0


def test_second_gate_event_never_frees_the_slot_again():
    result = run(BASE + [
        'M3,F01,OUT,,,2026-11-21T08:10:00+08:00,2026-11-21T08:10:00+08:00',
        'G1,F01,GATE,,,2026-11-21T08:20:00+08:00,2026-11-21T08:20:00+08:00',
        'G2,F01,GATE,,,2026-11-21T08:25:00+08:00,2026-11-21T08:25:00+08:00',
    ])
    custody = result['custody']
    assert [row['status'] for row in custody['gates']] == ['wrongful', 'duplicate']
    assert custody['counts']['gate_receipts'] == 2 and custody['counts']['gate_duplicates'] == 1
    assert len(custody['periods']) == 2  # never re-closes the freed period
    assert [row['kind'] for row in custody['differences']] == ['wrongful_exit']


def test_repeated_out_on_frozen_keeps_single_declaration():
    result = run(BASE + [
        'M3,F01,OUT,,,2026-11-21T08:10:00+08:00,2026-11-21T08:10:00+08:00',
        'M4,F01,OUT,,,2026-11-21T08:15:00+08:00,2026-11-21T08:15:00+08:00',
    ])
    custody = result['custody']
    assert custody['counts']['declared'] == 1
    assert any(row['event'] == 'M4' and row['code'] == 'DUP_EXIT' for row in custody['rows'])
    assert custody['states'] == {'F01': {'slot': 'A1', 'ticket': 'H09'}}


def test_gate_aliases_are_normalized():
    row = dict(source='moves', door='', signal='', celsius='', effective='2026-11-21T08:00:00Z',
               received='2026-11-21T08:00:00Z', event='G1', lot='F01', kind='EXIT', slot='', ticket='')
    assert normalize(row) is None and row['kind'] == 'GATE'


def test_gate_and_declaration_in_same_second_are_not_a_conflict():
    result = run(BASE + [
        'M3,F01,OUT,,,2026-11-21T08:20:00+08:00,2026-11-21T08:20:00+08:00',
        'G1,F01,GATE,,,2026-11-21T08:20:00+08:00,2026-11-21T08:20:00+08:00',
    ])
    custody = result['custody']
    assert custody['isolated'] == []
    assert [row['status'] for row in custody['gates']] == ['wrongful']
    assert custody['states'] == {}


def test_declared_exit_keeps_capacity_frozen_until_gate():
    declared = run(BASE + ['M3,F01,OUT,,,2026-11-21T08:10:00+08:00,2026-11-21T08:10:00+08:00'])
    confirmed = run(BASE + [
        'M3,F01,OUT,,,2026-11-21T08:10:00+08:00,2026-11-21T08:10:00+08:00',
        'G1,F01,GATE,,,2026-11-21T08:20:00+08:00,2026-11-21T08:20:00+08:00',
    ])
    assert declared['capacity']['counts']['frozen'] == 4
    assert declared['capacity']['counts']['free'] == 23
    assert confirmed['capacity']['counts']['frozen'] == 0
    assert confirmed['capacity']['counts']['free'] == 27
    assert confirmed['capacity']['balanced']


def test_custody_page_lists_exit_differences(client):
    payload = pack(BASE + [
        'M3,F01,OUT,,,2026-11-21T08:10:00+08:00,2026-11-21T08:10:00+08:00',
        'G1,F01,GATE,,,2026-11-21T08:20:00+08:00,2026-11-21T08:20:00+08:00',
    ])
    key = client.post('/api/receipts', json=payload).json['id']
    html = client.get(f'/view/custody?receipt={key}').get_data(as_text=True)
    assert '出闸差异' in html and '误出闸 · F01' in html and '待处理查验单' in html
