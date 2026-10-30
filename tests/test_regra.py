import unittest
from dataclasses import replace

from alerta_preco.modelos import Item, LinhaDePreco, Nota, RegraProduto
from alerta_preco.regra import (
    atinge,
    avaliar_rodada,
    chave_apelido,
    filtrar_notas,
    linhas_de_preco,
    mediana,
    simular,
)

RAIZ_A = "11111111"
RAIZ_B = "33333333"


def _item(codigo: str, preco: float, quantidade: float = 1, unidade: str = "CX", valor_total: float | None = None) -> Item:
    valor = preco * quantidade
    return Item(
        codigo_produto=codigo,
        descricao=f"PRODUTO {codigo}",
        quantidade=quantidade,
        unidade=unidade,
        valor_total=valor if valor_total is None else valor_total,
        valor_nf_item=valor,
        quantidade_tributada=None,
        unidade_tributada=None,
    )


def _nota(chave: str, data: str, *itens: Item, raiz: str = RAIZ_A, numero: str = "1") -> Nota:
    return Nota(
        chave_acesso=chave,
        numero=numero,
        data_emissao=data,
        natureza_operacao="VENDA",
        valor_fatura=sum(i.valor_nf_item for i in itens),
        emitente_cnpj=f"{raiz}000111",
        emitente_nome="FORNECEDOR A LTDA",
        destinatario_cnpj="22222222000122",
        itens=itens,
    )


def _linhas(*notas: Nota, regras: dict[str, RegraProduto] | None = None) -> list[LinhaDePreco]:
    return linhas_de_preco(list(notas), regras or {})


def _rodada(linhas: list[LinhaDePreco], *chaves: str, ultimo: dict[str, float] | None = None) -> list[str]:
    return [a.produto for a in avaliar_rodada(linhas, set(chaves), ultimo or {}).alertas]


class TestFiltros(unittest.TestCase):
    def _filtrar(self, *notas: Nota, canceladas: set[str] | None = None, fora: set[str] | None = None) -> tuple[list[str], list[str]]:
        f = filtrar_notas(list(notas), canceladas or set(), fora or set(), {RAIZ_A: "Fornecedor Á"})
        return [n.chave_acesso for n in f.validas], [n.numero for n in f.fora_por_boleto]

    def test_nota_comum_entra(self) -> None:
        self.assertEqual(self._filtrar(_nota("a", "2026-03-02", _item("1", 10)))[0], ["a"])

    def test_nota_sem_item_valido_fica_fora(self) -> None:
        bonificada = _item("1", 0, 2)
        self.assertEqual(self._filtrar(_nota("a", "2026-03-02", bonificada)), ([], []))

    def test_item_sem_valor_nao_conta_na_nota_que_fica(self) -> None:
        f = filtrar_notas([_nota("a", "2026-03-02", _item("1", 10), _item("2", 0, 2))], set(), set(), {})
        self.assertEqual(f.validas[0].chave_acesso, "a")
        self.assertEqual(len(linhas_de_preco(f.validas, {})), 1)

    def test_cancelada_fica_fora(self) -> None:
        self.assertEqual(self._filtrar(_nota("a", "2026-03-02", _item("1", 10)), canceladas={"a"}), ([], []))

    def test_nota_da_tabela_de_fora_pelo_apelido_e_numero(self) -> None:
        nota = _nota("a", "2026-03-02", _item("1", 10), numero="55")
        self.assertEqual(self._filtrar(nota, fora={"FORNECEDOR A|55"}), ([], []))
        self.assertEqual(self._filtrar(nota, fora={"FORNECEDOR A|57"})[0], ["a"])

    def test_boleto_abaixo_do_minimo_vai_para_nao_entrou(self) -> None:
        nota = _nota("a", "2026-03-02", _item("1", 96, valor_total=100), numero="7")
        self.assertEqual(self._filtrar(nota), ([], ["7"]))

    def test_boleto_nos_limites_entra(self) -> None:
        for chave, preco in (("a", 97.0), ("b", 135.0)):
            nota = _nota(chave, "2026-03-02", _item("1", preco, valor_total=100))
            self.assertEqual(self._filtrar(nota)[0], [chave])

    def test_boleto_acima_do_maximo_vai_para_nao_entrou(self) -> None:
        nota = _nota("a", "2026-03-02", _item("1", 136, valor_total=100), numero="8")
        self.assertEqual(self._filtrar(nota), ([], ["8"]))

    def test_nao_entrou_traz_itens_e_boleto(self) -> None:
        f = filtrar_notas([_nota("a", "2026-03-02", _item("1", 52, valor_total=100))], set(), set(), {})
        self.assertEqual((f.fora_por_boleto[0].soma_itens, f.fora_por_boleto[0].soma_boleto), (100, 52))
        self.assertEqual(f.fora_por_boleto[0].chave_acesso, "a")

    def test_itens_com_total_zero_nao_quebram_a_conta(self) -> None:
        nota = _nota("a", "2026-03-02", _item("1", 10, valor_total=0), numero="9")
        self.assertEqual(self._filtrar(nota), ([], ["9"]))


