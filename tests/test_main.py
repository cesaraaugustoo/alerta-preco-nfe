import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from alerta_preco.__main__ import main
from alerta_preco.estado import Estado, gravar_estado, registrar_envio
from alerta_preco.fontes import ErroDeFonte


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


class _BancoFalso:
    def __init__(self, _credenciais: object) -> None:
        pass

    def regras(self) -> dict[str, object]:
        return {}

    def canceladas(self) -> set[str]:
        return {"3" * 44}

    def insumos(self) -> dict[str, tuple[str, str]]:
        return {}

    def configs_espiao(self) -> list[object]:
        return []


def _xml(chave: str, data: str, preco: str, numero: str) -> str:
    return (
        f'<NFe><infNFe Id="NFe{chave}"><ide><natOp>VENDA</natOp><nNF>{numero}</nNF><dhEmi>{data}T08:00:00-03:00</dhEmi></ide>'
        "<emit><CNPJ>11111111000111</CNPJ><xNome>FORNECEDOR A</xNome></emit>"
        f'<det nItem="1"><prod><cProd>007</cProd><xProd>PRODUTO A</xProd><uCom>CX</uCom><qCom>2</qCom><vProd>{preco}</vProd></prod></det>'
        f"<total><vNF>{preco}</vNF></total><pag><detPag><tPag>15</tPag><vPag>{preco}</vPag></detPag></pag></infNFe></NFe>"
    )


class TestSimular(unittest.TestCase):
    def setUp(self) -> None:
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.pasta = Path(pasta.name)
        notas = self.pasta / "notas" / "2026"
        notas.mkdir(parents=True)
        (self.pasta / "negocio").mkdir()
        for nome in ("11-apelidos-fornecedores.md", "15-notas-fora-do-balanco.md", "01-empresa.md"):
            (self.pasta / "negocio" / nome).write_text("", encoding="utf-8")
        (self.pasta / "segredo.env").write_text("VITE_SUPABASE_URL=https://b.exemplo\nSUPABASE_SERVICE_ROLE_KEY=x\n", encoding="utf-8")
        (notas / "a.xml").write_text(_xml("1" * 44, "2026-03-03", "200.00", "1"), encoding="utf-8")
        (notas / "b.xml").write_text(_xml("2" * 44, "2026-03-10", "280.00", "2"), encoding="utf-8")
        (notas / "c.xml").write_text(_xml("3" * 44, "2026-03-11", "900.00", "3"), encoding="utf-8")  # cancelada
        config = self.pasta / "config.toml"
        config.write_text(
            f"[fontes]\npasta_notas = '{self.pasta / 'notas'}'\npasta_negocio = '{self.pasta / 'negocio'}'\n"
            f"arquivo_env = '{self.pasta / 'segredo.env'}'\n"
            "[envio]\nvps_ssh = 'u@s.exemplo'\narquivo_env_remoto = '/x'\ndestinatarios_extras = []\n",
            encoding="utf-8",
        )
        for alvo, valor in (("alerta_preco.config.CAMINHO_PADRAO", config), ("alerta_preco.__main__.Banco", _BancoFalso)):
            patch = mock.patch(alvo, valor)
            patch.start()
            self.addCleanup(patch.stop)

    def _rodar(self, *argumentos: str) -> tuple[int, str]:
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida), contextlib.redirect_stderr(saida):
            codigo = main(list(argumentos))
        return codigo, saida.getvalue()

    def test_mostra_os_alertas_semana_a_semana(self) -> None:
        codigo, saida = self._rodar("--simular", "2026-03-02", "2026-03-15")
        self.assertEqual(codigo, 0)
        self.assertIn("Por semana: 0, 1", saida)
        self.assertIn("Total: 1 alerta", saida)
        self.assertIn("007", saida)
        self.assertIn("100,00", saida)
        self.assertIn("140,00", saida)
        self.assertIn("mesmo produto", saida)

    def test_nao_grava_estado(self) -> None:
        estado = self.pasta / "estado.json"
        with mock.patch("alerta_preco.estado.CAMINHO_PADRAO", estado):
            self._rodar("--simular", "2026-03-02", "2026-03-15")
        self.assertFalse(estado.exists())

    def test_falha_do_banco_sai_com_um(self) -> None:
        def falhar(_self: object) -> set[str]:
            raise ErroDeFonte("Banco, tabela notas_fiscais: HTTP 500.")

        with mock.patch.object(_BancoFalso, "canceladas", falhar):
            codigo, saida = self._rodar("--simular", "2026-03-02", "2026-03-15")
        self.assertEqual(codigo, 1)
        self.assertIn("HTTP 500", saida)

    def test_data_invalida_sai_com_um(self) -> None:
        self.assertEqual(self._rodar("--simular", "ontem", "2026-03-15")[0], 1)


