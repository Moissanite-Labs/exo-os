"""Local proposal for the existing wtype stdin gate, with no GTK emulation."""
import subprocess
import time


def ready_for_y(ui, request_id='held_f6_a'):
    reviewed = ui.model.reviewed_approval
    return (reviewed is not None and reviewed.request_id == request_id
            and ui.card.is_active() and ui.card.armed())


def start_keys(*args, stdin=None):
    # Optional stdin preserves every existing call's inherited stdin behavior.
    return subprocess.Popen(['wtype', *args], stdin=stdin, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True)


def finish_keys_with_input(proc, payload, pump, timeout_snapshot, *, seconds=30):
    """Send input once, close EOF, and keep pumping during bounded retries."""
    deadline = time.monotonic() + seconds
    next_input = payload
    try:
        while True:
            pump()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timeout_snapshot('bounded wtype stdin delivery')
                raise AssertionError('timed out waiting for bounded wtype stdin delivery')
            try:
                stdout, stderr = proc.communicate(input=next_input, timeout=min(.02, remaining))
            except subprocess.TimeoutExpired:
                # Popen retains the pending input internally. Resending it would
                # raise ValueError and must never create another Y operation.
                next_input = None
                continue
            assert proc.returncode == 0, stderr
            return stdout, stderr
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