class TestLinhas(unittest.TestCase):
    def test_ordena_por_data_chave_e_posicao(self) -> None:
        linhas = _linhas(
            _nota("b", "2026-03-02", _item("3", 1)),
            _nota("a", "2026-03-03", _item("4", 1)),
            _nota("a2", "2026-03-02", _item("1", 1), _item("2", 1)),
        )
        self.assertEqual([x.codigo_produto for x in linhas], ["1", "2", "3", "4"])

    def test_preco_e_o_valor_do_item_pela_quantidade(self) -> None:
        self.assertEqual(_linhas(_nota("a", "2026-03-02", _item("1", 12.5, 4)))[0].preco, 12.5)

    def test_unidade_e_zero_a_esquerda_separam_produtos(self) -> None:
        linhas = _linhas(_nota("a", "2026-03-02", _item("007", 1, unidade="CX"), _item("007", 1, unidade="UN"), _item("7", 1)))
        self.assertEqual(
            [x.produto for x in linhas],
            [f"{RAIZ_A}|007|CX", f"{RAIZ_A}|007|UN", f"{RAIZ_A}|7|CX"],
        )

    def test_regra_da_a_materia_prima_e_o_fator(self) -> None:
        regras = {f"{RAIZ_A}|1": RegraProduto("insumo", 2.41)}
        linha = _linhas(_nota("a", "2026-03-02", _item("1", 10)), regras=regras)[0]
        self.assertEqual((linha.materia_prima, linha.fator), ("insumo", 2.41))

    def test_sem_regra_o_fator_e_um(self) -> None:
        linha = _linhas(_nota("a", "2026-03-02", _item("1", 10)))[0]
        self.assertEqual((linha.materia_prima, linha.fator), (None, 1))

    def test_regra_sem_catalogo_ou_sem_fator_nao_da_materia_prima(self) -> None:
        for regra in (RegraProduto(None, 2), RegraProduto("insumo", 0), RegraProduto("insumo", None)):
            linha = _linhas(_nota("a", "2026-03-02", _item("1", 10)), regras={f"{RAIZ_A}|1": regra})[0]
            self.assertIsNone(linha.materia_prima)


class TestMediana(unittest.TestCase):
    def test_impar_e_par(self) -> None:
        self.assertEqual(mediana([3, 1, 2]), 2)
        self.assertEqual(mediana([140, 100, 110, 125]), 117.5)


