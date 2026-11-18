"""safa-audio bootstrap package (M10d).

The wheel carries the app payload plus the frozen requirement locks for
the ``cpu`` and ``cu124`` variants — no torch, no models. ``safa setup``
provisions the heavy runtime into a private env under the user data dir;
``safa`` then launches it on http://127.0.0.1:8790.
"""

__version__ = "0.1.1"
