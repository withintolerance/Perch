# Perch

We made Perch because Frigate kept letting us down. We did not need object detection, and we did not want to fight the software just to see our cameras. We needed a recorder that stays out of the way: record our RTSP cameras, watch them live, and play back what happened.

Perch does that, and it does the parts we kept wishing the recorder already had.

- **Live view of every camera**, at the camera's own quality, on one page.
- **Playback of the real recording**, not a tiny preview. Click a moment on the day and it plays.
- **A motion timeline.** Move along the day and you can see where something moved, and how strong it was. Bright marks are movement.
- **Recording the way you actually want it.** All day, only at night, only when something moves, or both. A schedule can cross midnight, like 22:00 to 06:00.
- **Zones.** Draw a box on a camera, such as the door, and motion for that camera can mean just that box.
- **Home Assistant, without a custom component.** Each camera becomes a motion sensor. Each zone becomes its own sensor. Perch publishes them over MQTT, and Home Assistant picks them up.
- **A real login, when you want one.** It starts open, so you can set it up without making an account first. Turn on sign-in and the page locks.
- **More than one person.** An owner can add people and decide, for each one, which cameras they can see, and whether they get live view, playback, or settings.
- **Your disk, your folder.** Pick where recordings are stored. If the disk fills up, Perch deletes its own oldest clips and leaves everything else alone.
- **One connection per camera.** Perch pulls each RTSP stream once and shares it, so a Wi-Fi camera is not asked to feed three programs at the same time.

It does not try to name people, cars, or packages. The picture you record is the picture the camera sent.

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
- Move along the timeline to read how intense the motion was at that moment.
- Click a moment to play it.
- Scroll on the timeline to zoom in. Drag to move along the day.

## People and sign-in

Perch starts with no password, so the first visit is just the cameras. Anyone who can open the page can also change settings, including the camera passwords. Turn on sign-in before you share the address.

1. Open **Settings**.
2. Check **Require a sign-in**.
3. Create the owner username and password. The password needs at least 10 characters.
4. Click **Save sign-in**.

The owner can then add other people under **Users**:

- **Live view** means they can watch the cameras right now.
- **Playback** means they can open recordings.
- **Settings** means they can change cameras, recording, and Home Assistant.
- Each person gets a list of cameras. They only see the ones you tick.
- An owner always sees every camera and can change everything. You cannot lock yourself out by deleting the last owner.

## Recording choices

Open **Settings** and pick one:

- **24/7, keep everything.** Records all the time and keeps it.
- **Keep everything, only during these hours.** Records on the days and times you pick. A range that passes midnight, such as 22:00 to 06:00, is fine.
- **24/7, keep only pieces with motion.** Records all the time, then deletes a 15-second piece if nothing in it moved enough. The start of a movement stays in that piece.
- **Only during these hours, and only pieces with motion.** Both limits at once.

Zones change what "moved" means. Draw one on the camera snapshot in Settings, give it a name, and motion for that camera uses the box instead of the whole picture. Leave the list empty to use the whole frame.

You can also choose how many days to keep, and how much free disk to always leave. When free space gets low, Perch deletes the oldest of its own recordings.

## Home Assistant

If you already run Home Assistant, Perch can hand it a motion sensor for every camera and every zone. There is nothing to install in Home Assistant except the MQTT integration you may already have.

1. In Perch, open **Settings** and find **Home Assistant**.
2. Check **Publish motion**.
3. Enter the same broker address, port, username, and password Home Assistant uses.
4. Save.

Home Assistant then discovers the sensors on its own. A camera sensor turns on when that camera sees motion, and turns off a few seconds after the motion stops. A zone sensor does the same for the box you drew. You can use them in automations the same way as any other motion sensor: lights, notifications, whatever you already do.

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
