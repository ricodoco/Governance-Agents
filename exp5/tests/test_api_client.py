import json
import os
import tempfile
from unittest import mock

from exp5.sim import api_client as AC
from exp5.sim import api_run


class _R:
    def __init__(self, d): self.d = d
    def read(self): return json.dumps(self.d).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_extract_json():
    assert AC.extract_json('Sure:\n```json\n{"a": 1}\n```') == '{"a": 1}'
    assert AC.extract_json('{"a": 1} thanks') == '{"a": 1}'


def test_chat_and_cache():
    d = tempfile.mkdtemp()
    c = AC.AnthropicClient("m", cache_dir=d, api_key="k")
    reply = {"content": [{"type": "text", "text": '{"action": "decline"}'}], "usage": {"input_tokens": 5, "output_tokens": 3}, "stop_reason": "end_turn"}
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    with mock.patch("urllib.request.urlopen", return_value=_R(reply)) as u:
        a = c.chat(msgs)
        b = c.chat(msgs)
    assert a == b and a["content"] == '{"action": "decline"}' and c.calls == 1 and u.call_count == 1
    sent = json.loads(u.call_args[0][0].data)
    assert sent["system"] == "s" and sent["messages"] == [{"role": "user", "content": "u"}] and sent["temperature"] == 0.0


def test_api_run_usage():
    import pytest
    with pytest.raises(SystemExit):
        api_run.main([])
