import json
import threading
import unittest

from holiday_skeleton.brain import OllamaStreamingClient, PhraseChunker


class FakeResponse:
    def __init__(self, messages):
        self.lines = [json.dumps(message).encode("utf-8") for message in messages]
        self.closed = False
        self.iter_line_kwargs = []

    def raise_for_status(self):
        return None

    def iter_lines(self, **kwargs):
        self.iter_line_kwargs.append(kwargs)
        yield from self.lines

    def close(self):
        self.closed = True


class FakeHttp:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class GatedResponse(FakeResponse):
    def __init__(self, first_message, final_message, release_event):
        super().__init__([])
        self.first_message = first_message
        self.final_message = final_message
        self.release_event = release_event

    def iter_lines(self, **kwargs):
        self.iter_line_kwargs.append(kwargs)
        yield json.dumps(self.first_message).encode("utf-8")
        self.release_event.wait(timeout=1.0)
        yield json.dumps(self.final_message).encode("utf-8")


class BlockingResponse(FakeResponse):
    def __init__(self):
        super().__init__([])
        self.started = threading.Event()
        self.released = threading.Event()

    def iter_lines(self, **kwargs):
        self.iter_line_kwargs.append(kwargs)
        self.started.set()
        self.released.wait(timeout=1.0)
        if not self.closed:
            yield json.dumps({"response": "Too late.", "done": True}).encode("utf-8")

    def close(self):
        super().close()
        self.released.set()


class PhraseChunkerTests(unittest.TestCase):
    def test_sentence_boundary_can_span_token_fragments(self):
        chunker = PhraseChunker(minimum_chars=8, soft_chars=24, maximum_chars=48)

        self.assertEqual(chunker.feed("Ahoy there, "), [])
        self.assertEqual(chunker.feed("matey! The tide"), ["Ahoy there, matey!"])
        self.assertEqual(chunker.finish(), ["The tide"])

    def test_long_clause_uses_soft_punctuation(self):
        chunker = PhraseChunker(minimum_chars=8, soft_chars=20, maximum_chars=48)

        phrases = chunker.feed("The ocean is plotting again, and I do not trust it")

        self.assertEqual(phrases, ["The ocean is plotting again,"])
        self.assertEqual(chunker.finish(), ["and I do not trust it"])

    def test_punctuation_free_text_is_bounded_at_a_word(self):
        chunker = PhraseChunker(minimum_chars=5, soft_chars=20, maximum_chars=24)

        phrases = chunker.feed("one two three four five six seven eight nine")

        self.assertEqual(phrases, ["one two three four five"])
        self.assertEqual(chunker.finish(), ["six seven eight nine"])


class OllamaStreamingClientTests(unittest.TestCase):
    def make_client(self, messages):
        self.response = FakeResponse(messages)
        self.http = FakeHttp(self.response)
        return OllamaStreamingClient(
            http_client=self.http,
            url="http://ollama/api/generate",
            model="tiny",
            system_prompt="Be brief.",
            keep_alive="24h",
            options={"num_predict": 50},
            minimum_phrase_chars=8,
            soft_phrase_chars=24,
            maximum_phrase_chars=48,
        )

    def test_streams_phrases_and_retains_complete_reply(self):
        client = self.make_client([
            {"response": "Ahoy there, ", "done": False},
            {"response": "matey! ", "done": False},
            {"response": "The tide is weird.", "done": True},
        ])

        reply = client.start_reply("Hello")
        phrases = list(reply)
        result = reply.result

        self.assertEqual(phrases, ["Ahoy there, matey!", "The tide is weird."])
        self.assertEqual(result.text, "Ahoy there, matey! The tide is weird.")
        self.assertEqual(result.metrics.chunks_received, 3)
        self.assertEqual(result.metrics.phrases_emitted, 2)
        self.assertGreaterEqual(result.metrics.first_token_seconds, 0.0)
        self.assertGreaterEqual(result.metrics.first_phrase_seconds, 0.0)
        self.assertGreaterEqual(result.metrics.total_seconds, 0.0)
        self.assertIsNone(result.error)
        self.assertTrue(self.response.closed)

        url, kwargs = self.http.calls[0]
        self.assertEqual(url, "http://ollama/api/generate")
        self.assertTrue(kwargs["stream"])
        self.assertTrue(kwargs["json"]["stream"])
        self.assertEqual(kwargs["json"]["prompt"], "Hello")
        self.assertEqual(self.response.iter_line_kwargs[0]["chunk_size"], 1)

    def test_server_error_is_exposed_without_crashing_consumer(self):
        client = self.make_client([{"error": "model unavailable", "done": True}])

        reply = client.start_reply("Hello")

        self.assertEqual(list(reply), [])
        self.assertEqual(reply.result.error, "model unavailable")
        self.assertEqual(reply.result.text, "")

    def test_first_phrase_is_available_before_generation_finishes(self):
        release = threading.Event()
        response = GatedResponse(
            {"response": "First phrase. ", "done": False},
            {"response": "Second phrase.", "done": True},
            release,
        )
        self.response = response
        self.http = FakeHttp(response)
        client = OllamaStreamingClient(
            http_client=self.http,
            url="http://ollama/api/generate",
            model="tiny",
            system_prompt="Be brief.",
            keep_alive="24h",
            options={},
            minimum_phrase_chars=8,
            soft_phrase_chars=24,
            maximum_phrase_chars=48,
        )

        reply = client.start_reply("Hello")

        self.assertEqual(next(reply), "First phrase.")
        self.assertIsNone(reply.result)
        release.set()
        self.assertEqual(list(reply), ["Second phrase."])
        self.assertEqual(reply.result.text, "First phrase. Second phrase.")

    def test_pre_cancelled_reply_does_not_open_http_request(self):
        client = self.make_client([])
        stop_event = threading.Event()
        stop_event.set()

        reply = client.start_reply("Hello", stop_event=stop_event)

        self.assertEqual(list(reply), [])
        self.assertTrue(reply.result.metrics.interrupted)
        self.assertEqual(self.http.calls, [])

    def test_stop_closes_an_in_flight_stream(self):
        response = BlockingResponse()
        http = FakeHttp(response)
        client = OllamaStreamingClient(
            http_client=http,
            url="http://ollama/api/generate",
            model="tiny",
            system_prompt="Be brief.",
            keep_alive="24h",
            options={},
        )
        stop_event = threading.Event()
        reply = client.start_reply("Hello", stop_event=stop_event)
        self.assertTrue(response.started.wait(timeout=0.5))

        stop_event.set()

        self.assertEqual(list(reply), [])
        result = reply.wait(timeout=0.5)
        self.assertTrue(response.closed)
        self.assertTrue(result.metrics.interrupted)


if __name__ == "__main__":
    unittest.main()
