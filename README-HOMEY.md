# Lucent Video and Homey: movie night

*Nederlandse versie: [LEESMIJ-HOMEY.md](LEESMIJ-HOMEY.md)*

Lucent Video can tell Homey to put your living room into movie mode by itself. When you start a film or an episode, the lights go down. When the credits roll they slowly come up again, and afterwards everything is back the way it was.

This document is for whoever builds the Flow in Homey. You don't need to know anything about programming.

---

## How does it work?

At fixed moments Lucent Video sends a short signal to Homey. What happens then is up to you, in a Flow in Homey.

Lucent only sends signals for videos of **10 minutes or longer**, so nothing happens for trailers.

---

## Step 1: Set up Lucent Video

1. In Kodi, open the settings of **Lucent Video**.
2. Find **Homey cinema mode**.
3. Fill in your **Homey ID**. You find it in the Homey app under **Settings > General**.
   Do you have a Homey Pro (2023 or newer) or a Homey Pro mini? Then you may also fill in its **IP address**.
4. Press the **Test Homey** button.
5. Do you see "Homey received the signal"? Then it works.

---

## Step 2: The signals Lucent sends

Every signal goes to the webhook with the event **`lucent`**. The signal has a **tag**, which says what is happening.

The tags are fixed words. Use them exactly as written below, even though some of them are Dutch.

| Tag | When | Idea for what Homey does |
| --- | --- | --- |
| `start-film` | A film starts | Lights low, blinds closed |
| `start-serie` | An episode starts | Lights a little less low |
| `pauze` | You press pause | One lamp at half, so you can grab something |
| `verder` | You continue after a pause | Back to film or series mode |
| `aftiteling` | The credits of a film begin (see below) | Lights slowly brighter |
| `einde` | The video stops or has ended | Everything back the way it was |
| `test` | Only from the Test Homey button | Nothing, or a notification to try things out |

Good to know:

- **`aftiteling`** (credits) only comes for **films**, not for episodes.
- **`aftiteling`** only comes when TheIntroDB knows where the credits of that film begin. If TheIntroDB doesn't know, only `einde` comes.
- If a film has a scene **during or after the credits**, Lucent does **not** send `aftiteling`. The lights stay dimmed until `einde`, so you see that scene in the dark.
- Between two episodes that play one after the other by themselves, Lucent does **not** send `einde`, but it does send a new `start-serie`.
- Old versions of Lucent Video (up to and including 0.35.6) send `start` instead of `start-film` or `start-serie`. If your Flow treats `start` as a film, it works with both.

---

## Step 3: Build the Flow in Homey

Use an **Advanced Flow**. In it, one webhook card can lead to several branches.

1. Open the Homey app and make a new **Advanced Flow**.
2. As the start (When...), choose the card **Logic > A webhook has been received**.
3. At **Event**, fill in: `lucent`
   Note: exactly like that, in lower case and **without a space** after it.
4. Behind the webhook card, put a card **Logic > is exactly**.
   - Drag the **Tag** token of the webhook card into the first field.
   - Fill in the tag in the second field, for example `start-film`.
5. Behind the "is exactly" card, put what should happen, for example dimming a lamp.
6. Repeat steps 4 and 5 for every tag you want to use: `start-serie`, `pauze`, `verder`, `aftiteling` and `einde`.
7. Switch the Flow **on** and save it.

---

## Example: Movie night (Lucent)

This is how the example Flow **Filmavond (Lucent)** ("Movie night") works. Your lamps will have other names; use it as a starting point.

### What Homey remembers (variables)

The Flow uses a few variables in **Logic**:

| Variable | Type | What for |
| --- | --- | --- |
| Movie night active | yes/no | Is movie mode already on? |
| Movie night kind | text | `film`, `serie` or `einde` |
| Movie night big lamp was on | yes/no | To put the big lamp back correctly later |
| Movie night dining table was on | yes/no | The same for the dining table lamp |
| Movie night table lamp was on | yes/no | The same for the table lamp |
| Movie night table lamp brightness | number | The old brightness of the table lamp |

### On `start-film` or `start-serie`

1. Set **Movie night kind** to `film` or `serie`.
2. Switch **off** the Flow **Table lamp** (the one that reacts to the light sensor). Otherwise the lamp brightens again by itself.
3. Is **Movie night active** still **no**? Then this is the first start of the evening:
   - remember whether the big lamp, the dining table lamp and the table lamp were on, and how bright the table lamp was;
   - switch off the big lamp and the dining table lamp;
   - set **Movie night active** to **yes**.
4. Film: table lamp at **8%**, TV cabinet lights on, blinds closed.
   Series: table lamp at **25%**, TV cabinet lights on.

### On `pauze`

- Table lamp at **50%**, so you can grab something.

### On `verder`

- Table lamp back to **8%** (film) or **25%** (series). Look at **Movie night kind** for that.

### On `aftiteling`

Here the lights may already get brighter **slowly**, just like in the cinema.

1. Use the **Dim** card to set the table lamp to, for example, **40%**, with a **transition time of 20 seconds**. That way it gets lighter calmly.
2. Do **not** put everything back yet. That only happens on `einde`, so you can still watch the credits.

### On `einde`

1. Set **Movie night kind** to `einde`.
2. Wait **30 seconds**.
3. Check whether **Movie night kind** is still `einde`.
   - If it is something else, a new episode has started in the meantime. Do nothing; movie mode stays on.
   - If it is still `einde`, continue.
4. Put everything back the way it was:
   - lamps that were on go back on;
   - lamps that were off stay off;
   - the table lamp gets its old brightness back.
5. Set **Movie night active** to **no**.
6. Wait another **10 seconds** and switch the Flow **Table lamp** (light sensor) back **on**.

---

## Doesn't it work?

| What you see | What to do |
| --- | --- |
| Nothing happens at all | Press **Test Homey** in Lucent Video. Check your Homey ID or IP address. |
| Test Homey says "code 404" | The Homey ID is wrong. Locally via IP only works on a Homey Pro (2023 or newer) or Homey Pro mini. |
| Test Homey works, but the Flow doesn't start | Check that the event is exactly `lucent`, in lower case and **without a space** after it. |
| The Flow starts, but nothing else happens | Check that the tag in the **is exactly** card is spelled right, in lower case and with a hyphen: `start-film`, not `start film`. |
| Nothing happens at the credits | Normal for films TheIntroDB doesn't know, and for films with a scene after the credits. Then only `einde` comes. |
| After a crash the lights don't go back properly | Lucent couldn't send `einde`. Set your lights right by hand and set **Movie night active** to **no**. |

In the Homey app, look at the Flow under **History** to see whether it started. That tells you whether the signal arrived.