class TestAvaliacao(unittest.TestCase):
    def test_compara_com_a_mediana_das_quatro_ultimas(self) -> None:
        linhas = _linhas(
            _nota("a", "2026-03-02", _item("1", 500)),  # a quinta para trás não entra
            _nota("b", "2026-03-03", _item("1", 100)),
            _nota("c", "2026-03-04", _item("1", 100)),
            _nota("d", "2026-03-05", _item("1", 110)),
            _nota("e", "2026-03-06", _item("1", 125)),
            _nota("f", "2026-03-09", _item("1", 140, 2)),
        )
        alerta = avaliar_rodada(linhas, {"f"}, {}).alertas[0]
        self.assertEqual(alerta.base, 105)
        self.assertEqual(alerta.como, "mesmo produto")
        self.assertEqual(alerta.a_mais, 70)
        self.assertAlmostEqual(alerta.alta, 140 / 105 - 1)

    def test_mesmo_dia_nao_entra_na_base(self) -> None:
        linhas = _linhas(
            _nota("a", "2026-03-02", _item("1", 100, 10)),
            _nota("b", "2026-03-09", _item("1", 100, 10)),
            _nota("c", "2026-03-09", _item("1", 150, 10)),
        )
        alerta = avaliar_rodada(linhas, {"b", "c"}, {}).alertas[0]
        self.assertEqual(alerta.base, 100)  # a linha de 150 não puxa a base da outra do mesmo dia

    def test_nota_futura_nao_entra_na_base(self) -> None:
        linhas = _linhas(_nota("a", "2026-03-02", _item("1", 100, 10)), _nota("b", "2026-03-09", _item("1", 52, 10)))
        self.assertEqual(_rodada(linhas, "a"), [])

    def test_sem_base_conta_e_segue(self) -> None:
        avaliacao = avaliar_rodada(_linhas(_nota("a", "2026-03-02", _item("1", 100, 10))), {"a"}, {})
        self.assertEqual(avaliacao.alertas, [])
        self.assertEqual(avaliacao.produtos_sem_base, 1)

    def test_troca_compara_pela_materia_prima_com_fator(self) -> None:
        regras = {f"{RAIZ_A}|1": RegraProduto("insumo", 2.0), f"{RAIZ_B}|9": RegraProduto("insumo", 3.0)}
        linhas = linhas_de_preco(
            [
                _nota("a", "2026-03-02", _item("1", 74), raiz=RAIZ_A),  # 37 por unidade de controle
                _nota("b", "2026-03-09", _item("9", 125, 10), raiz=RAIZ_B),  # acima dos 111 da base
            ],
            regras,
        )
        alerta = avaliar_rodada(linhas, {"b"}, {}).alertas[0]
        self.assertEqual(alerta.como, "trocou")
        self.assertEqual(alerta.base, 111)
        self.assertEqual(alerta.a_mais, 140)
        self.assertEqual(alerta.materia_prima, "insumo")

    def test_troca_sem_compra_anterior_da_materia_prima_nao_tem_base(self) -> None:
        regras = {f"{RAIZ_A}|1": RegraProduto("insumo", 2.0)}
        avaliacao = avaliar_rodada(linhas_de_preco([_nota("a", "2026-03-02", _item("1", 74))], regras), {"a"}, {})
        self.assertEqual(avaliacao.produtos_sem_base, 1)

    def _troca(self, *notas: Nota) -> list[LinhaDePreco]:
        regras = {f"{RAIZ_A}|1": RegraProduto("insumo", 1.0), f"{RAIZ_B}|9": RegraProduto("insumo", 1.0)}
        return linhas_de_preco([_nota("a", "2026-03-02", _item("1", 100), raiz=RAIZ_A), *notas], regras)

    def test_produto_que_estreia_usa_a_mesma_base_em_todas_as_notas_da_rodada(self) -> None:
        linhas = self._troca(
            _nota("b", "2026-03-09", _item("9", 131, 13), raiz=RAIZ_B),
            _nota("c", "2026-03-11", _item("9", 131, 39), raiz=RAIZ_B),
        )
        alerta = avaliar_rodada(linhas, {"b", "c"}, {}).alertas[0]
        self.assertEqual(alerta.como, "trocou")
        self.assertEqual(alerta.base, 100)
        self.assertEqual(alerta.a_mais, 1612)  # 52 caixas a mais 31; antes, só as 13 da primeira nota contavam
        self.assertEqual(alerta.quantidade, 52)
        self.assertEqual(alerta.outras_notas, 1)

    def test_nota_seguinte_da_estreia_com_outro_preco_mantem_a_base(self) -> None:
        linhas = self._troca(
            _nota("b", "2026-03-09", _item("9", 110, 10), raiz=RAIZ_B),
            _nota("c", "2026-03-11", _item("9", 140, 10), raiz=RAIZ_B),
        )
        alerta = avaliar_rodada(linhas, {"b", "c"}, {}).alertas[0]
        self.assertEqual((alerta.base, alerta.preco), (100, 140))
        self.assertEqual(alerta.a_mais, 500)

    def test_nota_da_estreia_abaixo_da_base_nao_soma(self) -> None:
        linhas = self._troca(
            _nota("b", "2026-03-09", _item("9", 125, 10), raiz=RAIZ_B),
            _nota("c", "2026-03-11", _item("9", 94, 53), raiz=RAIZ_B),
        )
        self.assertEqual(avaliar_rodada(linhas, {"b", "c"}, {}).alertas[0].a_mais, 250)

    def test_notas_pequenas_da_estreia_somadas_passam_do_minimo(self) -> None:
        linhas = self._troca(
            _nota("b", "2026-03-09", _item("9", 110, 1), raiz=RAIZ_B),
            _nota("c", "2026-03-11", _item("9", 110, 1), raiz=RAIZ_B),
        )
        self.assertEqual(avaliar_rodada(linhas, {"b", "c"}, {}).alertas[0].a_mais, 20)

    def test_na_rodada_seguinte_a_troca_vira_mesmo_produto(self) -> None:
        linhas = self._troca(
            _nota("b", "2026-03-09", _item("9", 131, 13), raiz=RAIZ_B),
            _nota("c", "2026-03-16", _item("9", 150, 2), raiz=RAIZ_B),
        )
        alerta = avaliar_rodada(linhas, {"c"}, {}).alertas[0]
        self.assertEqual((alerta.como, alerta.base), ("mesmo produto", 131))

    def test_produto_com_historico_nao_olha_a_materia_prima(self) -> None:
        regras = {f"{RAIZ_A}|1": RegraProduto("insumo", 1.0), f"{RAIZ_B}|9": RegraProduto("insumo", 1.0)}
        linhas = linhas_de_preco(
            [
                _nota("a", "2026-03-02", _item("9", 10), raiz=RAIZ_B),
                _nota("b", "2026-03-03", _item("1", 100), raiz=RAIZ_A),
                _nota("c", "2026-03-09", _item("1", 100, 10), raiz=RAIZ_A),
            ],
            regras,
        )
        self.assertEqual(_rodada(linhas, "c"), [])

    def test_queda_de_preco_e_ignorada(self) -> None:
        linhas = _linhas(_nota("a", "2026-03-02", _item("1", 100, 10)), _nota("b", "2026-03-09", _item("1", 94, 10)))
        self.assertEqual(avaliar_rodada(linhas, {"b"}, {}).alertas, [])

    def test_limite_de_alta_nos_valores_exatos(self) -> None:
        passa = _linhas(_nota("a", "2026-03-02", _item("1", 100, 10)), _nota("b", "2026-03-09", _item("1", 105, 10)))
        self.assertEqual(len(_rodada(passa, "b")), 1)
        abaixo = _linhas(_nota("a", "2026-03-02", _item("1", 100, 10)), _nota("b", "2026-03-09", _item("1", 104.99, 10)))
        self.assertEqual(_rodada(abaixo, "b"), [])

    def test_cinco_por_cento_exato_nao_cai_por_erro_de_ponto_flutuante(self) -> None:
        self.assertLess(21.63 / 20.6 - 1, 0.05)
        linhas = _linhas(_nota("a", "2026-03-02", _item("1", 20.6, 20)), _nota("b", "2026-03-09", _item("1", 21.63, 20)))
        self.assertEqual(len(_rodada(linhas, "b")), 1)

    def test_limite_em_reais_nos_valores_exatos(self) -> None:
        passa = _linhas(_nota("a", "2026-03-02", _item("1", 100, 2)), _nota("b", "2026-03-09", _item("1", 110, 2)))
        self.assertEqual(len(_rodada(passa, "b")), 1)
        abaixo = _linhas(_nota("a", "2026-03-02", _item("1", 100, 2)), _nota("b", "2026-03-09", _item("1", 109.99, 2)))
        self.assertEqual(_rodada(abaixo, "b"), [])

    def test_atinge_ignora_o_erro_de_ponto_flutuante(self) -> None:
        self.assertTrue(atinge((10.1 - 10.0) * 200, 20))
        self.assertFalse(atinge(19.97, 20))

    def test_agrupa_as_linhas_do_produto_na_rodada(self) -> None:
        linhas = _linhas(
            _nota("a", "2026-03-02", _item("1", 100, 1)),
            _nota("b", "2026-03-09", _item("1", 110, 1)),  # +10, abaixo dos R$ 20 sozinha
            _nota("c", "2026-03-10", _item("1", 126, 1), numero="37"),  # acima da base de 105; a maior alta
            _nota("d", "2026-03-11", _item("1", 94, 5)),  # queda: não soma
        )
        alertas = avaliar_rodada(linhas, {"b", "c", "d"}, {}).alertas
        self.assertEqual(len(alertas), 1)
        self.assertEqual(alertas[0].a_mais, 31)
        self.assertEqual((alertas[0].preco, alertas[0].base), (126, 105))
        self.assertEqual(alertas[0].numero_nota, "37")
        self.assertEqual(alertas[0].quantidade, 2)
        self.assertEqual(alertas[0].outras_notas, 1)

    def test_agrupamento_guarda_as_lojas_sem_repetir(self) -> None:
        def nota(chave: str, data: str, preco: float, destino: str) -> Nota:
            return replace(_nota(chave, data, _item("1", preco, 1)), destinatario_cnpj=destino)

        linhas = _linhas(
            nota("a", "2026-03-02", 100, "X"),
            nota("b", "2026-03-09", 150, "Y"),
            nota("c", "2026-03-10", 150, "X"),
            nota("d", "2026-03-11", 150, "Y"),
        )
        self.assertEqual(avaliar_rodada(linhas, {"b", "c", "d"}, {}).alertas[0].destinatarios, ("Y", "X"))

    def test_alertas_do_maior_valor_para_o_menor(self) -> None:
        linhas = _linhas(
            _nota("a", "2026-03-02", _item("1", 100, 1), _item("2", 100, 1)),
            _nota("b", "2026-03-09", _item("1", 140, 1), _item("2", 150, 1)),
        )
        self.assertEqual([a.codigo_produto for a in avaliar_rodada(linhas, {"b"}, {}).alertas], ["2", "1"])

    def test_nao_olha_notas_fora_da_rodada(self) -> None:
        linhas = _linhas(_nota("a", "2026-03-02", _item("1", 100, 1)), _nota("b", "2026-03-09", _item("1", 150, 1)))
        self.assertEqual(_rodada(linhas, "a"), [])

    def test_marca_a_nota_atrasada(self) -> None:
        linhas = _linhas(_nota("a", "2026-03-02", _item("1", 100, 1)), _nota("b", "2026-03-09", _item("1", 150, 1)))
        self.assertTrue(avaliar_rodada(linhas, {"b"}, {}, atrasadas={"b"}).alertas[0].chegou_depois)
        self.assertFalse(avaliar_rodada(linhas, {"b"}, {}).alertas[0].chegou_depois)


