#!/bin/sh
# One-command install for a Linux server. Safe to run again.
set -eu

say() {
  printf '%s\n' "$*"
}

if [ "$(uname -s)" != "Linux" ]; then
  say "Perch's one-command install is for a Linux server."
  say "Docker on a Mac or Windows PC does not share the network the way the cameras need."
  exit 1
fi

run() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

docker_works() {
  docker info >/dev/null 2>&1
}

if ! command -v docker >/dev/null 2>&1; then
  say "Docker is not installed. Installing it now. You may be asked for your password."
  curl -fsSL https://get.docker.com | run sh
  if [ "$(id -u)" -ne 0 ]; then
    run usermod -aG docker "$USER" || true
  fi
fi

docker_cmd() {
  if docker_works; then
    docker "$@"
  else
    run docker "$@"
  fi
}

if ! docker_cmd info >/dev/null 2>&1; then
  say "Docker is installed, but this user cannot start it."
  say "Log out and back in, then run the installer again."
  exit 1
fi

if ! docker_cmd compose version >/dev/null 2>&1; then
  say "Docker is installed, but Docker Compose is missing."
  say "Install the Compose plugin, then run the installer again."
  say "https://docs.docker.com/compose/install/"
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  say "Installing git. You may be asked for your password."
  if command -v apt-get >/dev/null 2>&1; then
    run apt-get update -y
    run apt-get install -y git
  elif command -v dnf >/dev/null 2>&1; then
    run dnf install -y git
  elif command -v pacman >/dev/null 2>&1; then
    run pacman -Sy --noconfirm git
  else
    say "Install git with your system package manager, then run the installer again."
    exit 1
  fi
fi

if [ -f "$PWD/docker-compose.yml" ] && [ -f "$PWD/cameras.example.yaml" ]; then
  root=$PWD
else
  root=${PERCH_DIR:-$HOME/perch}
  if [ ! -f "$root/docker-compose.yml" ]; then
    say "Downloading Perch into $root"
    git clone https://github.com/withintolerance/Perch.git "$root"
  fi
fi

cd "$root"

lan_ip() {
  ip -4 route get 1.1.1.1 2>/dev/null | awk '{
    for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }
  }'
}

ip_address=$(lan_ip || true)
if [ -z "$ip_address" ]; then
  ip_address=127.0.0.1
fi

if [ ! -f cameras.yaml ]; then
  sed "s/^advertise_host: .*/advertise_host: ${ip_address}/" cameras.example.yaml > cameras.yaml
  say "Wrote cameras.yaml. Live view will use ${ip_address}."
else
  say "Keeping the cameras.yaml that is already here."
fi

if [ ! -f go2rtc.yaml ]; then
  cp go2rtc.yaml.example go2rtc.yaml
fi

mkdir -p data

say "Starting Perch. The first start downloads a few things and can take a few minutes."
docker_cmd compose up -d --build

say ""
say "Perch is running."
say "On a phone or computer on the same network, open:"
say "  http://${ip_address}:8090"
say ""
say "The page starts empty. Open Settings and add a camera."
say "The address looks like rtsp://user:password@192.168.1.20:554/stream1"
say "Anyone who can open that page can also change settings, until you turn on sign-in."
