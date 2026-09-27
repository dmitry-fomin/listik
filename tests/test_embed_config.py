"""Эмбеддинги: память в досчёте, модель и URL из config.toml (listik-sjw4, порция a).

Ollama не дёргается: `embed.ollama_embed` подменяется заглушкой, а в тестах URL —
`urllib.request.urlopen`. config.toml — временный файл через `paths.CONFIG_PATH`.
"""
from __future__ import annotations

import io
import json
import pathlib
import unittest
from unittest import mock

from listik import client, embed, paths, search, server, store
from tests.helpers import TempDbTestCase

VEC = [1.0, 0.0, 0.0, 0.0]
KINDS = ("task", "comment", "chunk", "memory")


class EmbedConfigTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        search.invalidate_vectors()
        self.addCleanup(search.invalidate_vectors)
        self.cfg_path = self.tmp_path / "config.toml"
        patcher = mock.patch.object(paths, "CONFIG_PATH", self.cfg_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls: list[str | None] = []

        def stub(texts, model=None):
            self.calls.append(model)
            return [list(VEC) for _ in texts]

        patcher = mock.patch.object(embed, "ollama_embed", side_effect=stub)
        self.stub = patcher.start()
        self.addCleanup(patcher.stop)

    def set_model(self, model: str, url: str = "http://example.invalid:1") -> None:
        self.cfg_path.write_text(f'[embed]\nmodel = "{model}"\nurl = "{url}"\n', encoding="utf-8")

    def remember(self, key: str = "note/one", text: str = "заметка про гвоздь") -> None:
        store.remember(self.conn, text, key=key, project="listik")

    def memory_rows(self) -> list:
        return self.conn.execute("SELECT memory_key, model FROM memory_embeddings").fetchall()

    def add_vec(self, table: str, key: str, model: str, task_id: str = "") -> None:
        blob = embed.vec_to_blob(VEC)
        if table == "memory":
            self.conn.execute(
                "INSERT INTO memory_embeddings(memory_key, model, dim, vec, text_hash, embedded_at)"
                " VALUES(?,?,?,?,?,?)", (key, model, 4, blob, "h", "t"))
        else:
            self.conn.execute(
                "INSERT INTO embeddings(doc_id, doc_kind, task_id, project, model, dim, vec, "
                "text_hash, embedded_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (key, "task", task_id or key, "listik", model, 4, blob, "h", "t"))
        self.conn.commit()

    # --- память считается (1–3) ---

    def test_embed_pending_defaults_include_memory_and_config_model(self) -> None:
        self.set_model("model-b")
        self.remember()
        res = embed.embed_pending(self.conn, verbose=False)
        self.assertEqual(res["model"], "model-b")
        self.assertEqual([tuple(r) for r in self.memory_rows()], [("note/one", "model-b")])
        self.assertEqual(self.calls, ["model-b"])

    def test_background_pass_fills_memory_and_invalidates(self) -> None:
        self.remember()
        with mock.patch.object(server, "get_conn", return_value=self.conn), \
             mock.patch("listik.documents.refresh_all", return_value={}), \
             mock.patch.object(search, "invalidate_vectors",
                               wraps=search.invalidate_vectors) as inval:
            server._background_pass()
        self.assertEqual(len(self.memory_rows()), 1)
        self.assertGreaterEqual(inval.call_count, 1)

    def test_api_embed_and_local_op_fill_memory(self) -> None:
        self.remember("note/one")
        with mock.patch.object(server, "get_conn", return_value=self.conn):
            status, res = server.handle("POST", "/api/embed", {}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertEqual({r["memory_key"] for r in self.memory_rows()}, {"note/one"})
        self.remember("note/two", "вторая заметка")
        res = client._local_op(self.conn, "embed", None, {})
        self.assertEqual(res["embedded"], 1)
        self.assertEqual({r["memory_key"] for r in self.memory_rows()}, {"note/one", "note/two"})

    def test_no_stale_kind_literals(self) -> None:
        root = pathlib.Path(server.__file__).parent
        for name in ("server.py", "client.py"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn('"task,comment,chunk"', text, name)
        self.assertNotIn('model=cfg["embed"]["model"]', (root / "server.py").read_text(encoding="utf-8"))

    # --- модель и URL из config.toml (4–8) ---

    def _urlopen_recorder(self, seen: list):
        def fake(req, timeout=None):
            if isinstance(req, str):
                seen.append((req, None))
                body = {"models": [{"name": "model-b:latest"}]}
            else:
                seen.append((req.full_url, json.loads(req.data)))
                body = {"embeddings": [VEC]}
            return io.BytesIO(json.dumps(body).encode("utf-8"))
        return fake

    def test_url_and_model_from_config(self) -> None:
        self.set_model("model-b")
        seen: list = []
        with mock.patch("urllib.request.urlopen", side_effect=self._urlopen_recorder(seen)):
            _real_embed(["текст"])
            health = embed.health()
        self.assertEqual(seen[0][0], "http://example.invalid:1/api/embed")
        self.assertEqual(seen[0][1]["model"], "model-b")
        self.assertEqual(seen[1][0], "http://example.invalid:1/api/tags")
        self.assertEqual(health["model"], "model-b")
        self.assertTrue(health["ok"])

    def test_defaults_without_config(self) -> None:
        self.assertFalse(self.cfg_path.exists())
        self.assertEqual(embed.settings(), {"model": paths.EMBED_MODEL,
                                            "url": paths.OLLAMA_URL.rstrip("/")})
        seen: list = []
        with mock.patch("urllib.request.urlopen", side_effect=self._urlopen_recorder(seen)):
            _real_embed(["текст"])
        self.assertEqual(seen[0][0], f"{paths.OLLAMA_URL}/api/embed")
        self.assertEqual(seen[0][1]["model"], paths.EMBED_MODEL)

    def test_vector_uses_config_model_and_explicit_model(self) -> None:
        self.set_model("model-b")
        self.add_vec("emb", "doc-a", "model-a")
        self.add_vec("emb", "doc-b", "model-b")
        hits = search.vector(self.conn, "запрос", 10)
        self.assertEqual(self.calls, ["model-b"])
        self.assertEqual([h["doc_id"] for h in hits], ["doc-b"])
        search.invalidate_vectors()
        hits = search.vector(self.conn, "запрос", 10, model="model-a")
        self.assertEqual(self.calls, ["model-b", "model-a"])
        self.assertEqual([h["doc_id"] for h in hits], ["doc-a"])

    def test_search_memories_filters_by_model(self) -> None:
        self.set_model("model-b")
        self.remember("note/a", "первая")
        self.remember("note/b", "вторая")
        self.add_vec("memory", "note/a", "model-a")
        self.add_vec("memory", "note/b", "model-b")
        found = search.search_memories(self.conn, "запрос", mode="vector")
        self.assertEqual(self.calls, ["model-b"])
        self.assertEqual([m["key"] for m in found], ["note/b"])

    def test_search_memories_skips_ollama_without_rows(self) -> None:
        self.set_model("model-b")
        self.remember("note/a", "заметка про гвоздь")
        self.assertEqual(search.search_memories(self.conn, "гвоздь", mode="vector"), [])
        self.add_vec("memory", "note/a", "model-a")
        found = search.search_memories(self.conn, "гвоздь", mode="hybrid")
        self.assertEqual(self.calls, [])
        self.assertEqual([m["key"] for m in found], ["note/a"])

    # --- пересчёт после смены модели (9–11) ---

    def test_model_change_reembeds_everything_once(self) -> None:
        spec = self.fixture_copy("long-spec.md")
        task = store.create_task(self.conn, title="задача про гвоздь", project="listik",
                                 spec_path=str(spec))
        store.add_comment(self.conn, task["id"], "комментарий про гвоздь", author="t")
        self.remember()
        self.set_model("model-a")
        embed.embed_pending(self.conn, verbose=False)
        self.assertEqual(embed.pending(self.conn, KINDS), [])

        self.set_model("model-b")
        stale = embed.pending(self.conn, KINDS)
        self.assertEqual({d["kind"] for d in stale}, set(KINDS))
        before = self.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]

        res = embed.embed_pending(self.conn, verbose=False)
        self.assertEqual(res["model"], "model-b")
        self.assertEqual(res["embedded"], len(stale))
        models = {r[0] for r in self.conn.execute("SELECT model FROM embeddings")}
        self.assertEqual(models, {"model-b"})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0], before)
        self.assertEqual([tuple(r) for r in self.memory_rows()], [("note/one", "model-b")])

        search.invalidate_vectors()
        found = search.search(self.conn, "гвоздь", mode="vector")
        self.assertIn(task["id"], json.dumps(found, ensure_ascii=False, default=str))
        mems = search.search_memories(self.conn, "гвоздь", mode="vector")
        self.assertEqual([m["key"] for m in mems], ["note/one"])

        self.assertEqual(embed.pending(self.conn, KINDS), [])


# Настоящая `ollama_embed` до подмены в setUp — для проверки URL и тела запроса.
_real_embed = embed.ollama_embed


if __name__ == "__main__":
    unittest.main()
