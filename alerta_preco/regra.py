"""Os filtros e os alertas. Funções puras, sem rede e sem disco."""

import re
import statistics
import unicodedata
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from .config import BOLETO_MAX, BOLETO_MIN, COMPRAS_NA_BASE, FOLGA_REPETICAO, LIMITE_ALTA, MINIMO_REAIS
from .modelos import Alerta, Item, LinhaDePreco, Nota, NotaForaDaConta, RegraProduto

MESMO_PRODUTO = "mesmo produto"
TROCOU = "trocou"
_CASAS_DA_COMPARACAO = 9


def chave_apelido(texto: str | None) -> str:
    """O nome sem acento, em maiúsculas e só com letras e números, para comparar apelidos."""
    decomposto = unicodedata.normalize("NFD", texto or "")
    sem_acento = "".join(c for c in decomposto if not 0x300 <= ord(c) <= 0x36F)
    return re.sub(r"[^A-Z0-9]+", " ", sem_acento.upper()).strip()


def atinge(valor: float, limite: float) -> bool:
    """`valor >= limite`, sem o erro de ponto flutuante: 21,63 / 20,60 é 5% exatos, mas a conta dá 4,99999...%."""
    return round(valor, _CASAS_DA_COMPARACAO) >= limite


def mediana(valores: Iterable[float]) -> float:
    return statistics.median(valores)


def _valido(item: Item) -> bool:
    return item.valor_nf_item > 0 and item.quantidade > 0


def _raiz(cnpj: str | None) -> str:
    return (cnpj or "")[:8]


@dataclass(frozen=True, slots=True)
class FiltroDeNotas:
    validas: list[Nota]
    fora_por_boleto: list[NotaForaDaConta]
    sem_item_valido: int
    canceladas: int
    fora_da_tabela: int


def filtrar_notas(
    notas: Iterable[Nota], canceladas: Collection[str], notas_fora: Collection[str], apelidos: Mapping[str, str]
) -> FiltroDeNotas:
    """Separa as notas que entram na conta. `notas_fora` tem as chaves "apelido|número" da tabela de notas fora."""
    validas: list[Nota] = []
    fora_por_boleto: list[NotaForaDaConta] = []
    sem_item_valido = quantas_canceladas = fora_da_tabela = 0
    for nota in notas:
        itens = [item for item in nota.itens if _valido(item)]
        if not itens:
            sem_item_valido += 1
            continue
        if nota.chave_acesso in canceladas:
            quantas_canceladas += 1
            continue
        apelido = apelidos.get(_raiz(nota.emitente_cnpj))
        if apelido and f"{chave_apelido(apelido)}|{nota.numero}" in notas_fora:
            fora_da_tabela += 1
            continue
        soma_boleto = sum(item.valor_nf_item for item in itens)
        soma_itens = sum(item.valor_total for item in itens)
        if soma_itens <= 0 or not BOLETO_MIN <= soma_boleto / soma_itens <= BOLETO_MAX:
            fora_por_boleto.append(
                NotaForaDaConta(
                    chave_acesso=nota.chave_acesso,
                    numero=nota.numero,
                    data_emissao=nota.data_emissao,
                    emitente_cnpj=nota.emitente_cnpj,
                    emitente_nome=nota.emitente_nome,
                    soma_itens=soma_itens,
                    soma_boleto=soma_boleto,
                )
            )
            continue
        validas.append(nota)
    return FiltroDeNotas(validas, fora_por_boleto, sem_item_valido, quantas_canceladas, fora_da_tabela)


def linhas_de_preco(notas: Iterable[Nota], regras: Mapping[str, RegraProduto]) -> list[LinhaDePreco]:
    """Uma linha por item válido, em ordem de data, chave e posição na nota."""
    linhas: list[LinhaDePreco] = []
    for nota in notas:
        raiz = _raiz(nota.emitente_cnpj)
        for posicao, item in enumerate(nota.itens):
            if not _valido(item):
                continue
            codigo = item.codigo_produto or ""
            regra = regras.get(f"{raiz}|{codigo}")
            tem_conversao = regra is not None and regra.catalogo_id and regra.fator and regra.fator > 0
            linhas.append(
                LinhaDePreco(
                    produto=f"{raiz}|{codigo}|{item.unidade}",
                    codigo_produto=codigo,
                    descricao=item.descricao,
                    unidade=item.unidade,
                    data_emissao=nota.data_emissao,
                    chave_acesso=nota.chave_acesso,
                    posicao=posicao,
                    numero_nota=nota.numero,
                    preco=item.valor_nf_item / item.quantidade,
                    quantidade=item.quantidade,
                    materia_prima=regra.catalogo_id if regra is not None and tem_conversao else None,
                    fator=regra.fator if regra is not None and regra.fator and tem_conversao else 1,
                    emitente_cnpj=nota.emitente_cnpj,
                    emitente_nome=nota.emitente_nome,
                    destinatario_cnpj=nota.destinatario_cnpj,
                )
            )
    return sorted(linhas, key=lambda x: (x.data_emissao, x.chave_acesso, x.posicao))


def _anteriores(linhas: Iterable[LinhaDePreco], data: str) -> list[LinhaDePreco]:
    return [x for x in linhas if x.data_emissao < data][-COMPRAS_NA_BASE:]


