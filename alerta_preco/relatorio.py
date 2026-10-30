"""O assunto e o corpo do e-mail, em texto e em HTML. Funções puras.

Os dois formatos saem da mesma estrutura, para não dizerem coisas diferentes. Só fatos: o relatório não
diz por que o preço subiu nem o que fazer.
"""

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from .modelos import Alerta, NotaForaDaConta, Relatorio
from .regra import TROCOU

DIAS_ATE_O_AVISO_DE_ATRASO = 3
AVISO_DE_ATRASO = "a pasta de notas pode estar atrasada"
COBERTURA = "Não cobre hortifruti nem compra sem nota."


def numero(valor: float, casas: int = 2) -> str:
    """Número no formato brasileiro: 1.234,56."""
    return f"{valor:,.{casas}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def brl(valor: float, casas: int = 2) -> str:
    return f"R$ {numero(valor, casas)}"


def dia_e_mes(data: str) -> str:
    return f"{data[8:10]}/{data[5:7]}"


def intervalo(de: str, ate: str) -> str:
    return f"de {dia_e_mes(de)} a {dia_e_mes(ate)}"


def _percentual(alta: float) -> str:
    return f"+{numero(alta * 100, 1)}%"


def _quantidade(valor: float) -> str:
    return numero(valor, 3).rstrip("0").rstrip(",")


def _do_xml(texto: str | None) -> str:
    """O texto como está escrito na nota: o XML traz `&amp;`, que escapado de novo viraria `&amp;amp;` no e-mail."""
    return html.unescape(texto or "")


def _plural(quantidade: int, singular: str, plural: str) -> str:
    return f"{quantidade} {singular if quantidade == 1 else plural}"


@dataclass(frozen=True, slots=True)
class DadosDoRelatorio:
    de: str
    ate: str
    alertas: Sequence[Alerta]
    fora_por_boleto: Sequence[NotaForaDaConta]
    falhas_de_cancelamento: int
    notas_na_rodada: int
    produtos_sem_base: int
    nota_mais_nova: str | None
    hoje: date
    apelidos: Mapping[str, str]
    lojas: Mapping[str, str]
    insumos: Mapping[str, tuple[str, str]]
    teste: bool = False


@dataclass(frozen=True, slots=True)
class _Cartao:
    titulo: str
    subtitulo: str
    linhas: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Secao:
    titulo: str
    cartoes: tuple[_Cartao, ...] = ()
    linhas: tuple[str, ...] = ()


def _fornecedor(cnpj: str | None, nome: str | None, apelidos: Mapping[str, str]) -> str:
    return apelidos.get((cnpj or "")[:8]) or _do_xml(nome)


def _subtitulo(alerta: Alerta, dados: DadosDoRelatorio) -> str:
    lojas = list(dict.fromkeys(dados.lojas[c] for c in alerta.destinatarios if c is not None and c in dados.lojas))
    partes = [_fornecedor(alerta.emitente_cnpj, alerta.emitente_nome, dados.apelidos), " e ".join(lojas)]
    return ", ".join(p for p in partes if p)


def _compra(alerta: Alerta) -> str:
    notas = f", e mais {_plural(alerta.outras_notas, 'nota', 'notas')}" if alerta.outras_notas else ""
    depois = " A nota chegou depois." if alerta.chegou_depois else ""
    return (
        f"{_quantidade(alerta.quantidade)} {alerta.unidade}, {brl(alerta.a_mais)} a mais. "
        f"NF {alerta.numero_nota}, {dia_e_mes(alerta.data_emissao)}{notas}.{depois}"
    )


def _cartao_subiu(alerta: Alerta, dados: DadosDoRelatorio) -> _Cartao:
    preco = f"{brl(alerta.preco)} por {alerta.unidade}, vinha a {brl(alerta.base)} ({_percentual(alerta.alta)})"
    return _Cartao(_do_xml(alerta.descricao), _subtitulo(alerta, dados), (preco, _compra(alerta)))


def _cartao_trocou(alerta: Alerta, dados: DadosDoRelatorio) -> _Cartao:
    insumo = dados.insumos.get(alerta.materia_prima or "")
    atual, antes = alerta.preco / alerta.fator, alerta.base / alerta.fator
    casas = 4 if min(atual, antes) < 1 else 2  # o preço por unidade de controle pode ser de centavos
    rotulo = f"Matéria-prima {insumo[0]}" if insumo else "Pela mesma matéria-prima"
    unidade = insumo[1] if insumo else alerta.unidade
    comparacao = f"{rotulo}: {brl(atual, casas)} por {unidade}, antes {brl(antes, casas)} ({_percentual(alerta.alta)})"
    linhas = (f"{brl(alerta.preco)} por {alerta.unidade}", comparacao, _compra(alerta))
    return _Cartao(_do_xml(alerta.descricao), _subtitulo(alerta, dados), linhas)


