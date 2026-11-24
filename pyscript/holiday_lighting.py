# DRAFT v10 — full rework: WLED Presets + Playlist instead of a continuous
# HA-side push loop. Design doc: holiday_lighting_playlist_redesign_plan.md
#
# v10: Replaces the v1-v9 architecture entirely. set_holiday_lighting() is now
#     one-shot: it computes today's winning calendar event, builds a "plan"
#     (static single push, or a wipe-cycle/fade-cycle ring of colors), saves
#     each stage as a WLED preset (101, 102, ...), and activates an inline
#     playlist referencing them. WLED then cycles device-side, in firmware,
#     forever — no task.create, no task.sleep, no task.unique, no perpetual
#     HA-side loop that can survive a pyscript.reload() under old code (the
#     actual bug chased for hours on 2026-11-23).
#
#     The design doc's "save the playlist itself as preset 199, skip
#     rebuilding when unchanged" optimization is NOT used — live testing hit
#     a real WLED firmware race (psave is processed asynchronously on a
#     later main-loop tick, so a save queued alongside "playlist" can lose
#     to a subsequent request that clears the in-memory playlist state
#     first). Every call does a full inline rebuild + activate instead: a
#     few more flash writes per day (negligible), no race condition.
#     input_text.holiday_lighting_signature now just tracks the last stage
#     count, purely so a shrinking plan cleans up its now-unused slots.
#
#     Calendar event schema changed with it — see the migrated Fall/
#     Thanksgiving/Christmas/Winter events. `colors:` is a comma-separated
#     ring (#RRGGBB or #RRGGBBWW for the real white channel), `style:` picks
#     static/wipe-cycle/fade-cycle, and the old hand-written 6-object
#     `sequence:` JSON array is gone — a wipe-cycle's stages are generated
#     from the color ring, not typed out per holiday.
#
#     Wipe is still a real per-stage WLED effect (not a playlist crossfade —
#     the two don't mix well, see design doc §3); playlist `transition` stays
#     at 0 for wipe-cycle so Wipe's own directional animation carries the
#     "transition" look, exactly as it did before, just computed from the
#     declared color ring at build time instead of tracked live across
#     task.sleep() calls that could desync from what WLED actually received.
#
#     shell_command.wled_get_info is unused here too (same as v8/v9) —
#     effect/palette names still come from HA's own exposed attributes.
#     set_holiday_lighting_off() is much simpler now: stop the playlist,
#     turn off. No task killing, so the mysterious CONNECTION_FAILED seen
#     from the old off() during live testing shouldn't recur (untested, but
#     that function doesn't exist in this form anymore either way).

CALENDAR_ENTITY = "calendar.led_schedule"
LIGHT_ENTITY = "light.outside_outside_led"
PALETTE_SELECT_ENTITY = "select.outside_outside_led_color_palette"
WLED_HOST = "192.168.2.132"

BASE_PRESET = 101
MAX_PRESETS = 30
SIG_ENTITY = "input_text.holiday_lighting_signature"  # tracks stage count only -- see set_holiday_lighting()

# v11: psave is processed ASYNCHRONOUSLY by WLED on a later main-loop tick.
# Firing the playlist-activation POST immediately after the last psave lets a
# still-pending save land AFTER the activation and clobber it: the device ends
# up at pl=-1 (no playlist running) while every single POST returned
# {"success":true}. Observed live 2026-11-24. Settle, then verify, then retry.
PSAVE_SETTLE_SECONDS = 2.0
ACTIVATE_ATTEMPTS = 3
ACTIVATE_VERIFY_DELAY = 1.5

# v12: gap between the synchronous (verbatim) preset writes. Those go down a
# different code path in WLED than psave -- see save_preset_verbatim() -- and
# that path writes presets.json immediately from inside the HTTP handler. WLED's
# own source comments that path with "often causes presets.json corruption", so
# we space the writes out and never interleave them with pending async saves.
VERBATIM_SAVE_GAP = 0.6

DEFAULT_BRIGHTNESS = 200

FALLBACK_COLORS = [[255, 180, 100, 0]]
FALLBACK_EFFECT = "Solid"


