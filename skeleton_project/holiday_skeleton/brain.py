"""Streaming Ollama replies and low-latency phrase assembly."""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional


@dataclass(frozen=True)
class ReplyMetrics:
    """Generation measurements for one Ollama response."""

    first_token_seconds: float
    first_phrase_seconds: float
    total_seconds: float
    chunks_received: int
    phrases_emitted: int
    interrupted: bool = False


@dataclass(frozen=True)
class ReplyResult:
    """Completed or interrupted reply, including any partial text."""

    text: str
    metrics: ReplyMetrics
    error: Optional[str] = None


class PhraseChunker:
    """Turn streamed token fragments into short, speakable phrases.

    Sentence endings are preferred.  A comma or similar clause boundary is
    used once the buffer is large enough, and a word boundary is forced at the
    maximum length so a punctuation-free response cannot delay speech forever.
    """

    _STRONG_END = frozenset(".!?")
    _SOFT_END = frozenset(",;:—–")
    _CLOSERS = frozenset("\"'’”)]}")

    def __init__(
        self,
        minimum_chars: int = 12,
        soft_chars: int = 36,
        maximum_chars: int = 72,
    ) -> None:
        self.minimum_chars = max(1, int(minimum_chars))
        self.soft_chars = max(self.minimum_chars, int(soft_chars))
        self.maximum_chars = max(self.soft_chars, int(maximum_chars))
        self._buffer = ""

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _strong_boundary(self) -> Optional[int]:
        for index, character in enumerate(self._buffer):
            boundary = index + 1
            if boundary < self.minimum_chars or character not in self._STRONG_END:
                continue
            while boundary < len(self._buffer) and self._buffer[boundary] in self._CLOSERS:
                boundary += 1
            if boundary == len(self._buffer) or self._buffer[boundary].isspace():
                return boundary
        return None

    def _soft_boundary(self) -> Optional[int]:
        if len(self._buffer) < self.soft_chars:
            return None
        limit = min(len(self._buffer), self.maximum_chars)
        candidates = [
            index + 1
            for index, character in enumerate(self._buffer[:limit])
            if index + 1 >= self.minimum_chars and character in self._SOFT_END
        ]
        return candidates[-1] if candidates else None

    def _maximum_boundary(self) -> Optional[int]:
        if len(self._buffer) < self.maximum_chars:
            return None
        boundary = self._buffer.rfind(" ", self.minimum_chars, self.maximum_chars + 1)
        if boundary >= self.minimum_chars:
            return boundary
        boundary = self._buffer.find(" ", self.maximum_chars)
        return boundary if boundary >= 0 else None

    def _next_boundary(self) -> Optional[int]:
        newline = self._buffer.find("\n", self.minimum_chars)
        if newline >= 0:
            return newline
        return self._strong_boundary() or self._soft_boundary() or self._maximum_boundary()

    def _take(self, boundary: int) -> Optional[str]:
        phrase = self._clean(self._buffer[:boundary])
        self._buffer = self._buffer[boundary:].lstrip()
        return phrase or None

    def feed(self, fragment: str) -> list[str]:
        if fragment:
            self._buffer += fragment
        phrases: list[str] = []
        while self._buffer:
            boundary = self._next_boundary()
            if boundary is None:
                break
            phrase = self._take(boundary)
            if phrase:
                phrases.append(phrase)
        return phrases

    def finish(self) -> list[str]:
        phrases = self.feed("")
        tail = self._clean(self._buffer)
        self._buffer = ""
        if tail:
            phrases.append(tail)
        return phrases


