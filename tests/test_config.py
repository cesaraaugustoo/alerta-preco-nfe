import tempfile
import unittest
from pathlib import Path

from alerta_preco.config import ErroDeConfiguracao, carregar_config, ler_config

RAIZ = Path(__file__).resolve().parent.parent


def _toml(pasta_notas: str, pasta_negocio: str, arquivo_env: str, extras: str = "[]") -> str:
    return f"""
[fontes]
pasta_notas = '{pasta_notas}'
pasta_negocio = '{pasta_negocio}'
arquivo_env = '{arquivo_env}'

[envio]
vps_ssh = 'usuario@servidor.exemplo'
arquivo_env_remoto = '/srv/alerta/.env'
destinatarios_extras = {extras}
"""


class TestConfig(unittest.TestCase):
    def setUp(self) -> None:
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.pasta = Path(pasta.name)
        (self.pasta / "notas").mkdir()
        (self.pasta / "negocio").mkdir()
        (self.pasta / "segredo.env").write_text("", encoding="utf-8")
        self.arquivo = self.pasta / "config.toml"

    def _escrever(self, **troca: str) -> None:
        caminhos = {
            "pasta_notas": str(self.pasta / "notas"),
            "pasta_negocio": str(self.pasta / "negocio"),
            "arquivo_env": str(self.pasta / "segredo.env"),
        }
        self.arquivo.write_text(_toml(**(caminhos | troca)), encoding="utf-8")

    def test_le_a_configuracao_completa(self) -> None:
        self._escrever(extras="['pessoa@exemplo.com']")
        config = carregar_config(self.arquivo)
        self.assertEqual(config.pasta_notas, self.pasta / "notas")
        self.assertEqual(config.vps_ssh, "usuario@servidor.exemplo")
        self.assertEqual(config.arquivo_env_remoto, "/srv/alerta/.env")
        self.assertEqual(config.destinatarios_extras, ("pessoa@exemplo.com",))

    def test_arquivo_ausente_diz_qual_falta(self) -> None:
        with self.assertRaisesRegex(ErroDeConfiguracao, "config.toml"):
            carregar_config(self.arquivo)

    def test_caminho_inexistente_diz_qual_falta(self) -> None:
        self._escrever(pasta_notas=str(self.pasta / "nao-existe"))
        with self.assertRaisesRegex(ErroDeConfiguracao, "pasta_notas.*nao-existe"):
            carregar_config(self.arquivo)

    def test_arquivo_no_lugar_de_pasta_e_recusado(self) -> None:
        self._escrever(pasta_negocio=str(self.pasta / "segredo.env"))
        with self.assertRaisesRegex(ErroDeConfiguracao, "pasta_negocio"):
            carregar_config(self.arquivo)

    def test_campo_ausente_diz_qual_falta(self) -> None:
        self.arquivo.write_text("[fontes]\npasta_notas = 'x'\n", encoding="utf-8")
        with self.assertRaisesRegex(ErroDeConfiguracao, "fontes.pasta_negocio"):
            ler_config(self.arquivo)

    def test_toml_invalido_nao_vaza_excecao_crua(self) -> None:
        self.arquivo.write_text("isto nao e toml", encoding="utf-8")
        with self.assertRaisesRegex(ErroDeConfiguracao, "config.toml"):
            ler_config(self.arquivo)

    def test_extras_precisam_ser_lista_de_texto(self) -> None:
        self._escrever(extras="'pessoa@exemplo.com'")
        with self.assertRaisesRegex(ErroDeConfiguracao, "envio.destinatarios_extras"):
            ler_config(self.arquivo)

    def test_exemplo_do_repositorio_e_valido(self) -> None:
        config = ler_config(RAIZ / "config.example.toml")
        self.assertTrue(config.vps_ssh)
        self.assertTrue(config.destinatarios_extras)


if __name__ == "__main__":
    unittest.main()
