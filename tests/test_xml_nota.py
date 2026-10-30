import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alerta_preco.xml_nota import arquivos_xml, como_dict, conferir_leitura, ler_arquivo, ler_nota_xml, ler_pasta

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ESPERADO = json.loads((FIXTURES / "esperado.json").read_text(encoding="utf-8"))
CHAVE_A = "1" * 44
CHAVE_B = "2" * 44


def _nota(chave: str = CHAVE_A, dh_emi: str = "2026-03-10T08:00:00-03:00", v_prod: str = "10.00", extra: str = "") -> str:
    return (
        f'<NFe><infNFe Id="NFe{chave}"><ide><natOp>VENDA</natOp><nNF>7</nNF><dhEmi>{dh_emi}</dhEmi></ide>'
        f'<det nItem="1"><prod><cProd>X</cProd><xProd>PRODUTO</xProd><uCom>UN</uCom><qCom>1</qCom>'
        f"<vProd>{v_prod}</vProd>{extra}</prod></det>"
        f"<total><vNF>{v_prod}</vNF></total><pag><detPag><tPag>15</tPag><vPag>{v_prod}</vPag></detPag></pag></infNFe></NFe>"
    )


class TestContrato(unittest.TestCase):
    """A saída de cada XML é igual à de lerNotaXml, gravada pelo JavaScript em esperado.json."""

    def test_cada_fixture_igual_ao_javascript(self) -> None:
        xmls = sorted(p.name for p in FIXTURES.glob("*.xml"))
        self.assertEqual(xmls, sorted(ESPERADO))
        for nome in xmls:
            with self.subTest(arquivo=nome):
                nota = ler_arquivo(FIXTURES / nome)
                self.assertEqual(como_dict(nota) if nota else None, ESPERADO[nome])


