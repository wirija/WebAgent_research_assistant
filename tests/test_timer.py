from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json

from webagent.state import Run, RunRequest


def test_elapsed_time_advances_and_freezes_persistently(tmp_path, monkeypatch):
    current = datetime(2026, 11, 1, 12, tzinfo=ZoneInfo('Asia/Singapore'))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current.astimezone(tz) if tz else current.replace(tzinfo=None)

    monkeypatch.setattr('webagent.state.datetime', Clock)
    run = Run(RunRequest(company='Timer test'), tmp_path)
    assert run.snapshot()['elapsed_seconds'] == 0
    current += timedelta(hours=1, minutes=2, seconds=3)
    assert run.snapshot()['elapsed_seconds'] == 3723
    run.status = 'cancelled'
    run.finalize()
    finished_at = run.finished_at
    current += timedelta(hours=5)
    assert run.snapshot()['elapsed_seconds'] == 3723
    run.finalize()
    assert run.finished_at == finished_at
    saved = json.loads((run.folder / 'catalogue.json').read_text())
    assert saved['elapsed_seconds'] == 3723 and saved['finished_at'] == finished_at


def test_request_has_no_run_limits():
    assert 'max_steps' not in RunRequest.model_fields
    assert 'max_documents' not in RunRequest.model_fields


def test_legacy_and_interrupted_runs_have_frozen_estimated_duration(tmp_path, monkeypatch):
    from webagent import server
    monkeypatch.setattr(server, 'OUTPUT', tmp_path)
    monkeypatch.setattr(server, 'RUNS', {})
    run = Run(RunRequest(company='Older run'), tmp_path)
    run.started_at = '2026-11-01T12:00:00+08:00'
    run.events = [{'time':'2026-11-01T12:02:03+08:00', 'kind':'info', 'message':'Last recorded activity'}]
    run.save()
    data = json.loads((run.folder / 'catalogue.json').read_text())
    data.pop('finished_at')
    data.pop('elapsed_seconds')
    (run.folder / 'catalogue.json').write_text(json.dumps(data))
    restored = server.read_run(run.id)
    assert restored['status'] == 'interrupted'
    assert restored['elapsed_estimated'] is True
    assert restored['elapsed_seconds'] == 123
    assert server.read_run(run.id)['elapsed_seconds'] == 123
