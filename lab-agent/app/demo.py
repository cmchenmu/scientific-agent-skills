"""Local-only demo data used by the browser and API smoke tests."""

from __future__ import annotations

from pathlib import Path

from app.services.local_archive import LocalArchive

DEMO_PROJECT = "mouse-neuro-demo"
DEMO_USERS = {
    "student-demo": ("Student Demo", "student"),
    "research-demo": ("Research Assistant Demo", "research-assistant"),
    "pi-demo": ("PI Demo", "pi"),
}


def ensure_demo_data(root: Path) -> LocalArchive:
    """Create a tiny non-sensitive corpus and development identities once."""
    archive = LocalArchive(root)
    archive.initialize()
    source = root / "demo-tissue-sop.html"
    if not source.exists():
        source.write_text(
            "<title>Tissue Storage SOP</title><h1>Storage</h1>"
            "<p>Store tissue on ice before fixation and record the collection time. "
            "组织固定前应将组织置于冰上，并记录采集时间。</p>"
            "<h1>Safety</h1><p>Review the approved protocol before handling samples.</p>",
            encoding="utf-8",
        )
    archive.ingest(
        source,
        DEMO_PROJECT,
        ["student", "research-assistant", "pi"],
        "internal-research",
    )
    for user_id, (display_name, role) in DEMO_USERS.items():
        archive.grant_role(user_id, DEMO_PROJECT, role, display_name)
    return archive
