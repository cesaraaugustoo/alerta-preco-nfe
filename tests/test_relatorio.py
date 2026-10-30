import unittest
from dataclasses import replace
from datetime import date

from alerta_preco.modelos import Alerta, NotaForaDaConta
from alerta_preco.relatorio import DadosDoRelatorio, brl, intervalo, montar_relatorio

CNPJ_EMITENTE = "11111111000111"
CNPJ_LOJA = "22222222000122"


def _alerta(**troca: object) -> Alerta:
    padrao = Alerta(
        produto="11111111|A1|CX",
        codigo_produto="A1",
        descricao="PRODUTO A",
        unidade="CX",
        preco=128.75,
        base=104.5,
        alta=128.75 / 104.5 - 1,
        a_mais=52.75,
        quantidade=2,
        numero_nota="124",
        data_emissao="2026-03-11",
        como="mesmo produto",
        materia_prima=None,
        fator=1,
        emitente_cnpj=CNPJ_EMITENTE,
        emitente_nome="FORNECEDOR A LTDA",
        destinatarios=(CNPJ_LOJA,),
        outras_notas=0,
        chegou_depois=False,
    )
    return replace(padrao, **troca)  # type: ignore[arg-type]


def _dados(**troca: object) -> DadosDoRelatorio:
    padrao = DadosDoRelatorio(
        de="2026-03-09",
        ate="2026-03-15",
        alertas=(),
        fora_por_boleto=(),
        falhas_de_cancelamento=0,
        notas_na_rodada=12,
        produtos_sem_base=3,
        nota_mais_nova="2026-03-15",
        hoje=date(2026, 3, 16),
        apelidos={"11111111": "Fornecedor A"},
        lojas={CNPJ_LOJA: "Loja X"},
        insumos={"7": ("INSUMO BASE", "KG")},
    )
    return replace(padrao, **troca)  # type: ignore[arg-type]


class TestFormato(unittest.TestCase):
    def test_moeda(self) -> None:
        self.assertEqual(brl(1234.56), "R$ 1.234,56")
        self.assertEqual(brl(5), "R$ 5,00")
        self.assertEqual(brl(1234567.8), "R$ 1.234.567,80")

    def test_intervalo_de_datas(self) -> None:
        self.assertEqual(intervalo("2026-10-26", "2026-11-01"), "de 26/10 a 01/11")


class TestAssunto(unittest.TestCase):
    def test_com_altas(self) -> None:
        dados = _dados(alertas=(_alerta(a_mais=49.28), _alerta(produto="p2", a_mais=30.29)))
        self.assertEqual(montar_relatorio(dados).assunto, "Preços: 2 subiram de 09/03 a 15/03 (R$ 80 a mais)")

    def test_singular(self) -> None:
        self.assertEqual(montar_relatorio(_dados(alertas=(_alerta(),))).assunto, "Preços: 1 subiu de 09/03 a 15/03 (R$ 53 a mais)")

    def test_milhar_no_assunto(self) -> None:
        self.assertIn("(R$ 1.235 a mais)", montar_relatorio(_dados(alertas=(_alerta(a_mais=1234.6),))).assunto)

    def test_sem_alta(self) -> None:
        self.assertEqual(montar_relatorio(_dados()).assunto, "Preços: nenhuma alta de 09/03 a 15/03")

    def test_teste(self) -> None:
        self.assertEqual(montar_relatorio(_dados(teste=True)).assunto, "[TESTE] Preços: nenhuma alta de 09/03 a 15/03")