class TestRepeticao(unittest.TestCase):
    def setUp(self) -> None:
        self.produto = f"{RAIZ_A}|1|CX"
        self.linhas = _linhas(_nota("a", "2026-03-02", _item("1", 100, 2)), _nota("b", "2026-03-09", _item("1", 125, 2)))

    def test_ate_dois_por_cento_do_ultimo_aviso_nao_repete(self) -> None:
        for ultimo in (125, 122.55, 127.5):
            self.assertEqual(_rodada(self.linhas, "b", ultimo={self.produto: ultimo}), [])

    def test_acima_da_folga_avisa(self) -> None:
        self.assertEqual(len(_rodada(self.linhas, "b", ultimo={self.produto: 100})), 1)

    def test_guarda_o_preco_do_aviso_sem_mexer_no_que_recebeu(self) -> None:
        recebido: dict[str, float] = {}
        avaliacao = avaliar_rodada(self.linhas, {"b"}, recebido)
        self.assertEqual(avaliacao.avisados, {self.produto: 125})
        self.assertEqual(recebido, {})

    def test_suprimido_nao_atualiza_o_ultimo_aviso(self) -> None:
        self.assertEqual(avaliar_rodada(self.linhas, {"b"}, {self.produto: 125.5}).avisados, {})

    def test_sobe_cai_e_sobe_de_novo(self) -> None:
        linhas = _linhas(
            _nota("a", "2026-03-02", _item("1", 100, 2)),
            _nota("b", "2026-03-09", _item("1", 111, 2)),
            _nota("c", "2026-03-16", _item("1", 100, 2)),
            _nota("d", "2026-03-23", _item("1", 122, 2)),
        )
        semanas = [
            ("2026-03-02", "2026-03-08"),
            ("2026-03-09", "2026-03-15"),
            ("2026-03-16", "2026-03-22"),
            ("2026-03-23", "2026-03-29"),
        ]
        resultado = simular(linhas, semanas)
        self.assertEqual([len(s.alertas) for s in resultado], [0, 1, 0, 1])
        self.assertEqual(resultado[3].alertas[0].base, 100)  # a mediana não deixou o 111 virar o novo normal
        self.assertEqual(resultado[3].alertas[0].preco, 122)

    def test_aumento_mantido_nao_volta_toda_semana(self) -> None:
        linhas = _linhas(
            _nota("a", "2026-03-02", _item("1", 100, 2)),
            _nota("b", "2026-03-09", _item("1", 125, 2)),
            _nota("c", "2026-03-16", _item("1", 125, 2)),
            _nota("d", "2026-03-23", _item("1", 125, 2)),
        )
        semanas = [("2026-03-02", "2026-03-08"), ("2026-03-09", "2026-03-15"), ("2026-03-16", "2026-03-22"), ("2026-03-23", "2026-03-29")]
        self.assertEqual([len(s.alertas) for s in simular(linhas, semanas)], [0, 1, 0, 0])


class TestChaveApelido(unittest.TestCase):
    def test_tira_acento_e_simbolo(self) -> None:
        self.assertEqual(chave_apelido("  Café & Cia. "), "CAFE CIA")
        self.assertEqual(chave_apelido(None), "")


if __name__ == "__main__":
    unittest.main()
