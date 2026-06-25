"""Project Phoenix visual dashboard.

A pure-standard-library web dashboard that lets you watch the digital twin run
a drive cycle like a vehicle instrument cluster, and view the regression test
suite as live pass/fail cards.

Run it with:

    .venv\\Scripts\\python.exe -m dashboard

then open the printed URL (default http://127.0.0.1:8000) in a browser.
"""

from .server import serve, build_app

__all__ = ["serve", "build_app"]