class TestCorpo(unittest.TestCase):
    def _ambos(self, dados: DadosDoRelatorio) -> tuple[str, str]:
        relatorio = montar_relatorio(dados)
        return relatorio.texto, relatorio.html

    def _em_ambos(self, dados: DadosDoRelatorio, *trechos: str) -> None:
        for corpo in self._ambos(dados):
            for trecho in trechos:
                self.assertIn(trecho, corpo)

    def test_semana_sem_alta_tem_a_linha_e_o_rodape(self) -> None:
        dados = _dados()
        self._em_ambos(dados, "Nenhum preço subiu de 09/03 a 15/03.", "Não cobre hortifruti nem compra sem nota.")
        texto, _ = self._ambos(dados)
        self.assertNotIn("SUBIU", texto)
        self.assertNotIn("NÃO ENTROU", texto)

    def test_resumo_com_itens_valor_e_periodo(self) -> None:
        dados = _dados(alertas=(_alerta(a_mais=1000.5), _alerta(produto="p2", a_mais=234.06)))
        self._em_ambos(dados, "2 itens subiram, R$ 1.234,56 a mais, de 09/03 a 15/03.")

    def test_item_que_subiu_traz_todos_os_fatos(self) -> None:
        self._em_ambos(
            _dados(alertas=(_alerta(),)),
            "PRODUTO A",
            "Fornecedor A",
            "Loja X",
            "R$ 128,75 por CX",
            "vinha a R$ 104,50",
            "+23,2%",
            "2 CX",
            "R$ 52,75 a mais",
            "NF 124",
            "11/03",
        )

    def test_sem_apelido_usa_a_razao_social(self) -> None:
        self._em_ambos(_dados(alertas=(_alerta(),), apelidos={}), "FORNECEDOR A LTDA")

    def test_duas_lojas_e_mais_notas(self) -> None:
        dados = _dados(
            alertas=(_alerta(destinatarios=(CNPJ_LOJA, "37"), outras_notas=2),),
            lojas={CNPJ_LOJA: "Loja X", "37": "Loja Y"},
        )
        self._em_ambos(dados, "Loja X e Loja Y", "NF 124, 11/03, e mais 2 notas")

    def test_quantidade_quebrada(self) -> None:
        self._em_ambos(_dados(alertas=(_alerta(quantidade=2.41),)), "2,41 CX")

    def test_nota_que_chegou_depois_e_marcada(self) -> None:
        self._em_ambos(_dados(alertas=(_alerta(chegou_depois=True),)), "chegou depois")

    def test_ordem_e_do_maior_valor_para_o_menor(self) -> None:
        dados = _dados(alertas=(_alerta(descricao="MENOR", a_mais=10), _alerta(produto="p2", descricao="MAIOR", a_mais=92)))
        for corpo in self._ambos(dados):
            self.assertLess(corpo.index("MAIOR"), corpo.index("MENOR"))

    def test_troca_fica_a_parte_com_a_materia_prima(self) -> None:
        trocou = _alerta(
            produto="p2", descricao="PRODUTO TROCADO", como="trocou", materia_prima="7", fator=2.0, preco=74.0, base=62.0, alta=0.194
        )
        dados = _dados(alertas=(_alerta(), trocou))
        self._em_ambos(
            dados,
            "PRODUTO TROCADO",
            "R$ 74,00 por CX",
            "INSUMO BASE",
            "R$ 37,00 por KG",
            "antes R$ 31,00",
            "+19,4%",
        )
        texto, html = self._ambos(dados)
        self.assertLess(texto.index("SUBIU"), texto.index("TROCOU DE FORNECEDOR OU MARCA"))
        self.assertLess(texto.index("PRODUTO A"), texto.index("TROCOU DE FORNECEDOR OU MARCA"))
        self.assertLess(texto.index("TROCOU DE FORNECEDOR OU MARCA"), texto.index("PRODUTO TROCADO"))
        self.assertLess(html.index("Trocou de fornecedor ou marca"), html.index("PRODUTO TROCADO"))

    def test_so_troca_nao_mostra_a_secao_subiu(self) -> None:
        trocou = _alerta(como="trocou", materia_prima="7", fator=1.0)
        texto, _ = self._ambos(_dados(alertas=(trocou,)))
        self.assertNotIn("SUBIU\n", texto)
        self.assertIn("TROCOU DE FORNECEDOR OU MARCA", texto)

    def test_preco_pequeno_por_unidade_de_controle_nao_vira_zero(self) -> None:
        trocou = _alerta(como="trocou", materia_prima="7", fator=2000.0, preco=104.0, base=94.0, alta=0.106)
        self._em_ambos(_dados(alertas=(trocou,)), "R$ 0,0520 por KG", "antes R$ 0,0470")

    def test_materia_prima_sem_nome_nao_quebra(self) -> None:
        trocou = _alerta(como="trocou", materia_prima="desconhecida", fator=1.0)
        self._em_ambos(_dados(alertas=(trocou,)), "a mesma matéria-prima")

    def test_nao_entrou_so_quando_ha(self) -> None:
        fora = NotaForaDaConta("c", "55", "2026-03-10", CNPJ_EMITENTE, "FORNECEDOR A LTDA", 275.0, 194.73)
        self._em_ambos(_dados(fora_por_boleto=(fora,)), "Fornecedor A", "55", "itens R$ 275,00", "boleto R$ 194,73")
        self.assertIn("NÃO ENTROU NA CONTA", montar_relatorio(_dados(fora_por_boleto=(fora,))).texto)

    def test_falha_do_cancelamento_e_avisada(self) -> None:
        self._em_ambos(_dados(falhas_de_cancelamento=2), "Não consegui conferir cancelamento")
        self.assertNotIn("Não consegui conferir", montar_relatorio(_dados()).texto)

    def test_rodape(self) -> None:
        self._em_ambos(_dados(), "Li até a nota de 15/03.", "12 notas", "3 produtos sem base")
        self._em_ambos(_dados(notas_na_rodada=1, produtos_sem_base=1), "1 nota,", "1 produto sem base")

    def test_nota_mais_nova_com_mais_de_tres_dias_avisa_o_atraso(self) -> None:
        self._em_ambos(_dados(nota_mais_nova="2026-03-12"), "pasta de notas pode estar atrasada")
        self.assertNotIn("atrasada", montar_relatorio(_dados(nota_mais_nova="2026-03-13")).texto)

    def test_sem_nenhuma_nota_avisa(self) -> None:
        self._em_ambos(_dados(nota_mais_nova=None, notas_na_rodada=0), "Não li nenhuma nota", "pasta de notas pode estar atrasada")


