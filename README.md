# Telegram File Bot

## Features
- Admin panel for IDs `8800277333` and `7812937031`
- Add/remove additional admins (the two initial admins are protected)
- Required-channel management
- Create a deep link that sends a collected batch of Telegram messages
- Unlimited normal user requests (subject to Telegram platform limits)
- Admin video controls: create link and request a new cover photo
- JSON persistence

## Important security note
Never put your Telegram bot token in source code or commit it to GitHub. If a token has been pasted into a chat or repository, revoke it through BotFather and use a fresh token in the `BOT_TOKEN` environment variable.

## Deploy to Render
1. Upload the contents of this ZIP to a **private** GitHub repository.
2. In Render, create a Blueprint from that repository, or create a Background Worker.
3. The included `render.yaml` describes a paid Starter worker and a persistent disk. Free Render services cannot guarantee continuous worker execution or persistent local storage.
4. Set `BOT_TOKEN` in the Render Environment settings.
5. Deploy and inspect the logs.

## Use
- Start the bot as an admin and choose **دریافت لینک همه فایل‌ها**.
- Send each item in the collection to the bot, then send `/finish`.
- Open the generated link to copy all collected messages to the user.
- Add a required channel only after the bot has been made an administrator in that channel.

## Limitations
- Telegram bot API cannot reboot the recipient's Telegram app when a deep link is clicked. The link starts the bot and sends the collection; it does not restart the bot.
- The current cover-photo workflow saves the requested cover and copies the original video message. Telegram's `copyMessage` does not allow changing the thumbnail of an existing video. Re-sending a video with a new thumbnail requires downloading and re-uploading the original video, and may be constrained by Telegram file-size and hosting limits. This starter implementation does not yet implement full video download/re-upload for cover replacement.
- JSON storage is suitable for a small bot. For larger usage, consider a database.
