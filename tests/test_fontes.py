import http.client
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from alerta_preco.fontes import (
    Banco,
    ConfigEspiao,
    Credenciais,
    ErroDeFonte,
    canceladas_no_espiao,
    ler_apelidos,
    ler_credenciais,
    ler_lojas,
    ler_notas_fora,
    ler_tabelas,
    periodos_de_consulta,
)
from alerta_preco.modelos import RegraProduto

CHAVE_FALSA = "chave-secreta-de-teste"
TOKEN_FALSO = "token-secreto-de-teste"


class _Resposta(io.BytesIO):
    def __enter__(self) -> "_Resposta":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _ok(dados: object) -> _Resposta:
    return _Resposta(json.dumps(dados).encode("utf-8"))


def _http(codigo: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://servidor.exemplo", codigo, "erro", None, io.BytesIO())  # type: ignore[arg-type]


class _Servidor:
    """Responde em ordem e guarda cada pedido."""

    def __init__(self, *respostas: Any) -> None:
        self.respostas = list(respostas)
        self.pedidos: list[urllib.request.Request] = []

    def __call__(self, pedido: urllib.request.Request) -> Any:
        self.pedidos.append(pedido)
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    def parametros(self, i: int) -> dict[str, str]:
        return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.pedidos[i].full_url).query))


class TestCredenciais(unittest.TestCase):
    def setUp(self) -> None:
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.arquivo = Path(pasta.name) / "segredo.env"

    def test_le_so_as_duas_variaveis(self) -> None:
        self.arquivo.write_text(
            f"# comentario\nOUTRA=nao-leia\nVITE_SUPABASE_URL=\"https://banco.exemplo/\"\nSUPABASE_SERVICE_ROLE_KEY='{CHAVE_FALSA}'\n",
            encoding="utf-8",
        )
        credenciais = ler_credenciais(self.arquivo)
        self.assertEqual(credenciais.url, "https://banco.exemplo")
        self.assertEqual(credenciais.chave, CHAVE_FALSA)

    def test_a_chave_nao_aparece_no_repr(self) -> None:
        self.assertNotIn(CHAVE_FALSA, repr(Credenciais("https://banco.exemplo", CHAVE_FALSA)))

    def test_falta_diz_qual_sem_mostrar_valor(self) -> None:
        self.arquivo.write_text(f"VITE_SUPABASE_URL=https://banco.exemplo\nOUTRA={CHAVE_FALSA}\n", encoding="utf-8")
        with self.assertRaises(ErroDeFonte) as erro:
            ler_credenciais(self.arquivo)
        self.assertIn("SUPABASE_SERVICE_ROLE_KEY", str(erro.exception))
        self.assertNotIn(CHAVE_FALSA, str(erro.exception))

    def test_arquivo_ausente_interrompe(self) -> None:
        with self.assertRaises(ErroDeFonte):
            ler_credenciais(self.arquivo)


