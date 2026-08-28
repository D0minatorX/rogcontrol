"""Noticing when a page's live readings have stopped coming back.

Every page in this app polls the machine on a timer -- two seconds for the
sensors, five for the battery -- through ``window.apply_async``, which turns
an exception on the worker thread into an ``error`` handed to the page's
``_on_sample``. Each page then dropped that error on the floor and returned,
on the reasoning that one failed frame is not worth a toast every two
seconds. That reasoning is right about ONE failed frame and wrong about all
of them: a page whose sampler has been raising for ten minutes shows the
same dashes as a machine that simply has no such sensor, and the only trace
is a traceback on a stderr nobody is reading.

So the rule here is neither "report every failure" nor "report none": report
once, after enough consecutive failures that it cannot be a blip, and then go
quiet again until the readings come back. Recovery resets the count, so a
sampler that fails, recovers and fails again says so twice -- which is the
signal that something is intermittent, and worth seeing.

Deliberately standard library only and free of GTK, so the counting rules can
be tested without a display. The toast is handed to a window by the caller;
:meth:`SampleFailures.failed` does the deciding and returns the sentence.
"""

from . import hardware

# Consecutive failures before the user is told. Five is about ten seconds on
# the two-second pages and half a minute on the battery page: long enough
# that a single slow nvidia-smi or a sensor file that vanished during a
# suspend cannot trigger it, short enough that a genuinely broken sampler is
# named while the user is still looking at the page it broke on.
CONSECUTIVE_FAILURE_LIMIT = 5


class SampleFailures:
    """Counts consecutive sampler failures for one page and decides when to
    speak.

    ``what`` names the readings in the user's words -- "CPU", "Battery" --
    and lands in the message as "CPU readings have stopped updating".
    """

    def __init__(self, what, limit=CONSECUTIVE_FAILURE_LIMIT):
        self.what = what
        self.limit = limit
        self.consecutive = 0
        # Whether the threshold message has already been shown for the run of
        # failures currently in progress. Without this a sampler that is
        # permanently broken would toast on every tick past the fifth, which
        # is the behaviour this class exists to avoid.
        self.reported = False

    def succeeded(self):
        """Record a frame that came back. Silent -- there is deliberately no
        "readings recovered" message.

        A recovery notice would be a second interruption for something the
        user can already see (the numbers are moving again), and on an
        intermittent sensor it would double the noise rather than halve it.
        What recovery does do is re-arm the report, so the next run of
        failures is announced rather than swallowed as "already told them"."""
        self.consecutive = 0
        self.reported = False

    def failed(self, error):
        """Record a frame that raised. Returns the sentence to show the user,
        or None for "not yet, or already said".

        The error itself is carried into the message rather than summarised.
        It is usually the one useful fact -- a missing sysfs path, a helper
        that is not installed -- and a page that says only "readings stopped"
        leaves the user with nothing to search for."""
        self.consecutive += 1
        if self.consecutive < self.limit or self.reported:
            return None
        self.reported = True
        return (f"{self.what} readings have stopped updating "
                f"({self.consecutive} failures): {error}")

    def report(self, window, error, source="app"):
        """:meth:`failed`, and then say it -- once to the window as a toast,
        once to the log.

        Both, not either. The toast is for the user who is looking at the
        page right now; the log is for the one who noticed an hour later that
        a reading had been stuck, and is the only half that survives the
        window being closed."""
        message = self.failed(error)
        if message is None:
            return
        window.toast(message)
        hardware.log(message, "ERROR", source=source,
                     dedupe_key=f"sample:{self.what}")
