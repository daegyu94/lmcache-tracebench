import signal

from recorder.process import ManagedProcess, stop_process


class _FinishedProcess:
    pid = 4242
    returncode = 1

    def poll(self):
        return self.returncode


class _LogFile:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_stop_process_kills_group_when_leader_already_exited(monkeypatch):
    signals = []

    def fake_killpg(process_group_id, sent_signal):
        signals.append((process_group_id, sent_signal))

    monkeypatch.setattr("recorder.process.os.killpg", fake_killpg)
    log_file = _LogFile()
    managed = ManagedProcess("vllm", [], _FinishedProcess(), log_file)

    return_code = stop_process(managed, timeout_seconds=0)

    assert return_code == 1
    assert signals == [
        (4242, signal.SIGTERM),
        (4242, 0),
        (4242, signal.SIGKILL),
    ]
    assert log_file.closed