class TestEscape(unittest.TestCase):
    def test_o_que_vem_do_xml_e_escapado_no_html(self) -> None:
        html = montar_relatorio(_dados(alertas=(_alerta(descricao="A<B & <script>x</script>"),))).html
        self.assertIn("A&lt;B &amp; &lt;script&gt;x&lt;/script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_entidade_do_xml_nao_vira_entidade_dupla(self) -> None:
        relatorio = montar_relatorio(_dados(alertas=(_alerta(descricao="CAFE &amp; LEITE &lt;1KG&gt;"),)))
        self.assertIn("CAFE & LEITE <1KG>", relatorio.texto)
        self.assertIn("CAFE &amp; LEITE &lt;1KG&gt;", relatorio.html)
        self.assertNotIn("&amp;amp;", relatorio.html)

    def test_razao_social_e_tabelas_tambem(self) -> None:
        dados = _dados(alertas=(_alerta(emitente_nome="X &amp; Y"),), apelidos={}, lojas={CNPJ_LOJA: "<Loja>"})
        html = montar_relatorio(dados).html
        self.assertIn("X &amp; Y", html)
        self.assertIn("&lt;Loja&gt;", html)


class TestHtml(unittest.TestCase):
    def setUp(self) -> None:
        self.html = montar_relatorio(_dados(alertas=(_alerta(),))).html

    def test_uma_coluna_de_ate_640_px(self) -> None:
        self.assertIn("max-width:640px", self.html)

    def test_sem_recurso_externo_nem_tabela(self) -> None:
        for proibido in ("<img", "<link", "<script", "<style", "http://", "https://", "<table"):
            self.assertNotIn(proibido, self.html)

    def test_declara_utf8_e_idioma(self) -> None:
        self.assertIn('charset="utf-8"', self.html)
        self.assertIn('lang="pt-BR"', self.html)

    def test_sem_emoji(self) -> None:
        self.assertTrue(all(ord(c) < 0x2000 for c in self.html))


if __name__ == "__main__":
    unittest.main()
