import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from alerta_preco.__main__ import main
from alerta_preco.envio import ErroDeEnvio
from alerta_preco.estado import Estado, carregar_estado, gravar_estado, registrar_envio
from alerta_preco.fontes import ErroDeFonte
from alerta_preco.relatorio import montar_relatorio


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


class _Cenario(unittest.TestCase):
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

class TestSimular(_Cenario):
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


class _CenarioDeEnsaio(_Cenario):
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

class TestEnsaio(_CenarioDeEnsaio):
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

    def test_lista_da_configuracao_chega_ao_relatorio(self) -> None:
        config = self.pasta / "config.toml"
        config.write_text("fornecedores_boleto_conhecido = ['FORNECEDOR B']\n" + config.read_text(encoding="utf-8"), encoding="utf-8")
        with mock.patch("alerta_preco.__main__.montar_relatorio", wraps=montar_relatorio) as montar:
            self._rodar("--semana", "2026-03-09")
        self.assertEqual(montar.call_args.args[0].fornecedores_boleto_conhecido, ("FORNECEDOR B",))

    def test_estado_corrompido_sai_com_um(self) -> None:
        self.local.mkdir()
        (self.local / "estado.json").write_text("{ quebrado", encoding="utf-8")
        codigo, saida = self._rodar("--semana", "2026-03-09")
        self.assertEqual(codigo, 1)
        self.assertIn("estado.json", saida)


class TestEmail(_CenarioDeEnsaio):
    RESPOSTA = "E-mail enviado para 1 destinatario(s)."

    def setUp(self) -> None:
        super().setUp()
        config = self.pasta / "config.toml"
        config.write_text(config.read_text(encoding="utf-8").replace("destinatarios_extras = []", "destinatarios_extras = ['pessoa@exemplo.com']"), encoding="utf-8")
        self.estado = self.local / "estado.json"
        for nome, destino in (("enviar", "enviar_email"), ("aviso", "aviso_na_tela")):
            patch = mock.patch(f"alerta_preco.__main__.{destino}", return_value=self.RESPOSTA)
            setattr(self, nome, patch.start())
            self.addCleanup(patch.stop)

    def _lido(self) -> Estado:
        estado = carregar_estado(self.estado)
        assert estado is not None
        return estado

    def test_sem_email_nao_envia(self) -> None:
        self._rodar("--semana", "2026-03-09")
        self.enviar.assert_not_called()

    def test_rodada_de_verdade_envia_com_os_extras_e_grava_o_estado(self) -> None:
        codigo, saida = self._rodar("--email", "--semana", "2026-03-09")
        self.assertEqual(codigo, 0)
        self.assertIn(self.RESPOSTA, saida)
        vps, env, relatorio, extras = self.enviar.call_args.args
        self.assertEqual((vps, env), ("u@s.exemplo", "/x"))
        self.assertEqual(relatorio.assunto, "Preços: 1 subiu de 09/03 a 15/03 (R$ 80 a mais)")
        self.assertEqual(extras, ("pessoa@exemplo.com",))
        estado = self._lido()
        self.assertEqual(estado.semanas_enviadas, ["2026-03-09"])
        self.assertEqual(estado.ultimo_avisado, {"11111111|007|CX": 140.0})
        self.assertIn("2" * 44, estado.notas_avaliadas)

    def test_sem_semana_a_rodada_e_a_semana_passada(self) -> None:
        self._rodar("--email")
        self.assertEqual(self._lido().semanas_enviadas, ["2026-03-09"])

    def test_teste_vai_sem_os_extras_marcado_e_sem_gravar_estado(self) -> None:
        codigo, _ = self._rodar("--email", "--teste", "--semana", "2026-03-09")
        self.assertEqual(codigo, 0)
        _, _, relatorio, extras = self.enviar.call_args.args
        self.assertEqual(extras, ())
        self.assertTrue(relatorio.assunto.startswith("[TESTE] "))
        self.assertFalse(self.estado.exists())

    def test_teste_ignora_a_semana_ja_enviada(self) -> None:
        gravar_estado(registrar_envio(None, date(2026, 3, 9), set(), {}), self.estado)
        self.assertEqual(self._rodar("--email", "--teste", "--semana", "2026-03-09")[0], 0)
        self.enviar.assert_called_once()

    def test_semana_repetida_recusa_com_zero_e_uma_linha(self) -> None:
        gravar_estado(registrar_envio(None, date(2026, 3, 9), set(), {}), self.estado)
        codigo, saida = self._rodar("--email", "--semana", "2026-03-09")
        self.assertEqual(codigo, 0)
        self.enviar.assert_not_called()
        self.assertIn("já foi enviada", saida)
        self.assertEqual(len(saida.strip().splitlines()), 1)

    def test_reenviar_manda_de_novo_com_os_mesmos_alertas(self) -> None:
        self._rodar("--email", "--semana", "2026-03-09")
        self.enviar.reset_mock()
        codigo, _ = self._rodar("--email", "--reenviar", "--semana", "2026-03-09")
        self.assertEqual(codigo, 0)
        self.assertIn("1 subiu", self.enviar.call_args.args[2].assunto)
        self.assertEqual(self._lido().semanas_enviadas, ["2026-03-09"])
        self.assertEqual(self._lido().ultimo_avisado, {"11111111|007|CX": 140.0})

    def test_falha_de_envio_nao_grava_estado_e_guarda_o_relatorio(self) -> None:
        self.enviar.side_effect = ErroDeEnvio("Falha no envio: SMTPException")
        codigo, saida = self._rodar("--email", "--semana", "2026-03-09")
        self.assertEqual(codigo, 1)
        self.assertFalse(self.estado.exists())
        self.assertTrue((self.local / "2026-03-09.html").exists())
        self.assertIn("SMTPException", saida)
        self.assertIn("2026-03-09.txt", saida)
        self.aviso.assert_called_once()

    def test_a_proxima_tentativa_depois_da_falha_funciona(self) -> None:
        self.enviar.side_effect = ErroDeEnvio("fora do ar")
        self._rodar("--email", "--semana", "2026-03-09")
        self.enviar.side_effect = None
        self.assertEqual(self._rodar("--email", "--semana", "2026-03-09")[0], 0)
        self.assertEqual(self._lido().semanas_enviadas, ["2026-03-09"])

    def test_falha_do_banco_nao_envia_e_avisa_na_tela(self) -> None:
        def falhar(_self: object) -> set[str]:
            raise ErroDeFonte("Banco, tabela notas_fiscais: HTTP 500.")

        with mock.patch.object(_BancoFalso, "canceladas", falhar):
            codigo, _ = self._rodar("--email", "--semana", "2026-03-09")
        self.assertEqual(codigo, 1)
        self.enviar.assert_not_called()
        self.aviso.assert_called_once()

    def test_ensaio_com_falha_nao_mostra_aviso_na_tela(self) -> None:
        def falhar(_self: object) -> set[str]:
            raise ErroDeFonte("Banco, tabela notas_fiscais: HTTP 500.")

        with mock.patch.object(_BancoFalso, "canceladas", falhar):
            self.assertEqual(self._rodar("--semana", "2026-03-09")[0], 1)
        self.aviso.assert_not_called()

    def test_teste_ou_reenviar_sem_email_sao_recusados(self) -> None:
        for opcao in ("--teste", "--reenviar"):
            codigo, saida = self._rodar(opcao, "--semana", "2026-03-09")
            self.assertEqual(codigo, 1)
            self.assertIn("--email", saida)
        self.enviar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