def _base(
    linha: LinhaDePreco,
    por_produto: Mapping[str, list[LinhaDePreco]],
    por_materia: Mapping[str, list[LinhaDePreco]],
) -> tuple[float, str] | None:
    anteriores = _anteriores(por_produto[linha.produto], linha.data_emissao)
    if anteriores:
        return mediana(x.preco for x in anteriores), MESMO_PRODUTO
    if linha.materia_prima is None:
        return None
    outras = _anteriores(por_materia[linha.materia_prima], linha.data_emissao)
    if not outras:
        return None
    return mediana(x.preco / x.fator for x in outras) * linha.fator, TROCOU


@dataclass(frozen=True, slots=True)
class _Alta:
    linha: LinhaDePreco
    base: float
    alta: float
    como: str


def _repete(preco: float, ultimo: float | None) -> bool:
    return ultimo is not None and round(abs(preco / ultimo - 1), _CASAS_DA_COMPARACAO) <= FOLGA_REPETICAO


@dataclass(frozen=True, slots=True)
class Avaliacao:
    alertas: list[Alerta]
    produtos_sem_base: int
    avisados: dict[str, float]


def avaliar_rodada(
    linhas: Sequence[LinhaDePreco],
    chaves: Collection[str],
    ultimo_avisado: Mapping[str, float],
    atrasadas: Collection[str] = frozenset(),
) -> Avaliacao:
    """Os alertas das notas em `chaves`. `linhas` traz o histórico inteiro, na ordem de `linhas_de_preco`."""
    por_produto: dict[str, list[LinhaDePreco]] = defaultdict(list)
    por_materia: dict[str, list[LinhaDePreco]] = defaultdict(list)
    for linha in linhas:
        por_produto[linha.produto].append(linha)
        if linha.materia_prima is not None:
            por_materia[linha.materia_prima].append(linha)

    altas: dict[str, list[_Alta]] = defaultdict(list)
    sem_base: set[str] = set()
    # O produto que estreia na rodada mantém a base da primeira nota: comparada com a estreia, a segunda
    # nota daria alta zero, e o relatório contaria só parte do que a troca custou.
    estreias: dict[str, float] = {}
    for linha in linhas:
        if linha.chave_acesso not in chaves:
            continue
        if linha.produto in estreias:
            base, como = estreias[linha.produto], TROCOU
        else:
            achada = _base(linha, por_produto, por_materia)
            if achada is None:
                sem_base.add(linha.produto)
                continue
            base, como = achada
            if como == TROCOU:
                estreias[linha.produto] = base
        alta = linha.preco / base - 1
        if alta > 0:
            altas[linha.produto].append(_Alta(linha, base, alta, como))

    alertas: list[Alerta] = []
    for produto, grupo in altas.items():
        a_mais = sum((a.linha.preco - a.base) * a.linha.quantidade for a in grupo)
        maior = max(grupo, key=lambda a: a.alta)
        if not (atinge(maior.alta, LIMITE_ALTA) and atinge(a_mais, MINIMO_REAIS)):
            continue
        if _repete(maior.linha.preco, ultimo_avisado.get(produto)):
            continue
        linha = maior.linha
        alertas.append(
            Alerta(
                produto=produto,
                codigo_produto=linha.codigo_produto,
                descricao=linha.descricao,
                unidade=linha.unidade,
                preco=linha.preco,
                base=maior.base,
                alta=maior.alta,
                a_mais=a_mais,
                quantidade=sum(a.linha.quantidade for a in grupo),
                numero_nota=linha.numero_nota,
                data_emissao=linha.data_emissao,
                como=maior.como,
                materia_prima=linha.materia_prima,
                fator=linha.fator,
                emitente_cnpj=linha.emitente_cnpj,
                emitente_nome=linha.emitente_nome,
                destinatarios=tuple(dict.fromkeys(a.linha.destinatario_cnpj for a in grupo)),
                outras_notas=len({a.linha.chave_acesso for a in grupo}) - 1,
                chegou_depois=linha.chave_acesso in atrasadas,
            )
        )
    alertas.sort(key=lambda a: -a.a_mais)
    return Avaliacao(alertas, len(sem_base), {a.produto: a.preco for a in alertas})


@dataclass(frozen=True, slots=True)
class ResultadoDaSemana:
    de: str
    ate: str
    alertas: list[Alerta]
    produtos_sem_base: int


def simular(linhas: Sequence[LinhaDePreco], semanas: Iterable[tuple[str, str]]) -> list[ResultadoDaSemana]:
    """Repete as rodadas em ordem, levando o último aviso de uma semana para a outra."""
    ultimo_avisado: dict[str, float] = {}
    resultado: list[ResultadoDaSemana] = []
    for de, ate in semanas:
        chaves = {x.chave_acesso for x in linhas if de <= x.data_emissao <= ate}
        avaliacao = avaliar_rodada(linhas, chaves, ultimo_avisado)
        ultimo_avisado.update(avaliacao.avisados)
        resultado.append(ResultadoDaSemana(de, ate, avaliacao.alertas, avaliacao.produtos_sem_base))
    return resultado
