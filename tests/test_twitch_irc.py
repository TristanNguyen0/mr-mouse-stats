import socket

import pytest

from mr_mouse_stats.twitch.irc import ProtocolViolation, ReadOnlyIrcClient


class FakeSocket:
    """Scripted socket: each recv() returns the next chunk. A None chunk
    simulates a quiet socket (timeout); StopIteration ends the connection."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = []
        self.closed = False

    def settimeout(self, value):
        pass

    def sendall(self, data):
        self.sent.append(data.decode())

    def recv(self, size):
        if not self.chunks:
            return b""  # server closed
        chunk = self.chunks.pop(0)
        if chunk is None:
            raise socket.timeout()
        return chunk

    def close(self):
        self.closed = True


def line(s):
    return (s + "\r\n").encode()


def make_client(sockets, channels=("alpha", "beta"), **kwargs):
    sockets = list(sockets)
    sleeps = []

    def connect():
        if not sockets:
            raise OSError("no more sockets scripted")
        return sockets.pop(0)

    client = ReadOnlyIrcClient(
        list(channels), nick="justinfan11111", connect=connect,
        sleep=sleeps.append, **kwargs,
    )
    return client, sleeps


def take(gen, n):
    """Consume until n real messages have been yielded; return them."""
    out = []
    for item in gen:
        if item is not None:
            out.append(item)
            if len(out) == n:
                break
    return out


PRIVMSG = ":user!user@user.tmi.twitch.tv PRIVMSG #alpha :hello"


def test_handshake_sends_only_allowed_verbs():
    sock = FakeSocket([line(PRIVMSG)])
    client, _ = make_client([sock])
    take(client.run(), 1)
    verbs = [s.split(" ", 1)[0] for s in sock.sent]
    assert verbs == ["CAP", "NICK", "JOIN"]
    assert sock.sent[0].startswith("CAP REQ :twitch.tv/tags twitch.tv/commands")
    assert sock.sent[1] == "NICK justinfan11111\r\n"
    assert sock.sent[2] == "JOIN #alpha,#beta\r\n"


def test_send_refuses_privmsg_structurally():
    client, _ = make_client([FakeSocket([])])
    client._sock = FakeSocket([])
    with pytest.raises(ProtocolViolation):
        client._send("PRIVMSG #alpha :hi")
    with pytest.raises(ProtocolViolation):
        client._send("privmsg #alpha :hi")  # case-insensitive
    assert client._sock.sent == []


def test_join_batches_are_paced():
    channels = [f"chan{i}" for i in range(40)]  # 3 batches of 15/15/10
    sock = FakeSocket([line(PRIVMSG.replace("#alpha", "#chan0"))])
    client, sleeps = make_client([sock], channels=channels)
    take(client.run(), 1)
    joins = [s for s in sock.sent if s.startswith("JOIN")]
    assert len(joins) == 3
    assert all(j.count("#") <= 15 for j in joins)
    assert sleeps[:2] == [10.0, 10.0]


def test_ping_answered_with_pong():
    sock = FakeSocket([line("PING :tmi.twitch.tv"), line(PRIVMSG)])
    client, _ = make_client([sock])
    take(client.run(), 1)
    assert "PONG :tmi.twitch.tv\r\n" in sock.sent


def test_join_confirmation_tracking():
    sock = FakeSocket([
        line(":justinfan11111!justinfan11111@justinfan11111.tmi.twitch.tv JOIN #alpha"),
        line(PRIVMSG),
    ])
    client, _ = make_client([sock])
    take(client.run(), 1)
    assert client.confirmed_joins == {"alpha"}
    assert client.unconfirmed_joins == {"beta"}


def test_heartbeat_on_quiet_socket():
    sock = FakeSocket([None, None, line(PRIVMSG)])
    client, _ = make_client([sock])
    gen = client.run()
    items = []
    for item in gen:
        items.append(item)
        if item is not None:
            break
    assert items == [None, None, items[-1]]
    assert items[-1].text == "hello"


def test_reconnects_with_backoff_after_disconnect():
    sock1 = FakeSocket([line(PRIVMSG)])  # then recv -> b"" (closed)
    sock2 = FakeSocket([line(PRIVMSG.replace("hello", "again"))])
    client, sleeps = make_client([sock1, sock2])
    messages = take(client.run(), 2)
    assert [m.text for m in messages] == ["hello", "again"]
    assert sock1.closed
    assert 1.0 in sleeps  # backoff sleep between connections
    # second handshake re-sent the JOINs
    assert any(s.startswith("JOIN") for s in sock2.sent)


def test_server_reconnect_command_triggers_reconnect():
    sock1 = FakeSocket([line(":tmi.twitch.tv RECONNECT")])
    sock2 = FakeSocket([line(PRIVMSG)])
    client, _ = make_client([sock1, sock2])
    messages = take(client.run(), 1)
    assert messages[0].text == "hello"


def test_channel_names_normalized():
    client, _ = make_client([FakeSocket([])], channels=("#Alpha", "BETA"))
    assert client.channels == ["alpha", "beta"]


def test_join_adds_channels_on_a_live_connection():
    sock = FakeSocket([line(PRIVMSG)])
    client, _ = make_client([sock])
    take(client.run(), 1)
    sock.sent.clear()

    added = client.join(["#Gamma", "delta"])
    assert added == ["gamma", "delta"]  # normalized
    assert sock.sent == ["JOIN #gamma,#delta\r\n"]
    # persisted, so a reconnect re-joins them
    assert client.channels == ["alpha", "beta", "gamma", "delta"]


def test_join_ignores_channels_already_joined():
    sock = FakeSocket([line(PRIVMSG)])
    client, _ = make_client([sock])
    take(client.run(), 1)
    sock.sent.clear()

    assert client.join(["alpha", "#BETA"]) == []
    assert sock.sent == []  # no redundant JOIN traffic


def test_join_paces_large_additions():
    sock = FakeSocket([line(PRIVMSG)])
    client, sleeps = make_client([sock])
    take(client.run(), 1)
    sock.sent.clear()
    sleeps.clear()

    client.join([f"new{i}" for i in range(20)])  # 15 + 5
    joins = [s for s in sock.sent if s.startswith("JOIN")]
    assert len(joins) == 2
    assert sleeps == [10.0]


def test_join_before_connecting_defers_to_handshake():
    client, _ = make_client([FakeSocket([])])
    assert client.join(["gamma"]) == ["gamma"]
    assert client._sock is None  # nothing sent; handshake will cover it
    assert "gamma" in client.channels