def _nao_entrou(dados: DadosDoRelatorio) -> tuple[str, ...]:
    linhas = [
        f"Nota com boleto diferente dos itens: {_fornecedor(n.emitente_cnpj, n.emitente_nome, dados.apelidos)} "
        f"{n.numero}, itens {brl(n.soma_itens)}, boleto {brl(n.soma_boleto)}."
        for n in dados.fora_por_boleto
    ]
    if dados.falhas_de_cancelamento:
        consultas = _plural(dados.falhas_de_cancelamento, "consulta", "consultas")
        linhas.append(
            f"Não consegui conferir cancelamento em {consultas}: uma nota cancelada pode ter entrado na conta."
        )
    return tuple(linhas)


def _rodape(dados: DadosDoRelatorio) -> str:
    contagem = (
        f"{_plural(dados.notas_na_rodada, 'nota', 'notas')}, "
        f"{_plural(dados.produtos_sem_base, 'produto sem base', 'produtos sem base')}."
    )
    if dados.nota_mais_nova is None:
        return f"Não li nenhuma nota: {AVISO_DE_ATRASO}. {contagem} {COBERTURA}"
    leitura = f"Li até a nota de {dia_e_mes(dados.nota_mais_nova)}."
    dias = (dados.hoje - date.fromisoformat(dados.nota_mais_nova)).days
    atraso = f" A nota mais nova tem {dias} dias: {AVISO_DE_ATRASO}." if dias > DIAS_ATE_O_AVISO_DE_ATRASO else ""
    return f"{leitura}{atraso} {contagem} {COBERTURA}"


def _em_texto(resumo: str, secoes: Sequence[_Secao], rodape: str) -> str:
    blocos = [resumo]
    for secao in secoes:
        partes = [secao.titulo.upper()]
        partes += [f"{c.titulo}\n{c.subtitulo}\n" + "\n".join(c.linhas) for c in secao.cartoes]
        partes += secao.linhas
        blocos.append("\n\n".join(partes) if secao.cartoes else "\n".join(partes))
    blocos.append(rodape)
    return "\n\n".join(blocos) + "\n"


_FONTE = "font-family:Arial,Helvetica,sans-serif;color:#1f2933"
_TITULO_DA_SECAO = "font-size:13px;font-weight:bold;letter-spacing:0.5px;color:#52606d;margin:24px 0 8px"
_CARTAO = "border:1px solid #d9e2ec;border-radius:6px;padding:12px 14px;margin:0 0 10px"
_TITULO_DO_CARTAO = "font-size:15px;font-weight:bold;margin:0 0 2px"
_SUBTITULO = "font-size:13px;color:#52606d;margin:0 0 8px"
_LINHA = "font-size:14px;line-height:20px;margin:0 0 4px"


def _p(estilo: str, texto: str) -> str:
    return f'<p style="{estilo}">{html.escape(texto)}</p>'


def _em_html(resumo: str, secoes: Sequence[_Secao], rodape: str) -> str:
    corpo = [_p("font-size:16px;line-height:22px;font-weight:bold;margin:0 0 8px", resumo)]
    for secao in secoes:
        corpo.append(_p(_TITULO_DA_SECAO, secao.titulo))
        for c in secao.cartoes:
            linhas = "".join(_p(_LINHA, linha) for linha in c.linhas)
            corpo.append(
                f'<div style="{_CARTAO}">{_p(_TITULO_DO_CARTAO, c.titulo)}{_p(_SUBTITULO, c.subtitulo)}{linhas}</div>'
            )
        corpo += [_p(_LINHA, linha) for linha in secao.linhas]
    corpo.append(_p("font-size:12px;line-height:18px;color:#52606d;margin:24px 0 0", rodape))
    return (
        '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
        f'<body style="margin:0;padding:16px;background:#ffffff;{_FONTE}">'
        f'<div style="max-width:640px;margin:0 auto">{"".join(corpo)}</div></body></html>\n'
    )


def montar_relatorio(dados: DadosDoRelatorio) -> Relatorio:
    periodo = intervalo(dados.de, dados.ate)
    alertas = sorted(dados.alertas, key=lambda a: -a.a_mais)
    quantos = len(alertas)
    total = sum(a.a_mais for a in alertas)

    if quantos:
        verbo = "subiu" if quantos == 1 else "subiram"
        assunto = f"Preços: {quantos} {verbo} {periodo} ({brl(total, 0)} a mais)"
        resumo = f"{_plural(quantos, 'item', 'itens')} {verbo}, {brl(total)} a mais, {periodo}."
    else:
        assunto = f"Preços: nenhuma alta {periodo}"
        resumo = f"Nenhum preço subiu {periodo}."
    if dados.teste:
        assunto = f"[TESTE] {assunto}"

    secoes: list[_Secao] = []
    subiu = tuple(_cartao_subiu(a, dados) for a in alertas if a.como != TROCOU)
    trocou = tuple(_cartao_trocou(a, dados) for a in alertas if a.como == TROCOU)
    nao_entrou = _nao_entrou(dados)
    if subiu:
        secoes.append(_Secao("Subiu", cartoes=subiu))
    if trocou:
        secoes.append(_Secao("Trocou de fornecedor ou marca", cartoes=trocou))
    if nao_entrou:
        secoes.append(_Secao("Não entrou na conta", linhas=nao_entrou))

    rodape = _rodape(dados)
    return Relatorio(assunto, _em_texto(resumo, secoes, rodape), _em_html(resumo, secoes, rodape))
