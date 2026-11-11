from __future__ import annotations

import os

from .ui.main_window import ArchiveBuilderApp


def main() -> None:
    app = ArchiveBuilderApp()
    if os.environ.get("ARCHIVE_BUILDER_STARTUP_PROBE") == "1":
        app.after(250, app.destroy)
    app.mainloop()
