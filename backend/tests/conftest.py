import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for p in (BACKEND, TESTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pytest  # noqa: E402


class _MemoryKeyring:
    """Almacén de claves en memoria: pytest NUNCA toca el Administrador de credenciales real."""

    def __init__(self):
        self.store = {}

    def set_password(self, service, user, password):
        self.store[(service, user)] = password

    def get_password(self, service, user):
        return self.store.get((service, user))

    def delete_password(self, service, user):
        if (service, user) not in self.store:
            raise KeyError("no existe")
        del self.store[(service, user)]


@pytest.fixture(autouse=True)
def _local_test_env(monkeypatch, tmp_path):
    """Fase 5C: el TestClient usa Host "testserver" (se permite solo aquí); el estado del agente va a una
    carpeta temporal y la API key a un almacén en memoria."""
    monkeypatch.setenv("AUDITOR_ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("AUDITOR_DATA_DIR", str(tmp_path / "datos"))
    import keyring

    mem = _MemoryKeyring()
    monkeypatch.setattr(keyring, "set_password", mem.set_password)
    monkeypatch.setattr(keyring, "get_password", mem.get_password)
    monkeypatch.setattr(keyring, "delete_password", mem.delete_password)
    yield mem