def parse_description(description):
    """LED Schedule events store 'key: value' lines. See the design doc for
    the full field table (priority, style, colors, brightness, effect,
    speed, intensity, palette, hold, hold_effect, hold_speed,
    hold_intensity, wipe, wipe_speed, wipe_mirror, crossfade, transition,
    options)."""
    fields = {}
    for line in (description or "").splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()
    return fields


def hex_to_rgbw(token):
    """#RRGGBB (6 hex digits) or #RRGGBBWW (8 hex digits, explicit white
    channel) -> [r, g, b, w]. Uses list comprehensions, not generator
    expressions -- pyscript's AST evaluator doesn't implement
    ast_generatorexp (confirmed live, 2026-11-24)."""
    token = token.strip().lstrip("#")
    if len(token) >= 8:
        return [int(token[i:i + 2], 16) for i in (0, 2, 4, 6)]
    rgb = [int(token[i:i + 2], 16) for i in (0, 2, 4)]
    return rgb + [0]


def parse_color_ring(raw):
    return [hex_to_rgbw(tok) for tok in raw.split(",") if tok.strip()]


def resolve_effect_and_palette(effect_name, palette_name, effects, palettes):
    """Shared by every stage builder. Falls back to fx=0/pal=0 if the
    device's effect/palette lists aren't available (and logs why)."""
    if not effects:
        log.warning("holiday_lighting: no effects list available (device unreachable?) — using fx=0, pal=0")
        return 0, 0

    if effect_name not in effects:
        log.warning(f"holiday_lighting: effect '{effect_name}' not found on this WLED device, falling back to Solid")
        effect_name = "Solid" if "Solid" in effects else effects[0]
    fx_id = effects.index(effect_name)

    pal_id = 0
    if palette_name:
        if palette_name in palettes:
            pal_id = palettes.index(palette_name)
        else:
            log.warning(f"holiday_lighting: palette '{palette_name}' not found on this WLED device, using Default")

    return fx_id, pal_id


def get_wled_effects_and_palettes():
    """Effect/palette NAMES aren't usefully available from WLED's JSON API on
    this device, so read them from what HA's WLED integration already
    exposes: effect_list on the light entity, options on the palette select
    entity. Both are populated straight from the device at connect time, in
    the same index order WLED itself uses internally."""
    light_attrs = state.getattr(LIGHT_ENTITY)
    effects = list((light_attrs or {}).get("effect_list") or [])
    if not effects:
        log.error(f"holiday_lighting: no effect_list on {LIGHT_ENTITY} — is it connected/available?")

    palette_attrs = state.getattr(PALETTE_SELECT_ENTITY)
    if palette_attrs is None:
        log.warning(f"holiday_lighting: {PALETTE_SELECT_ENTITY} not found — proceeding with no palette names")
    palettes = list((palette_attrs or {}).get("options") or [])

    return effects, palettes


def base_segment(colors, fx_id, speed, intensity, pal_id, mirror=False):
    return {
        "id": 0, "start": 0, "stop": 360,
        "on": True, "bri": 255, "frz": False,
        "col": colors, "fx": fx_id, "sx": speed, "ix": intensity, "pal": pal_id,
        "rev": False, "mi": mirror, "sel": True,
    }


