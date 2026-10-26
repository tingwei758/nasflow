import httpx
import pytest
import respx

from nasflow.qbit import QbitClient, QbitUnavailable

BASE = "http://qb:8080"
LOGIN = f"{BASE}/api/v2/auth/login"
INFO = f"{BASE}/api/v2/torrents/info"


def torrent(hash_, progress=1.0, state="seeding", name="Some.Release.2020"):
    return {
        "hash": hash_,
        "name": name,
        "content_path": f"/downloads/{name}",
        "save_path": "/downloads/",
        "state": state,
        "progress": progress,
        "completed_on": 1700000000,
    }


@pytest.fixture
def client():
    return QbitClient(BASE, "admin", "pw")


def test_logs_in_then_lists(client):
    with respx.mock(assert_all_called=False) as mock:
        login = mock.post(LOGIN).respond(200, text="Ok.")
        mock.get(INFO).respond(200, json=[torrent("a")])
        client.completed_torrents()
        assert login.called


def test_only_returns_fully_complete_torrents(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).respond(200, text="Ok.")
        mock.get(INFO).respond(
            200,
            json=[
                torrent("a", 1.0, "seeding"),
                torrent("b", 0.4, "downloading"),
                torrent("c", 1.0, "moving"),
                torrent("d", 1.0, "checkingUP"),
            ],
        )
        assert {t.hash for t in client.completed_torrents()} == {"a"}


def test_accepts_the_usual_finished_states(client):
    finished = ["uploading", "stalledUP", "pausedUP", "queuedUP", "forcedUP", "stoppedUP"]
    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).respond(200, text="Ok.")
        mock.get(INFO).respond(
            200, json=[torrent(f"h{i}", 1.0, s) for i, s in enumerate(finished)]
        )
        assert len(client.completed_torrents()) == len(finished)


def test_reauthenticates_on_403(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).respond(200, text="Ok.")
        mock.get(INFO).mock(
            side_effect=[
                httpx.Response(403, text="Forbidden"),
                httpx.Response(200, json=[torrent("a")]),
            ]
        )
        assert len(client.completed_torrents()) == 1


def test_gives_up_after_one_reauth(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).respond(200, text="Ok.")
        mock.get(INFO).respond(403, text="Forbidden")
        with pytest.raises(QbitUnavailable):
            client.completed_torrents()


def test_failed_login_raises_unavailable(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).respond(200, text="Fails.")
        with pytest.raises(QbitUnavailable):
            client.completed_torrents()


def test_connection_error_raises_unavailable(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(QbitUnavailable):
            client.completed_torrents()


def test_malformed_payload_raises_unavailable(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).respond(200, text="Ok.")
        mock.get(INFO).respond(200, text="not json")
        with pytest.raises(QbitUnavailable):
            client.completed_torrents()


def test_torrent_fields_are_carried_through(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).respond(200, text="Ok.")
        mock.get(INFO).respond(200, json=[torrent("abc", name="黑镜.Black.Mirror.S06")])
        t = client.completed_torrents()[0]
        assert t.hash == "abc"
        assert t.name == "黑镜.Black.Mirror.S06"
        assert t.content_path == "/downloads/黑镜.Black.Mirror.S06"


def test_password_never_appears_in_exception_text(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(QbitUnavailable) as exc:
            client.completed_torrents()
        assert "pw" not in str(exc.value).split()


def test_settling_while_any_torrent_is_checked_or_moved(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).respond(200, text="Ok.")
        info = mock.get(INFO)
        for state in ("checkingResumeData", "checkingUP", "checkingDL", "moving"):
            info.respond(200, json=[torrent("a"), torrent("b", 1.0, state)])
            assert client.settling() is True
        info.respond(200, json=[torrent("a"), torrent("b", 0.4, "downloading")])
        assert client.settling() is False
