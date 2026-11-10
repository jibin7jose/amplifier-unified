"""Fail-closed keyboard chords to the exact owned managed-guest Chromium."""
import ctypes as c
import json
import os
import sys
from pathlib import Path


def main():
    result = {'route': 'guest-X11-XTEST', 'status': 'rejected', 'pressed': [],
              'released': [], 'releaseErrors': [], 'keys': sys.argv[1:]}
    display = None
    started = False
    try:
        contract = json.loads(Path(os.environ['AMPLIFIER_TEST_CONTRACT']).read_text())
        if contract.get('profileId') != 'browser-test' or contract.get('workspace') != '/workspace':
            raise ValueError('Managed browser-test contract required')
        binding = json.loads(os.environ['INLINE_MEDIA_X11_BINDING'])
        if not os.environ.get('DISPLAY') or binding['display'] != os.environ['DISPLAY']:
            raise ValueError('Owned display binding mismatch')
        pid = int(binding['pid'])
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if stat[19] != binding['startTime']:
            raise ValueError('Owned browser process identity changed')
        command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        if ('--user-data-dir=' + binding['profile']).encode() not in command:
            raise ValueError('Owned browser profile identity mismatch')
        names = sys.argv[1:]
        if not names or any(name not in {'Control_L', 'Shift_L', 'Tab', 'Escape', 'space',
                                        '0', 't', 'equal', 'minus', 'Return', 'Up', 'Down',
                                        'Left', 'Right', 'Home', 'End'} for name in names):
            raise ValueError('Unsupported qualification key chord')
        x = c.CDLL('libX11.so.6')
        t = c.CDLL('libXtst.so.6')
        x.XOpenDisplay.argtypes = [c.c_char_p]
        x.XOpenDisplay.restype = c.c_void_p
        x.XStringToKeysym.argtypes = [c.c_char_p]
        x.XStringToKeysym.restype = c.c_ulong
        x.XKeysymToKeycode.argtypes = [c.c_void_p, c.c_ulong]
        x.XKeysymToKeycode.restype = c.c_ubyte
        x.XGetInputFocus.argtypes = [c.c_void_p, c.POINTER(c.c_ulong), c.POINTER(c.c_int)]
        x.XInternAtom.argtypes = [c.c_void_p, c.c_char_p, c.c_int]
        x.XInternAtom.restype = c.c_ulong
        x.XGetWindowProperty.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong, c.c_long, c.c_long,
                                        c.c_int, c.c_ulong, c.POINTER(c.c_ulong), c.POINTER(c.c_int),
                                        c.POINTER(c.c_ulong), c.POINTER(c.c_ulong), c.POINTER(c.c_void_p)]
        x.XQueryTree.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_ulong), c.POINTER(c.c_ulong),
                                c.POINTER(c.c_void_p), c.POINTER(c.c_uint)]
        x.XFree.argtypes = [c.c_void_p]
        x.XSync.argtypes = [c.c_void_p, c.c_int]
        x.XCloseDisplay.argtypes = [c.c_void_p]
        t.XTestFakeKeyEvent.argtypes = [c.c_void_p, c.c_uint, c.c_int, c.c_ulong]
        display = x.XOpenDisplay(None)
        if not display:
            raise ValueError('Cannot open owned guest display')
        focus, revert = c.c_ulong(), c.c_int()
        x.XGetInputFocus(display, c.byref(focus), c.byref(revert))
        if focus.value <= 1:
            raise ValueError('No focused guest window')
        # Focus may be a child window. Walk only its ancestors and require the
        # first published _NET_WM_PID to identify our exact launched process.
        window, owner, lineage = focus.value, None, []
        atom = x.XInternAtom(display, b'_NET_WM_PID', 1)
        cardinal = x.XInternAtom(display, b'CARDINAL', 1)
        for _ in range(16):
            lineage.append(window)
            kind, fmt, count, remaining, data = c.c_ulong(), c.c_int(), c.c_ulong(), c.c_ulong(), c.c_void_p()
            status = x.XGetWindowProperty(display, window, atom, 0, 1, 0, cardinal,
                                         c.byref(kind), c.byref(fmt), c.byref(count), c.byref(remaining), c.byref(data))
            try:
                if status == 0 and kind.value == cardinal and fmt.value == 32 and count.value == 1 and data.value:
                    owner = c.cast(data, c.POINTER(c.c_ulong))[0]
                    break
            finally:
                if data.value:
                    x.XFree(data)
            root, parent, children, count_children = c.c_ulong(), c.c_ulong(), c.c_void_p(), c.c_uint()
            if not x.XQueryTree(display, window, c.byref(root), c.byref(parent), c.byref(children), c.byref(count_children)):
                raise ValueError('Cannot inspect focused window ancestry')
            if children.value:
                x.XFree(children)
            if parent.value in (0, window):
                break
            window = parent.value
        result.update(display=binding['display'], browserPid=pid, startTime=binding['startTime'],
                      focusWindow=focus.value, ownerWindow=window, windowPid=owner, lineage=lineage)
        if owner != pid:
            raise ValueError('Focused X window is not the owned Chromium process')
        # A negative-control expected window can only narrow this binding.
        expected = os.environ.get('INLINE_MEDIA_EXPECT_WINDOW')
        if expected and window != int(expected):
            raise ValueError('Expected owned X window mismatch')
        codes = [x.XKeysymToKeycode(display, x.XStringToKeysym(name.encode())) for name in names]
        if not all(codes):
            raise ValueError('Guest keyboard mapping unavailable')
        pressed = []
        try:
            for code in codes:
                started = True
                if not t.XTestFakeKeyEvent(display, code, 1, 0):
                    raise RuntimeError('XTEST key press rejected')
                pressed.append(code)
                result['pressed'].append(code)
            result['status'] = 'completed'
        finally:
            for code in reversed(pressed):
                if t.XTestFakeKeyEvent(display, code, 0, 0):
                    result['released'].append(code)
                else:
                    result['releaseErrors'].append(code)
            x.XSync(display, 0)
            if result['releaseErrors']:
                result['status'] = 'uncertain'
    except Exception as error:
        result.update(status='uncertain' if started else 'rejected', error=str(error))
    finally:
        if display:
            x.XCloseDisplay(display)
        print(json.dumps(result), flush=True)
    return 0 if result['status'] == 'completed' else 3 if result['status'] == 'uncertain' else 2


if __name__ == '__main__':
    sys.exit(main())