def build_plan(fields, effects, palettes):
    """Turns a parsed calendar event's fields into {"style", "bri", "stages"}.
    Each stage is {"seg": <WLED segment dict>, "dur_ds": ..., "trans_ds": ...}
    (dur_ds/trans_ds only matter for cycle styles — ignored for static)."""
    import json

    colors_raw = fields.get("colors") or fields.get("color")
    colors = parse_color_ring(colors_raw) if colors_raw else FALLBACK_COLORS
    style = fields.get("style") or ("static" if len(colors) <= 1 else "wipe-cycle")
    brightness = int(fields.get("brightness", DEFAULT_BRIGHTNESS) or DEFAULT_BRIGHTNESS)
    options_raw = fields.get("options")
    try:
        extra_options = json.loads(options_raw) if options_raw else {}
    except (ValueError, TypeError):
        log.warning(f"holiday_lighting: couldn't parse 'options' as JSON, ignoring: {options_raw!r}")
        extra_options = {}

    if style != "static" and fields.get("palette"):
        log.warning(f"holiday_lighting: 'palette' is ignored for style '{style}' — cycle styles force "
                    f"pal=0 so Wipe draws col[0]/col[1] directly instead of a palette gradient")

    if style == "static":
        effect_name = fields.get("effect", FALLBACK_EFFECT)
        speed = int(fields.get("speed", 128) or 128)
        intensity = int(fields.get("intensity", 128) or 128)
        fx_id, pal_id = resolve_effect_and_palette(effect_name, fields.get("palette"), effects, palettes)
        seg = base_segment([colors[0], [0, 0, 0, 0], [0, 0, 0, 0]], fx_id, speed, intensity, pal_id)
        seg.update(extra_options)
        return {"style": "static", "bri": brightness, "stages": [{"seg": seg, "dur_ds": 0, "trans_ds": 0}]}

    hold = int(fields.get("hold", 45) or 45)
    hold_effect = fields.get("hold_effect", "Solid")
    hold_speed = int(fields.get("hold_speed", 128) or 128)
    hold_intensity = int(fields.get("hold_intensity", 128) or 128)
    hold_fx_id, _ = resolve_effect_and_palette(hold_effect, None, effects, palettes)
    mirror = (fields.get("wipe_mirror", "false").strip().lower() == "true")

    n = len(colors)
    stages = []

    if style == "wipe-cycle":
        wipe_speed = int(fields.get("wipe_speed", 180) or 180)
        wipe_raw = fields.get("wipe")
        if wipe_raw:
            wipe = int(wipe_raw)
        else:
            cycle_ms = 750 + (255 - wipe_speed) * 150
            wipe = max(1, round(cycle_ms / 2 / 1000))
        wipe_fx_id, _ = resolve_effect_and_palette("Wipe", None, effects, palettes)

        for i in range(n):
            hold_seg = base_segment([colors[i], [0, 0, 0, 0], [0, 0, 0, 0]], hold_fx_id, hold_speed, hold_intensity, 0, mirror)
            hold_seg.update(extra_options)
            stages.append({"seg": hold_seg, "dur_ds": hold * 10, "trans_ds": 0})

            nxt = colors[(i + 1) % n]
            wipe_seg = base_segment([nxt, colors[i], [0, 0, 0, 0]], wipe_fx_id, wipe_speed, 128, 0, mirror)
            wipe_seg.update(extra_options)
            # reset_phase marks the stages that must be saved verbatim with
            # "tb": 0 so the Wipe always starts at LED 0 going forward.
            stages.append({"seg": wipe_seg, "dur_ds": wipe * 10, "trans_ds": 0,
                           "reset_phase": True})

    else:  # fade-cycle
        crossfade = int(fields.get("crossfade", 2) or 2)
        for i in range(n):
            seg = base_segment([colors[i], [0, 0, 0, 0], [0, 0, 0, 0]], hold_fx_id, hold_speed, hold_intensity, 0)
            seg.update(extra_options)
            stages.append({"seg": seg, "dur_ds": hold * 10, "trans_ds": crossfade * 10})

    return {"style": style, "bri": brightness, "stages": stages}


def post(payload):
    """POST a state/preset/playlist change to WLED.

    Returns True only if WLED itself answered success. IMPORTANT (v11): True
    means "the device accepted and parsed this request", NOT "the intended
    effect is now in place". Those are different claims, and conflating them
    is exactly what hid the 2026-11-24 activation failure — six preset saves
    and a playlist activation all returned success while the playlist never
    actually started. For anything whose effect matters, verify device state
    afterwards; see verify_playlist_running().

    Also hardened vs the old version: a non-zero curl exit or an empty/
    unparseable body now counts as failure instead of silently comparing
    unequal to a hardcoded string.
    """
    import json
    result = shell_command.wled_set_state(host=WLED_HOST, payload=json.dumps(payload), return_response=True)
    result = result or {}
    rc = result.get("returncode", -1)
    stdout = (result.get("stdout") or "").strip()

    ok = False
    if rc == 0 and stdout:
        try:
            ok = bool(json.loads(stdout).get("success"))
        except (ValueError, TypeError, AttributeError):
            ok = False

    if not ok:
        log.warning(f"holiday_lighting: WLED push FAILED — rc={rc} stdout={stdout!r} payload={payload}")
    return ok


