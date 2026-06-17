"""Auto-duck (lower volume of) other apps while Talker is recording.

Wispr Flow, AquaVoice etc. do this so your Spotify/YouTube/podcast doesn't
bleed into the microphone. Windows exposes per-session volume via CoreAudio
(`IAudioSessionManager2` → `ISimpleAudioVolume`), the cleanest Python wrapper
is **pycaw**.

Optional pip install:
    pip install pycaw comtypes

Without pycaw the ducker is a no-op (Talker still records fine, just no
duck).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_OUR_PID = os.getpid()


def _release_com_cycles() -> None:
    """Collect cyclic garbage NOW, on the CALLING thread. pycaw/comtypes COM
    objects (IMMDeviceEnumerator, IAudioEndpointVolume, …) end up in reference
    cycles, so refcounting alone won't free them — they linger until some GC
    pass collects them, and if that pass runs on a *different* thread than the
    one that created them, Release() is a cross-apartment call → native access
    violation (0xc0000005) that kills the process. Forcing the collection here,
    on the thread that just created them, makes that Release happen on the
    owning thread, where it is safe."""
    try:
        import gc
        gc.collect()
    except Exception:
        pass

# Crash-resilience: when master-ducking starts we record the pre-duck volume
# here; restore() deletes it. If Talker crashes mid-duck (e.g. while recording),
# this file survives and the NEXT launch un-sticks the (possibly muted) volume.
_DUCK_STATE = Path(__file__).parent / ".duck_restore"


class AudioDucker:
    """Lower playback volume during recording, restore it afterwards. Two
    backends:
      - mode="master": single endpoint volume cut (works on EVERYTHING,
        including system sounds and apps that don't register a session).
        Most reliable. Talker itself is NOT exempt (we don't play anything).
      - mode="sessions": per-app session volumes (CoreAudio). Skips our own
        PID. Works for apps that have an active session at duck-start time.

    Both call self._restore() at stop time, idempotent.
    """

    def __init__(self, duck_level: float = 0.2, mode: str = "master") -> None:
        self._duck_level = max(0.0, min(1.0, float(duck_level)))
        self._mode = mode
        # session backend: (ISimpleAudioVolume, original_volume) tuples
        self._saved_sessions: list[tuple[Any, float]] = []
        # master backend: ONLY the original scalar to restore — never the COM
        # pointer. An IAudioEndpointVolume pointer is apartment-bound; using or
        # releasing it from a different thread than created it is a
        # cross-apartment call → native crash (access violation in _ctypes.pyd).
        self._saved_master: float | None = None
        self._active = False

    def is_available(self) -> bool:
        try:
            from pycaw.pycaw import AudioUtilities  # noqa: F401
            return True
        except ImportError:
            return False

    def set_options(self, mode: str, duck_level: float) -> None:
        self._mode = mode
        self._duck_level = max(0.0, min(1.0, float(duck_level)))

    # ── start ────────────────────────────────────────────────────────────────

    def start(self) -> bool:
        if self._active:
            return True
        try:
            from pycaw.pycaw import AudioUtilities
        except ImportError:
            logger.debug("pycaw not installed — audio ducker disabled")
            return False

        if self._mode == "master":
            ok = self._start_master()
        else:
            ok = self._start_sessions()
        self._active = True
        return ok

    @staticmethod
    def _acquire_endpoint():
        """Create a FRESH IAudioEndpointVolume on the CURRENT thread.

        COM interface pointers are apartment-bound: using OR releasing one from
        a different thread than created it is a cross-apartment call that can
        crash natively (access violation in _ctypes.pyd). _duck_start runs on
        the keyboard-hook thread while restore() can run on the GUI/worker
        thread, so we NEVER store the pointer — we re-acquire per call and let
        it be released on the same thread that made it."""
        import comtypes
        from comtypes import CLSCTX_ALL, cast, POINTER
        from pycaw.api.endpointvolume import IAudioEndpointVolume
        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
        from pycaw.constants import CLSID_MMDeviceEnumerator
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        enumerator = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator,
            IMMDeviceEnumerator,
            comtypes.CLSCTX_INPROC_SERVER,
        )
        # eRender=0 (output), eMultimedia=1 (default for app sounds)
        mm_device = enumerator.GetDefaultAudioEndpoint(0, 1)
        interface = mm_device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))

    @staticmethod
    def recover_stuck_volume() -> None:
        """Call once at startup. If a previous run ducked the master volume and
        crashed before restoring (state file left behind), put the volume back.
        Only acts when the volume is still clearly below the saved pre-duck level
        — never clobbers a level the user has since set themselves."""
        if not _DUCK_STATE.exists():
            return
        saved = None
        try:
            saved = float(_DUCK_STATE.read_text(encoding="utf-8").strip())
        except Exception:
            pass
        ep = None
        try:
            if saved is not None:
                ep = AudioDucker._acquire_endpoint()
                cur = float(ep.GetMasterVolumeLevelScalar())
                if cur < saved - 0.03:          # still ducked → un-stick it
                    ep.SetMasterVolumeLevelScalar(saved, None)
                    logger.info(f"Recovered master volume {cur:.0%} → {saved:.0%} "
                                "after unclean exit")
        except Exception:
            logger.debug("duck recovery failed", exc_info=True)
        finally:
            try:
                _DUCK_STATE.unlink(missing_ok=True)
            except Exception:
                pass
            ep = None
            _release_com_cycles()   # release the COM cycle on THIS thread

    def _start_master(self) -> bool:
        endpoint = None
        try:
            endpoint = self._acquire_endpoint()
            cur = float(endpoint.GetMasterVolumeLevelScalar())
            # Persist the pre-duck level BEFORE cutting it, so a crash mid-duck
            # leaves a breadcrumb the next launch can recover from.
            try:
                _DUCK_STATE.write_text(f"{cur:.4f}", encoding="utf-8")
            except Exception:
                pass
            endpoint.SetMasterVolumeLevelScalar(min(cur, self._duck_level), None)
            # Store ONLY the scalar; `endpoint` is released here, on this thread.
            self._saved_master = cur
            logger.info(f"Ducker(master): {cur:.0%} → {self._duck_level:.0%}")
            return True
        except Exception:
            logger.exception("master-ducker failed")
            return False
        finally:
            # Drop the COM pointer + collect its cycle here, on the creating
            # thread (never leave it for a foreign-thread GC → crash).
            endpoint = None
            _release_com_cycles()

    def _start_sessions(self) -> bool:
        from pycaw.pycaw import AudioUtilities
        try:
            sessions = AudioUtilities.GetAllSessions()
        except Exception:
            logger.exception("Could not enumerate audio sessions")
            return False
        n = 0
        for sess in sessions:
            try:
                proc = sess.Process
                if proc and proc.pid == _OUR_PID:
                    continue
                vol = sess.SimpleAudioVolume
                if vol is None:
                    continue
                cur = float(vol.GetMasterVolume())
                if cur <= self._duck_level + 0.01:
                    continue
                self._saved_sessions.append((vol, cur))
                vol.SetMasterVolume(self._duck_level, None)
                n += 1
            except Exception:
                logger.debug("Skipping a session", exc_info=True)
        if n:
            logger.info(f"Ducker(sessions): {n} → {self._duck_level:.0%}")
        else:
            logger.info("Ducker(sessions): no active sessions to duck")
        sessions = None
        _release_com_cycles()    # drop transient session COM wrappers on this thread
        return n > 0

    # ── restore ──────────────────────────────────────────────────────────────

    def restore(self) -> None:
        if not self._active:
            return
        # Master — re-acquire a fresh endpoint on THIS thread (the stored pointer
        # would be cross-apartment; calling/releasing it here would crash).
        endpoint = None
        if self._saved_master is not None:
            orig = self._saved_master
            try:
                endpoint = self._acquire_endpoint()
                endpoint.SetMasterVolumeLevelScalar(orig, None)
                logger.info(f"Ducker(master) restored to {orig:.0%}")
            except Exception:
                logger.debug("Could not restore master volume", exc_info=True)
            self._saved_master = None
            try:
                _DUCK_STATE.unlink(missing_ok=True)
            except Exception:
                pass
        # Sessions
        for vol, orig in self._saved_sessions:
            try:
                vol.SetMasterVolume(orig, None)
            except Exception:
                logger.debug("Could not restore a session volume", exc_info=True)
        self._saved_sessions.clear()
        self._active = False
        # Release every COM pointer touched above + collect their cycles on THIS
        # thread, so no apartment-bound pointer is left for a foreign-thread GC.
        endpoint = None
        _release_com_cycles()