class StreamingReply(Iterator[str]):
    """Background Ollama producer consumed as phrases by the controller thread."""

    _DONE = object()

    def __init__(
        self,
        http_client: Any,
        url: str,
        payload: dict[str, Any],
        timeout: Any,
        stop_event: Optional[threading.Event],
        chunker: PhraseChunker,
        clock: Callable[[], float],
        queue_size: int = 8,
    ) -> None:
        self.http_client = http_client
        self.url = url
        self.payload = payload
        self.timeout = timeout
        self.stop_event = stop_event
        self.chunker = chunker
        self.clock = clock
        self.started_at = clock()
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max(1, queue_size))
        self._cancel_event = threading.Event()
        self._response: Any = None
        self._result: Optional[ReplyResult] = None
        self._thread = threading.Thread(target=self._produce, daemon=True)
        self._thread.start()

    def _stopped(self) -> bool:
        return self._cancel_event.is_set() or (
            self.stop_event is not None and self.stop_event.is_set()
        )

    def _put(self, item: Any) -> bool:
        while not self._stopped():
            try:
                self._queue.put(item, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    @staticmethod
    def _decode_line(raw_line: Any) -> str:
        if isinstance(raw_line, bytes):
            return raw_line.decode("utf-8", "replace")
        return str(raw_line)

    def _produce(self) -> None:
        response = None
        pieces: list[str] = []
        first_token_at: Optional[float] = None
        first_phrase_at: Optional[float] = None
        chunks_received = 0
        phrases_emitted = 0
        error: Optional[str] = None

        try:
            if self._stopped():
                return
            response = self.http_client.post(
                self.url,
                json=self.payload,
                stream=True,
                timeout=self.timeout,
            )
            self._response = response
            response.raise_for_status()
            # Requests otherwise buffers larger blocks before yielding lines,
            # which works against token-level latency on localhost.
            for raw_line in response.iter_lines(chunk_size=1, decode_unicode=True):
                if self._stopped():
                    break
                line = self._decode_line(raw_line).strip()
                if not line:
                    continue
                message = json.loads(line)
                if message.get("error"):
                    raise RuntimeError(str(message["error"]))

                fragment = str(message.get("response") or "")
                if fragment:
                    now = self.clock()
                    if first_token_at is None:
                        first_token_at = now
                    chunks_received += 1
                    pieces.append(fragment)
                    for phrase in self.chunker.feed(fragment):
                        if first_phrase_at is None:
                            first_phrase_at = self.clock()
                        if not self._put(phrase):
                            break
                        phrases_emitted += 1

                if message.get("done"):
                    break

            if not self._stopped():
                for phrase in self.chunker.finish():
                    if first_phrase_at is None:
                        first_phrase_at = self.clock()
                    if not self._put(phrase):
                        break
                    phrases_emitted += 1
        except Exception as exc:
            if not self._stopped():
                error = str(exc)
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            self._response = None

            finished_at = self.clock()
            self._result = ReplyResult(
                text="".join(pieces).strip(),
                metrics=ReplyMetrics(
                    first_token_seconds=(first_token_at - self.started_at) if first_token_at else 0.0,
                    first_phrase_seconds=(first_phrase_at - self.started_at) if first_phrase_at else 0.0,
                    total_seconds=finished_at - self.started_at,
                    chunks_received=chunks_received,
                    phrases_emitted=phrases_emitted,
                    interrupted=self._stopped(),
                ),
                error=error,
            )
            try:
                self._queue.put_nowait(self._DONE)
            except queue.Full:
                pass

    def __iter__(self) -> "StreamingReply":
        return self

    def __next__(self) -> str:
        while True:
            if self._stopped():
                self.cancel()
                raise StopIteration
            if not self._thread.is_alive() and self._queue.empty():
                raise StopIteration
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._stopped() and not self._thread.is_alive():
                    raise StopIteration
                continue
            if item is self._DONE:
                raise StopIteration
            return str(item)

    @property
    def result(self) -> Optional[ReplyResult]:
        return self._result

    def cancel(self) -> None:
        self._cancel_event.set()
        response = self._response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def wait(self, timeout: Optional[float] = None) -> Optional[ReplyResult]:
        self._thread.join(timeout=timeout)
        return self._result


class OllamaStreamingClient:
    """Create streaming Ollama `/api/generate` requests."""

    def __init__(
        self,
        http_client: Any,
        url: str,
        model: str,
        system_prompt: str,
        keep_alive: str,
        options: dict[str, Any],
        timeout: Any = (3, 30),
        minimum_phrase_chars: int = 12,
        soft_phrase_chars: int = 36,
        maximum_phrase_chars: int = 72,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.http_client = http_client
        self.url = url
        self.model = model
        self.system_prompt = system_prompt
        self.keep_alive = keep_alive
        self.options = dict(options)
        self.timeout = timeout
        self.minimum_phrase_chars = minimum_phrase_chars
        self.soft_phrase_chars = soft_phrase_chars
        self.maximum_phrase_chars = maximum_phrase_chars
        self.clock = clock

    def start_reply(
        self,
        prompt: str,
        stop_event: Optional[threading.Event] = None,
    ) -> StreamingReply:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": self.system_prompt,
            "stream": True,
            "keep_alive": self.keep_alive,
            "options": self.options,
        }
        return StreamingReply(
            http_client=self.http_client,
            url=self.url,
            payload=payload,
            timeout=self.timeout,
            stop_event=stop_event,
            chunker=PhraseChunker(
                minimum_chars=self.minimum_phrase_chars,
                soft_chars=self.soft_phrase_chars,
                maximum_chars=self.maximum_phrase_chars,
            ),
            clock=self.clock,
        )
