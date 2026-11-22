import socket

from photo_backer_upper import automation
from photo_backer_upper.automation import nas_endpoint, wait_for_nas


def test_nas_endpoint_defaults_and_explicit_port():
    assert nas_endpoint("smb://user@10.0.0.5/Share") == ("10.0.0.5", 445)
    assert nas_endpoint("afp://server.local/Share") == ("server.local", 548)
    assert nas_endpoint("smb://server:1445/Share") == ("server", 1445)
    assert nas_endpoint("not a url") is None


def test_wait_for_nas_succeeds_when_listening():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    port = srv.getsockname()[1]
    try:
        assert wait_for_nas(f"smb://127.0.0.1:{port}/X", timeout=1)
    finally:
        srv.close()


def test_wait_for_nas_gives_up(monkeypatch):
    def refuse(*a, **k):
        raise OSError("no route")
    monkeypatch.setattr(automation.socket, "create_connection", refuse)
    monkeypatch.setattr(automation.time, "sleep", lambda s: None)
    assert not wait_for_nas("smb://10.0.0.5/X", timeout=0)
