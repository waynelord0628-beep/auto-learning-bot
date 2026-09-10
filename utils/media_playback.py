"""Start an unstarted jPlayer video using its normal playback API."""

START_VIDEO = """
// Observe every native video; starting playback remains limited to known jPlayer controls.
const videos = Array.from(document.querySelectorAll('video'));
for (const v of videos) {
    if (!v.paused && !v.ended && !v.error) return 'playing';
    if (arguments[1]) continue;
    if (v.error || v.ended || v.currentTime > 0.1 || v.readyState < 2) continue;
    if (!v.closest('.jp-jplayer') || !window.jQuery || !jQuery.fn.jPlayer) continue;
    const player = jQuery(v.closest('.jp-jplayer'));
    if (arguments[0]) player.jPlayer('mute');
    player.jPlayer('play');
    return 'requested';
}
return 'unavailable';
"""


def start_unstarted_video(driver, muted=False, inspect_only=False):
    """Inspect at most 32 frames; always restore the classroom frame context.

    Does not seek, change speed, restart a paused lesson, or alter study records.
    'requested' means a play request was issued, not proof of advancing playback.
    """
    remaining = 32

    def visit(depth):
        nonlocal remaining
        remaining -= 1
        state = driver.execute_script(START_VIDEO, muted, inspect_only)
        if state != 'unavailable':
            return state
        if depth >= 6:
            return state
        for frame in driver.find_elements('css selector', 'frame,iframe'):
            if remaining <= 0:
                break
            entered = False
            try:
                driver.switch_to.frame(frame)
                entered = True
                state = visit(depth + 1)
                if state != 'unavailable':
                    return state
            except Exception:
                pass  # A navigating or inaccessible frame can be retried later.
            finally:
                if entered:
                    driver.switch_to.parent_frame()
        return 'unavailable'

    try:
        driver.switch_to.default_content()
        return visit(0)
    except Exception:
        return 'unavailable'
    finally:
        driver.switch_to.default_content()
