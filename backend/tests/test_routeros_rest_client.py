from backend.routeros.rest_client import RouterOSRestClient


def test_rest_delete_uses_record_url_for_wireguard_peer_and_queue(monkeypatch):
    client = RouterOSRestClient(
        host="10.0.0.1",
        port=443,
        username="admin",
        password="secret",
    )
    calls = []

    def fake_request(method, path, json=None):
        calls.append((method, path, json))

    monkeypatch.setattr(client, "_request", fake_request)

    client.remove_wireguard_peer("wg0", "*ABC")
    client.remove_simple_queue("*Q1")

    assert calls == [
        ("DELETE", "/interface/wireguard/peers/*ABC", None),
        ("DELETE", "/queue/simple/*Q1", None),
    ]
