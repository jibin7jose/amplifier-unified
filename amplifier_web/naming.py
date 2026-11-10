"""Native session names with a disposable Unified presentation cache."""
from contextlib import contextmanager, ExitStack
import io
import json
import os
import re
from pathlib import Path

from amplifier_foundation.session.metadata import NAMING_FIELDS, SessionMetadataStore

FIELDS = (*NAMING_FIELDS, 'naming_completed_inputs', 'name_auto', 'name_auto_revision', 'name_policy_revision', 'naming_refinement')
PLACEHOLDERS = {'New chat', 'New conversation', 'A new conversation', 'Untitled conversation'}


TITLE_LIMIT = 64
_REMINDER = re.compile(r'<system-reminder\b[^>]*>.*?</system-reminder>', re.DOTALL | re.IGNORECASE)
_LEADING_NOISE = re.compile(r'^(?:(?:#{1,6}|>|[-*+]|\d+[.)]|```\w*)\s+|@\S+\s+)+')
_CONTEXT_FILE = re.compile(r'^<context_file\b[^>]*>', re.IGNORECASE)


def request_text(text):
    """Omit leading injected context files from request-derived labels."""
    if not isinstance(text, str):
        return ''
    text = _REMINDER.sub(' ', text).lstrip()
    while _CONTEXT_FILE.match(text):
        end = text.lower().find('</context_file>')
        if end < 0:
            # An incomplete context record is not a user request preview.
            return ''
        text = text[end + len('</context_file>'):].lstrip()
    return text


def fallback_title(text, limit=TITLE_LIMIT):
    """A readable interim title from a user's request, or None when empty."""
    if not isinstance(text, str):
        return None
    text = ' '.join(request_text(text).replace('```', ' ').split())
    if not text:
        return None
    stripped = _LEADING_NOISE.sub('', text + ' ').strip()
    text = stripped or text  # A message of only mentions keeps its words.
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    space = cut.rfind(' ')
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(' ,;:-–—') + '…'


def automatic(session):
    if 'autoName' in session:
        return bool(session['autoName'])
    return session.get('titleSource') != 'manual' and session.get('nativeNameSource') != 'manual'


def automatic_metadata(metadata):
    if 'name_auto' in metadata:
        # An explicit rename by another host takes precedence over a previous
        # opt-in. Generated writes advance this marker with the name revision.
        return bool(metadata['name_auto']) and not (metadata.get('name_source') == 'manual'
            and metadata.get('name_revision', 0) != metadata.get('name_auto_revision', 0))
    return not metadata.get('name') or metadata.get('name_source') in {'fallback', 'generated'}


def set_automatic(home, session, enabled):
    from amplifier_foundation.session.metadata import metadata_lock
    store = SessionMetadataStore(directory_for(home, session))
    if store.history.exists():
        with metadata_lock(store.history.session_dir):
            current = store.read()
            current.update(name_auto=enabled, name_auto_revision=current.get('name_revision', 0),
                           name_policy_revision=current.get('name_policy_revision', 0) + 1)
            # Foundation's lock and existing atomic metadata writer are shared
            # with its name writer; no separate name store or transcript write.
            store.history._save_metadata_unlocked(current)
            session['nativeNameRevision'] = current.get('name_revision', 0)
            session['nativeNamePolicyRevision'] = current.get('name_policy_revision', 0)
    session['autoName'] = enabled


def accept_generated(directory, candidate, *, explicit=False, refinement=None):
    """CAS both the name and its policy; a late result never wins over edits."""
    from amplifier_foundation.session.metadata import metadata_lock
    from datetime import datetime, UTC
    store = SessionMetadataStore(directory)
    with metadata_lock(store.history.session_dir):
        current = store.read()
        revision = current.get('name_revision', 0)
        if candidate.get('name_revision', 0) != revision or candidate.get('name_policy_revision', 0) != current.get('name_policy_revision', 0):
            return current, False
        enabled = automatic_metadata(current)
        if not explicit and not enabled:
            return current, False
        title = candidate.get('name')
        if not isinstance(title, str) or not title.strip():
            raise ValueError('The naming model did not return a chat name.')
        title = title.strip()[:200]
        description = candidate.get('description', current.get('description'))
        if (title == current.get('name') and description == current.get('description')
                and current.get('name_source') == ('generated' if enabled else 'manual')):
            return current, False
        now = datetime.now(UTC).isoformat()
        current.update(name=title, name_source='generated' if enabled else 'manual',
                       name_revision=revision + 1, name_updated_at=now, name_generated_at=now)
        if refinement is not None:
            current['naming_refinement'] = refinement
        if 'name_auto' in current:
            current['name_auto_revision'] = revision + 1
        if isinstance(candidate.get('description'), str):
            current.update(description=candidate['description'][:1000], description_updated_at=now)
        store.history._save_metadata_unlocked(current)
        return current, True


def claim_refinement(directory, base, user_digest):
    """Consume the one early attempt before inference; unknown calls never replay."""
    from amplifier_foundation.session.metadata import metadata_lock
    store = SessionMetadataStore(directory)
    with metadata_lock(store.history.session_dir):
        current = store.read()
        marker = current.get('naming_refinement') or {}
        if (not automatic_metadata(current) or marker.get('status') != 'pending'
                or marker.get('user_digest') == user_digest
                or any(current.get(key, 0) != base.get(key, 0)
                       for key in ('name_revision', 'name_policy_revision'))):
            return False
        current['naming_refinement'] = {**marker, 'status': 'consumed'}
        store.history._save_metadata_unlocked(current)
        return True