def save_preset_verbatim(slot, name, seg):
    """Save a preset whose stored JSON is EXACTLY what we POST.

    Why this exists: a normal `psave` does NOT store the JSON we send -- WLED
    re-serializes the device's *current* state via serializeState(), which has
    no "tb" key, so a timebase reset can never survive into the preset. But
    presets.cpp takes a different branch when the posted object contains "o"
    and no "playlist": it strips o/v/time/error/psave and writes the remaining
    object to presets.json verbatim. That lets us embed "tb": 0.

    Why "tb": 0 matters: WLED's Wipe effect derives its position from
    `strip.now % cycleTime` -- a FREE-RUNNING clock, not time-since-effect-
    start -- so an applied Wipe preset otherwise begins at an arbitrary point,
    sometimes mid-sweep (looks like a snap) and sometimes in the reverse half
    (looks like the old color wiping back over the new one). `tb` sets
    strip.timebase = tb - millis(), so "tb": 0 zeroes strip.now. Presets are
    applied through deserializeState(), which honours "tb" and is NOT guarded
    by presetId -- so this fires on every playlist application of this preset,
    at the start of every wipe stage, device-side, with HA uninvolved.

    Only wipe stages get this. Putting it on the Solid hold presets would reset
    the clock at hold start instead, so the wipe `hold` seconds later would
    begin at (hold*1000 % cycleTime) -- actively breaking the alignment.

    Bonus: this path is synchronous (presetToSave = 0, immediate file write),
    so these three presets are not exposed to the async-psave race at all.
    """
    return post({
        "o": True,            # <- selects the verbatim/"API call" save branch
        "n": name,
        "on": True,
        "transition": 0,
        "mainseg": 0,
        "tb": 0,              # <- the whole point: zero the effect timebase
        "seg": [seg],
        "psave": slot,
    })


def read_device_state():
    """GET /json and return the parsed 'state' object, or None if the device
    is unreachable or answered something unparseable."""
    import json
    result = shell_command.wled_get_info(host=WLED_HOST, return_response=True)
    result = result or {}
    if result.get("returncode", -1) != 0:
        log.warning(f"holiday_lighting: wled_get_info failed — result={result!r}")
        return None
    try:
        return json.loads((result.get("stdout") or "").strip()).get("state")
    except (ValueError, TypeError, AttributeError):
        log.warning("holiday_lighting: wled_get_info returned unparseable JSON")
        return None


def verify_playlist_running():
    """True if the device currently reports an active playlist.

    WLED reports pl=-1 for "no playlist"; an inline (unsaved) playlist shows
    as pl=0, and one loaded from preset N shows as pl=N. So the test is
    "not -1", NOT a truthiness check — pl=0 is our normal healthy case and
    would read as falsy.
    """
    device_state = read_device_state()
    if device_state is None:
        return False
    return device_state.get("pl", -1) != -1


def stop_playlist():
    """Unload any playlist currently running on the device.

    NOTE: `{"pl": -1}` does NOT do this, despite looking like it should.
    "pl" is OUTPUT-ONLY in WLED's JSON API -- json.cpp writes it in
    serializeState() (line 661) and never reads it anywhere, so posting it is
    a silent no-op. Verified live 2026-11-24: POSTing {"pl":-1} left pl=0 and
    the playlist carried on advancing ps=105 -> 106.

    What actually unloads a playlist is a direct state POST that sets "fx" on
    a segment: json.cpp's segment handler calls unloadPlaylist() whenever fx
    is present and the request is not a preset application
    (`if (!presetId && currentPlaylist>=0) unloadPlaylist();`). So we send a
    minimal fx write. v1-v12 "worked" only because the psave POSTs that
    immediately follow also carry fx and unloaded it as a side effect.
    """
    return post({"on": True, "transition": 0, "mainseg": 0,
                 "seg": [{"id": 0, "fx": 0}]})


