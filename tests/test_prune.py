from memdiff.episode import prune, msize


def convo(n):
    m = [{"role": "system", "content": "s" * 100}, {"role": "user", "content": "u" * 200}]
    for i in range(n):
        m.append({"role": "assistant", "content": "", "tool_calls": [{"id": str(i), "type": "function", "function": {"name": "x", "arguments": "{}"}}]})
        m.append({"role": "tool", "tool_call_id": str(i), "content": "t" * 900})
    return m


def test_prefix_stable_between_jumps():
    msgs = convo(5)
    cut = 0
    prefixes = []
    for k in range(60):
        msgs += convo(1)[2:]
        view, cut = prune(msgs, 20000, cut)
        assert sum(map(msize, view)) <= 20000
        assert view[0]["role"] == "system" and view[1]["role"] == "user" and view[2]["role"] != "tool"
        prefixes.append(cut)
    jumps = sum(1 for a, b in zip(prefixes, prefixes[1:]) if a != b)
    assert 1 <= jumps <= 8