@contextmanager
def _open_presentation(path):
    """Read replaceable naming presentation, not a private/immutable snapshot.

    Keep ordinary path following and text decoding. On Windows the retained
    leaf must allow its publisher to replace it; no traversal pins or ACL policy
    belong to these two legacy presentation reads.
    """
    if os.name != 'nt':
        with path.open('r') as stream:
            yield stream
        return
    import ctypes as C
    import msvcrt

    filename = str(path)
    if '\0' in filename:
        raise ValueError('embedded null character')
    kernel = C.WinDLL('kernel32', use_last_error=True)
    create, close = kernel.CreateFileW, kernel.CloseHandle
    create.restype = C.c_void_p
    create.argtypes = [C.c_wchar_p, C.c_uint32, C.c_uint32, C.c_void_p,
                       C.c_uint32, C.c_uint32, C.c_void_p]
    close.restype, close.argtypes = C.c_int32, [C.c_void_p]
    # GENERIC_READ, SHARE_READ|WRITE|DELETE, OPEN_EXISTING, NORMAL attributes.
    # NULL security attributes make the handle non-inheritable. No reparse flag:
    # like Path.read_text, this follows an ordinary symlink and accepts relative paths.
    handle = create(filename, 0x80000000, 7, None, 3, 0x80, None)
    if handle == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        close(handle)
        raise
    try:
        # The context alone owns fd. A failed wrapper constructor may consume
        # and close its input; closefd=False prevents it from closing/recycling
        # our descriptor before the outer cleanup. Unwind wrappers first.
        with ExitStack() as wrappers:
            raw = wrappers.enter_context(io.FileIO(fd, 'r', closefd=False))
            buffered = wrappers.enter_context(io.BufferedReader(raw))
            text = wrappers.enter_context(io.TextIOWrapper(buffered))
            # Default encoding and universal newlines match Path.read_text.
            yield text
    finally:
        os.close(fd)


def legacy(directory):
    try:
        with _open_presentation(Path(directory) / 'naming.json') as stream:
            text = stream.read()
        value = json.loads(text)
        return {k: v for k, v in value.items() if k in FIELDS} if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def read(directory):
    metadata = SessionMetadataStore(directory).read()
    old = legacy(directory)
    # Legacy data is a read-only fallback, never an override of a native name.
    value = old if not metadata.get('name') else {k: v for k, v in old.items() if k == 'naming_completed_inputs'}
    return {**value, **{k: v for k, v in metadata.items() if k in FIELDS}}


def directory_for(home, session):
    if session.get('nativeProject'):
        from .automatic_history import directory
        return directory(session)
    from .host.storage import SessionStore
    return SessionStore.for_app(home, session.get('workspace') or Path.cwd()).directory(session.get('runtimeSessionId') or session['id'])


def initial_name(directory, session=None):
    old = legacy(directory)
    if session is None:
        try:
            with _open_presentation(Path(directory) / 'unified' / 'view.json') as stream:
                text = stream.read()
            session = json.loads(text)
        except (OSError, ValueError):
            session = {}
    if not isinstance(session, dict):
        session = {}
    title = old.get('name') or session.get('title')
    source = old.get('name_source') or session.get('titleSource')
    source = source if source in {'manual', 'generated'} else 'fallback'
    return title, source, old, session


def adopt(directory, session=None):
    """Fill missing native names without replacing an existing host's choice."""
    store = SessionMetadataStore(directory)
    if not store.history.exists():
        return {}  # An empty composer is not a native execution session.
    current = store.read()
    if current.get('name'):
        return current
    title, source, old, session = initial_name(directory, session)
    if isinstance(title, str) and title.strip():
        return store.set_name(title.strip()[:200], source=source,
                              description=old.get('description') or session.get('description'),
                              only_if_missing=True)
    return store.read()


def refresh(home, session, *, migrate=False):
    directory = directory_for(home, session)
    metadata = adopt(directory, session) if migrate else SessionMetadataStore(directory).read()
    if metadata.get('name'):
        session['nativeNameRevision'] = metadata.get('name_revision', 0)
        session['nativeNamePolicyRevision'] = metadata.get('name_policy_revision', 0)
        source = metadata.get('name_source', 'manual')
        display_source = ('manual' if session.get('titleSource') == 'manual' else 'native') if session.get('historyManaged') else source
        session.update(title=metadata['name'], titleSource=display_source, nativeNameSource=source)
        session['autoName'] = automatic_metadata(metadata)
    if 'description' in metadata:
        session['description'] = metadata['description']
    return metadata


def prepare_regeneration(home, session):
    """Finish first-use naming migration before taking the click-time guard.

    A worker checkpoint may otherwise initialize the native name/policy after
    the snapshot, making our own initialization look like a concurrent edit.
    Existing choices are never overwritten; later edits still invalidate it.
    """
    from .host.storage import SessionStore
    directory = directory_for(home, session)
    _, _, _, view = initial_name(directory)
    current = SessionMetadataStore(directory).read()
    enabled = automatic_metadata(current) if current.get('name') else view.get('autoName', automatic(session))
    adopt(directory, session)
    SessionStore._initial_naming_policy(directory, enabled)
    refresh(home, session)
    return read(directory)


def persist(home, session, *, shared_rename=False, expected_revision=None):
    directory = directory_for(home, session)
    store = SessionMetadataStore(directory)
    if not store.history.exists():
        return  # Unified's view retains the title until the first native save.
    source = session.get('titleSource', 'automatic')
    title = session.get('title', '')
    if title and (shared_rename or title not in PLACEHOLDERS):
        store.set_name(title[:200], source='manual' if shared_rename else
                       ('generated' if source == 'generated' else 'fallback'),
                       description=session.get('description') if source == 'generated' and not shared_rename else None,
                       expected_revision=expected_revision)
    refresh(home, session)