class TestBanco(unittest.TestCase):
    def _banco(self, *respostas: Any) -> tuple[Banco, _Servidor]:
        servidor = _Servidor(*respostas)
        return Banco(Credenciais("https://banco.exemplo", CHAVE_FALSA), abrir=servidor), servidor

    def test_so_faz_get_com_os_cabecalhos(self) -> None:
        banco, servidor = self._banco(_ok([]))
        banco.canceladas()
        pedido = servidor.pedidos[0]
        self.assertEqual(pedido.get_method(), "GET")
        self.assertEqual(pedido.get_header("Apikey"), CHAVE_FALSA)
        self.assertEqual(pedido.get_header("Authorization"), f"Bearer {CHAVE_FALSA}")
        self.assertTrue(pedido.full_url.startswith("https://banco.exemplo/rest/v1/notas_fiscais?"))
        self.assertEqual(servidor.parametros(0)["status"], "eq.cancelada")

    def test_pagina_de_mil_em_mil(self) -> None:
        pagina = [{"chave_acesso": str(i)} for i in range(1000)]
        banco, servidor = self._banco(_ok(pagina), _ok([{"chave_acesso": "fim"}]))
        self.assertEqual(len(banco.canceladas()), 1001)
        self.assertEqual((servidor.parametros(0)["offset"], servidor.parametros(0)["limit"]), ("0", "1000"))
        self.assertEqual(servidor.parametros(1)["offset"], "1000")

    def test_pagina_exata_pede_mais_uma_e_para(self) -> None:
        pagina = [{"chave_acesso": str(i)} for i in range(1000)]
        banco, servidor = self._banco(_ok(pagina), _ok([]))
        self.assertEqual(len(banco.canceladas()), 1000)
        self.assertEqual(len(servidor.pedidos), 2)

    def test_regras_pela_raiz_e_codigo_sem_normalizar_o_zero(self) -> None:
        banco, _ = self._banco(_ok([{"cnpj_raiz": "11111111", "codigo_produto": "007", "catalogo_id": 5, "fator": 2.41}]))
        self.assertEqual(banco.regras(), {"11111111|007": RegraProduto("5", 2.41)})

    def test_regra_sem_catalogo_e_sem_fator(self) -> None:
        banco, _ = self._banco(_ok([{"cnpj_raiz": "1", "codigo_produto": "2", "catalogo_id": None, "fator": None}]))
        self.assertEqual(banco.regras(), {"1|2": RegraProduto(None, None)})

    def test_insumos_e_config_do_espiao(self) -> None:
        banco, servidor = self._banco(_ok([{"id": 5, "nome": "INSUMO", "unidade": "KG"}]))
        self.assertEqual(banco.insumos(), {"5": ("INSUMO", "KG")})
        banco, servidor = self._banco(_ok([{"cnpj": "2", "esp_cloud_token": TOKEN_FALSO, "user_token": TOKEN_FALSO}]))
        configs = banco.configs_espiao()
        self.assertEqual(configs[0].cnpj, "2")
        self.assertEqual(servidor.parametros(0)["ativo"], "eq.true")
        self.assertNotIn(TOKEN_FALSO, repr(configs))

    def test_falha_interrompe_sem_mostrar_a_chave(self) -> None:
        for falha in (_http(500), urllib.error.URLError("sem rede"), TimeoutError("demorou")):
            banco, _ = self._banco(falha)
            with self.assertRaises(ErroDeFonte) as erro:
                banco.canceladas()
            self.assertNotIn(CHAVE_FALSA, str(erro.exception))
            self.assertIn("notas_fiscais", str(erro.exception))

    def test_resposta_invalida_interrompe(self) -> None:
        banco, _ = self._banco(_Resposta(b"nao e json"))
        with self.assertRaises(ErroDeFonte):
            banco.canceladas()

    def test_conexao_cortada_no_meio_da_resposta_interrompe(self) -> None:
        banco, _ = self._banco(http.client.IncompleteRead(b""))
        with self.assertRaisesRegex(ErroDeFonte, "notas_fiscais"):
            banco.canceladas()

    def test_resposta_que_nao_e_lista_interrompe(self) -> None:
        banco, _ = self._banco(_ok({"mensagem": "erro"}))
        with self.assertRaisesRegex(ErroDeFonte, "notas_fiscais"):
            banco.canceladas()

    def test_linha_sem_a_coluna_esperada_interrompe(self) -> None:
        banco, _ = self._banco(_ok([{"outra": 1}]))
        with self.assertRaisesRegex(ErroDeFonte, "regra_produto"):
            banco.regras()


class TestPeriodos(unittest.TestCase):
    def test_pedacos_de_30_dias(self) -> None:
        self.assertEqual(
            periodos_de_consulta("2026-01-01", "2026-03-05"),
            [("2026-01-01", "2026-01-30"), ("2026-01-31", "2026-03-01"), ("2026-03-02", "2026-03-05")],
        )

    def test_um_dia(self) -> None:
        self.assertEqual(periodos_de_consulta("2026-01-01", "2026-01-01"), [("2026-01-01", "2026-01-01")])


