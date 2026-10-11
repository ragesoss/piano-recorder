# Setting up the Raspberry Pi 3 as the control unit

Headless from start to finish: no monitor or keyboard. First done on
2026-10-10 with Raspberry Pi OS Lite (64-bit), the 2026-10-06 release
(Debian 13, Trixie).

## The files here

- `user-data` (cloud-init):
  - hostname `practice`, the time zone, locale en_US.UTF-8;
  - one user, who logs in with this desktop's SSH key, with no password
    and sudo without a password;
  - SSH on, password login off; the Wi-Fi country.
- `network-config` (netplan v2): built-in Wi-Fi (`wlan0`) to the home
  Wi-Fi, and Ethernet as a fallback.

The user name, time zone, Wi-Fi country, Wi-Fi name and password, and the
key are placeholders here (`...-HERE`). The real values go only into the
copies on the card (step 4).

**On the 2026-10-06 image, the Wi-Fi part of `network-config` does not
survive the first boot.**
- cloud-init reads it and generates the connection. The image's cloud.cfg
  then calls a Raspberry Pi module, `netplan_nm_patch`, which is not
  installed ("Could not find module named cc_netplan_nm_patch" in
  `/var/log/cloud-init.log`).
- After the first-boot reboot, only NetworkManager's default Ethernet
  connection is left, and the Pi never joins Wi-Fi.
- So: first boot on Ethernet, then add Wi-Fi over SSH (step 8). A newer
  image may fix this; if it joins Wi-Fi by itself, step 8 is not needed.

cloud-init reads both once, on first boot. Changing them after that does
nothing. Re-flash, or change the running system over SSH.

## Steps

1. **Get the image and check it.** Image URLs and SHA-256 sums are in
   `https://downloads.raspberrypi.com/os_list_imagingutility_v4.json`
   ("Raspberry Pi OS Lite (64-bit)"). Each `.img.xz` has a `.sha256` file
   beside it. Decompress with `xz -dk`.
2. **Write it to the card** (USB reader, `/dev/disk/by-id/usb-…`; check
   with `lsblk` that it is the card). It needs root:
   ```
   sudo sh -c 'dd if=IMG of=DEV bs=4M conv=fsync status=progress && cmp -n SIZE IMG DEV && echo verified'
   ```
   `SIZE` is the image's size in bytes.
3. **Copy the first-boot files** to the card's boot partition (`bootfs`;
   `udisksctl mount -b DEVp1` mounts it without root): `user-data`,
   `network-config`, and an empty file named `ssh`.
4. **Fill in the placeholders** in the card's copies:
   - in `user-data`:
     - `USER-NAME-HERE`: the user name;
     - `TIME-ZONE-HERE`: a tz database name, for example Europe/Berlin;
     - `WIFI-COUNTRY-HERE`: the Wi-Fi country, two letters, for example DE;
     - `SSH-PUBLIC-KEY-HERE`: this desktop's public key, the one line in
       its `~/.ssh/*.pub` file (for example `~/.ssh/id_ed25519.pub`);
   - in `network-config`:
     - `WIFI-NAME-HERE`: the Wi-Fi network's name (SSID);
     - `WIFI-COUNTRY-HERE`: the same two letters as in `user-data`;
     - `WIFI-PASSWORD-HERE`: the Wi-Fi password.
5. **Unmount and eject:** `udisksctl unmount -b DEVp1`, then
   `udisksctl power-off -b DEV`.
6. **Boot the Pi.** Use a 5 V, 2.5 A supply, with nothing in its USB ports
   (no Edimax Wi-Fi adapter: the built-in Wi-Fi keeps the USB bus free for
   the mixer). The first boot resizes the filesystem and reboots once.
   Allow 2-3 minutes.
   Plug in an Ethernet cable for the first boot (see above).
7. **Find it:** `ping practice.local`, or the router's client list, or
   `nmap -sn` on your LAN (for example `nmap -sn 192.168.1.0/24`; a Pi 3's
   MAC address starts with `b8:27:eb`). Then `ssh USER@practice.local`,
   with the user name from `user-data`.
8. **Wi-Fi, over SSH.** Run this in a terminal, with `USER` and
   `WIFI-NAME-HERE` filled in (in double quotes if the name has spaces).
   It asks for the password on the Pi, so the password goes nowhere else:
   ```
   ssh -t USER@practice.local 'read -rsp "Wi-Fi password: " P; echo; sudo nmcli connection add type wifi ifname wlan0 con-name WIFI-NAME-HERE ssid WIFI-NAME-HERE wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$P" && sudo nmcli connection up WIFI-NAME-HERE; unset P'
   ```
   - `wpa-psk` (WPA2) matters if the Wi-Fi is mixed WPA2/WPA3: the Pi 3B's
     Wi-Fi does WPA2 only.
   - Then turn off Wi-Fi power saving, which causes lag and dropouts on a
     Pi that should always answer:
     `sudo nmcli connection modify WIFI-NAME-HERE 802-11-wireless.powersave 2`.
   - Reboot. Check that it comes back on Wi-Fi with the cable out. Reserve
     its Wi-Fi address on the router (slot 7 in `docs/components.md`).

Problems with cloud-init show in `/var/log/cloud-init.log` and in
`cloud-init status --long`.