class TestLeitura(unittest.TestCase):
    def test_meio_centavo_sobe_como_no_javascript(self) -> None:
        nota = ler_arquivo(FIXTURES / "meio_centavo.xml")
        assert nota is not None
        self.assertEqual([i.valor_nf_item for i in nota.itens], [6.38, 5.88])

    def test_soma_das_duplicatas_nao_e_compensada(self) -> None:
        nota = ler_arquivo(FIXTURES / "dez_duplicatas.xml")
        assert nota is not None
        self.assertEqual(nota.valor_fatura, 0.9999999999999999)

    def test_valor_invalido_vira_zero(self) -> None:
        nota = ler_nota_xml(_nota(v_prod="abc"))
        assert nota is not None
        self.assertEqual(nota.itens[0].valor_total, 0)

    def test_numero_com_lixo_no_fim_usa_o_comeco(self) -> None:
        nota = ler_nota_xml(_nota(v_prod="12,50"))
        assert nota is not None
        self.assertEqual(nota.itens[0].valor_total, 12)

    def test_codigo_mantem_zero_a_esquerda_e_numero_nao(self) -> None:
        nota = ler_arquivo(FIXTURES / "duas_duplicatas.xml")
        assert nota is not None
        self.assertEqual(nota.itens[0].codigo_produto, "000777")
        self.assertEqual(nota.numero, "106")

    def test_texto_sem_chave_nao_e_nota(self) -> None:
        self.assertIsNone(ler_nota_xml("<evento><chNFe>124</chNFe></evento>"))

    def test_arquivo_preserva_o_texto_cru(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "a.xml"
            arquivo.write_bytes(_nota(extra="").replace("PRODUTO", "CAF\xc9 \r\nFORTE").encode("latin-1"))
            nota = ler_arquivo(arquivo)
        assert nota is not None
        self.assertEqual(nota.itens[0].descricao, "CAF� \r\nFORTE")


class TestPasta(unittest.TestCase):
    def setUp(self) -> None:
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.raiz = Path(pasta.name)

    def _gravar(self, caminho: str, texto: str) -> None:
        arquivo = self.raiz / caminho
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(texto, encoding="utf-8")

    def test_varre_todos_os_niveis_sem_a_pasta_teste(self) -> None:
        self._gravar("2026/MES/a.xml", "x")
        self._gravar("2026/SERVICO/000/MES/2026/b.XML", "x")
        self._gravar("2026/MES/c.pdf", "x")
        self._gravar("2026/teste/d.xml", "x")
        self._gravar("2025/MES/e.xml", "x")
        nomes = [p.name for p in arquivos_xml(self.raiz, [2026])]
        self.assertEqual(nomes, ["a.xml", "b.XML"])

    def test_sem_anos_olha_a_raiz_inteira(self) -> None:
        self._gravar("2026/a.xml", "x")
        self._gravar("2025/b.xml", "x")
        self._gravar("TESTE/c.xml", "x")
        self.assertEqual(sorted(p.name for p in arquivos_xml(self.raiz)), ["a.xml", "b.xml"])

    def test_filtra_pela_data_de_emissao(self) -> None:
        self._gravar("2026/a.xml", _nota(CHAVE_A, "2026-03-10T08:00:00-03:00"))
        self._gravar("2026/b.xml", _nota(CHAVE_B, "2026-03-11T08:00:00-03:00"))
        leitura = ler_pasta(self.raiz, "2026-03-11", "2026-03-20")
        self.assertEqual([n.chave_acesso for n in leitura.notas], [CHAVE_B])

    def test_janela_na_virada_le_o_ano_anterior(self) -> None:
        self._gravar("2025/a.xml", _nota(CHAVE_A, "2025-12-30T08:00:00-03:00"))
        self._gravar("2026/b.xml", _nota(CHAVE_B, "2026-01-02T08:00:00-03:00"))
        leitura = ler_pasta(self.raiz, "2025-12-29", "2026-01-04")
        self.assertEqual({n.chave_acesso for n in leitura.notas}, {CHAVE_A, CHAVE_B})

    def test_janela_no_ano_nao_le_o_ano_anterior(self) -> None:
        self._gravar("2025/a.xml", _nota(CHAVE_A, "2026-03-10T08:00:00-03:00"))
        leitura = ler_pasta(self.raiz, "2026-03-01", "2026-03-20")
        self.assertEqual(leitura.notas, [])

    def test_mesma_chave_conta_uma_vez(self) -> None:
        self._gravar("2026/a.xml", _nota(CHAVE_A))
        self._gravar("2026/sub/b.xml", _nota(CHAVE_A))
        self.assertEqual(len(ler_pasta(self.raiz, "2026-03-01", "2026-03-20").notas), 1)

    def test_o_que_nao_e_nota_e_contado(self) -> None:
        self._gravar("2026/a.xml", _nota(CHAVE_A))
        self._gravar("2026/b.xml", "<evento/>")
        leitura = ler_pasta(self.raiz, "2026-03-01", "2026-03-20")
        self.assertEqual(len(leitura.notas), 1)
        self.assertEqual(leitura.ilegiveis, 1)

    def test_arquivo_que_nao_abre_e_contado(self) -> None:
        self._gravar("2026/a.xml", _nota(CHAVE_A))
        with mock.patch.object(Path, "read_bytes", side_effect=OSError("preso")):
            leitura = ler_pasta(self.raiz, "2026-03-01", "2026-03-20")
        self.assertEqual(leitura.notas, [])
        self.assertEqual(leitura.ilegiveis, 1)

    def test_pasta_ausente_interrompe(self) -> None:
        with self.assertRaisesRegex(FileNotFoundError, "nao-existe"):
            ler_pasta(self.raiz / "nao-existe", "2026-03-01", "2026-03-20")


class TestConferencia(unittest.TestCase):
    def setUp(self) -> None:
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.raiz = Path(pasta.name)
        (self.raiz / "2026").mkdir()
        (self.raiz / "2026" / "a.xml").write_text(_nota(CHAVE_A, v_prod="10.00"), encoding="utf-8")
        (self.raiz / "2026" / "b.xml").write_text("<evento/>", encoding="utf-8")
        nota = ler_nota_xml(_nota(CHAVE_A, v_prod="10.00"))
        assert nota is not None
        self.gabarito = {"2026/a.xml": como_dict(nota), "2026/b.xml": None}

    def test_tudo_igual_da_os_totais(self) -> None:
        c = conferir_leitura(self.raiz, self.gabarito)
        self.assertEqual((c.arquivos, c.iguais, c.notas, c.itens), (2, 2, 1, 1))
        self.assertEqual((c.total_javascript, c.total_python), (10.0, 10.0))
        self.assertEqual(c.diferencas, [])
        self.assertTrue(c.tudo_igual)

    def test_centavo_de_diferenca_e_acusado(self) -> None:
        self.gabarito["2026/a.xml"]["itens"][0]["valor_nf_item"] = 10.03
        c = conferir_leitura(self.raiz, self.gabarito)
        self.assertEqual(c.iguais, 1)
        self.assertFalse(c.tudo_igual)
        self.assertIn("2026/a.xml", c.diferencas[0])
        self.assertIn("valor_nf_item", c.diferencas[0])

    def test_abaixo_da_tolerancia_passa(self) -> None:
        self.gabarito["2026/a.xml"]["itens"][0]["valor_nf_item"] = 10.004
        self.assertTrue(conferir_leitura(self.raiz, self.gabarito).tudo_igual)

    def test_texto_diferente_e_acusado(self) -> None:
        self.gabarito["2026/a.xml"]["itens"][0]["descricao"] = "OUTRO"
        self.assertFalse(conferir_leitura(self.raiz, self.gabarito).tudo_igual)

    def test_item_a_mais_e_acusado(self) -> None:
        self.gabarito["2026/a.xml"]["itens"].append(self.gabarito["2026/a.xml"]["itens"][0])
        self.assertFalse(conferir_leitura(self.raiz, self.gabarito).tudo_igual)

    def test_nota_que_so_um_lado_le_e_acusada(self) -> None:
        self.gabarito["2026/b.xml"] = self.gabarito["2026/a.xml"]
        self.assertFalse(conferir_leitura(self.raiz, self.gabarito).tudo_igual)

    def test_arquivo_so_no_gabarito_e_acusado(self) -> None:
        self.gabarito["2026/fantasma.xml"] = None
        c = conferir_leitura(self.raiz, self.gabarito)
        self.assertEqual(c.so_no_gabarito, ["2026/fantasma.xml"])
        self.assertFalse(c.tudo_igual)

    def test_arquivo_so_na_pasta_e_acusado(self) -> None:
        del self.gabarito["2026/b.xml"]
        c = conferir_leitura(self.raiz, self.gabarito)
        self.assertEqual(c.so_na_pasta, ["2026/b.xml"])
        self.assertFalse(c.tudo_igual)


if __name__ == "__main__":
    unittest.main()
