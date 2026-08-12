class GiteaError(Exception):
    """User-facing error (400-class) from the Gitea conversation.

    Its own module rather than a member of ``client``: ``remotes`` and ``views``
    both raise and catch it, and importing ``client`` for an exception class
    would pull ``requests`` and the settings reads into paths that do not need
    them.
    """
