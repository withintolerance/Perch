# Perch

We made Perch because Frigate kept letting us down. We did not need object detection, and we did not want to fight the software just to see our cameras. We needed something simple: record our RTSP cameras, watch them live, and play back what happened.

That is all Perch does. It keeps the camera's own picture, shows every camera on one page, and marks the timeline where something moved.

## Install it

You need a Linux computer that will stay on, like a small server or an old PC. One command downloads Perch, installs Docker if you don't have it, and starts the recorder:

```sh
curl -fsSL https://raw.githubusercontent.com/withintolerance/Perch/main/install.sh | sh
```

The first run can take a few minutes. When it finishes, it prints an address like `http://192.168.1.50:8090`. Open that address in a browser on the same network.

The page is empty at first. That is normal. You add cameras next.

Perch is stored in a folder called `perch` in your home folder. Recordings go in `perch/data` until you pick another folder in Settings.

## Add a camera

1. Click **Settings**.
2. Scroll to **Add a camera**.
3. Type a name you will recognize, such as `Front door`.
4. Paste the camera's RTSP address. It starts with `rtsp://` and usually includes a username and password, like `rtsp://user:password@192.168.1.20:554/stream1`.
5. Click **Add camera**, then go back to the camera list.

The live picture should show up within a few seconds. Your camera's phone app is where you find that RTSP address. Look for a switch named RTSP and turn it on.

## Watch a recording

Click a camera. Under the picture is a timeline of that day.

- Bright marks are movement. A taller, brighter mark means more movement.
- Click a moment to play it.
- Scroll on the timeline to zoom in. Drag to move along the day.

Playback is the original recording, not a tiny preview.

## Turn on a password

Until you turn on sign-in, anyone who can open the page can watch the cameras and change settings, including the camera passwords.

When you want a login:

1. Open **Settings**.
2. Check **Require a sign-in**.
3. Create the owner username and password. The password needs at least 10 characters.
4. Click **Save sign-in**.

That owner can later add other people, and can choose which cameras each person can see.

## Recording choices

Open **Settings** and pick one:

- **24/7, keep everything.** Records all the time and keeps it.
- **Keep everything, only during these hours.** Records on the days and times you pick. A range that passes midnight, such as 22:00 to 06:00, is fine.
- **24/7, keep only pieces with motion.** Records all the time, then deletes a 15-second piece if nothing in it moved enough.
- **Only during these hours, and only pieces with motion.** Both limits at once.

Perch also deletes the oldest recordings, and only its own, when the disk gets too full.

## Home Assistant

Skip this if you do not use Home Assistant.

Turn on **Publish motion** in Settings and point it at the same MQTT broker Home Assistant already uses. Each camera becomes a motion sensor. If you draw a zone on a camera, that zone becomes its own sensor too.

## If it does not work

**The browser cannot open the page.** Use the address the installer printed, including `:8090`. The server and the browser need to be on the same network.

**A camera stays on "Connecting" or "not ready."** The RTSP address is wrong, the camera is off, or the username and password were rejected. Fix the address under Settings. The other cameras are not affected.

**Live video works on the server, but not from your phone.** Perch has to tell your phone where to connect. The installer guesses the server's address. If the server has more than one network, edit `advertise_host` in `cameras.yaml` inside the `perch` folder, then run:

```sh
cd ~/perch
docker compose restart
```

**You want to stop it.**

```sh
cd ~/perch
docker compose down
```

Your recordings stay in the `data` folder.

## Privacy

Leave sign-in off only on a home network you trust. With sign-in on, watching and changing settings require the login. Camera passwords stay on the server. They are not shown on the main screen.
