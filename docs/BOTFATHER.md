# Bot profile

Everything a user sees before the bot does any work: the name, the photo, the
blurbs and the command menu.

Most of it is **already in code**. [`bot/profile.py`](../bot/profile.py) holds the
copy and pushes it to Telegram on every startup, so a rebuild on a fresh host
restores the profile automatically and copy changes go through review like
anything else.

Two things the Bot API can't set, so they're manual and one-time:

| Field | Why it's manual |
|---|---|
| **Display name** | Rate-limited by Telegram; changed rarely, and a failed sync at startup would be noisy for no gain. |
| **Profile photo** | No Bot API method exists at all. BotFather only. |

---

## 1. Create the bot

Message [@BotFather](https://t.me/BotFather):

```
/newbot
```

| Prompt | Answer |
|---|---|
| Name | `X Downloader` |
| Username | must end in `bot`, e.g. `xdl_<something>_bot` |

Keep the token it gives you — that goes in `.env` as `TELEGRAM_BOT_TOKEN` and is
the only real secret in this project. `/revoke` rotates it if it ever leaks.

The username is permanent and globally unique; the display name is not, so don't
agonise over the name.

## 2. Set the photo

`/setuserpic`, pick the bot, then upload:

```
assets/logo.png
```

512×512 PNG. Telegram crops profile photos to a **circle** — the source
[`assets/logo.svg`](../assets/logo.svg) keeps every element inside the safe
radius, so nothing is clipped. Re-render after editing the SVG:

```bash
rsvg-convert -w 512 -h 512 assets/logo.svg -o assets/logo.png
```

## 3. Everything else is automatic

Start the bot once and `bot/profile.py` sets:

- **Command menu** (`/` button) — `/start` and `/help`
- **Short description** — profile page, under the photo
- **Description** — the empty-chat screen, above the Start button

Confirm it landed:

```bash
docker compose logs | grep "profile synced"
```

A failure here is logged as a warning and never blocks startup — the bot works
fine with a stale profile.

> Telegram rejects the *whole* update if any field is over length (120 chars for
> the short description, 512 for the description). Keep edits under those limits
> or the profile sync silently stops applying.

## 4. Lock it down

The access gate in [`bot/access.py`](../bot/access.py) already ignores everyone
outside `ALLOWED_USER_IDS`, but these BotFather settings shrink the surface
further — worth doing while the bot is private.

`/mybots` → pick the bot → **Bot Settings**:

| Setting | Value | Why |
|---|---|---|
| Allow Groups? | **Off** | Nothing about this bot makes sense in a group, and it can't be added to one by mistake. |
| Group Privacy | **On** (default) | Moot with groups off, but leave it. |
| Inline Mode | **Off** (default) | Not implemented. |

Skip `/setdomain` and webhook settings entirely — long-polling means there's no
domain and no public endpoint.

---

## Changing the copy later

Edit the constants in [`bot/profile.py`](../bot/profile.py) and redeploy.
The next startup pushes the change. The one thing to keep honest is
`COMMANDS`: only list commands that have a handler in `main.py`, or the menu
offers users a dead end. There's a test for that too.
