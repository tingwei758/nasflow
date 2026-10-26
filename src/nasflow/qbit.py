"""qBittorrent WebUI client.

nasflow polls this rather than using qBittorrent's AutoRun hook, for two reasons:
the hook would run inside qBittorrent's own container and depend on whatever
binaries that image ships, and — more importantly — the filesystem cannot tell
a finished download from an unfinished one. Partial files carry no marker, and
a multi-file torrent is moved one file at a time. Only the API knows.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

_TIMEOUT = httpx.Timeout(15.0)

# States meaning "all data is written and settled". `moving` and `checking*`
# are excluded: the files are still in flight.
_FINISHED_STATES = frozenset(
    {
        "uploading",
        "stalledUP",
        "pausedUP",
        "stoppedUP",
        "queuedUP",
        "forcedUP",
        "seeding",
    }
)

# States where qBittorrent is still settling a torrent's data: rechecking it
# (including the recheck of every torrent after a restart) or moving it.
_IN_FLIGHT_PREFIXES = ("checking", "moving")


class QbitUnavailable(RuntimeError):
    """qBittorrent could not be reached or refused us. Retryable."""


@dataclass(frozen=True)
class Torrent:
    hash: str
    name: str
    content_path: str
    save_path: str
    state: str
    progress: float
    completed_on: int


class QbitClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        client: httpx.Client | None = None,
    ):
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._client = client or httpx.Client(timeout=_TIMEOUT)
        self._authenticated = False

    def _login(self) -> None:
        try:
            response = self._client.post(
                f"{self._base}/api/v2/auth/login",
                data={"username": self._username, "password": self._password},
                headers={"Referer": self._base},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise QbitUnavailable(f"login request failed: {exc}") from exc

        if "Ok." not in response.text:
            raise QbitUnavailable("login rejected: check qb_username / qb_password")
        self._authenticated = True

    def _fetch_info(self) -> httpx.Response:
        try:
            return self._client.get(f"{self._base}/api/v2/torrents/info")
        except httpx.HTTPError as exc:
            raise QbitUnavailable(f"torrent list request failed: {exc}") from exc

    def _list(self) -> list[dict]:
        if not self._authenticated:
            self._login()

        response = self._fetch_info()
        if response.status_code == 403:
            # Session expired. One re-login, then give up.
            self._authenticated = False
            self._login()
            response = self._fetch_info()

        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise QbitUnavailable(f"could not read torrent list: {exc}") from exc

        if not isinstance(payload, list):
            raise QbitUnavailable("torrent list was not a list")
        return payload

    def completed_torrents(self) -> list[Torrent]:
        """Every torrent whose data is fully written and settled."""
        return [
            _to_torrent(raw)
            for raw in self._list()
            if float(raw.get("progress", 0)) >= 1.0 and raw.get("state") in _FINISHED_STATES
        ]

    def settling(self) -> bool:
        """Whether any torrent is still being checked or moved.

        Right after qBittorrent starts, every torrent is rechecked and none of
        them counts as completed yet, so a list taken then is not the whole set.
        """
        states = (str(raw.get("state", "")) for raw in self._list())
        return any(state.startswith(_IN_FLIGHT_PREFIXES) for state in states)


def _to_torrent(raw: dict) -> Torrent:
    return Torrent(
        hash=raw.get("hash", ""),
        name=raw.get("name", ""),
        content_path=raw.get("content_path", ""),
        save_path=raw.get("save_path", ""),
        state=raw.get("state", ""),
        progress=float(raw.get("progress", 0.0)),
        completed_on=int(raw.get("completed_on", 0)),
    )
