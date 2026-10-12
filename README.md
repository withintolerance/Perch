# Cameras

A small recorder for RTSP cameras. It shows a live grid, keeps the original stream, and draws motion on a timeline. An owner account stands in front of it and can add other people with access to only some cameras.

## Run

You need Docker, or Python 3.10+ with ffmpeg and a separate [go2rtc](https://github.com/AlexxIT/go2rtc) process.

```sh
cp cameras.example.yaml cameras.yaml
cp go2rtc.yaml.example go2rtc.yaml
# set advertise_host in cameras.yaml to the address browsers will use
docker compose up -d --build
```

Open `http://<host>:8090`. The first visit creates the owner account. Owners can add more people under Settings → Users and choose which cameras they can see, and whether they get live view, playback, or settings.

`cameras.yaml` is read once, the first time the database is empty. After that, cameras, users, the recording folder, the schedule, and Home Assistant live in the database and the Settings page.

Set the recording folder in Settings. New files go there. Files already saved stay put and remain playable.

## Recording

- **24/7, keep everything** records continuously and does not care about motion.
- **Keep everything, only during these hours** records on the days and times you pick. A window that passes midnight, such as 22:00–06:00, is fine.
- **Keep only pieces with motion** still records continuously, then deletes a 15-second piece when nothing in it crosses the motion percent. The start of a movement stays in that piece. This can be combined with the hours limit.

The disk floor deletes the oldest recordings, ours only, when free space gets low.

## Home Assistant

Turn on MQTT in Settings and use the same broker Home Assistant already has. Each camera is a motion entity. Each zone you draw is another one. Discovery is published to `homeassistant/binary_sensor/...`.

## Security

The site, the recordings, and the live-view handshake require the session cookie. The WebRTC media port (`8555` by default) has to be reachable by the browser, so keep this host on a network you trust, or put it behind HTTPS on a private address. Camera passwords stay on the server and are not shown on the main screen.

Recordings are plain files under the data directory. Protect that disk the same way you protect the account.
