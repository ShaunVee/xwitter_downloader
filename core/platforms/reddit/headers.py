"""The one header set Reddit answers.

Only User-Agent. Setting anything else, even Accept: */* which is the value
httpx sends by default, reliably earns a 403: injecting a header changes the
order they go out in, and Reddit fingerprints that order. Verified by sending
the identical value both ways, with only the ordering differing.

In its own module because every Reddit-bound request needs the same value: both
providers and the share-link redirect in `urls`. The redirect not using it was a
bug. It inherited whatever User-Agent the shared client happened to carry, so a
/s/ link could be refused while the providers sailed through, and the refusal
surfaced to the user as "that doesn't look like a Reddit post link".

Sent from one place now, `relay`, which is also where those requests get routed
when Reddit is blocking this server's address outright. The same value is
repeated once more in `relay/worker.js`, because the far end of that route is a
different runtime and has no way to import this.
"""

from __future__ import annotations

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}
