/* Front end.
 *
 * The server only ever hands back JSON. Media is fetched here, straight from
 * video.twimg.com, which reflects arbitrary origins in Access-Control-Allow-Origin
 * and so is readable cross-origin. That matters for more than bandwidth: the
 * `download` attribute is ignored on cross-origin URLs, so a plain link would
 * navigate to the video and play it instead of saving it under a real name.
 * Reading the response into a blob gives us a same-origin blob: URL, where
 * `download` is honoured, at the cost of buffering the file in memory.
 */

const form = document.getElementById("resolve-form");
const input = document.getElementById("url");
const submit = document.getElementById("submit");
const message = document.getElementById("message");
const results = document.getElementById("results");
const template = document.getElementById("media-card");

const KIND_LABELS = { video: "Video", gif: "GIF", photo: "Photo" };

function say(text, kind) {
  message.textContent = text;
  message.className = `message${kind ? ` ${kind}` : ""}`;
  message.hidden = !text;
}

function humanSize(bytes) {
  if (!bytes) return "";
  const mb = bytes / (1024 * 1024);
  if (mb < 1) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${mb.toFixed(mb < 10 ? 1 : 0)} MB`;
}

function humanDuration(seconds) {
  if (!seconds) return "";
  const total = Math.round(seconds);
  const mins = Math.floor(total / 60);
  return `${mins}:${String(total % 60).padStart(2, "0")}`;
}

/* Insert the quality into the filename, so saving 1080p and 720p of the same
   post doesn't leave you with "file (1).mp4". */
function nameFor(filename, label, multipleRungs) {
  if (!multipleRungs) return filename;
  const dot = filename.lastIndexOf(".");
  if (dot < 1) return `${filename}-${label}`;
  return `${filename.slice(0, dot)}-${label}${filename.slice(dot)}`;
}

async function saveToDisk(url, filename, onProgress) {
  const response = await fetch(url, {
    mode: "cors",
    credentials: "omit",
    // Mandatory. video.twimg.com hotlink-protects on Referer: it serves the
    // file to a request with no Referer, and 403s any Referer that isn't
    // x.com. The browser's default policy would attach ours on every request,
    // so without this every download fails. (Origin is fine: it only checks
    // Referer.)
    referrerPolicy: "no-referrer",
  });
  if (!response.ok) {
    throw new Error(`the source CDN returned ${response.status}`);
  }

  // content-length is CORS-safelisted, so it is readable without the CDN
  // opting in via Access-Control-Expose-Headers.
  const total = Number(response.headers.get("content-length")) || 0;
  const chunks = [];
  let received = 0;

  const reader = response.body.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    onProgress(received, total);
  }

  const blob = new Blob(chunks, { type: response.headers.get("content-type") || "" });
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();

  // Revoking immediately can cancel the save in some browsers.
  setTimeout(() => URL.revokeObjectURL(href), 60_000);
  return received;
}

function renderCard(item, payloadMeta) {
  const node = template.content.cloneNode(true);
  const card = node.querySelector(".card");
  const img = node.querySelector(".thumb:not(.placeholder)");
  const placeholder = node.querySelector(".thumb.placeholder");
  const badge = node.querySelector(".badge");
  const meta = node.querySelector(".meta");
  const qualities = node.querySelector(".qualities");
  const progress = node.querySelector(".progress");
  const bar = node.querySelector(".bar");
  const fill = node.querySelector(".bar span");
  const progressLabel = node.querySelector(".progress-label");

  if (item.thumbnail) {
    img.src = item.thumbnail;
    img.hidden = false;
    placeholder.remove();
    img.addEventListener("error", () => { img.hidden = true; }, { once: true });
  }

  badge.textContent = KIND_LABELS[item.kind] || item.kind;

  const bits = [];
  if (item.width && item.height) bits.push(`${item.width}x${item.height}`);
  const duration = humanDuration(item.duration_s);
  if (duration) bits.push(duration);
  meta.textContent = bits.join(" · ") || "Ready to download";

  const multipleRungs = item.variants.length > 1;

  item.variants.forEach((variant) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "quality";
    button.innerHTML = `${variant.label}<span class="size">${humanSize(variant.size_bytes)}</span>`;

    button.addEventListener("click", async () => {
      // Media the browser is not allowed to fetch goes through the server,
      // which sets Content-Disposition itself. Navigating to it is enough, and
      // avoids buffering a file we never needed to touch in JS.
      if (item.delivery !== "direct") {
        progress.hidden = false;
        bar.classList.add("indeterminate");
        progressLabel.textContent =
          item.needs_mux
            ? "Joining audio and video on the server\u2026"
            : "Fetching through the server\u2026";

        const href =
          `/api/download?platform=${encodeURIComponent(payloadMeta.platform)}` +
          `&post_id=${encodeURIComponent(payloadMeta.post_id)}` +
          `&item=${item.index}&variant=${variant.index}`;
        const anchor = document.createElement("a");
        anchor.href = href;
        anchor.download = "";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();

        // The browser owns the download from here; there is no progress event
        // to listen to, so say so rather than leaving a bar spinning forever.
        setTimeout(() => {
          bar.classList.remove("indeterminate");
          fill.style.width = "100%";
          progressLabel.textContent = "Started. Check your downloads.";
        }, 1500);
        return;
      }

      const buttons = qualities.querySelectorAll("button");
      buttons.forEach((b) => (b.disabled = true));
      progress.hidden = false;
      bar.classList.add("indeterminate");
      fill.style.width = "";
      progressLabel.textContent = "Starting…";

      try {
        const saved = await saveToDisk(
          variant.url,
          nameFor(item.filename, variant.label, multipleRungs),
          (received, total) => {
            if (total) {
              bar.classList.remove("indeterminate");
              fill.style.width = `${Math.round((received / total) * 100)}%`;
              progressLabel.textContent =
                `${humanSize(received)} of ${humanSize(total)}`;
            } else {
              progressLabel.textContent = `${humanSize(received)} downloaded`;
            }
          }
        );
        bar.classList.remove("indeterminate");
        fill.style.width = "100%";
        progressLabel.textContent = `Saved · ${humanSize(saved)}`;
      } catch (error) {
        bar.classList.remove("indeterminate");
        fill.style.width = "0";
        progressLabel.textContent =
          `Download failed: ${error.message}. The link may have expired; try resolving the post again.`;
      } finally {
        buttons.forEach((b) => (b.disabled = false));
      }
    });

    qualities.appendChild(button);
  });

  return card;
}

async function resolve(url) {
  submit.disabled = true;
  results.replaceChildren();
  say("Looking up that post…", "busy");

  try {
    const response = await fetch(`/api/resolve?url=${encodeURIComponent(url)}`);
    const payload = await response.json();

    if (!response.ok) {
      say(payload.error || "Something went wrong.", "error");
      return;
    }

    const heading = payload.author ? `From @${payload.author}` : "Ready";
    say(`${heading}: ${payload.media.length} item(s) found.`);
    payload.media.forEach((item) =>
      results.appendChild(renderCard(item, payload))
    );
  } catch {
    say("Couldn't reach the server. Check your connection and try again.", "error");
  } finally {
    submit.disabled = false;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const url = input.value.trim();
  if (url) resolve(url);
});

// Deep link support: /?url=... resolves on load, so the site can be wired up as
// a share target or a bookmarklet.
/* Keep the "works with" line in step with the registry, so shipping a new
   platform is a server change only. The markup carries the current list as a
   fallback, so a failed request leaves the page correct rather than blank. */
async function showSupported() {
  try {
    const response = await fetch("/api/platforms");
    if (!response.ok) return;

    const { platforms } = await response.json();
    const labels = platforms.map((p) => p.label);
    if (!labels.length) return;

    const list =
      labels.length === 1
        ? labels[0]
        : `${labels.slice(0, -1).join(", ")} and ${labels.at(-1)}`;
    document.getElementById("supported").textContent = `Works with ${list} links.`;
  } catch {
    /* Leave the fallback text in place. */
  }
}

showSupported();

const preset = new URLSearchParams(location.search).get("url");
if (preset) {
  input.value = preset;
  resolve(preset);
} else {
  input.focus();
}
