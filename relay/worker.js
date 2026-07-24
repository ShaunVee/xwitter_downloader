/**
 * justthefile relay: the one address in the system Reddit still talks to.
 *
 * Reddit blocks by IP, and it blocks the VPS this project runs on. Measured
 * from the server, with a browser User-Agent, on 2026-07-24:
 *
 *     www.reddit.com/r/../s/<token>   403   page titled "Blocked"
 *     old.reddit.com/comments/<id>/   403   same
 *     www.reddit.com/comments/<id>/.json  403   same
 *     oauth.reddit.com                403   (401 without a key elsewhere)
 *     v.redd.it/<id>/CMAF_720.mp4     206   fine
 *     i.redd.it/<anything>            404   fine, meaning reachable
 *
 * So the ban covers the website and not the CDNs. Video, audio and images
 * still come straight from the server as before, at full speed and no cost
 * here; only the small "what is in this post" questions come through this
 * worker, which is why the free plan's 100k requests/day is not a constraint.
 *
 * Reddit's own API would have been the supported way through and is closed:
 * self-serve app registration ended in late 2025, and new credentials are
 * granted by application under the Responsible Builder Policy.
 *
 * Two modes, because the bot asks Reddit two different kinds of question:
 *
 *     ?mode=page       fetch it, hand back the body under Reddit's own status
 *     ?mode=redirect   follow nothing, report where it points as JSON
 *
 * The second exists because a /s/ share link carries no post ID at all. Only
 * the Location header matters there, so the body is never fetched.
 *
 * Locked to reddit.com and behind a shared secret: an open relay on a public
 * address gets found and used by strangers, and this one would be used to
 * launder traffic at the exact site that blocked us.
 */

// Subdomains included: old, www, oauth and the bare domain are all in play.
const ALLOWED_HOST = /^([a-z0-9-]+\.)*reddit\.com$/;

// The one header set Reddit answers. Anything more earns a 403, ordering
// included. Kept in step with core/platforms/reddit/headers.py by hand: two
// runtimes, one value, and no way to share it.
const USER_AGENT =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // No key configured is a misconfiguration, not an invitation.
    if (!env.RELAY_KEY || request.headers.get("X-Relay-Key") !== env.RELAY_KEY) {
      return new Response("unauthorized\n", { status: 401 });
    }

    const target = url.searchParams.get("url") || "";
    let host;
    try {
      host = new URL(target).hostname.toLowerCase();
    } catch {
      return new Response("bad url\n", { status: 400 });
    }
    if (!ALLOWED_HOST.test(host)) {
      return new Response("host not allowed\n", { status: 400 });
    }

    if (url.searchParams.get("mode") === "redirect") {
      const hop = await fetch(target, {
        headers: { "User-Agent": USER_AGENT },
        redirect: "manual",
      });
      const location = hop.headers.get("Location");
      return Response.json({
        status: hop.status,
        // Relative Locations are legal and Reddit has used them. Resolved
        // here so the caller never has to know that.
        final: location ? new URL(location, target).toString() : null,
      });
    }

    const page = await fetch(target, {
      headers: { "User-Agent": USER_AGENT },
      redirect: "follow",
    });

    // Reddit's status is passed through untouched: a 403 here has to stay
    // distinguishable from this worker's own 401, or the caller cannot tell
    // "Reddit refused the relay too" from "your key is wrong".
    return new Response(page.body, {
      status: page.status,
      headers: {
        "Content-Type": page.headers.get("Content-Type") || "text/plain",
        "X-Relay-Final-Url": page.url,
      },
    });
  },
};
