"""Send edits immediately, retaining the newest edit while USB is busy."""


class LatestChangeQueue:
    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.busy = False
        self.pending = None

    def submit(self, settings):
        if self.busy:
            self.pending = settings
            return
        self.busy = True
        self.dispatch(settings)

    def complete(self):
        if self.pending is not None:
            settings, self.pending = self.pending, None
            self.dispatch(settings)
        else:
            self.busy = False
