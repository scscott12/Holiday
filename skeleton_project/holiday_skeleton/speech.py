"""Warm Piper synthesis with direct PCM playback and live jaw movement."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Sequence

import numpy as np


@dataclass(frozen=True)
class SpeechMetrics:
    """Latency and duration measurements for one spoken response."""

    first_audio_seconds: float
    total_seconds: float
    audio_seconds: float
    frames_written: int
    interrupted: bool = False
    phrases_spoken: int = 0
    cached_phrases: int = 0


@dataclass(frozen=True)
class SpeechCacheMetrics:
    """Result of pre-rendering canned lines into the engine-local cache."""

    requested_entries: int
    new_entries: int
    existing_entries: int
    failed_entries: int
    total_entries: int
    warmup_seconds: float
    audio_seconds: float
    pcm_bytes: int
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class _CachedSpeech:
    """Raw voice audio plus its precomputed jaw envelope."""

    frames: tuple[bytes, ...]
    levels: tuple[float, ...]
    samples: int


class SpeechEngineError(RuntimeError):
    """Speech failure that records whether the visitor heard any audio."""

    def __init__(self, message: str, audio_started: bool = False) -> None:
        super().__init__(message)
        self.audio_started = audio_started


def split_pcm16_frames(
    pcm: bytes,
    sample_rate: int,
    channels: int = 1,
    frame_ms: float = 20.0,
) -> list[bytes]:
    """Split interleaved signed 16-bit PCM into frame-aligned blocks."""

    bytes_per_sample_frame = 2 * channels
    samples_per_frame = max(1, int(round(sample_rate * frame_ms / 1000.0)))
    bytes_per_frame = samples_per_frame * bytes_per_sample_frame
    usable = len(pcm) - (len(pcm) % bytes_per_sample_frame)
    return [pcm[pos : min(pos + bytes_per_frame, usable)] for pos in range(0, usable, bytes_per_frame)]


def scale_pcm16(frame: bytes, volume: float) -> bytes:
    """Apply volume to PCM without wrapping on int16 overflow."""

    volume = min(2.0, max(0.0, float(volume)))
    if not frame or abs(volume - 1.0) < 1e-6:
        return frame
    samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
    return np.clip(np.round(samples * volume), -32768, 32767).astype(np.int16).tobytes()


def jaw_envelope(
    frames: Sequence[bytes],
    channels: int = 1,
    noise_floor: float = 90.0,
) -> np.ndarray:
    """Return normalized, lightly smoothed RMS levels for PCM frames."""

    rms_values = []
    for frame in frames:
        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
        if channels > 1 and samples.size >= channels:
            samples = samples[: samples.size - (samples.size % channels)]
            samples = samples.reshape(-1, channels).mean(axis=1)
        rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
        rms_values.append(max(0.0, rms - noise_floor))

    if not rms_values:
        return np.zeros(0, dtype=np.float32)

    raw = np.asarray(rms_values, dtype=np.float32)
    reference = float(np.percentile(raw, 95))
    if reference <= 1e-6:
        return np.zeros(raw.size, dtype=np.float32)

    normalized = np.clip(raw / reference, 0.0, 1.0)
    smoothed = np.empty_like(normalized)
    previous = 0.0
    for index, level in enumerate(normalized):
        # Fast opening with a softer release keeps speech readable mechanically.
        blend = 0.75 if level >= previous else 0.45
        previous = blend * float(level) + (1.0 - blend) * previous
        smoothed[index] = previous
    return smoothed


class PiperSpeechEngine:
    """Load one Piper voice and keep one low-latency output stream warm."""

    def __init__(
        self,
        voice: Any,
        audio_module: Any,
        jaw_set: Callable[[float], None],
        volume_getter: Callable[[], float],
        rest_fraction: float,
        maximum_fraction: float,
        output_device: Any = None,
        frame_ms: float = 20.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.voice = voice
        self.audio_module = audio_module
        self.jaw_set = jaw_set
        self.volume_getter = volume_getter
        self.rest_fraction = float(rest_fraction)
        self.maximum_fraction = float(maximum_fraction)
        self.output_device = output_device
        self.frame_ms = max(5.0, float(frame_ms))
        self.clock = clock
        self.sample_rate = int(voice.config.sample_rate)
        self._cache: dict[str, _CachedSpeech] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._stream = audio_module.RawOutputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            device=output_device,
            blocksize=0,
            latency="low",
        )
        self._stream.start()

    @classmethod
    def load(
        cls,
        model_path: str,
        audio_module: Any,
        jaw_set: Callable[[float], None],
        volume_getter: Callable[[], float],
        rest_fraction: float,
        maximum_fraction: float,
        config_path: Optional[str] = None,
        output_device: Any = None,
        frame_ms: float = 20.0,
    ) -> "PiperSpeechEngine":
        """Load the ONNX voice once, then open the reusable output stream."""

        from piper import PiperVoice

        voice = PiperVoice.load(model_path, config_path=config_path)
        return cls(
            voice=voice,
            audio_module=audio_module,
            jaw_set=jaw_set,
            volume_getter=volume_getter,
            rest_fraction=rest_fraction,
            maximum_fraction=maximum_fraction,
            output_device=output_device,
            frame_ms=frame_ms,
        )

    def _restart_stream(self, interrupted: bool) -> None:
        try:
            if interrupted and hasattr(self._stream, "abort"):
                self._stream.abort()
            else:
                self._stream.stop()
            if not self._closed:
                self._stream.start()
        except Exception:
            # The original playback exception, if any, is more useful to callers.
            pass

    @staticmethod
    def _cache_key(text: Any) -> str:
        """Normalize insignificant whitespace while preserving spoken wording."""

        return " ".join(str(text or "").split())

    def _frames_from_chunk(self, chunk: Any) -> tuple[list[bytes], np.ndarray]:
        if int(chunk.sample_width) != 2:
            raise ValueError(f"unsupported Piper sample width: {chunk.sample_width}")
        if int(chunk.sample_channels) != 1:
            raise ValueError(f"unsupported Piper channel count: {chunk.sample_channels}")
        if int(chunk.sample_rate) != self.sample_rate:
            raise ValueError(
                f"Piper sample rate changed from {self.sample_rate} to {chunk.sample_rate}"
            )

        frames = split_pcm16_frames(
            chunk.audio_int16_bytes,
            sample_rate=self.sample_rate,
            channels=1,
            frame_ms=self.frame_ms,
        )
        return frames, jaw_envelope(frames)

    def _render_for_cache(self, text: str) -> _CachedSpeech:
        frames: list[bytes] = []
        levels: list[float] = []
        samples = 0
        for chunk in self.voice.synthesize(text):
            chunk_frames, chunk_levels = self._frames_from_chunk(chunk)
            frames.extend(chunk_frames)
            levels.extend(float(level) for level in chunk_levels)
            samples += sum(len(frame) // 2 for frame in chunk_frames)
        if not frames:
            raise ValueError("Piper produced no PCM audio")
        return _CachedSpeech(tuple(frames), tuple(levels), samples)

    @property
    def cache_entries(self) -> int:
        return len(self._cache)

    @property
    def cache_pcm_bytes(self) -> int:
        return sum(sum(len(frame) for frame in item.frames) for item in self._cache.values())

    def cache_phrases(self, phrases: Iterable[str]) -> SpeechCacheMetrics:
        """Pre-render unique canned lines without writing anything to the speaker.

        The cache belongs to this loaded voice instance, so restarting after a
        model, voice-config, frame-size, or prompt change cannot reuse stale
        audio. Individual failures are reported but do not disable live TTS.
        """

        requested = list(dict.fromkeys(
            key for key in (self._cache_key(text) for text in phrases) if key
        ))
        with self._lock:
            if self._closed:
                raise SpeechEngineError("speech engine is closed")
            started_at = self.clock()
            new_entries = 0
            existing_entries = 0
            failed_entries = 0
            rendered_samples = 0
            errors: list[str] = []

            for text in requested:
                if text in self._cache:
                    existing_entries += 1
                    continue
                try:
                    rendered = self._render_for_cache(text)
                except Exception as error:
                    failed_entries += 1
                    errors.append(f"{text[:48]}: {error}")
                    continue
                self._cache[text] = rendered
                new_entries += 1
                rendered_samples += rendered.samples

            return SpeechCacheMetrics(
                requested_entries=len(requested),
                new_entries=new_entries,
                existing_entries=existing_entries,
                failed_entries=failed_entries,
                total_entries=len(self._cache),
                warmup_seconds=self.clock() - started_at,
                audio_seconds=rendered_samples / float(self.sample_rate),
                pcm_bytes=self.cache_pcm_bytes,
                errors=tuple(errors),
            )

    def warm_up(self, text: str = "Ready.") -> float:
        """Run one silent inference so the first visitor avoids ONNX cold start."""

        with self._lock:
            if self._closed:
                raise SpeechEngineError("speech engine is closed")
            started_at = self.clock()
            for chunk in self.voice.synthesize(text):
                # Materialize the property because Piper synthesis is lazy.
                _ = chunk.audio_int16_bytes
            return self.clock() - started_at

    def speak(
        self,
        text: str,
        stop_event: Optional[threading.Event] = None,
        first_audio: Optional[Callable[[float], None]] = None,
    ) -> SpeechMetrics:
        """Synthesize and play text without a temporary WAV file."""

        if not text:
            return SpeechMetrics(0.0, 0.0, 0.0, 0)

        return self.speak_phrases(
            [text],
            stop_event=stop_event,
            first_audio=first_audio,
        )

    def speak_phrases(
        self,
        phrases: Iterable[str],
        stop_event: Optional[threading.Event] = None,
        first_audio: Optional[Callable[[float], None]] = None,
    ) -> SpeechMetrics:
        """Play phrases as they arrive while keeping one output stream open.

        The iterable may block while an LLM produces its next phrase.  Only
        active Piper synthesis/playback is included in ``total_seconds``; time
        waiting on the iterable is intentionally excluded.
        """

        with self._lock:
            if self._closed:
                raise SpeechEngineError("speech engine is closed")

            first_audio_seconds = 0.0
            active_seconds = 0.0
            samples_written = 0
            frames_written = 0
            phrases_spoken = 0
            cached_phrases = 0
            interrupted = False

            try:
                for text in phrases:
                    text = str(text).strip()
                    if not text:
                        continue
                    if stop_event is not None and stop_event.is_set():
                        interrupted = True
                        break

                    phrase_started_at = self.clock()
                    phrase_audio_started = False
                    cached = self._cache.get(self._cache_key(text))
                    if cached is not None:
                        cached_phrases += 1
                        rendered_chunks: Iterable[tuple[Sequence[bytes], Sequence[float]]] = (
                            (cached.frames, cached.levels),
                        )
                    else:
                        rendered_chunks = (
                            self._frames_from_chunk(chunk)
                            for chunk in self.voice.synthesize(text)
                        )

                    for frames, levels in rendered_chunks:
                        for frame, level in zip(frames, levels):
                            if stop_event is not None and stop_event.is_set():
                                interrupted = True
                                break

                            jaw_fraction = self.rest_fraction + (
                                self.maximum_fraction - self.rest_fraction
                            ) * float(level)
                            self.jaw_set(jaw_fraction)
                            self._stream.write(scale_pcm16(frame, self.volume_getter()))
                            frames_written += 1
                            samples_written += len(frame) // 2

                            if not phrase_audio_started:
                                phrase_audio_started = True
                                if phrases_spoken == 0:
                                    first_audio_seconds = self.clock() - phrase_started_at
                                    if first_audio is not None:
                                        first_audio(first_audio_seconds)

                        if interrupted:
                            break

                    active_seconds += self.clock() - phrase_started_at
                    if phrase_audio_started:
                        phrases_spoken += 1
                    self.jaw_set(self.rest_fraction)
                    if interrupted:
                        break
            except Exception as error:
                raise SpeechEngineError(
                    str(error), audio_started=frames_written > 0
                ) from error
            finally:
                self.jaw_set(self.rest_fraction)
                self._restart_stream(interrupted)

            return SpeechMetrics(
                first_audio_seconds=first_audio_seconds,
                total_seconds=active_seconds,
                audio_seconds=samples_written / float(self.sample_rate),
                frames_written=frames_written,
                interrupted=interrupted,
                phrases_spoken=phrases_spoken,
                cached_phrases=cached_phrases,
            )

    def close(self) -> None:
        """Release the persistent PortAudio stream."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cache.clear()
            self.jaw_set(self.rest_fraction)
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
