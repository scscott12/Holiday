"""Command-only speech interruption while the skeleton is talking."""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Iterable, Optional

import numpy as np

from .audio import SpeechGate, resample_linear_int16


class BargeInAction(str, Enum):
    """What the active visit should do after speech is interrupted."""

    END_VISIT = "end_visit"
    LISTEN = "listen"


@dataclass(frozen=True)
class BargeInMatch:
    """One accepted interruption command."""

    transcript: str
    action: BargeInAction
    detected_seconds: float = 0.0


def _normalize(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


class BargeInMatcher:
    """Match only explicit commands and reject likely playback echo."""

    def __init__(
        self,
        stop_commands: Iterable[str] = ("stop", "quiet"),
        listen_commands: Iterable[str] = ("wait",),
        wake_words: Iterable[str] = ("skeleton",),
        require_wake_word: bool = False,
    ) -> None:
        stop = tuple(dict.fromkeys(filter(None, map(_normalize, stop_commands))))
        listen = tuple(dict.fromkeys(filter(None, map(_normalize, listen_commands))))
        wakes = tuple(dict.fromkeys(filter(None, map(_normalize, wake_words))))

        commands: dict[str, BargeInAction] = {}
        for command in stop:
            if not require_wake_word:
                commands[command] = BargeInAction.END_VISIT
                commands[f"please {command}"] = BargeInAction.END_VISIT
                commands[f"{command} please"] = BargeInAction.END_VISIT
        for command in listen:
            if not require_wake_word:
                commands[command] = BargeInAction.LISTEN
                commands[f"please {command}"] = BargeInAction.LISTEN
                commands[f"{command} please"] = BargeInAction.LISTEN
        for wake in wakes:
            for phrase in (wake, f"hey {wake}", f"okay {wake}", f"ok {wake}"):
                commands[phrase] = BargeInAction.LISTEN
            for command in stop:
                commands[f"{wake} {command}"] = BargeInAction.END_VISIT
                commands[f"hey {wake} {command}"] = BargeInAction.END_VISIT
            for command in listen:
                commands[f"{wake} {command}"] = BargeInAction.LISTEN
                commands[f"hey {wake} {command}"] = BargeInAction.LISTEN

        self._commands = commands

    @property
    def grammar(self) -> tuple[str, ...]:
        """Phrases supplied to Vosk's constrained grammar."""

        return tuple(self._commands) + ("[unk]",)

    def match(self, transcript: str, expected_speech: str = "") -> Optional[BargeInMatch]:
        candidate = _normalize(transcript)
        action = self._commands.get(candidate)
        if action is None:
            return None

        # The microphone hears the nearby speaker. If the exact command phrase
        # appears in the phrase being played, favor avoiding a false stop.
        expected = f" {_normalize(expected_speech)} "
        if expected.strip() and f" {candidate} " in expected:
            return None
        return BargeInMatch(transcript=candidate, action=action)


class BargeInDetector:
    """Require a stable partial result, while accepting final results at once."""

    def __init__(
        self,
        matcher: BargeInMatcher,
        partial_confirmations: int = 2,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.matcher = matcher
        self.partial_confirmations = max(1, int(partial_confirmations))
        self.clock = clock
        self.started_at = clock()
        self._candidate = ""
        self._confirmations = 0
        self._result: Optional[BargeInMatch] = None

    @property
    def result(self) -> Optional[BargeInMatch]:
        return self._result

    def inspect(
        self,
        transcript: str,
        expected_speech: str = "",
        final: bool = False,
    ) -> Optional[BargeInMatch]:
        if self._result is not None:
            return self._result

        match = self.matcher.match(transcript, expected_speech)
        if match is None:
            self._candidate = ""
            self._confirmations = 0
            return None

        if final:
            self._confirmations = self.partial_confirmations
        elif match.transcript == self._candidate:
            self._confirmations += 1
        else:
            self._candidate = match.transcript
            self._confirmations = 1

        if self._confirmations < self.partial_confirmations:
            return None

        self._result = replace(
            match,
            detected_seconds=max(0.0, self.clock() - self.started_at),
        )
        return self._result


class AnyStopEvent:
    """Event-like view that becomes set when any supplied event is set."""

    def __init__(self, *events: Any) -> None:
        self._events = tuple(event for event in events if event is not None)

    def is_set(self) -> bool:
        return any(event.is_set() for event in self._events)


class BargeInMonitor:
    """Run a constrained Vosk recognizer beside active speaker playback."""

    def __init__(
        self,
        audio_module: Any,
        recognizer_factory: Callable[[str], Any],
        matcher: BargeInMatcher,
        input_device: Any,
        capture_rate: int,
        recognition_rate: int,
        blocksize: int,
        energy_threshold: float,
        minimum_voiced_seconds: float = 0.10,
        preroll_seconds: float = 0.20,
        end_silence_seconds: float = 0.30,
        partial_confirmations: int = 2,
        parent_stop_event: Optional[threading.Event] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.audio_module = audio_module
        self.recognizer_factory = recognizer_factory
        self.matcher = matcher
        self.input_device = input_device
        self.capture_rate = int(capture_rate)
        self.recognition_rate = int(recognition_rate)
        self.blocksize = int(blocksize)
        self.energy_threshold = float(energy_threshold)
        self.minimum_voiced_seconds = float(minimum_voiced_seconds)
        self.preroll_seconds = float(preroll_seconds)
        self.end_silence_seconds = float(end_silence_seconds)
        self.partial_confirmations = int(partial_confirmations)
        self.parent_stop_event = parent_stop_event
        self.clock = clock

        self.interrupt_event = threading.Event()
        self._monitor_stop = threading.Event()
        self._expected_lock = threading.Lock()
        self._expected_speech = ""
        self._result_lock = threading.Lock()
        self._result: Optional[BargeInMatch] = None
        self.error: Optional[str] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def result(self) -> Optional[BargeInMatch]:
        with self._result_lock:
            return self._result

    @property
    def active(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def set_expected_speech(self, text: str) -> None:
        with self._expected_lock:
            self._expected_speech = str(text or "")

    def _expected(self) -> str:
        with self._expected_lock:
            return self._expected_speech

    @staticmethod
    def _result_text(payload: str, key: str) -> str:
        try:
            return str(json.loads(payload).get(key) or "").strip()
        except Exception:
            return ""

    def _parent_stopped(self) -> bool:
        return bool(
            self.parent_stop_event is not None
            and self.parent_stop_event.is_set()
        )

    def _latch(self, match: BargeInMatch) -> None:
        with self._result_lock:
            if self._result is None:
                self._result = match
                self.interrupt_event.set()

    def _run(self) -> None:
        audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=64)
        resample_state = {
            "previous": np.zeros(0, dtype=np.int16),
            "phase": 0.0,
        }
        gate = SpeechGate(
            sample_rate=self.recognition_rate,
            energy_threshold=self.energy_threshold,
            preroll_seconds=self.preroll_seconds,
            minimum_voiced_seconds=self.minimum_voiced_seconds,
            end_silence_seconds=self.end_silence_seconds,
        )
        detector = BargeInDetector(
            self.matcher,
            partial_confirmations=self.partial_confirmations,
            clock=self.clock,
        )

        def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            try:
                audio_queue.put_nowait(bytes(indata))
            except queue.Full:
                pass

        try:
            grammar = json.dumps(self.matcher.grammar)
            recognizer = self.recognizer_factory(grammar)
            with self.audio_module.RawInputStream(
                samplerate=self.capture_rate,
                blocksize=self.blocksize,
                device=self.input_device,
                dtype="int16",
                channels=1,
                callback=callback,
            ):
                while not self._monitor_stop.is_set() and not self._parent_stopped():
                    try:
                        data = audio_queue.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    samples = np.frombuffer(data, dtype=np.int16)
                    resampled = resample_linear_int16(
                        samples,
                        self.capture_rate,
                        self.recognition_rate,
                        resample_state,
                    )
                    now = self.clock()
                    gated = gate.process(resampled, now)
                    if not gated.audio.size:
                        continue

                    final = bool(recognizer.AcceptWaveform(gated.audio.tobytes()))
                    if final:
                        transcript = self._result_text(recognizer.Result(), "text")
                    else:
                        transcript = self._result_text(
                            recognizer.PartialResult(), "partial"
                        )
                    match = detector.inspect(
                        transcript,
                        expected_speech=self._expected(),
                        final=final,
                    )
                    if match is not None:
                        self._latch(match)
                        break
        except Exception as error:
            self.error = str(error)

    def start(self) -> "BargeInMonitor":
        if self._thread is not None:
            return self
        self._thread = threading.Thread(
            target=self._run,
            name="skeleton-barge-in",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self, timeout: float = 1.0) -> Optional[BargeInMatch]:
        self._monitor_stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, float(timeout)))
        return self.result