def pick_winning_event(events):
    """Highest priority wins; ties broken by (shortest duration, latest
    start) so a narrower/newer override beats a broad standing schedule
    without depending on hand-tuned priority numbers alone."""
    import datetime
    parsed = []
    for ev in events:
        fields = parse_description(ev.get("description"))
        try:
            priority = int(fields.get("priority", "0"))
        except ValueError:
            priority = 0
        start_raw, end_raw = ev.get("start"), ev.get("end")
        try:
            start_ts = datetime.datetime.fromisoformat(start_raw).timestamp()
        except (ValueError, TypeError):
            start_ts = 0.0
        try:
            duration = datetime.datetime.fromisoformat(end_raw).timestamp() - start_ts
        except (ValueError, TypeError):
            duration = float("inf")
        parsed.append((priority, duration, -start_ts, ev.get("summary", "?"), fields))

    parsed.sort(key=lambda p: (-p[0], p[1], p[2]))
    top = parsed[0]
    if len(parsed) > 1 and parsed[1][0] == top[0]:
        log.info(f"holiday_lighting: priority tie at {top[0]} between '{top[3]}' and '{parsed[1][3]}' — "
                 f"picked '{top[3]}' by duration/start tiebreak")
    return top[3], top[4]


@service
def set_holiday_lighting():
    """Call at dusk-minus-1h or on any LED Schedule calendar event start/end.
    Picks today's highest-priority event, builds its plan, and either does a
    single static push or builds/activates a WLED-side playlist. One-shot —
    no background task, no loop."""
    import datetime
    import json

    today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + datetime.timedelta(days=1)

    response = calendar.get_events(
        entity_id=CALENDAR_ENTITY,
        start_date_time=today_start.isoformat(),
        end_date_time=today_end.isoformat(),
    )
    events = response.get(CALENDAR_ENTITY, {}).get("events", []) if response else []

    if not events:
        log.warning(f"holiday_lighting: no LED Schedule events active for {today_start.date()} — using fallback")
        name, fields = "Fallback", {}
    else:
        name, fields = pick_winning_event(events)

    effects, palettes = get_wled_effects_and_palettes()
    plan = build_plan(fields, effects, palettes)

    if plan["style"] == "static":
        # No "pl": -1 here -- it is a no-op (see stop_playlist()). This POST
        # unloads any running playlist by virtue of carrying "fx" in its seg.
        post({"on": True, "bri": plan["bri"], "lor": 0,
              "transition": 7, "mainseg": 0, "seg": [plan["stages"][0]["seg"]]})
        log.info(f"holiday_lighting: static push for '{name}' applied")
        return

    # NOTE (2026-11-24): the design doc's "save the playlist as preset 199,
    # skip rebuilding when unchanged" optimization is NOT used here. Live
    # testing hit a real WLED firmware race: psave is processed
    # asynchronously on a later main-loop tick, so a save queued in the same
    # request as "playlist" can get serialized AFTER a later request (e.g. a
    # stop) has already cleared the in-memory playlist state, producing an
    # inconsistent/empty saved preset 199. Reproduced multiple times with
    # different symptoms (plain color snapshot instead of a playlist; preset
    # not found at all) depending on timing. Rather than ship something
    # timing-sensitive, every call does a full inline rebuild + activate --
    # more flash writes (a few/day, not a problem) and no device-side-only
    # resume shortcut, but no race condition either. `prev_count` is still
    # tracked (just the stage count, not a full signature) purely so a
    # shrinking plan cleans up its now-unused preset slots.
    prev_raw = state.get(SIG_ENTITY) or ""
    try:
        prev_count = int(prev_raw) if prev_raw else 0
    except ValueError:
        prev_count = 0

    n_stages = len(plan["stages"])
    if n_stages > MAX_PRESETS:
        log.error(f"holiday_lighting: '{name}' needs {n_stages} presets, more than MAX_PRESETS={MAX_PRESETS} — truncating")
        plan["stages"] = plan["stages"][:MAX_PRESETS]
        n_stages = MAX_PRESETS

    stop_playlist()  # stop before rewriting slots the running playlist reads from

    # Phase 1 -- time-independent stages (the Solid holds) via WLED's normal
    # asynchronous psave path.
    for i, st in enumerate(plan["stages"]):
        if st.get("reset_phase"):
            continue
        post({"on": True, "transition": 0, "mainseg": 0,
              "seg": [st["seg"]],
              "psave": BASE_PRESET + i, "n": f"HL{i:02d}",
              "ib": False, "sb": True})

    # Drain those queued async writes BEFORE any synchronous presets.json write,
    # so the two never overlap -- that overlap is the corruption mode WLED warns
    # about, since the HTTP handler and the main loop are different contexts.
    task.sleep(PSAVE_SETTLE_SECONDS)

    # Phase 2 -- wipe stages, saved verbatim so they carry "tb": 0 and reset the
    # effect timebase every time the playlist applies them.
    for i, st in enumerate(plan["stages"]):
        if not st.get("reset_phase"):
            continue
        save_preset_verbatim(BASE_PRESET + i, f"HL{i:02d}", st["seg"])
        task.sleep(VERBATIM_SAVE_GAP)

    for slot in range(BASE_PRESET + n_stages, BASE_PRESET + max(prev_count, n_stages)):
        post({"pdel": slot})

    playlist_obj = {
        "ps": [BASE_PRESET + i for i in range(n_stages)],
        "dur": [st["dur_ds"] for st in plan["stages"]],
        "transition": [st["trans_ds"] for st in plan["stages"]],
        "repeat": 0, "end": 0,
    }
    activate = {"on": True, "bri": plan["bri"], "transition": 0, "lor": 0, "playlist": playlist_obj}

    # Let the queued psave writes drain before activating, then CONFIRM the
    # playlist is actually running rather than trusting the success response.
    # This is a bounded sleep inside a one-shot service — NOT a return of the
    # v1-v9 perpetual task.create/while-True loop that caused the zombie-task
    # bug. No background task is created; the service still returns promptly.
    task.sleep(PSAVE_SETTLE_SECONDS)

    running = False
    for attempt in range(1, ACTIVATE_ATTEMPTS + 1):
        post(activate)
        task.sleep(ACTIVATE_VERIFY_DELAY)
        if verify_playlist_running():
            running = True
            if attempt > 1:
                log.warning(f"holiday_lighting: playlist needed {attempt} activation attempts "
                            f"(pending-psave race) — succeeded")
            break
        log.warning(f"holiday_lighting: playlist still not running after activation attempt "
                    f"{attempt}/{ACTIVATE_ATTEMPTS} (device reports pl=-1) — retrying")

    input_text.set_value(entity_id=SIG_ENTITY, value=str(n_stages))

    if running:
        log.info(f"holiday_lighting: rebuilt {n_stages}-stage '{plan['style']}' plan for '{name}' "
                 f"and CONFIRMED the playlist is running on the device")
    else:
        log.error(f"holiday_lighting: rebuilt {n_stages}-stage '{plan['style']}' plan for '{name}' but the "
                  f"playlist is NOT running after {ACTIVATE_ATTEMPTS} attempts — the lights are stuck on "
                  f"whatever state they were last in. Check the device is reachable and that nothing else "
                  f"(WLED phone app / web UI) is pushing state to it, since any direct state POST carrying "
                  f"'fx' unloads a running playlist (WLED json.cpp).")


@service
def set_holiday_lighting_off():
    """Call at midnight. Unloads the playlist and turns the light off.

    Both halves matter. Previously this sent `{"pl": -1}` (a no-op, see
    stop_playlist()) and then `{"on": false}`, so the lights went dark only
    because handlePlaylist() refuses to advance while bri == 0 -- the playlist
    stayed LOADED, and anything that turned the light back on overnight would
    have resumed the show mid-sequence. One POST now does both: the "seg"/fx
    write unloads the playlist, and "on": false fades out over 2 s.
    """
    post({"on": False, "transition": 20, "mainseg": 0,
          "seg": [{"id": 0, "fx": 0}]})
