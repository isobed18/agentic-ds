"""Concurrency signalling for the staging workspace."""


class StagingWorkspaceConflict(ValueError):
    """Raised when a client acts on a staging snapshot that is no longer latest.

    Separate from the other ValueErrors the staging routes raise, because the
    two mean opposite things to a caller. A malformed request is the client's
    mistake and repeating it will fail again; a stale base artifact is nobody's
    mistake -- the workspace simply moved -- and the same request against the
    current snapshot will succeed.

    Both used to arrive as 400, so a caller could only tell them apart by
    matching on the message text. The automation and project stores already
    distinguish their revision conflicts this way, and `updateAutomationSafely`
    on the client keys its retry off the resulting 409 (#167).

    A ValueError still, so existing handlers that catch the broad type keep
    working.
    """
