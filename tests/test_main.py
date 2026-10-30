import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alerta_preco.__main__ import main


class TestConferirLeitura(unittest.TestCase):
    def setUp(self) -> None:
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.pasta = Path(pasta.name)
        for nome in ("notas", "negocio"):
            (self.pasta / nome).mkdir()
        (self.pasta / "segredo.env").write_text("", encoding="utf-8")
        (self.pasta / "notas" / "a.xml").write_text("<evento/>", encoding="utf-8")
        self.config = self.pasta / "config.toml"
        self.config.write_text(
            f"[fontes]\npasta_notas = '{self.pasta / 'notas'}'\npasta_negocio = '{self.pasta / 'negocio'}'\n"
            f"arquivo_env = '{self.pasta / 'segredo.env'}'\n"
            "[envio]\nvps_ssh = 'u@s.exemplo'\narquivo_env_remoto = '/x'\ndestinatarios_extras = []\n",
            encoding="utf-8",
        )
        patch = mock.patch("alerta_preco.config.CAMINHO_PADRAO", self.config)
        patch.start()
        self.addCleanup(patch.stop)

    def _rodar(self, gabarito: dict[str, object]) -> tuple[int, str]:
        arquivo = self.pasta / "gabarito.json"
        arquivo.write_text(json.dumps(gabarito), encoding="utf-8")
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida), contextlib.redirect_stderr(saida):
            codigo = main(["--conferir-leitura", str(arquivo)])
        return codigo, saida.getvalue()

    def test_tudo_igual_sai_com_zero(self) -> None:
        codigo, saida = self._rodar({"a.xml": None})
        self.assertEqual(codigo, 0)
        self.assertIn("1 de 1", saida)

    def test_diferenca_sai_com_um(self) -> None:
        codigo, saida = self._rodar({"fantasma.xml": None})
        self.assertEqual(codigo, 1)
        self.assertIn("fantasma.xml", saida)

    def test_sem_config_diz_qual_falta_e_sai_com_um(self) -> None:
        self.config.unlink()
        codigo, saida = self._rodar({})
        self.assertEqual(codigo, 1)
        self.assertIn("config.toml", saida)

    def test_gabarito_ausente_sai_com_um(self) -> None:
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida), contextlib.redirect_stderr(saida):
            codigo = main(["--conferir-leitura", str(self.pasta / "nao-existe.json")])
        self.assertEqual(codigo, 1)
        self.assertIn("nao-existe.json", saida.getvalue())


if __name__ == "__main__":
    unittest.main()
