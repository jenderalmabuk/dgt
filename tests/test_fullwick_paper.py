from pathlib import Path
import tempfile
import pandas as pd
import fusionnew.paper_fullwick as p


def frame(rows):
    return pd.DataFrame(rows, columns=['open_time','open','high','low','close'])


def test_pending_fill_and_sl_first():
    with tempfile.TemporaryDirectory() as td:
        journal = Path(td) / 'j.jsonl'; state = {'pending': {}, 'open': {}, 'closed': [], 'seen': []}
        t = pd.Timestamp.now().floor('5min') - pd.Timedelta(minutes=10)
        setup = {'side':'BULL','decision_time':t,'entry':100.,'sl':99.,'tp':103.,'volume_ratio':2.}
        bars = frame([(t,101,102,100.5,101),(t+pd.Timedelta(minutes=5),100.5,103.5,98.5,100)])
        p.latest_setup=lambda symbol,tp_mode='fixed':(setup,bars)
        p.oi_meta=lambda *args:{'oi_available':True,'oi_spike':False}
        p.scan_symbol('TESTUSDT',state,journal)
        assert not state['pending'] and not state['open']
        assert state['closed'][0]['reason']=='SL' and state['closed'][0]['netR'] < -1
        assert [__import__('json').loads(x)['event'] for x in journal.read_text().splitlines()] == ['SETUP','FILLED','CLOSED']


def test_oi_arm_rejects_without_spike():
    with tempfile.TemporaryDirectory() as td:
        journal = Path(td) / 'j.jsonl'; state = {'pending': {}, 'open': {}, 'closed': [], 'seen': []}
        t = pd.Timestamp.now().floor('5min') - pd.Timedelta(minutes=10)
        setup = {'side':'BULL','decision_time':t,'entry':100.,'sl':99.,'tp':103.,'volume_ratio':2.}
        p.latest_setup=lambda symbol,tp_mode='fixed':(setup,frame([(t,101,102,100.5,101)]))
        p.oi_meta=lambda *args:{'oi_available':True,'oi_spike':False}
        p.scan_symbol('TESTUSDT',state,journal,oi_gate=True)
        assert not state['pending'] and not state['open']
        assert __import__('json').loads(journal.read_text())['event'] == 'OI_REJECT'


if __name__ == '__main__':
    test_pending_fill_and_sl_first(); test_oi_arm_rejects_without_spike(); print('fullwick_paper: PASS')
