# Lucent Video

Films and series for Kodi, made for the Lucent skin but working in any skin.

- `plugin.video.lucent/` — the add-on itself.
- `repo/` — the Kodi repository folder. The **Lucent Repository** (`repository.lucent` 1.0.2 or newer) reads it, so Kodi updates the add-on by itself.

Install the Lucent Repository once (see [djdirty60/Lucent](https://github.com/djdirty60/Lucent)), then install Lucent Video from it.

Homey (movie night: lights down when a film starts): see [README-HOMEY.md](README-HOMEY.md) (Nederlands: [LEESMIJ-HOMEY.md](LEESMIJ-HOMEY.md)).

Developers: the public actions for other add-ons and skins are listed in [plugin.video.lucent/README.md](plugin.video.lucent/README.md).

## Publishing a new version

1. Put the new add-on folder (with `icon.png`) somewhere, for example `/tmp/plugin.video.lucent`.
2. `python3 tools/build_repo.py /tmp/plugin.video.lucent .` (copies the source and rebuilds `repo/`).
3. Check that no keys or tokens are in the change, commit and push to `main`.
4. Kodi picks the new version up through the Lucent Repository by itself.