class TestEspiao(unittest.TestCase):
    CONFIG = ConfigEspiao("22222222000122", TOKEN_FALSO, TOKEN_FALSO)
    DE, ATE = "2026-03-01", "2026-03-07"

    def setUp(self) -> None:
        self.esperas: list[float] = []

    def _rodar(self, *respostas: Any) -> tuple[set[str], list[str], _Servidor]:
        servidor = _Servidor(*respostas)
        canceladas, falhas = canceladas_no_espiao([self.CONFIG], self.DE, self.ATE, abrir=servidor, esperar=self.esperas.append)
        return canceladas, falhas, servidor

    def test_acha_a_cancelada_e_manda_os_tokens_no_cabecalho(self) -> None:
        canceladas, falhas, servidor = self._rodar(
            _ok({"notas": [{"situacao": "Cancelada", "chaveAcesso": "a"}, {"situacao": "Autorizada", "chaveAcesso": "b"}], "codigoProximaPagina": "-1"})
        )
        self.assertEqual((canceladas, falhas), ({"a"}, []))
        pedido = servidor.pedidos[0]
        self.assertEqual(pedido.get_method(), "GET")
        self.assertEqual(pedido.get_header("Esp-cloud-token"), TOKEN_FALSO)
        self.assertNotIn(TOKEN_FALSO, pedido.full_url)
        self.assertEqual(servidor.parametros(0)["modelo"], "55")
        self.assertEqual(servidor.parametros(0)["cnpjCpf"], self.CONFIG.cnpj)

    def test_aceita_lista_direta(self) -> None:
        self.assertEqual(self._rodar(_ok([{"situacao": "CANCELADA", "chaveAcesso": "a"}]))[0], {"a"})

    def test_segue_a_paginacao(self) -> None:
        canceladas, _, servidor = self._rodar(
            _ok({"notas": [{"situacao": "cancelada", "chaveAcesso": "a"}], "codigoProximaPagina": "X"}),
            _ok({"notas": [{"situacao": "cancelada", "chaveAcesso": "b"}], "codigoProximaPagina": "-1"}),
        )
        self.assertEqual(canceladas, {"a", "b"})
        self.assertEqual(servidor.parametros(1)["codigoProximaPagina"], "X")

    def test_404_e_periodo_vazio(self) -> None:
        self.assertEqual(self._rodar(_http(404))[:2], (set(), []))

    def test_429_espera_e_tenta_de_novo(self) -> None:
        canceladas, falhas, _ = self._rodar(_http(429), _http(429), _ok([{"situacao": "cancelada", "chaveAcesso": "a"}]))
        self.assertEqual((canceladas, falhas), ({"a"}, []))
        self.assertEqual(self.esperas, [10, 10])

    def test_429_desiste_depois_de_quatro_tentativas(self) -> None:
        _, falhas, servidor = self._rodar(*[_http(429) for _ in range(5)])
        self.assertEqual(len(servidor.pedidos), 5)
        self.assertEqual(len(self.esperas), 4)
        self.assertEqual(len(falhas), 1)

    def test_falha_vira_aviso_sem_token(self) -> None:
        for resposta in (_http(500), urllib.error.URLError("sem rede"), _Resposta(b"nao e json")):
            _, falhas, _ = self._rodar(resposta)
            self.assertEqual(len(falhas), 1)
            self.assertNotIn(TOKEN_FALSO, falhas[0])

    def test_conexao_cortada_vira_aviso(self) -> None:
        self.assertEqual(len(self._rodar(http.client.IncompleteRead(b""))[1]), 1)

    def test_resposta_de_formato_estranho_vira_aviso(self) -> None:
        for nota in ({"situacao": {"codigo": 3}, "chaveAcesso": "a"}, "nao e objeto"):
            canceladas, falhas, _ = self._rodar(_ok({"notas": [nota]}))
            self.assertEqual((canceladas, len(falhas)), (set(), 1))

    def test_proxima_pagina_vazia_encerra(self) -> None:
        canceladas, falhas, servidor = self._rodar(_ok({"notas": [{"situacao": "cancelada", "chaveAcesso": "a"}], "codigoProximaPagina": ""}))
        self.assertEqual((canceladas, falhas, len(servidor.pedidos)), ({"a"}, [], 1))

    def test_proxima_pagina_repetida_vira_aviso_em_vez_de_laco(self) -> None:
        pagina = {"notas": [], "codigoProximaPagina": "X"}
        _, falhas, servidor = self._rodar(_ok(pagina), _ok(pagina))
        self.assertEqual((len(falhas), len(servidor.pedidos)), (1, 2))

    def test_falha_de_um_periodo_nao_para_o_seguinte(self) -> None:
        servidor = _Servidor(_http(500), _ok([{"situacao": "cancelada", "chaveAcesso": "a"}]))
        canceladas, falhas = canceladas_no_espiao([self.CONFIG], "2026-01-01", "2026-02-15", abrir=servidor, esperar=self.esperas.append)
        self.assertEqual((canceladas, len(falhas)), ({"a"}, 1))


class TestTabelas(unittest.TestCase):
    def test_apelidos(self) -> None:
        texto = "# Titulo\n| Raiz | Apelido |\n|---|---|\n| 11111111 | FORNECEDOR A | obs |\r\n| 124 | curto |\n"
        self.assertEqual(ler_apelidos(texto), {"11111111": "FORNECEDOR A"})

    def test_notas_fora_com_a_mesma_chave_do_leitor_original(self) -> None:
        texto = "| Apelido | NF | Loja |\n|---|---|---|\n| Fornecedor Ñ | 7731 | X |\n| FORNECEDOR A | 79 | Y |\n"
        self.assertEqual(ler_notas_fora(texto), {"FORNECEDOR N|7731", "FORNECEDOR A|79"})

    def test_lojas(self) -> None:
        texto = (
            "| **Loja A** | RAZAO (22.222.222/0001-22) | fora do formato |\n"
            "| Razão | 22.222.222/0001-22 | Loja A | fonte |\n| Outra | 11.111.111/0001-11 | Loja B |\n"
        )
        self.assertEqual(ler_lojas(texto), {"22222222000122": "Loja A", "11111111000111": "Loja B"})

    def test_le_os_tres_arquivos(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            (raiz / "11-apelidos-fornecedores.md").write_text("| 11111111 | A |\n", encoding="utf-8")
            (raiz / "15-notas-fora-do-balanco.md").write_text("| A | 1 |\n", encoding="utf-8")
            (raiz / "01-empresa.md").write_text("| X | 22.222.222/0001-22 | Loja A |\n", encoding="utf-8")
            tabelas = ler_tabelas(raiz)
        self.assertEqual(tabelas.apelidos, {"11111111": "A"})
        self.assertEqual(tabelas.notas_fora, {"A|1"})
        self.assertEqual(tabelas.lojas, {"22222222000122": "Loja A"})

    def test_arquivo_ausente_interrompe_dizendo_qual(self) -> None:
        with tempfile.TemporaryDirectory() as pasta, self.assertRaisesRegex(ErroDeFonte, "11-apelidos"):
            ler_tabelas(Path(pasta))


if __name__ == "__main__":
    unittest.main()
