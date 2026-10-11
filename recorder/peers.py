"""Hand-over of the speakers between programs on the control unit.

Only one program plays through the speakers at a time ("A second program
on the control unit" in docs/components.md). Before recording or playing,
the recorder asks each peer in the config to let go: POST <peer>/api/yield.
A peer answers once its playback device is free, or 409 with the reason it
cannot let go. A peer that does not answer is not running, so it holds
nothing.
"""

import json
import urllib.error
import urllib.request


class PeerBusy(Exception):
    pass


def claim(peers: list[str], timeout: float = 8.0) -> None:
    """Ask every peer to let go of the speakers. Raises PeerBusy."""
    for url in peers:
        req = urllib.request.Request(url.rstrip("/") + "/api/yield", method="POST", data=b"")
        try:
            urllib.request.urlopen(req, timeout=timeout).read()
        except urllib.error.HTTPError as e:
            if e.code == 409:
                try:
                    reason = json.loads(e.read()).get("detail", "")
                except ValueError:
                    reason = ""
                raise PeerBusy(reason or f"{url} is using the speakers")
            # anything else: the peer is broken or old; do not block on it
        except (urllib.error.URLError, OSError):
            pass  # not running
