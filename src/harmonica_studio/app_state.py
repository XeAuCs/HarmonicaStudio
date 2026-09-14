"""Application-owned state, independent of desktop widgets and HTTP."""
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Transport(str, Enum):
    READY = 'ready'
    PLAYING = 'playing'
    PAUSED = 'paused'
    ENDED = 'ended'


class FollowUp(str, Enum):
    NONE = 'none'
    PLAY = 'play'
    GAME = 'game'
    ARM = 'arm'


@dataclass
class AppState:
    project: dict | None = None
    project_path: Path | None = None
    auto_project_path: Path | None = None
    source: Path | None = None
    parts: dict = field(default_factory=dict)
    names: dict = field(default_factory=dict)
    keys: list = field(default_factory=list)
    selected_part: tuple | None = None
    result: tuple | None = None
    revision: int = 0
    document_id: int = 0
    saved_revision: int = 0
    autosave_revision: int = -1
    save_error: str = ''
    transition: str | None = None
    export_revision: int = -1
    score_duration: float = 0.0
    transport: Transport = Transport.READY
    logical_seek: float = 0.0
    position: float = 0.0
    preview_duration: float = 0.0
    position_label: str = '未播放'
    show_cursor: bool = False
    message: str = '从曲库选歌，或拖入 MIDI。'
    library_revision: int = 0
    library_root: Path | None = None
    library_error: str = ''
    closed: bool = False

    @property
    def project_dirty(self):
        return self.project is not None and self.revision != self.saved_revision

    @property
    def export_dirty(self):
        return self.project is not None and (self.result is None or self.revision != self.export_revision)

    @property
    def has_notes(self):
        return bool(self.project and self.project['notes'])

    def set_project(self, project, *, saved=False, new_document=False):
        self.project = project
        self.revision += 1
        if new_document:
            self.document_id += 1
            self.autosave_revision = -1
            self.save_error = ''
        self.score_duration = max((n['end'] for n in project['notes']), default=0) if project else 0
        if saved:
            self.saved_revision = self.revision