class TestEnsaio(TestSimular):
    """O mesmo cenário: 03/03 a 100,00; 10/03 a 140,00; 11/03 cancelada no banco."""

    def setUp(self) -> None:
        super().setUp()
        self.local = self.pasta / "local"
        for alvo, valor in (
            ("alerta_preco.config.PASTA_LOCAL", self.local),
            ("alerta_preco.estado.CAMINHO_PADRAO", self.local / "estado.json"),
            ("alerta_preco.__main__._hoje", lambda: date(2026, 3, 17)),
        ):
            patch = mock.patch(alvo, valor)
            patch.start()
            self.addCleanup(patch.stop)

    def test_semana_mostra_e_salva_a_previa(self) -> None:
        codigo, saida = self._rodar("--semana", "2026-03-09")
        self.assertEqual(codigo, 0)
        self.assertIn("Preços: 1 subiu de 09/03 a 15/03 (R$ 80 a mais)", saida)
        self.assertIn("R$ 140,00 por CX", saida)
        texto = (self.local / "2026-03-09.txt").read_text(encoding="utf-8")
        html = (self.local / "2026-03-09.html").read_text(encoding="utf-8")
        self.assertIn("R$ 140,00 por CX", texto)
        self.assertIn("R$ 140,00 por CX", html)
        self.assertIn("nada enviado", saida)

    def test_qualquer_dia_vale_pela_segunda_da_semana(self) -> None:
        self.assertEqual(self._rodar("--semana", "2026-03-12")[0], 0)
        self.assertTrue((self.local / "2026-03-09.txt").exists())

    def test_sem_argumento_e_a_semana_passada(self) -> None:
        codigo, _ = self._rodar()
        self.assertEqual(codigo, 0)
        self.assertTrue((self.local / "2026-03-09.txt").exists())

    def test_ensaio_nao_grava_estado(self) -> None:
        self._rodar("--semana", "2026-03-09")
        self.assertFalse((self.local / "estado.json").exists())

    def test_o_servico_de_notas_cancela_a_nota_da_rodada(self) -> None:
        with mock.patch("alerta_preco.__main__.canceladas_no_espiao", return_value=({"2" * 44}, [])) as espiao:
            _, saida = self._rodar("--semana", "2026-03-09")
        self.assertEqual(espiao.call_args.args[1:3], ("2026-03-09", "2026-03-15"))
        self.assertIn("nenhuma alta", saida)

    def test_falha_do_servico_de_notas_segue_e_avisa(self) -> None:
        with mock.patch("alerta_preco.__main__.canceladas_no_espiao", return_value=(set(), ["falhou"])):
            codigo, saida = self._rodar("--semana", "2026-03-09")
        self.assertEqual(codigo, 0)
        self.assertIn("Não consegui conferir cancelamento", saida)

    def test_nota_atrasada_entra_com_estado(self) -> None:
        estado = self.local / "estado.json"
        gravar_estado(Estado(semanas_enviadas=["2026-03-02"], notas_avaliadas={"1" * 44}), estado)
        with mock.patch("alerta_preco.__main__._hoje", lambda: date(2026, 3, 24)):
            _, saida = self._rodar("--semana", "2026-03-16")
        self.assertIn("R$ 140,00 por CX", saida)
        self.assertIn("chegou depois", saida)

    def test_semana_ja_enviada_nao_e_suprimida_no_ensaio(self) -> None:
        estado = self.local / "estado.json"
        gravar_estado(registrar_envio(None, date(2026, 3, 9), {"2" * 44}, {"11111111|007|CX": 140.0}), estado)
        _, saida = self._rodar("--semana", "2026-03-09")
        self.assertIn("R$ 140,00 por CX", saida)

    def test_preco_ja_avisado_em_outra_semana_nao_repete(self) -> None:
        gravar_estado(Estado(semanas_enviadas=["2026-03-02"], ultimo_avisado={"11111111|007|CX": 140.0}), self.local / "estado.json")
        _, saida = self._rodar("--semana", "2026-03-09")
        self.assertIn("nenhuma alta", saida)

    def test_estado_corrompido_sai_com_um(self) -> None:
        self.local.mkdir()
        (self.local / "estado.json").write_text("{ quebrado", encoding="utf-8")
        codigo, saida = self._rodar("--semana", "2026-03-09")
        self.assertEqual(codigo, 1)
        self.assertIn("estado.json", saida)


if __name__ == "__main__":
    unittest.main()
